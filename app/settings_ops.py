"""Shared content-preference settings: read + apply, used by both the installer wiring
and the dashboard settings panel so they behave identically.

Accessors are injected:
  arr(app, path, method="GET", data=None) -> (status, body)   # app in {"radarr","sonarr"}
  qbit() -> (opener, base_url) or (None, None)                 # authenticated qBittorrent
  bazarr_post(fields)  / bazarr_get(path)                      # optional, for subtitles

Canonical settings dict keys: min_seeders, audio_language, seed_after_complete,
max_active_downloads, subtitle_langs.

**Quality correctness is TRaSH's, not ours.** Custom formats, their scores, which qualities a profile
allows, its cutoff and its minFormatScore, and the per-quality size limits all come from the guide
(`app/trash.py` reads and diffs it; `apply_trash` below writes it). This module used to hand-roll five
custom formats and overwrite every quality definition with one MB-per-minute figure; both are gone.
What stays the panel's here: the audio language (a household preference, and a real field on a Radarr
profile), the per-indexer seeder threshold, media management and naming.
"""
import json, urllib.parse, urllib.request

def _lang_id(arr, app, name):
    st, langs = arr(app, "/language")
    for l in (langs or []):
        if l.get("name") == name: return l["id"]
    return 1

_MM_KEYS = {"propers", "copy_hardlinks", "recycle_bin", "recycle_days", "min_free_mb"}
def _ok(st): return isinstance(st, int) and 200 <= st < 300
def apply_content(s, arr, bazarr_post=None, apps=("radarr", "sonarr"), log=lambda *a: None):
    """Apply audio language / seeders / media-management to the given arrs. Returns a list of error strings
    (empty = everything accepted) so callers can report an honest result.

    It deliberately does NOT touch a profile's qualities, cutoff, format scores or the quality definitions:
    those are the guide's, and a save here must never quietly undo a sync (`apply_trash`)."""
    errs = []
    lang_name = s.get("audio_language", "Any")
    for app in apps:
        # Radarr honours a per-profile language; Sonarr v4 has no such field at all, so the setting is offered
        # for movies only rather than faked with custom formats the guide would then fight over.
        if app == "radarr" and "audio_language" in s:
            lang = {"id": -1, "name": "Any"} if lang_name == "Any" else {"id": _lang_id(arr, app, lang_name), "name": lang_name}
            st, profs = arr(app, "/qualityprofile")
            for p in (profs or []):
                if p.get("language") == lang: continue
                p["language"] = lang
                st, r = arr(app, "/qualityprofile/%d" % p["id"], "PUT", p)
                if not _ok(st): errs.append(f"{app} profile '{p.get('name')}' rejected ({st})")
        if s.get("min_seeders") not in (None, ""):
            st, idxs = arr(app, "/indexer")
            for idx in (idxs or []):
                changed = False
                for f in idx.get("fields", []):
                    if f["name"] == "minimumSeeders": f["value"] = int(s["min_seeders"]); changed = True
                if changed: arr(app, "/indexer/%d" % idx["id"], "PUT", idx)
        # media management (propers + hardlinks/recycle-bin/free-space) — only keys that are present
        if _MM_KEYS & set(s):
            st, mm = arr(app, "/config/mediamanagement")
            if isinstance(mm, dict):
                if "propers" in s: mm["downloadPropersAndRepacks"] = "preferAndUpgrade" if s.get("propers") else "doNotPrefer"
                if "copy_hardlinks" in s: mm["copyUsingHardlinks"] = bool(s.get("copy_hardlinks"))
                if "recycle_bin" in s: mm["recycleBin"] = s.get("recycle_bin") or ""
                if "recycle_days" in s: mm["recycleBinCleanupDays"] = int(s.get("recycle_days") or 0)
                if "min_free_mb" in s: mm["minimumFreeSpaceWhenImporting"] = int(s.get("min_free_mb") or 100)
                arr(app, "/config/mediamanagement", "PUT", mm)
        if "rename" in s:
            st, nm = arr(app, "/config/naming")
            if isinstance(nm, dict):
                nm["renameMovies" if app == "radarr" else "renameEpisodes"] = bool(s.get("rename"))
                arr(app, "/config/naming", "PUT", nm)
        log(f"{app}: settings applied" + (f" with {len(errs)} error(s)" if errs else ""))

    if bazarr_post and s.get("subtitle_langs"):
        langs = [x.strip() for x in str(s["subtitle_langs"]).split(",") if x.strip()]
        hi = "True" if s.get("hearing_impaired") else "False"
        prof = [{"profileId": 1, "name": "Default", "cutoff": None, "originalFormat": False, "tag": None,
                 "mustContain": [], "mustNotContain": [],
                 "items": [{"id": i + 1, "language": l, "audio_exclude": "False", "audio_only_include": "False", "hi": hi, "forced": "False"}
                           for i, l in enumerate(langs)]}]
        try:
            bazarr_post([(BAZARR_PROFILES_KEY, json.dumps(prof)),
                         ("settings-general-serie_default_profile", "1"),
                         ("settings-general-movie_default_profile", "1")]); log("Bazarr subtitle langs applied")
        except Exception as e: log(f"bazarr failed: {e}")
    return errs

def read_content(arr, app="radarr", bazarr_get=None):
    out = {}
    if app == "radarr":
        st, profs = arr(app, "/qualityprofile")
        if profs: out["audio_language"] = (profs[0].get("language") or {}).get("name", "Any")
    st, idxs = arr(app, "/indexer")
    for idx in (idxs or []):
        for f in idx.get("fields", []):
            if f["name"] == "minimumSeeders" and f.get("value") is not None:
                out["min_seeders"] = f["value"]; break
        if "min_seeders" in out: break
    st, mm = arr(app, "/config/mediamanagement")
    if isinstance(mm, dict):
        out["propers"] = (mm.get("downloadPropersAndRepacks") == "preferAndUpgrade")
        out["copy_hardlinks"] = bool(mm.get("copyUsingHardlinks"))
        out["recycle_bin"] = mm.get("recycleBin") or ""
        out["recycle_days"] = mm.get("recycleBinCleanupDays") or 0
        out["min_free_mb"] = mm.get("minimumFreeSpaceWhenImporting") or 100
    st, nm = arr(app, "/config/naming")
    if isinstance(nm, dict): out["rename"] = bool(nm.get("renameMovies" if app == "radarr" else "renameEpisodes"))
    return out

# ---- TRaSH Guides: the one writer of custom formats, quality profiles and quality definitions ----
# app/trash.py reads the guide and works out the difference; nothing there writes. Everything below is
# driven by a plan that a person has already seen in full (Settings ▸ Quality & size ▸ TRaSH Guides).
def apply_trash(pl, arr, log=lambda *a: None):
    """Write one TRaSH plan into one arr. The order is the order the preview showed it, and it is the only
    order that works: a profile can score a custom format only once the format exists, and the quality
    definitions are global, so they go last and independently. Returns a list of error strings."""
    app = pl["app"]; a = pl["apply"]; errs = []
    to_update = set(pl["formats"]["update"])
    st, have = arr(app, "/customformat")
    by_name = {c["name"]: c for c in (have or []) if isinstance(c, dict)}
    ids = {c["name"]: c["id"] for c in (have or []) if isinstance(c, dict)}
    for f in a["formats"]:
        name = f["name"]; body = a["bodies"][name]; cur = by_name.get(name)
        if cur is None:
            st, r = arr(app, "/customformat", "POST", body)
            if isinstance(r, dict) and r.get("id"): ids[name] = r["id"]
            else: errs.append(f"{app}: custom format {name!r} refused ({st})")
        elif name in to_update:
            # the guide's regex has moved on; update in place so every profile already scoring it follows
            merged = dict(cur); merged.update(body); merged["id"] = cur["id"]
            st, r = arr(app, "/customformat/%d" % cur["id"], "PUT", merged)
            if not _ok(st): errs.append(f"{app}: custom format {name!r} not updated ({st})")
    log(f"{app}: {len(a['formats'])} custom formats in place")

    st, profs = arr(app, "/qualityprofile")
    cur = next((p for p in (profs or []) if isinstance(p, dict) and p.get("name") == pl["profile"]), None)
    if cur is None:
        # start a new profile from the app's own schema, so any field this version of the arr expects and the
        # guide says nothing about arrives with its default rather than missing
        st, sch = arr(app, "/qualityprofile/schema")
        body = {k: v for k, v in sch.items() if k != "id"} if isinstance(sch, dict) else {}
    else:
        body = dict(cur)
    body["name"] = pl["profile"]
    body.update({k: v for k, v in a["profile"].items() if v is not None})
    if a["items"]: body["items"] = a["items"]
    if a["cutoff"] is not None: body["cutoff"] = a["cutoff"]
    # every format the arr knows appears, so one this profile does not want is scored 0 rather than left at
    # whatever a previous sync (or the panel's old hand-rolled formats) gave it
    want = {f["name"]: f["score"] for f in a["formats"]}
    body["formatItems"] = [{"format": fid, "name": nm, "score": want.get(nm, 0)} for nm, fid in sorted(ids.items())]
    if a.get("language") and app == "radarr":
        body["language"] = {"id": _lang_id(arr, app, a["language"]), "name": a["language"]}
    if cur: st, r = arr(app, "/qualityprofile/%d" % cur["id"], "PUT", body)
    else:   st, r = arr(app, "/qualityprofile", "POST", body)
    if not _ok(st): errs.append(f"{app}: profile {pl['profile']!r} refused ({st})")
    else: log(f"{app}: profile {pl['profile']!r} " + ("updated" if cur else "created"))

    st, defs = arr(app, "/qualitydefinition")
    sizes = {q["quality"]: q for q in a["sizes"]}; n = 0
    for q in (defs or []):
        w = sizes.get((q.get("quality") or {}).get("name"))
        if not w: continue
        if (q.get("minSize"), q.get("preferredSize"), q.get("maxSize")) == (w["min"], w["preferred"], w["max"]): continue
        q["minSize"], q["preferredSize"], q["maxSize"] = w["min"], w["preferred"], w["max"]
        st, r = arr(app, "/qualitydefinition/%d" % q["id"], "PUT", q)
        if _ok(st): n += 1
        else: errs.append(f"{app}: size limits for {w['quality']} rejected ({st})")
    log(f"{app}: {n} quality size limits set")
    return errs

def arr_state(arr, app):
    """Everything a TRaSH apply overwrites, keyed by NAME so a snapshot means the same thing on another box:
    each quality profile's allowed qualities, format scores, cutoff and score floors, plus the global quality
    definitions. Custom formats are not in it on purpose — an apply only ever creates one, and a format
    nothing scores changes nothing, so restoring the profiles below is what undoes the sync."""
    st, cfs = arr(app, "/customformat")
    names = {c["id"]: c["name"] for c in (cfs or []) if isinstance(c, dict)}
    out = {"profiles": [], "quality_definitions": []}
    st, profs = arr(app, "/qualityprofile")
    for p in (profs or []):
        if not isinstance(p, dict): continue
        allowed = {}
        def walk(items):
            for it in items or []:
                q = it.get("quality")
                nm = q.get("name") if isinstance(q, dict) else it.get("name")
                if nm: allowed[nm] = bool(it.get("allowed"))
                if it.get("items"): walk(it["items"])
        walk(p.get("items"))
        out["profiles"].append({"name": p.get("name"), "upgradeAllowed": p.get("upgradeAllowed"),
                                "cutoff": _cutoff_name(p), "language": (p.get("language") or {}).get("name"),
                                "minFormatScore": p.get("minFormatScore"), "cutoffFormatScore": p.get("cutoffFormatScore"),
                                "minUpgradeFormatScore": p.get("minUpgradeFormatScore"), "allowed": allowed,
                                # a format scored 0 is a format this profile does not score: keeping the zeroes
                                # would make two identical profiles compare unequal after a sync created formats
                                "formats": {names.get(f.get("format")) or f.get("name"): f.get("score")
                                            for f in p.get("formatItems", [])
                                            if (f.get("score") or 0) and (names.get(f.get("format")) or f.get("name"))}})
    st, defs = arr(app, "/qualitydefinition")
    out["quality_definitions"] = [{"quality": (q.get("quality") or {}).get("name"), "min": q.get("minSize"),
                                   "preferred": q.get("preferredSize"), "max": q.get("maxSize")} for q in (defs or [])]
    return out

def _cutoff_name(p):
    cid = p.get("cutoff")
    for it in p.get("items", []):
        if it.get("id") == cid and it.get("name"): return it["name"]
        q = it.get("quality")
        if isinstance(q, dict) and q.get("id") == cid: return q["name"]
        for sub in it.get("items", []):
            sq = sub.get("quality")
            if isinstance(sq, dict) and sq.get("id") == cid: return sq["name"]
    return None

def apply_arr_state(state, arr, app, log=lambda *a: None):
    """Put an `arr_state` back. Profiles are matched by name and rewritten in place; a profile the snapshot
    does not know is LEFT ALONE and named in the result — deleting a profile titles are already on is not a
    rollback, it is a second accident. Returns (errors, list of profiles left alone)."""
    errs = []; want = {p["name"]: p for p in state.get("profiles", []) if p.get("name")}
    st, cfs = arr(app, "/customformat")
    ids = {c["name"]: c["id"] for c in (cfs or []) if isinstance(c, dict)}
    st, profs = arr(app, "/qualityprofile")
    extra = []
    for p in (profs or []):
        if not isinstance(p, dict): continue
        w = want.get(p.get("name"))
        if not w: extra.append(p.get("name")); continue
        def walk(items):
            for it in items or []:
                q = it.get("quality")
                nm = q.get("name") if isinstance(q, dict) else it.get("name")
                if nm in w["allowed"]: it["allowed"] = w["allowed"][nm]
                if it.get("items"): walk(it["items"])
        walk(p.get("items"))
        for k in ("upgradeAllowed", "minFormatScore", "cutoffFormatScore", "minUpgradeFormatScore"):
            if w.get(k) is not None: p[k] = w[k]
        if w.get("language") and app == "radarr": p["language"] = {"id": _lang_id(arr, app, w["language"]), "name": w["language"]}
        cid = _cutoff_id_by_name(p, w.get("cutoff"))
        if cid is not None: p["cutoff"] = cid
        p["formatItems"] = [{"format": fid, "name": nm, "score": w["formats"].get(nm, 0)} for nm, fid in sorted(ids.items())]
        st, r = arr(app, "/qualityprofile/%d" % p["id"], "PUT", p)
        if not _ok(st): errs.append(f"{app}: profile {p.get('name')!r} not restored ({st})")
    sizes = {q["quality"]: q for q in state.get("quality_definitions", []) if q.get("quality")}
    st, defs = arr(app, "/qualitydefinition")
    for q in (defs or []):
        w = sizes.get((q.get("quality") or {}).get("name"))
        if not w: continue
        if (q.get("minSize"), q.get("preferredSize"), q.get("maxSize")) == (w["min"], w["preferred"], w["max"]): continue
        q["minSize"], q["preferredSize"], q["maxSize"] = w["min"], w["preferred"], w["max"]
        st, r = arr(app, "/qualitydefinition/%d" % q["id"], "PUT", q)
        if not _ok(st): errs.append(f"{app}: size limits for {w['quality']} not restored ({st})")
    log(f"{app}: {len(want)} profiles restored" + (f"; left alone: {', '.join(extra)}" if extra else ""))
    return errs, extra

def _cutoff_id_by_name(p, name):
    if not name: return None
    for it in p.get("items", []):
        if it.get("name") == name and it.get("id") is not None: return it["id"]
        q = it.get("quality")
        if isinstance(q, dict) and q.get("name") == name: return q["id"]
        for sub in it.get("items", []):
            sq = sub.get("quality")
            if isinstance(sq, dict) and sq.get("name") == name: return sq["id"]
    return None

# ---- qBittorrent download controls (speeds, active limits, ratio, alt-speed) ----
_B_PER_MB = 1048576
def read_qbit(qbit):
    """Return download-control settings; speeds in MB/s (0 = unlimited)."""
    out = {"dl_limit": 0, "up_limit": 0, "alt_dl_limit": 0, "alt_up_limit": 0, "scheduler_enabled": False,
           "sched_from": 8, "sched_to": 23, "max_active_downloads": 3, "max_active_uploads": 3,
           "seed_after_complete": False, "max_ratio": 0}
    op, url = qbit()
    if not op: return out
    try:
        p = json.load(op.open(url + "/api/v2/app/preferences"))
        out["dl_limit"] = round((p.get("dl_limit", 0) or 0) / _B_PER_MB, 2)
        out["up_limit"] = round((p.get("up_limit", 0) or 0) / _B_PER_MB, 2)
        out["alt_dl_limit"] = round((p.get("alt_dl_limit", 0) or 0) / _B_PER_MB, 2)
        out["alt_up_limit"] = round((p.get("alt_up_limit", 0) or 0) / _B_PER_MB, 2)
        out["scheduler_enabled"] = bool(p.get("scheduler_enabled"))
        out["sched_from"] = p.get("schedule_from_hour", 8); out["sched_to"] = p.get("schedule_to_hour", 23)
        out["max_active_downloads"] = p.get("max_active_downloads", 3)
        out["max_active_uploads"] = p.get("max_active_uploads", 3)
        ratio_on = bool(p.get("max_ratio_enabled")); ratio = float(p.get("max_ratio") or 0)
        # "don't seed" is expressed as a ratio limit of 0; a positive limit still means seeding is on
        out["seed_after_complete"] = not (ratio_on and ratio <= 0)
        out["max_ratio"] = ratio if ratio_on and ratio > 0 else 0
    except Exception:
        pass
    return out

def apply_qbit(s, arr, qbit, log=lambda *a: None):
    """Apply download controls. MB/s inputs → bytes/s. Also flips arr removeCompletedDownloads."""
    def mb(k):
        try: return int(float(s.get(k) or 0) * _B_PER_MB)
        except Exception: return 0
    seed = bool(s.get("seed_after_complete"))
    try: mdl = int(float(s.get("max_active_downloads") or 2))
    except Exception: mdl = 2
    if mdl <= 0: mdl = 2                 # 0 / negative = "unlimited" in qBittorrent — never send that
    up = int(s.get("max_active_uploads") or 3) if seed else 0
    ratio = float(s.get("max_ratio") or 0)
    # total active cap must leave room for seeds so they don't crowd out download slots.
    # seeding off = ratio limit enabled at 0 (stop immediately) — same encoding the installer uses.
    prefs = {"dl_limit": mb("dl_limit"), "up_limit": mb("up_limit"),
             "alt_dl_limit": mb("alt_dl_limit"), "alt_up_limit": mb("alt_up_limit"),
             "scheduler_enabled": bool(s.get("scheduler_enabled")),
             # qBittorrent applies a schedule bound only when the hour AND the minute arrive together (appcontroller.cpp: hasKey(hour) && hasKey(min))
             "schedule_from_hour": int(s.get("sched_from") or 8), "schedule_from_min": 0, "schedule_to_hour": int(s.get("sched_to") or 23), "schedule_to_min": 0,
             "queueing_enabled": True, "max_active_downloads": mdl, "max_active_torrents": mdl + up,
             "max_active_uploads": up,
             "max_ratio_enabled": (not seed) or ratio > 0, "max_ratio": ratio if (seed and ratio > 0) else 0,
             "max_seeding_time_enabled": not seed, "max_seeding_time": 0 if not seed else -1}
    op, url = qbit()
    if op:
        try:
            op.open(urllib.request.Request(url + "/api/v2/app/setPreferences",
                    data=urllib.parse.urlencode({"json": json.dumps(prefs)}).encode(), headers={"Referer": url}))
            log("qBittorrent prefs applied")
        except Exception as e: log(f"qbit prefs failed: {e}")
    # auto-remove: explicit setting if given, else remove when not seeding
    remove = s.get("remove_completed")
    remove = (not seed) if remove is None else bool(remove)
    for app in ("radarr", "sonarr"):
        st, dcs = arr(app, "/downloadclient")
        for c in (dcs or []):
            c["removeCompletedDownloads"] = remove
            arr(app, "/downloadclient/%d" % c["id"], "PUT", c)

# ---- Bazarr (providers, scoring, upgrade, embedded, languages) ----
BAZARR_PROVIDERS = ["opensubtitlescom", "subdl", "podnapisi", "tvsubtitles", "yifysubtitles",
                    "subf2m", "subsource", "napiprojekt", "titlovi", "embeddedsubtitles"]
# Form key Bazarr's POST /api/system/settings uses for the language-profile list (same as the installer).
BAZARR_PROFILES_KEY = "languages-profiles"
_BZ_BOOL = lambda v: "True" if v else "False"          # profile items: Bazarr compares these as the strings "True"/"False"
_BZ_FORM_BOOL = lambda v: "true" if v else "false"     # general settings: save_settings turns only lowercase 'true'/'false' into bool; "True" stays a string and fails the is_type_of=bool validator (406)
def read_bazarr(bazarr_get):
    """Read Bazarr general settings + the default language profile for the panel."""
    out = {"providers": BAZARR_PROVIDERS, "enabled_providers": [], "subtitle_langs": "en",
           "hearing_impaired": False, "forced": False, "upgrade_subs": False, "days_to_upgrade_subs": 7,
           "minimum_score": 90, "minimum_score_movie": 70, "adaptive_searching": False,
           "use_embedded_subs": True, "embedded_subs_show_desired": True,
           "ignore_pgs_subs": False, "ignore_vobsub_subs": False}
    try:
        s = bazarr_get("/api/system/settings") or {}
        g = s.get("general", {})
        ep = g.get("enabled_providers") or []
        out["enabled_providers"] = ep if isinstance(ep, list) else [x for x in str(ep).split(",") if x]
        for k in ("upgrade_subs", "adaptive_searching", "use_embedded_subs", "embedded_subs_show_desired",
                  "ignore_pgs_subs", "ignore_vobsub_subs"):
            if k in g: out[k] = bool(g.get(k))
        for k in ("days_to_upgrade_subs", "minimum_score", "minimum_score_movie"):
            if g.get(k) is not None: out[k] = g.get(k)
    except Exception:
        pass
    try:
        # the settings GET has no languages block; profiles live on their own endpoint
        profs = bazarr_get("/api/system/languages/profiles")
        profs = profs.get("data") if isinstance(profs, dict) else profs
        if isinstance(profs, list) and profs:
            items = profs[0].get("items", [])
            out["subtitle_langs"] = ",".join(i.get("language") for i in items if i.get("language")) or "en"
            out["hearing_impaired"] = any(str(i.get("hi")).lower() in ("true", "1") for i in items)
            out["forced"] = any(str(i.get("forced")).lower() in ("true", "1") for i in items)
    except Exception:
        pass
    return out
def apply_bazarr(s, bazarr_post, log=lambda *a: None):
    """Apply Bazarr general settings + the default language profile (langs/HI/forced). Returns a list of error
    strings (empty = accepted). Bazarr 1.6 requires every profile item to carry audio_only_include: without it the
    POST is a 500 (KeyError in list_missing_subtitles) — and, worse, the profile is stored broken before the
    crash, so every later subtitle search fails the same way."""
    fields = []
    if "enabled_providers" in s:
        provs = s.get("enabled_providers") or []
        if isinstance(provs, str): provs = [x for x in provs.split(",") if x]
        for p in provs: fields.append(("settings-general-enabled_providers", p))
    for k in ("upgrade_subs", "adaptive_searching", "use_embedded_subs", "embedded_subs_show_desired",
              "ignore_pgs_subs", "ignore_vobsub_subs"):
        if k in s: fields.append((f"settings-general-{k}", _BZ_FORM_BOOL(s.get(k))))
    for k in ("days_to_upgrade_subs", "minimum_score", "minimum_score_movie"):
        if k in s and s.get(k) not in (None, ""): fields.append((f"settings-general-{k}", str(int(float(s.get(k))))))
    if s.get("subtitle_langs"):
        langs = [x.strip() for x in str(s["subtitle_langs"]).split(",") if x.strip()]
        hi = _BZ_BOOL(s.get("hearing_impaired")); forced = _BZ_BOOL(s.get("forced"))
        prof = [{"profileId": 1, "name": "Default", "cutoff": None, "originalFormat": False, "tag": None,
                 "mustContain": [], "mustNotContain": [],
                 "items": [{"id": i + 1, "language": l, "audio_exclude": "False", "audio_only_include": "False", "hi": hi, "forced": forced}
                           for i, l in enumerate(langs)]}]
        fields += [(BAZARR_PROFILES_KEY, json.dumps(prof)),
                   ("settings-general-serie_default_profile", "1"),
                   ("settings-general-movie_default_profile", "1")]
    errs = []
    if fields:
        try: bazarr_post(fields); log("Bazarr settings applied")
        except Exception as e: errs.append(str(e)[:160]); log(f"bazarr failed: {e}")
    return errs
