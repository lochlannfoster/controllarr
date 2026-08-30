"""The library classifier behind the panel: every monitored title in one of seven stages.

generate() takes injected service accessors (arr, qbit, H) so it works both inside the
controllarr container (services reached by container name) and on the host (localhost).
Release-search classification is bounded per call and cached (3 h), so it's cheap to call
often — the live data (queue %, availability, resources) refreshes every call."""
import json, os, shutil, time

SEARCH_BUDGET = 6          # release searches per call
RECHECK_SEC = 3 * 3600     # re-classify a waiting item at most this often

# Single source of truth for the pipeline vocabulary (board sections, filter, badges, tallies, docs).
STAGES = ["Unavailable", "Searching", "Downloading", "Importing", "Partial", "Waiting", "Available"]
ORDER = {"Available": 0, "Downloading": 1, "Importing": 1, "Partial": 2, "Searching": 3, "Waiting": 4, "Unavailable": 5}

def first_missing_season(series):
    """The season to search for a show: the first monitored season (>0) still missing episodes, else the
    last monitored season, else 1. Sonarr ships per-season statistics with the series object, so this
    costs no extra API call (the old code always searched season 1)."""
    best = None
    for s in sorted(series.get("seasons") or [], key=lambda x: x.get("seasonNumber") or 0):
        n = s.get("seasonNumber") or 0
        if n <= 0 or not s.get("monitored"): continue
        st = s.get("statistics") or {}
        if (st.get("episodeFileCount") or 0) < (st.get("episodeCount") or 0): return n
        best = n
    return best or 1

def _poster(images):
    for im in images or []:
        u = im.get("remoteUrl") or ""
        if "poster" in (im.get("coverType") or "") and u.startswith("http"):
            return u
    return ""

def generate(arr, qbit, H, js_base, config_dir, min_seeders=5, backup_dir="", cache=None, data_dir=""):
    """Return (status_dict, cache). `cache` is the persistent per-item classification cache.
    `data_dir` is the media volume to report disk usage for (falls back to '/')."""
    cache = cache if cache is not None else {}
    now = time.time()
    searches = [0]

    # Jellyseerr request map (who requested what)
    req_by_tmdb, req_by_tvdb = {}, {}
    try:
        js_key = json.load(open(os.path.join(config_dir, "jellyseerr", "settings.json")))["main"]["apiKey"]
        st, rs = H("GET", js_base + "/api/v1/request?take=500&sort=added", headers={"X-Api-Key": js_key})
        for r in (rs or {}).get("results", []) if isinstance(rs, dict) else []:
            who = (r.get("requestedBy") or {}).get("jellyfinUsername") or (r.get("requestedBy") or {}).get("displayName")
            m = r.get("media", {})
            if m.get("tmdbId"): req_by_tmdb[m["tmdbId"]] = who
            if m.get("tvdbId"): req_by_tvdb[m["tvdbId"]] = who
    except Exception:
        pass

    def queue_map(app):
        st, q = arr(app, "/queue?pageSize=500&includeUnknownSeriesItems=true")
        out = {}
        for r in (q or {}).get("records", []):
            key = r.get("movieId") if app == "radarr" else r.get("seriesId")
            d = out.setdefault(key, {"size": 0, "left": 0, "state": r.get("trackedDownloadState") or r.get("status"),
                                     "timeleft": r.get("timeleft", ""), "hashes": []})
            d["size"] += r.get("size", 0) or 0; d["left"] += r.get("sizeleft", 0) or 0
            h = (r.get("downloadId") or "").lower()
            if h and h not in d["hashes"]: d["hashes"].append(h)
        for d in out.values():
            d["pct"] = round(100 * (d["size"] - d["left"]) / d["size"]) if d["size"] else 0
        return out
    rq = queue_map("radarr"); sq = queue_map("sonarr")

    def classify(app, item_key, search_path):
        ck = f"{app}:{item_key}"; prev = cache.get(ck, {})
        if prev and (now - prev.get("checked", 0)) < RECHECK_SEC:
            return prev["stage"], prev.get("reason", ""), prev.get("seeders", 0)
        if searches[0] >= SEARCH_BUDGET:
            return prev.get("stage", "Searching"), prev.get("reason", "checking…"), prev.get("seeders", 0)
        searches[0] += 1
        st, rel = arr(app, search_path); rel = rel if isinstance(rel, list) else []
        ok = [r for r in rel if not r.get("rejected")]
        best = max((r.get("seeders") or 0) for r in rel) if rel else 0
        if not rel:
            stage, reason = "Unavailable", "No torrents found"
        elif ok:
            stage, reason = "Searching", "release available — grabbing"
        elif best < min_seeders:
            stage, reason = "Unavailable", f"Only low-seed (max {best})"
        else:
            cats = {}
            for r in rel:
                for rej in (r.get("rejections") or []):
                    low = rej.lower()
                    if "larger than maximum" in low: k = "size"
                    elif "not wanted in profile" in low: k = "quality"
                    elif "unknown series" in low or "unable to parse" in low or "identify correct episode" in low or "matches an alias" in low: k = "match"
                    elif "existing file" in low: k = "have"
                    else: continue
                    cats[k] = cats.get(k, 0) + 1
            top = max(cats, key=cats.get) if cats else ""
            reason = {"size": f"Rejected: too big for the size limit ({best} seeders available)",
                      "quality": f"Rejected: quality not allowed ({best} seeders available)",
                      "match": "Can't match releases to this show (naming/scene mismatch)",
                      "have": "Already have these episodes"}.get(top, f"Rejected by rules ({best} seeders exist)")
            stage = "Available" if top == "have" else "Unavailable"
        cache[ck] = {"stage": stage, "reason": reason, "seeders": best, "checked": now}
        return stage, reason, best

    # the profile a title is on decides what it may grab, so the row says which one — and lets it be changed there
    profname = {app: {p.get("id"): p.get("name") for p in (arr(app, "/qualityprofile")[1] or []) if isinstance(p, dict)}
                for app in ("radarr", "sonarr")}
    items = []
    for m in (arr("radarr", "/movie")[1] or []):
        if not m.get("monitored"): continue
        base = dict(id=m["id"], tmdbId=m.get("tmdbId"), title=m["title"], year=m.get("year"),
                    kind="movie", poster=_poster(m.get("images")), who=req_by_tmdb.get(m.get("tmdbId"), ""),
                    size=m.get("sizeOnDisk", 0) or 0, runtime=m.get("runtime") or 0,
                    profile=profname["radarr"].get(m.get("qualityProfileId"), ""))
        if m.get("hasFile"):
            items.append(dict(base, stage="Available", reason="", detail="")); continue
        if m["id"] in rq:
            d = rq[m["id"]]
            items.append(dict(base, stage="Importing" if "import" in str(d["state"]).lower() else "Downloading",
                              reason="", hashes=d.get("hashes"), detail=f"{d['pct']}%" + (f" · {d['timeleft']}" if d['timeleft'] else ""))); continue
        if not m.get("isAvailable"):
            items.append(dict(base, stage="Waiting", reason="not released yet", detail="")); continue
        stage, reason, _ = classify("radarr", m["id"], "/release?movieId=%d" % m["id"])
        items.append(dict(base, stage=stage, reason=reason, detail=""))

    for s in (arr("sonarr", "/series")[1] or []):
        if not s.get("monitored"): continue
        stt = s.get("statistics", {}); have = stt.get("episodeFileCount", 0); tot = stt.get("episodeCount", 0)
        base = dict(id=s["id"], tvdbId=s.get("tvdbId"), title=s["title"], kind="tv",
                    poster=_poster(s.get("images")), who=req_by_tvdb.get(s.get("tvdbId"), ""),
                    size=stt.get("sizeOnDisk", 0) or 0, runtime=s.get("runtime") or 0, have=have, total=tot,   # runtime = minutes per episode
                    profile=profname["sonarr"].get(s.get("qualityProfileId"), ""))
        if tot and have >= tot:
            items.append(dict(base, stage="Available", reason="", detail=f"{have}/{tot}")); continue
        if s["id"] in sq:
            d = sq[s["id"]]
            items.append(dict(base, stage="Downloading", reason="", hashes=d.get("hashes"), detail=f"{have}/{tot} · {d['pct']}%")); continue
        season = first_missing_season(s)
        if have > 0:
            stage, reason, _ = classify("sonarr", s["id"], "/release?seriesId=%d&seasonNumber=%d" % (s["id"], season))
            # a show with files but missing episodes is never "Available": an "already have" verdict for the
            # searched season still leaves gaps elsewhere, so it stays Partial
            items.append(dict(base, stage="Partial" if stage in ("Searching", "Available") else stage,
                              reason=reason if stage == "Unavailable" else f"getting remaining episodes (S{season})",
                              detail=f"{have}/{tot}")); continue
        stage, reason, _ = classify("sonarr", s["id"], "/release?seriesId=%d&seasonNumber=%d" % (s["id"], season))
        items.append(dict(base, stage=stage, reason=reason, detail=f"{have}/{tot}"))

    du = shutil.disk_usage(data_dir if data_dir and os.path.isdir(data_dir) else "/")
    cpu = os.cpu_count() or 1; load = os.getloadavg()[0]
    res = dict(load=round(load, 2), cpu=cpu, cpu_pct=min(100, round(100 * load / cpu)),
               disk_pct=round(100 * du.used / du.total), disk_free=round(du.free / 1e9), disk_total=round(du.total / 1e9))
    try:
        mem = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1); mem[k] = int(v.strip().split()[0])
        avail = mem.get("MemAvailable", 0)
        res["mem_pct"] = round(100 * (mem["MemTotal"] - avail) / mem["MemTotal"])
        res["ram_free"] = round(avail / 1024 / 1024, 1); res["ram_total"] = round(mem["MemTotal"] / 1024 / 1024, 1)
    except Exception:
        res["mem_pct"] = 0; res["ram_free"] = 0; res["ram_total"] = 0

    health = {"unavailable": sum(1 for i in items if i["stage"] == "Unavailable")}
    try:
        files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir)] if backup_dir and os.path.isdir(backup_dir) else []
        if files:
            health["last_backup_h"] = round((now - os.path.getmtime(max(files, key=os.path.getmtime))) / 3600, 1)
    except Exception:
        pass
    idx_warn = []
    for app in ("radarr", "sonarr"):
        st, h = arr(app, "/health")
        for w in (h or []):
            if "indexer" in w.get("message", "").lower(): idx_warn.append(w["message"][:80])
    health["indexer_warnings"] = idx_warn

    activity = []
    for app in ("radarr", "sonarr"):
        st, h = arr(app, "/history?pageSize=15&sortKey=date&sortDirection=descending&eventType=3")
        for r in (h or {}).get("records", [])[:8]:
            activity.append((r.get("date", "")[:16].replace("T", " "), r.get("sourceTitle", "")[:60]))
    activity = sorted(activity, reverse=True)[:12]

    items.sort(key=lambda i: (ORDER.get(i["stage"], 9), i["title"].lower()))
    summary = {k: sum(1 for i in items if i["stage"] == k) for k in STAGES}   # every stage, counted once

    return {"generated": now, "summary": summary, "items": items, "resources": res,
            "health": health, "activity": activity, "searches": searches[0]}, cache
