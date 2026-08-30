"""Adopt existing on-disk media into Radarr + Sonarr (the "my files are there but the arrs
don't know about them" case). Injected arr(app, path, method="GET", data=None) -> (status, body).

TV is added with monitor="existing" and no missing-episode search, so importing a partial
season doesn't stampede-download the gaps."""
import urllib.parse

def import_existing(arr, log=lambda *a: None):
    out = {}
    # ---- Radarr ----
    try:
        qp = (arr("radarr", "/qualityprofile")[1] or [{}])[0].get("id")
        root = (arr("radarr", "/rootfolder")[1] or [{}])[0]
        have = {m.get("tmdbId") for m in (arr("radarr", "/movie")[1] or [])}
        added = failed = 0
        for f in root.get("unmappedFolders", []):
            try:
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
        out["movies"] = {"added": added, "failed": failed}
    except Exception as e:
        out["movies"] = {"error": str(e)}
    # ---- Sonarr ----
    try:
        sqp = (arr("sonarr", "/qualityprofile")[1] or [{}])[0].get("id")
        sroot = (arr("sonarr", "/rootfolder")[1] or [{}])[0]
        have = {s.get("tvdbId") for s in (arr("sonarr", "/series")[1] or [])}
        added = failed = 0
        for f in sroot.get("unmappedFolders", []):
            try:
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
        out["series"] = {"added": added, "failed": failed}
    except Exception as e:
        out["series"] = {"error": str(e)}
    return out
