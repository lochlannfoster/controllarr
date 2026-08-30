"""Shared content-preference settings: read + apply, used by both the installer wiring
and the dashboard settings panel so they behave identically.

Accessors are injected:
  arr(app, path, method="GET", data=None) -> (status, body)   # app in {"radarr","sonarr"}
  qbit() -> (opener, base_url) or (None, None)                 # authenticated qBittorrent
  bazarr_post(fields)  / bazarr_get(path)                      # optional, for subtitles

Canonical settings dict keys: size_cap, size_max, min_seeders, audio_language,
allow_unknown, prefer_h264, seed_after_complete, max_active_downloads, subtitle_langs.
"""
import json, re, urllib.parse, urllib.request

X265 = r"\b(x265|h\.?265|hevc)\b"
X264 = r"\b(x264|h\.?264|avc)\b"

def _lang_id(arr, app, name):
    st, langs = arr(app, "/language")
    for l in (langs or []):
        if l.get("name") == name: return l["id"]
    return 1

# Release-title patterns for foreign dubs (Radarr and Sonarr score these -1000 when audio language = Original).
DUB_RX = r"\b(FRENCH|VFF|VFQ|VF2|VFI|TRUEFRENCH|MULTi|DUBBED|ITA(LIAN)?|GERMAN|SPANISH|LATINO|CASTELLANO|DUAL[.-]?AUDIO|HINDI|RUS(SIAN)?)\b"
_CF_SPECS = {   # name -> (implementation, fields)
    "x265-HEVC":       ("ReleaseTitleSpecification", [{"name": "value", "value": X265}]),
    "x264-H264":       ("ReleaseTitleSpecification", [{"name": "value", "value": X264}]),
    "Dubbed-penalty":  ("ReleaseTitleSpecification", [{"name": "value", "value": DUB_RX}]),
    "Original-language": ("LanguageSpecification", [{"name": "value", "value": -2}]),   # -2 = "Original" in the arrs
}
def _cf_ids(arr, app, names, create=True):
    """{name: customFormatId} for `names`, creating any that are missing (when create=True)."""
    st, existing = arr(app, "/customformat")
    have = {c["name"]: c["id"] for c in (existing or [])}
    out = {}
    for nm in names:
        if nm in have: out[nm] = have[nm]; continue
        if not create or nm not in _CF_SPECS: continue
        impl, fields = _CF_SPECS[nm]
        st, body = arr(app, "/customformat", "POST", {"name": nm, "includeCustomFormatWhenRenaming": False,
            "specifications": [{"name": nm, "implementation": impl, "negate": False, "required": True, "fields": fields}]})
        if isinstance(body, dict) and body.get("id"): out[nm] = body["id"]
    return out
def _format_scores(arr, app, prefer_h264, original_lang):
    """The custom-format scores every profile should carry. Formats are created when a preference is ON and
    zeroed (not deleted) when it is OFF, so turning a preference off actually takes effect."""
    scores = {}
    on = ["x265-HEVC", "x264-H264"] if prefer_h264 else []
    if original_lang: on += ["Dubbed-penalty"] + (["Original-language"] if app == "sonarr" else [])
    ids_on = _cf_ids(arr, app, on, create=True)
    ids_off = _cf_ids(arr, app, [n for n in _CF_SPECS if n not in on], create=False)
    want = {"x265-HEVC": -500, "x264-H264": 100, "Dubbed-penalty": -1000, "Original-language": 50}
    for nm, fid in ids_on.items(): scores[fid] = want[nm]
    for nm, fid in ids_off.items(): scores[fid] = 0
    return scores

def _set_unknown(items, allowed):
    for it in items:
        if (it.get("quality") or {}).get("name") == "Unknown":
            it["allowed"] = allowed
        if it.get("items"): _set_unknown(it["items"], allowed)

_MM_KEYS = {"propers", "copy_hardlinks", "recycle_bin", "recycle_days", "min_free_mb"}
def _ok(st): return isinstance(st, int) and 200 <= st < 300
def apply_content(s, arr, bazarr_post=None, apps=("radarr", "sonarr"), log=lambda *a: None):
    """Apply quality/size/seeders/language/media-management to the given arrs. Returns a list of
    error strings (empty = everything accepted) so callers can report an honest result."""
    errs = []
    cap = int(s["size_cap"]); maxcap = int(s.get("size_max") or max(int(cap * 1.25), 50))
    if maxcap < cap: errs.append(f"max size {maxcap} < preferred {cap} MB/min"); maxcap = cap
    lang_name = s.get("audio_language", "Any")
    for app in apps:
        # Radarr honours a per-profile language; Sonarr v4 has no such field (language is done with custom
        # formats), so the real language preference is the custom-format scores below (both apps).
        lang = {"id": -1, "name": "Any"} if lang_name == "Any" else {"id": _lang_id(arr, app, lang_name), "name": lang_name}
        scores = _format_scores(arr, app, bool(s.get("prefer_h264")), lang_name == "Original")
        st, profs = arr(app, "/qualityprofile")
        for p in (profs or []):
            if "language" in p or app == "radarr": p["language"] = lang
            p["upgradeAllowed"] = True
            _set_unknown(p.get("items", []), bool(s.get("allow_unknown")))
            if scores:
                items = p.get("formatItems", []); known = {i.get("format"): i for i in items}
                for fid, sc in scores.items():
                    if fid in known: known[fid]["score"] = sc
                    else: items.append({"format": fid, "name": "", "score": sc})
                p["formatItems"] = items; p["minFormatScore"] = -10000; p.setdefault("minUpgradeFormatScore", 1)
            st, r = arr(app, "/qualityprofile/%d" % p["id"], "PUT", p)
            if not _ok(st): errs.append(f"{app} profile '{p.get('name')}' rejected ({st})")
        st, defs = arr(app, "/qualitydefinition")
        for q in (defs or []):
            if q.get("minSize") and q["minSize"] > 2: q["minSize"] = 2
            q["preferredSize"] = cap; q["maxSize"] = maxcap
            st, r = arr(app, "/qualitydefinition/%d" % q["id"], "PUT", q)
            if not _ok(st): errs.append(f"{app} quality size rejected ({st})"); break
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
    st, defs = arr(app, "/qualitydefinition")
    if defs:
        out["size_cap"] = defs[0].get("preferredSize"); out["size_max"] = defs[0].get("maxSize")
    st, profs = arr(app, "/qualityprofile")
    if profs:
        if profs[0].get("language"):                       # Radarr: per-profile language
            out["audio_language"] = (profs[0].get("language") or {}).get("name", "Any")
        else:                                              # Sonarr v4: infer from the Original-language custom format
            ids = _cf_ids(arr, app, ["Original-language"], create=False)
            oid = ids.get("Original-language")
            out["audio_language"] = "Original" if oid and any(i.get("format") == oid and (i.get("score") or 0) > 0
                                                              for i in profs[0].get("formatItems", [])) else "Any"
        def has_unknown(items):
            for it in items:
                if (it.get("quality") or {}).get("name") == "Unknown" and it.get("allowed"): return True
                if it.get("items") and has_unknown(it["items"]): return True
            return False
        out["allow_unknown"] = has_unknown(profs[0].get("items", []))
        xid = _cf_ids(arr, app, ["x265-HEVC"], create=False).get("x265-HEVC")   # the h264 preference is the x265 penalty; the dub penalty is negative too and must not count
        out["prefer_h264"] = bool(xid) and any(i.get("format") == xid and (i.get("score") or 0) < 0 for i in profs[0].get("formatItems", []))
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
