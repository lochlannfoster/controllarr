"""Adopt existing on-disk media into Radarr + Sonarr (the "my files are there but the arrs
don't know about them" case). Injected arr(app, path, method="GET", data=None) -> (status, body).

TV is added with monitor="existing" and no missing-episode search, so importing a partial
season doesn't stampede-download the gaps.

A folder is only adopted when it actually holds media. A purge deletes files, not directories, so an
emptied folder outlives the title it held; adopting one re-adds the very thing that was purged, with
nothing in it. `_media_in` asks the arr itself (its manual-import scan of the folder) rather than
looking at the disk, because the panel sees the library at a different path than the arrs do — when
it can see it at all.
"""
import urllib.parse

def _media_in(arr, app, path):
    """True when the arr finds importable media under `path`, False when it certainly finds none,
    None when the question could not be answered (an old arr, a timeout) — the caller adopts on None,
    because refusing to adopt on a broken probe would quietly import nothing at all."""
    try:
        st, rows = arr(app, "/manualimport?filterExistingFiles=false&folder=" + urllib.parse.quote(path))
        if not (200 <= int(st) < 300) or not isinstance(rows, list): return None
        return any(r.get("path") for r in rows if isinstance(r, dict))
    except Exception:
        return None

def _first_profile(arr, app):
    return ((arr(app, "/qualityprofile")[1] or [{}])[0] or {}).get("id")

def import_existing(arr, log=lambda *a: None, profiles=None):
    """Adopt every root-folder entry the arrs do not know yet. `profiles` is {"radarr": id, "sonarr": id};
    an app left out falls back to that app's first profile."""
    profiles = profiles or {}
    out = {}
    # ---- Radarr ----
    try:
        qp = profiles.get("radarr") or _first_profile(arr, "radarr")
        root = (arr("radarr", "/rootfolder")[1] or [{}])[0]
        have = {m.get("tmdbId") for m in (arr("radarr", "/movie")[1] or [])}
        added = failed = skipped = 0
        for f in root.get("unmappedFolders", []):
            try:
                if _media_in(arr, "radarr", f["path"]) is False:
                    skipped += 1; log(f"movie skip {f.get('name')}: no media in the folder"); continue
                hits = arr("radarr", "/movie/lookup?term=" + urllib.parse.quote(f["name"]))[1]
                if not hits: failed += 1; continue
                m = hits[0]
                if m.get("tmdbId") in have: continue
                arr("radarr", "/movie", "POST", {"title": m["title"], "tmdbId": m["tmdbId"], "year": m.get("year"),
                    "qualityProfileId": qp, "rootFolderPath": root["path"], "monitored": True,
                    "minimumAvailability": "released", "path": f["path"], "addOptions": {"searchForMovie": False}})
                have.add(m.get("tmdbId")); added += 1; log(f"movie + {m['title']}")
            except Exception as e:
                failed += 1; log(f"movie fail {f.get('name')}: {e}")
        arr("radarr", "/command", "POST", {"name": "RescanMovie"})
        out["movies"] = {"added": added, "failed": failed, "skipped": skipped}
    except Exception as e:
        out["movies"] = {"error": str(e)}
    # ---- Sonarr ----
    try:
        sqp = profiles.get("sonarr") or _first_profile(arr, "sonarr")
        sroot = (arr("sonarr", "/rootfolder")[1] or [{}])[0]
        have = {s.get("tvdbId") for s in (arr("sonarr", "/series")[1] or [])}
        added = failed = skipped = 0
        for f in sroot.get("unmappedFolders", []):
            try:
                if _media_in(arr, "sonarr", f["path"]) is False:
                    skipped += 1; log(f"series skip {f.get('name')}: no media in the folder"); continue
                hits = arr("sonarr", "/series/lookup?term=" + urllib.parse.quote(f["name"]))[1]
                if not hits: failed += 1; continue
                s = hits[0]
                if s.get("tvdbId") in have: continue
                arr("sonarr", "/series", "POST", {"title": s["title"], "tvdbId": s["tvdbId"], "qualityProfileId": sqp,
                    "rootFolderPath": sroot["path"], "path": f["path"], "monitored": True, "seasonFolder": True,
                    "seriesType": s.get("seriesType", "standard"),
                    "addOptions": {"searchForMissingEpisodes": False, "searchForCutoffUnmetEpisodes": False, "monitor": "existing"}})
                have.add(s.get("tvdbId")); added += 1; log(f"series + {s['title']}")
            except Exception as e:
                failed += 1; log(f"series fail {f.get('name')}: {e}")
        arr("sonarr", "/command", "POST", {"name": "RescanSeries"})
        out["series"] = {"added": added, "failed": failed, "skipped": skipped}
    except Exception as e:
        out["series"] = {"error": str(e)}
    return out
