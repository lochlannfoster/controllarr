"""Data layer for the Controllarr panel: every source the dashboard reads, behind one cache.

Accessors are injected (same pattern as board_gen) so this module can be exercised with fakes:
  arr(app, path, method="GET", data=None) -> (status, body)      app in {"radarr","sonarr"}
  prowlarr(path, method="GET", data=None) -> (status, body)
  js(path, method="GET", data=None) -> (status, body)             Jellyseerr
  http(method, url, headers=None, data=None, timeout=..) -> (status, body)   services.http
  docker_raw(path) -> bytes                                        Docker socket (read-only)
  torrents() -> list of qBittorrent rows as /api/qbit-torrents returns them (with `why`)
  transfer() -> qBittorrent transfer/info dict
  status() -> the board_gen status dict (items, resources, health, activity, generated)
  bazarr_get(path) -> parsed JSON or None

Every public payload carries {"generated": ts, "sources": {name: {"ok", "age_s", "err"}}} so the
client can render partial / error / stale states per card without a second request.
"""
import concurrent.futures, json, os, re, threading, time, urllib.parse, urllib.request

DISK_LEVELS = (80, 90, 95)              # same thresholds as scripts/disk-check.py
BACKUP_STALE_H = 36
POSTER_CACHE_MAX = 300
POSTER_TTL = 86400

# ---------------------------------------------------------------- cache
class Sources:
    """Per-source cache: TTL for good results, a short TTL for failures (so a dead service is not
    hammered on every poll), and the last good value kept for stale rendering."""
    def __init__(self): self._c = {}; self._lock = threading.Lock()
    def get(self, key, ttl, fn, fail_ttl=10):
        now = time.time()
        with self._lock: ent = self._c.get(key)
        if ent and now - ent["ts"] < (ttl if ent["ok"] else fail_ttl):
            return ent["data"], self._meta(ent, now)
        try:
            data = fn()
            ent = {"data": data, "ts": now, "ok": True, "err": None, "good_ts": now}
        except Exception as e:
            ent = {"data": ent["data"] if ent else None, "ts": now, "ok": False,
                   "err": (type(e).__name__ + ": " + str(e))[:140], "good_ts": ent.get("good_ts") if ent else None}
        with self._lock: self._c[key] = ent
        return ent["data"], self._meta(ent, now)
    @staticmethod
    def _meta(ent, now):
        return {"ok": ent["ok"], "age_s": (round(now - ent["good_ts"]) if ent.get("good_ts") else None), "err": ent["err"]}
    def invalidate(self, *keys):
        with self._lock:
            for k in keys: self._c.pop(k, None)

def _fail(msg): raise RuntimeError(msg)
def _ok(st): return isinstance(st, int) and 200 <= st < 300
def _need(st, body, what):
    """Turn the (status, body) convention into an exception the cache can record."""
    if st is None: _fail(f"{what} unreachable")
    if not _ok(st): _fail(f"{what} HTTP {st}")
    return body

def _iso_to_ts(s):
    try:
        s = (s or "")[:19]
        return time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception: return 0

def gb(b):
    try: return f"{(b or 0) / 1e9:.1f} GB"
    except Exception: return ""

_ANSI = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
def _last_log_line(raw, limit=200):
    """The last non-empty line of a Docker logs body. Non-TTY containers multiplex stdout/stderr into 8-byte-headed frames
    (type, 3 × 0, big-endian length); TTY containers stream raw bytes."""
    text = b""
    if len(raw) >= 8 and raw[0] in (0, 1, 2) and raw[1:4] == b"\x00\x00\x00":
        i = 0
        while i + 8 <= len(raw):
            n = int.from_bytes(raw[i + 4:i + 8], "big"); text += raw[i + 8:i + 8 + n]; i += 8 + n
    else: text = raw
    lines = [ln.strip() for ln in _ANSI.sub(b"", text).decode(errors="replace").splitlines() if ln.strip()]
    return lines[-1][:limit] if lines else ""
_TASK_WORDS = {"RssSync": "RSS sync", "MoviesSearch": "searching", "MissingMoviesSearch": "searching", "CutoffUnmetMoviesSearch": "searching upgrades",
               "SeriesSearch": "searching", "SeasonSearch": "searching", "EpisodeSearch": "searching", "MissingEpisodeSearch": "searching", "CutoffUnmetEpisodeSearch": "searching upgrades",
               "RefreshMovie": "refreshing metadata", "RefreshSeries": "refreshing metadata", "RefreshMonitoredDownloads": "checking downloads", "DownloadedMoviesScan": "importing",
               "DownloadedEpisodesScan": "importing", "RenameFiles": "renaming", "RenameMovie": "renaming", "RenameSeries": "renaming", "Backup": "backing up", "ApplicationIndexerSync": "syncing indexers",
               "IndexerSync": "syncing indexers", "ImportListSync": "syncing lists", "Housekeeping": "housekeeping", "CheckHealth": "health check", "MessagingCleanup": "housekeeping"}
def _task_words(names):
    """'searching ×3, RSS sync' from a list of arr command names; '' when nothing is running."""
    counts = {}
    for n in names:
        w = _TASK_WORDS.get(n) or re.sub(r"(?<!^)(?=[A-Z])", " ", str(n or "")).lower()
        counts[w] = counts.get(w, 0) + 1
    return ", ".join(w + (f" ×{c}" if c > 1 else "") for w, c in counts.items())

# ---------------------------------------------------------------- panel
class Panel:
    def __init__(self, *, arr, prowlarr, js, http, docker_raw, torrents, transfer, status, bazarr_get,
                 env, config_dir, apikey, arr_base, expected=None, services=None, docker=True, cache_dir="",
                 jellyfin_url="http://jellyfin:8096", flaresolverr_url=None, qbit_host="qbittorrent", grace_days=None):
        self.arr, self.prowlarr, self.js, self.http = arr, prowlarr, js, http
        self.docker_raw, self.torrents, self.transfer, self.status = docker_raw, torrents, transfer, status
        self.bazarr_get, self.E, self.config_dir, self.apikey, self.arr_base = bazarr_get, env, config_dir, apikey, arr_base
        # `services`: what this install connects to — a service not listed is absent, not down. `expected`: the
        # container names Docker is asked about when a socket is available at all (it defaults to `services`).
        self.svc = services if services is not None else [x.strip() for x in env.get("SERVICES", "").split(",") if x.strip()]
        self.expected = expected if expected is not None else ([x.strip() for x in env.get("EXPECTED_CONTAINERS", "").split(",") if x.strip()] or list(self.svc))
        self.docker = bool(docker)
        self.has = lambda name: not self.svc or name in self.svc
        self.cache_dir = cache_dir
        self.jellyfin_url = jellyfin_url
        self.flaresolverr_url = flaresolverr_url or (f"http://{qbit_host}:8191/" if qbit_host == "gluetun" else "http://flaresolverr:8191/")
        self.vpn_enabled = qbit_host == "gluetun"
        self.grace_days = grace_days if grace_days is not None else int(env.get("RECONCILE_GRACE_DAYS", "14") or 14)
        self.S = Sources()
        self._cpu_prev = None; self._stat_prev = {}   # previous /proc/stat and per-container CPU samples (percentages are deltas)
        self._swap_prev = None                         # (pswpin + pswpout pages, ts): swap traffic is a delta too

    # ---------------- docker
    def containers(self):
        def fn():
            by = {}
            for c in json.loads(self.docker_raw("/containers/json?all=1")):
                for n in c.get("Names", []): by[n.lstrip("/")] = c
            return by
        return self.S.get("containers", 20, fn)
    def inspect(self, name):
        return self.S.get("inspect:" + name, 20, lambda: json.loads(self.docker_raw(f"/containers/{name}/json")))
    def services(self):
        """[{name, state, health}] for every expected container; missing ones are reported as such."""
        by, meta = self.containers()
        if by is None: return None, meta
        out = []
        for n in (self.expected or sorted(by)):
            c = by.get(n) or {}
            st = c.get("Status", "")
            out.append({"name": n, "id": (c.get("Id") or "")[:12], "state": c.get("State", "missing"),
                        "health": "unhealthy" if "(unhealthy)" in st else ("healthy" if "(healthy)" in st else ""),
                        "status": st})
        return out, meta

    def vpn(self):
        """gluetun health + exit IP / location / forwarded port from its logs, plus the namespace check the
        port-sync watchdog does: a dependent that started before gluetun's current start is orphaned."""
        if not self.vpn_enabled or not self.docker: return {"enabled": False}, {"ok": True, "age_s": 0, "err": None}
        def fn():
            j = json.loads(self.docker_raw("/containers/gluetun/json"))
            st = j.get("State", {}) or {}
            health = (st.get("Health") or {}).get("Status")
            out = {"enabled": True, "health": health, "up": health == "healthy" or (st.get("Running") and not health),
                   "ip": None, "loc": None, "port": None, "orphaned": [], "started": st.get("StartedAt")}
            gid, gstart = j.get("Id", ""), _iso_to_ts(st.get("StartedAt"))
            for dep in ("qbittorrent", "prowlarr", "flaresolverr"):
                try:
                    d = json.loads(self.docker_raw(f"/containers/{dep}/json"))
                    mode = (d.get("HostConfig") or {}).get("NetworkMode", "")
                    ds = d.get("State", {}) or {}
                    if not mode.startswith("container:"): continue
                    if not ds.get("Running"): continue
                    if mode.split(":", 1)[1] not in (gid,) and not gid.startswith(mode.split(":", 1)[1]):
                        out["orphaned"].append(dep); continue
                    if gstart and _iso_to_ts(ds.get("StartedAt")) < gstart: out["orphaned"].append(dep)
                except Exception: pass
            try:
                logs = self.docker_raw("/containers/gluetun/logs?stdout=true&stderr=true&tail=400")
                m = re.findall(rb"Public IP address is (\d+\.\d+\.\d+\.\d+)(?: \(([^)]*)\))?", logs)
                if m:
                    out["ip"] = m[-1][0].decode(errors="ignore")
                    out["loc"] = ((m[-1][1].decode(errors="ignore") or "").split(" - source")[0].strip()) or None
                pf = re.findall(rb"port forwarded is (\d+)", logs)
                if pf: out["port"] = pf[-1].decode(errors="ignore")
            except Exception: pass
            return out
        return self.S.get("vpn", 30, fn)

    # ---------------- arr / prowlarr / jellyseerr
    def arr_health(self):
        def fn():
            out = []
            for app in ("radarr", "sonarr"):
                st, h = self.arr(app, "/health")
                for w in _need(st, h, app) or []:
                    out.append({"app": app, "type": w.get("type"), "source": w.get("source"), "message": (w.get("message") or "")[:160]})
            return out
        return self.S.get("arr_health", 30, fn)
    def prowlarr_health(self):
        return self.S.get("prowlarr_health", 30, lambda: [{"app": "prowlarr", "type": w.get("type"), "source": w.get("source"),
                                                          "message": (w.get("message") or "")[:160]}
                                                         for w in _need(*self.prowlarr("/health"), "prowlarr") or []])
    def flaresolverr(self):
        def fn():
            st, body = self.http("GET", self.flaresolverr_url, timeout=5, expect_json=False)
            if st != 200: _fail(f"FlareSolverr HTTP {st}")
            return {"ok": True}
        return self.S.get("flaresolverr", 30, fn)
    def queue_issues(self):
        """Queue records the arrs flag: status=warning (stalled / no connections), trackedDownloadStatus=warning
        (import problems), or an errorMessage."""
        def fn():
            out = []
            for app in ("radarr", "sonarr"):
                st, q = self.arr(app, "/queue?pageSize=200&includeUnknownSeriesItems=true")
                for r in (_need(st, q, app) or {}).get("records", []):
                    tds, stt = r.get("trackedDownloadStatus"), r.get("status")
                    msgs = [m for sm in (r.get("statusMessages") or []) for m in (sm.get("messages") or [])]
                    if tds in ("warning", "error") or stt in ("warning", "failed") or r.get("errorMessage"):
                        out.append({"app": app, "kind": "movie" if app == "radarr" else "tv",
                                    "id": r.get("movieId") if app == "radarr" else r.get("seriesId"),
                                    "qid": r.get("id"), "title": r.get("title"), "hash": (r.get("downloadId") or "").lower(),
                                    "status": stt, "tracked": tds, "state": r.get("trackedDownloadState"),
                                    "error": (r.get("errorMessage") or "; ".join(msgs))[:160], "sizeleft": r.get("sizeleft", 0)})
            return out
        return self.S.get("queue_issues", 15, fn)
    def pending_requests(self):
        def fn():
            st, r = self.js("/request?filter=pending&take=50&sort=added")
            out = []
            for q in (_need(st, r, "jellyseerr") or {}).get("results", []):
                m = q.get("media") or {}
                who = (q.get("requestedBy") or {}).get("jellyfinUsername") or (q.get("requestedBy") or {}).get("displayName") or "?"
                out.append({"reqId": q.get("id"), "type": q.get("type"), "tmdbId": m.get("tmdbId"), "tvdbId": m.get("tvdbId"),
                            "who": who, "added": (q.get("createdAt") or "")[:16].replace("T", " "),
                            "seasons": [s.get("seasonNumber") for s in (q.get("seasons") or [])]})
            return out
        return self.S.get("pending", 15, fn)
    def versions(self):
        def fn():
            out = {}
            for app in ("radarr", "sonarr"):
                st, r = self.arr(app, "/system/status")
                if _ok(st) and isinstance(r, dict): out[app] = r.get("version")
            st, r = self.prowlarr("/system/status")
            if _ok(st) and isinstance(r, dict): out["prowlarr"] = r.get("version")
            st, r = self.js("/status")
            if _ok(st) and isinstance(r, dict): out["jellyseerr"] = r.get("version"); out["jellyseerr_update"] = bool(r.get("updateAvailable"))
            st, r = self.http("GET", self.jellyfin_url + "/System/Info/Public", timeout=5)
            if _ok(st) and isinstance(r, dict): out["jellyfin"] = r.get("Version")
            return out
        return self.S.get("versions", 3600, fn)

    # ---------------- jellyfin
    def _jf_headers(self):
        k = self.apikey("jellyfin")
        if not k: _fail("no Jellyfin API key (JELLYFIN_APIKEY in app.env)")
        return {"Authorization": f'MediaBrowser Token="{k}", Client="Controllarr", Device="panel", DeviceId="controllarr", Version="1"'}
    def jellyfin_sessions(self):
        def fn():
            st, r = self.http("GET", self.jellyfin_url + "/Sessions?activeWithinSeconds=300", headers=self._jf_headers(), timeout=5)
            out = []
            for s in _need(st, r, "jellyfin") or []:
                npi = s.get("NowPlayingItem")
                if not npi: continue
                ps = s.get("PlayState") or {}; ti = s.get("TranscodingInfo") or {}
                method = ps.get("PlayMethod") or ("Transcode" if ti else "DirectPlay")
                reasons = ti.get("TranscodeReasons") or []
                title = npi.get("Name") or ""
                if npi.get("SeriesName"): title = f"{npi['SeriesName']} — {npi.get('SeasonName','')} {npi.get('IndexNumber') and 'E%02d' % npi['IndexNumber'] or ''} {title}".replace("  ", " ").strip()
                ticks = npi.get("RunTimeTicks") or 0; pos = ps.get("PositionTicks") or 0
                out.append({"user": s.get("UserName"), "client": s.get("Client"), "device": s.get("DeviceName"),
                            "title": title, "type": npi.get("Type"), "method": method, "paused": bool(ps.get("IsPaused")),
                            "reasons": reasons[:3], "video": ti.get("VideoCodec"), "audio": ti.get("AudioCodec"),
                            "bitrate": ti.get("Bitrate"), "pct": round(100 * pos / ticks) if ticks else None,
                            "itemId": npi.get("Id")})
            return out
        return self.S.get("jf_sessions", 5, fn)

    # ---------------- optional companion state: a stack that also keeps a retry ledger (nothing here writes it)
    def ledger_state(self, name):
        def fn():
            if not self.config_dir: return {}
            p = os.path.join(self.config_dir, f".{name}-state.json")
            if not os.path.exists(p): return {}
            with open(p) as f: return json.load(f)
        return self.S.get("state:" + name, 30, fn)
    def hostname(self):
        """The box's own name from the Docker daemon (the container's hostname is a random id); '' when unreadable."""
        try: return (json.loads(self.docker_raw("/info")) or {}).get("Name") or ""
        except Exception: return ""

    # ---------------- system: the host, every container, and what each part of the stack is doing
    @staticmethod
    def _proc_cpu():
        with open("/proc/stat") as f: v = [int(x) for x in f.readline().split()[1:9]]
        return {"total": sum(v), "idle": v[3] + v[4], "iowait": v[4]}
    def host(self):
        """CPU and iowait as deltas since the previous read, load, memory, swap (occupancy AND traffic — parked pages are
        harmless, pages moving in and out are not), uptime, the media disk. /proc is the host's inside a container, so
        this is the box, not the panel."""
        def fn():
            out = {"cpu_pct": None, "iowait_pct": None, "swap_io": None}
            try:
                cur = self._proc_cpu(); prev = self._cpu_prev
                if not prev: time.sleep(0.25); prev, cur = cur, self._proc_cpu()
                self._cpu_prev = cur; d = cur["total"] - prev["total"]
                if d > 0: out["cpu_pct"] = round(100 * (1 - (cur["idle"] - prev["idle"]) / d)); out["iowait_pct"] = round(100 * (cur["iowait"] - prev["iowait"]) / d)
            except Exception: pass
            try: out["load"] = [round(x, 2) for x in os.getloadavg()]
            except Exception: out["load"] = None
            out["cpus"] = os.cpu_count() or 1
            try:
                mem = {}
                with open("/proc/meminfo") as f:
                    for line in f:
                        k, v = line.split(":", 1); mem[k] = int(v.strip().split()[0]) * 1024
                out["mem_total"] = mem.get("MemTotal", 0); out["mem_used"] = mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)
                out["swap_total"] = mem.get("SwapTotal", 0); out["swap_used"] = mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)
            except Exception: out.update(mem_total=0, mem_used=0, swap_total=0, swap_used=0)
            try:   # pages swapped in + out per second since the previous read: the figure that says whether swap is a problem
                vm = {}
                with open("/proc/vmstat") as f:
                    for line in f:
                        k, _, v = line.partition(" ")
                        if k in ("pswpin", "pswpout"): vm[k] = int(v)
                pages, now = vm.get("pswpin", 0) + vm.get("pswpout", 0), time.time(); prev = self._swap_prev
                if prev and now > prev[1]: out["swap_io"] = round((pages - prev[0]) / (now - prev[1]))
                self._swap_prev = (pages, now)
            except Exception: pass
            try:
                with open("/proc/uptime") as f: out["uptime_s"] = round(float(f.read().split()[0]))
            except Exception: out["uptime_s"] = None
            temps = []
            for z in range(8):
                try:
                    with open(f"/sys/class/thermal/thermal_zone{z}/temp") as f: temps.append(int(f.read().strip()) / 1000)
                except Exception: break
            out["temp_c"] = round(max(temps)) if temps else None
            res = (self.status() or {}).get("resources") or {}
            out["disk"] = {k: res.get(k) for k in ("disk_pct", "disk_free", "disk_total")}
            return out
        return self.S.get("host", 8, fn)
    def container_stats(self):
        """[{name, id, state, health, status, cpu_pct, mem_mb, mem_limit_mb, log}] for every expected container: one one-shot
        stats read and the last log line per running container, fetched in parallel. CPU is the delta against the
        previous read, so the first poll shows —."""
        def fn():
            by, meta = self.containers()
            if by is None: _fail(meta.get("err") or "Docker unreachable")
            def one(n):
                c = by.get(n) or {}; st = c.get("Status", "")
                row = {"name": n, "id": (c.get("Id") or "")[:12], "state": c.get("State", "missing"), "status": st,
                       "health": "unhealthy" if "(unhealthy)" in st else ("healthy" if "(healthy)" in st else ""),
                       "cpu_pct": None, "mem_mb": None, "mem_limit_mb": None, "log": ""}
                if c.get("State") != "running": return row
                try:
                    s = json.loads(self.docker_raw(f"/containers/{c['Id']}/stats?stream=false&one-shot=true"))
                    cs = s.get("cpu_stats") or {}; ms = s.get("memory_stats") or {}
                    tot = (cs.get("cpu_usage") or {}).get("total_usage") or 0; sys_ = cs.get("system_cpu_usage") or 0
                    ncpu = cs.get("online_cpus") or len((cs.get("cpu_usage") or {}).get("percpu_usage") or []) or (os.cpu_count() or 1)
                    prev = self._stat_prev.get(n)
                    if prev and sys_ > prev[1] and tot >= prev[0]: row["cpu_pct"] = round(100 * (tot - prev[0]) / (sys_ - prev[1]) * ncpu, 1)
                    self._stat_prev[n] = (tot, sys_)
                    st2 = ms.get("stats") or {}
                    usage = (ms.get("usage") or 0) - (st2.get("inactive_file") or st2.get("total_inactive_file") or 0)
                    row["mem_mb"] = round(usage / 1048576); lim = ms.get("limit") or 0
                    row["mem_limit_mb"] = round(lim / 1048576) if lim and lim < (1 << 60) else None
                except Exception as e: row["err"] = (type(e).__name__ + ": " + str(e))[:80]
                try: row["log"] = _last_log_line(self.docker_raw(f"/containers/{c['Id']}/logs?stdout=true&stderr=true&tail=5"))
                except Exception: pass
                return row
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex: return list(ex.map(one, self.expected or sorted(by)))
        return self.S.get("cstats", 10, fn)
    def tasks(self):
        """What each app says it is doing right now: running/queued commands in the arrs and Prowlarr, Jellyfin's
        scheduled tasks, Jellyseerr's jobs, Bazarr's tasks. One short phrase per container name; None = not asked."""
        def fn():
            out = {}
            for app in ("radarr", "sonarr"):
                st, cmds = self.arr(app, "/command")
                out[app] = _task_words([c.get("name") for c in cmds if c.get("status") in ("started", "queued")]) if isinstance(cmds, list) else None
            st, cmds = self.prowlarr("/command")
            out["prowlarr"] = _task_words([c.get("name") for c in cmds if c.get("status") in ("started", "queued")]) if isinstance(cmds, list) else None
            try:
                st, j = self.http("GET", self.jellyfin_url + "/ScheduledTasks", headers=self._jf_headers(), timeout=5)
                out["jellyfin"] = ", ".join(f"{t.get('Name')} {round(t.get('CurrentProgressPercentage') or 0)} %" for t in j if t.get("State") == "Running") if isinstance(j, list) else None
            except Exception: out["jellyfin"] = None
            st, jobs = self.js("/settings/jobs")
            out["jellyseerr"] = ", ".join(j.get("name") or j.get("id") or "" for j in jobs if j.get("running")) if isinstance(jobs, list) else None
            tk = self.bazarr_get("/api/system/tasks"); rows = tk.get("data") if isinstance(tk, dict) else tk
            out["bazarr"] = ", ".join(t.get("name") or "" for t in rows if t.get("job_running")) if isinstance(rows, list) else None
            return out
        return self.S.get("tasks", 10, fn)
    def system(self):
        """The resource monitor: host figures, every container with CPU / memory / its current task / last log line."""
        out = {"generated": time.time()}; metas = {}
        out["host"], metas["host"] = self.host()
        rows, metas["docker"] = self.container_stats() if self.docker else ([], None); rows = rows or []
        tasks, metas["tasks"] = self.tasks(); tasks = tasks or {}
        try: tors = self.torrents()
        except Exception: tors = []
        dl = sum(1 for t in tors if (t.get("state") or "") in ("downloading", "forcedDL", "metaDL", "forcedMetaDL", "stalledDL", "queuedDL", "checkingDL"))
        up = sum(1 for t in tors if (t.get("state") or "").endswith("UP") or t.get("state") == "uploading")
        vpn, _ = self.vpn(); st = self.status() or {}
        for r in rows:
            n = r["name"]
            if n == "qbittorrent": r["task"] = f"{dl} downloading · {up} seeding" if tors else ("idle" if r["state"] == "running" else "")
            elif n == "gluetun": r["task"] = ("tunnel up · " + (vpn.get("ip") or "") + (" :" + str(vpn["port"]) if vpn.get("port") else "")) if vpn.get("up") else ("tunnel down" if vpn.get("enabled") else "")
            elif n == "controllarr": r["task"] = f"library scan {round(time.time() - st['generated'])} s ago · {st.get('searches', 0)} searches" if st.get("generated") else "first library scan"
            elif n == "flaresolverr": r["task"] = "answering" if (self.S._c.get("flaresolverr") or {}).get("ok") else ""
            else: r["task"] = tasks.get(n) if tasks.get(n) is not None else ("" if n in tasks else None)
            if r["task"] == "" and r["state"] == "running": r["task"] = "idle"
        out["containers"] = rows; out["sources"] = self._sources(**metas)
        return out

    # ---------------- builders
    def _sources(self, **metas):
        return {k: v for k, v in metas.items() if v is not None}

    @staticmethod
    def _subject(text, key=None, who=False):
        """One real name an attention item's own text carries, for the client's incognito pass (never used here)."""
        return {"text": text, "key": key or text, "who": bool(who)}

    def attention(self):
        """Tier 1: only actionable problems, each with one primary action. Empty list is the good state.

        An item whose sentences carry a real name (a release, a title, a requester) also lists it under
        `subjects`: incognito replaces exactly that, in the browser, and leaves the reason and the counts —
        the part that makes the row worth reading — alone. Nothing here is ever substituted server-side."""
        items = []; metas = {}
        st = self.status(); res = st.get("resources", {}) or {}; hp = st.get("health", {}) or {}
        # the board (disk, unavailable titles, torrent<->title matching) is only trustworthy once the
        # background regeneration has run; until then the client shows a skeleton, not "nothing needs you"
        metas["board"] = {"ok": bool(st.get("generated")), "age_s": round(time.time() - st["generated"]) if st.get("generated") else None,
                          "err": None if st.get("generated") else "first library scan still running"}
        # VPN
        vpn, metas["vpn"] = self.vpn()
        if vpn and vpn.get("enabled"):
            if not vpn.get("up"):
                items.append(dict(id="vpn", sev="danger", kind="vpn", title="VPN tunnel is down",
                    detail=f"gluetun is {vpn.get('health') or 'not running'} — qBittorrent, Prowlarr and FlareSolverr have no network until it recovers (kill-switch); its last log line is in System",
                    facts=[f"exit {vpn.get('ip') or '—'}", f"port {vpn.get('port') or '—'}"], actions=[]))
            elif vpn.get("orphaned"):
                items.append(dict(id="vpn-orphan", sev="warn", kind="orphaned", title=f"{', '.join(vpn['orphaned'])} lost the VPN namespace",
                    detail="gluetun restarted after they started; they hold a dead network until the port-sync watchdog re-creates them (runs every 5 min)",
                    facts=[f"gluetun up since {(vpn.get('started') or '')[:16].replace('T', ' ')}"], actions=[]))
        elif metas["vpn"] and not metas["vpn"]["ok"]:
            items.append(dict(id="vpn-unknown", sev="warn", kind="vpn", title="Can't read VPN status", detail=metas["vpn"]["err"] or "Docker socket unreachable", facts=[], actions=[]))
        # containers (only with a readable Docker socket; without one Controllarr does not report on them at all)
        svcs, metas["services"] = self.services() if self.docker else ([], None)
        for s in svcs or []:
            if s["state"] != "running" or s["health"] == "unhealthy":
                word = {"missing": "is missing", "exited": "has stopped", "running": "is unhealthy"}.get(s["state"] if s["state"] != "running" else "running", f"is {s['state']}")
                items.append(dict(id="svc:" + s["name"], sev="danger" if s["state"] != "running" else "warn", kind="container",
                    title=f"{s['name']} {word}", detail=(s.get("status") or "not created") + (" — autoheal restarts it after 3 failed checks" if s["health"] == "unhealthy" else "") + " — its last log line is in System; docker logs " + s["name"] + " has the rest",
                    facts=[], actions=[]))
        # torrents (only with a torrent client in the stack)
        tors = None
        if self.has("qbittorrent"):
            try: tors = self.torrents(); metas["qbit"] = {"ok": True, "age_s": 0, "err": None}
            except Exception as e: metas["qbit"] = {"ok": False, "age_s": None, "err": str(e)[:140]}
        for t in tors or []:
            if t.get("why") and (t.get("state") or "").startswith(("stalled", "metaDL", "forcedMetaDL")) and (t.get("progress") or 0) < 100:
                name = t.get("group") or t.get("name") or ""
                act = []
                if t.get("matched") and t.get("iid"):
                    act.append({"label": "Blocklist & retry", "cap": "can_remove", "confirm": True,
                                "body": {"action": "blocklist_retry", "kind": t["kind"], "id": t["iid"], "title": name}})
                    act.append({"label": "Open", "open": {"kind": t["kind"], "id": t["iid"]}})
                else:
                    act.append({"label": "Remove torrent", "cap": "can_remove", "confirm": True, "body": {"action": "t_delete", "hash": t["hash"], "name": name}})
                act.append({"label": "Reannounce", "body": {"action": "t_reannounce", "hash": t["hash"]}})
                items.append(dict(id="stalled:" + t["hash"], sev="danger", kind="stalled", title=f"{name} — stalled",
                    detail=t["why"], facts=[f"queue #{t.get('priority') or '—'}", f"{t.get('progress', 0)} %", gb(t.get("size")), f"{t.get('num_seeds', 0)} seeds / {t.get('num_leechs', 0)} peers"],
                    subjects=[self._subject(name, f"{t['kind']}:{t['iid']}" if t.get("matched") and t.get("iid") else name)],
                    actions=act))
        # import problems
        qi, metas["queue"] = self.queue_issues()
        stalled_hashes = {i["id"].split(":", 1)[1] for i in items if i["kind"] == "stalled"}
        for q in qi or []:
            if q["hash"] in stalled_hashes: continue      # already listed with the richer torrent reason
            if q["tracked"] in ("warning", "error") or q["status"] == "failed":
                items.append(dict(id="import:" + str(q["qid"]), sev="warn", kind="import", title=f"{q['title'][:70]} — import needs attention",
                    detail=q["error"] or f"{q['app']} reports {q['tracked']}", facts=[q["app"], f"{gb(q.get('sizeleft'))} left"],
                    subjects=[self._subject(q["title"][:70], f"{q['kind']}:{q['id']}" if q.get("id") else q["title"][:70])],
                    actions=[{"label": "Blocklist & retry", "cap": "can_remove", "confirm": True, "body": {"action": "blocklist_retry", "kind": q["kind"], "id": q["id"], "title": q["title"]}},
                             {"label": "Open", "open": {"kind": q["kind"], "id": q["id"]}}]))
        # indexers / flaresolverr (Prowlarr and FlareSolverr are one optional part; absent = no row, not an error)
        ah, metas["arr_health"] = self.arr_health()
        ph, metas["prowlarr_health"] = self.prowlarr_health() if self.has("prowlarr") else ([], None)
        fs, metas["flaresolverr"] = self.flaresolverr() if self.has("flaresolverr") else (None, None)
        for w in (ah or []) + (ph or []):
            msg = (w.get("message") or ""); src = w.get("source") or ""
            if w.get("type") in ("error", "warning") and any(k in msg.lower() for k in ("indexer", "download client", "unavailable", "failed")):
                client = "download client" in msg.lower() or "RemotePathMapping" in src or "DownloadClient" in src
                detail = src
                if "RemotePathMapping" in src and "does not appear to exist" in msg:
                    detail = f"{src} — the folder qBittorrent's category points at is missing or not mounted; on the host: mkdir -p DATA_DIR/torrents/{{movies,tv}} (the installer creates them)"
                items.append(dict(id=f"health:{w['app']}:{src}", sev="warn" if w["type"] == "warning" else "danger", kind="client" if client else "indexer",
                    title=f"{w['app']}: {msg[:160]}", detail=detail, facts=[],
                    actions=[{"label": "Test all indexers", "body": {"action": "indexers_test_all"}}] if w["app"] == "prowlarr" and not client else []))
        if self.vpn_enabled and metas["flaresolverr"] and not metas["flaresolverr"]["ok"]:
            items.append(dict(id="flaresolverr", sev="warn", kind="indexer", title="FlareSolverr not answering", detail=metas["flaresolverr"]["err"] or "",
                              facts=["Cloudflare-protected indexers will fail until it is back"], actions=[]))
        # disk
        pct = res.get("disk_pct")
        if isinstance(pct, (int, float)) and pct >= DISK_LEVELS[0]:
            lvl = max(l for l in DISK_LEVELS if pct >= l)
            nxt = next((l for l in DISK_LEVELS if l > pct), None)
            items.append(dict(id="disk", sev="danger" if lvl >= 90 else "warn", kind="disk", title=f"Disk {pct} % full",
                detail=f"{res.get('disk_free', 0)} GB free of {res.get('disk_total', 0)} GB on the media volume" + (f" — next warning at {nxt} %" if nxt else " — downloads will fail when it fills"),
                facts=[f"disk-check warned at {lvl} %"], actions=[{"label": "Library by size", "jump": "#library?sort=size"}]))
        # pending requests
        pr, metas["requests"] = self.pending_requests()
        for r in pr or []:
            items.append(dict(id=f"req:{r['reqId']}", sev="info", kind="request", title=f"{r['who']} requested a {r['type']}",
                detail=("seasons " + ", ".join(str(s) for s in r["seasons"]) if r.get("seasons") else "") , facts=[r["added"]],
                subjects=[self._subject(r["who"], who=True)],
                actions=[{"label": "Approve", "cap": "can_manage_requests", "body": {"action": "req_approve", "reqId": r["reqId"]}},
                         {"label": "Decline", "cap": "can_manage_requests", "confirm": True, "body": {"action": "req_decline", "reqId": r["reqId"]}}],
                ids={"tmdbId": r.get("tmdbId"), "tvdbId": r.get("tvdbId")}))
        # unavailable past grace
        rec, metas["recovery"] = self.ledger_state("recovery")
        now = time.time()
        for it in st.get("items", []):
            if it.get("stage") != "Unavailable": continue
            # a companion retry ledger, when one exists: keyed m<movieId>, stamping `unavailable_since` (movies only)
            entry = (rec or {}).get(f"m{it.get('id')}") if it.get("kind") == "movie" else None
            first = entry.get("unavailable_since") if isinstance(entry, dict) else None
            days = round((now - first) / 86400) if first else None
            items.append(dict(id=f"unavail:{it.get('kind')}:{it.get('id')}", sev="warn", kind="unavailable",
                title=f"{it.get('title')} — unavailable" + (f" for {days} d" if days else ""),
                detail=it.get("reason") or "no usable release", facts=[x for x in (it.get("detail"), f"req: {it.get('who')}" if it.get("who") else "") if x],
                subjects=[self._subject(it.get("title"), f"{it.get('kind')}:{it.get('id')}")] + ([self._subject(it.get("who"), who=True)] if it.get("who") else []),
                actions=[{"label": "Search again", "body": {"action": "retry", "kind": it.get("kind"), "id": it.get("id"), "title": it.get("title")}},
                         {"label": "Open", "open": {"kind": it.get("kind"), "id": it.get("id")}}]))
        # backup
        lb = hp.get("last_backup_h")
        bd = self.E.get("BACKUP_DIR", "")
        enabled = str(self.E.get("ENABLE_BACKUPS", "")).lower() == "true"
        if bd and os.path.isdir(bd) and enabled:   # only while backups are on; a mounted but unused folder is not a problem
            if lb is None or lb > BACKUP_STALE_H:
                items.append(dict(id="backup", sev="warn", kind="backup", title="No recent config backup" if lb is None else f"Last backup {round(lb)} h ago",
                    detail=f"nightly backup-config.sh writes to {bd} at 03:30 — check active/backup-config.log on the server", facts=[], actions=[]))
        order = {"danger": 0, "warn": 1, "info": 2}
        items.sort(key=lambda i: (order.get(i["sev"], 9), i["kind"], i["title"]))
        return {"generated": time.time(), "items": items, "sources": self._sources(**metas)}

    def live(self):
        out = {"generated": time.time()}; metas = {}
        if not self.has("qbittorrent"):   # no torrent client in this install: nothing to show, nothing to report as failed
            out["torrents"] = []; out["transfer"] = None
        else:
          try:
            out["torrents"] = self.torrents(); tr = self.transfer() or {}
            out["transfer"] = {"dl": tr.get("dl_info_speed", 0), "up": tr.get("up_info_speed", 0), "dl_limit": tr.get("dl_rate_limit", 0),
                               "up_limit": tr.get("up_rate_limit", 0), "alt": bool(tr.get("use_alt_speed_limits")), "dht": tr.get("dht_nodes", 0),
                               "connection": tr.get("connection_status")}
            metas["qbit"] = {"ok": True, "age_s": 0, "err": None}
          except Exception as e:
            out["torrents"] = []; out["transfer"] = None; metas["qbit"] = {"ok": False, "age_s": None, "err": str(e)[:140]}
        out["sessions"], metas["jellyfin"] = self.jellyfin_sessions()
        vpn, metas["vpn"] = self.vpn(); out["vpn"] = vpn
        out["sources"] = self._sources(**metas)
        return out

    def reference(self):
        v, mv = self.versions(); svcs, ms = self.services() if self.docker else ([], None)
        host = self.E.get("SERVER_HOST", "localhost")
        def port(k, d): return self.E.get(k, d)
        links = [("Jellyfin", f"http://{host}:{port('JELLYFIN_PORT', '8096')}", "jellyfin", v and v.get("jellyfin")),
                 ("Jellyseerr", f"http://{host}:{port('JELLYSEERR_PORT', '5055')}", "jellyseerr", v and v.get("jellyseerr")),
                 ("Radarr", f"http://{host}:{port('RADARR_PORT', '7878')}", "radarr", v and v.get("radarr")),
                 ("Sonarr", f"http://{host}:{port('SONARR_PORT', '8989')}", "sonarr", v and v.get("sonarr")),
                 ("Prowlarr", f"http://{host}:{port('PROWLARR_PORT', '9696')}", "prowlarr", v and v.get("prowlarr")),
                 ("qBittorrent", f"http://{host}:{port('QBIT_PORT', '8080')}", "qbittorrent", None),
                 ("Bazarr", f"http://{host}:{port('BAZARR_PORT', '6767')}", "bazarr", None),
                 ("ntfy", f"http://{host}:{port('NTFY_PORT', '8090')}", "ntfy", None)]
        present = {s["name"] for s in (svcs or [])}
        apps = [{"name": n, "url": u, "container": c, "version": ver,
                 "state": next((s["state"] for s in (svcs or []) if s["name"] == c), None)}
                for n, u, c, ver in links if self.has(c) and (c in present or not present)]
        return {"generated": time.time(), "apps": apps,
                "jellyseerr_update": bool(v and v.get("jellyseerr_update")), "sources": self._sources(versions=mv, services=ms)}

    # ---------------- consequence text for confirmations (the server knows the counts)
    def consequence(self, a, item_detail):
        """Return (title, text) describing exactly what an action will do, with real counts.

        `incognito=1` on the query means the page that asked is drawing pseudonyms: the title and name in `a`
        already are one, and the names this method would add from qBittorrent are left out. Every count stays —
        a confirmation that cannot say how many files it deletes is not a confirmation."""
        act = a.get("action"); kind = a.get("kind"); aid = a.get("id"); title = a.get("title") or a.get("name") or "this item"
        incog = str(a.get("incognito") or "").lower() in ("1", "true", "yes")
        def tors_for(kind, aid):
            try:
                d = item_detail(kind, aid) or {}
                return d.get("torrents") or [], d
            except Exception: return [], {}
        if act == "purge":
            tors, d = tors_for(kind, aid)
            size = d.get("sizeOnDisk") or 0
            req = " and the Jellyseerr request" if (a.get("tmdbId") or a.get("tvdbId")) else ""
            return (f"Purge {title}", f"Deletes {gb(size) or '0 GB'} of files on disk, removes {len(tors)} torrent{'s' if len(tors) != 1 else ''} from qBittorrent{req}, and drops the title from {'Radarr' if kind == 'movie' else 'Sonarr'}; Bazarr and Jellyfin are told to rescan so it disappears there too. Can't be undone.")
        if act == "blocklist_retry":
            tors, _ = tors_for(kind, aid)
            names = "" if incog else ", ".join(((t.get("name") or "")[:60] + ("…" if len(t.get("name") or "") > 60 else "")) for t in tors[:2])
            names = names or "the current download"
            return (f"Blocklist & retry {title}", f"Blocks {names} so it is never picked again, removes {len(tors) or 'its'} torrent{'s' if len(tors) != 1 else ''} from qBittorrent, then searches for a different release.")
        if act == "q_remove":
            tors, _ = tors_for(kind, aid)
            return (f"Remove {title} from the queue", f"Removes {len(tors) or 'its'} torrent{'s' if len(tors) != 1 else ''} from qBittorrent; downloaded files are kept" + (" and the release is blocklisted." if a.get("blocklist") else "."))
        if act == "t_delete":
            return ("Remove torrent", f"Removes {a.get('name') or 'this torrent'} from qBittorrent. " + ("Its downloaded files are deleted too." if a.get("deleteFiles") else "Downloaded files are kept."))
        if act == "qall_pause":
            try: tors = self.torrents()
            except Exception: tors = []
            dl = [t for t in tors if (t.get("state") or "").endswith("DL") or t.get("state") == "downloading"]
            return ("Pause all", f"Stops {len(tors)} torrent{'s' if len(tors) != 1 else ''} — {len(dl)} downloading" + (": " + ", ".join((t.get('group') or t.get('name') or '')[:30] for t in dl[:3]) if dl and not incog else "") + ". Seeding stops too until you resume.")
        if act == "qall_resume":
            return ("Resume all", "Starts every stopped torrent again, downloads and seeds.")
        if act in ("episode_delete_file", "episode_delete_files"):
            n = len([x for x in str(a.get("episodeFileIds") or a.get("episodeFileId") or "").split(",") if x.strip()])
            return (f"Delete {n} episode file{'s' if n != 1 else ''}" if n > 1 else "Delete episode file", f"Deletes {'these episodes' if n > 1 else 'this episode'}' file{'s' if n > 1 else ''} from disk. Torrents are not touched; Sonarr will look for the episode{'s' if n > 1 else ''} again while {'they are' if n > 1 else 'it is'} tracked.")
        if act == "import_library":
            return ("Import existing files", "Scans the movie and TV root folders and adds every folder Radarr/Sonarr don't know yet, without searching for missing episodes.")
        if act == "req_decline":
            return ("Decline request", "Marks the request declined in Jellyseerr; the requester sees it as declined. Nothing is downloaded or deleted.")
        if act == "config_defaults":
            return ("Restore defaults", "Rewrites the panel-managed settings in Radarr, Sonarr, qBittorrent and Bazarr to the installer's defaults. The download cap stays.")
        if act == "config_import":
            return ("Load config", "Applies every value in the file to Radarr, Sonarr, qBittorrent, Bazarr and the notification settings now.")
        if act == "user_delete":
            return (f"Remove user {a.get('username') or ''}".strip(), "Deletes this login. Their open sessions end at the next request.")
        return (act or "Confirm", "")

    # ---------------- poster proxy (allowlisted: the two arr hosts, mediacover paths only)
    def poster(self, kind, iid, size="250"):
        """(bytes, content_type) for a poster, fetched with the header key and cached on disk for a day."""
        app = {"movie": "radarr", "tv": "sonarr"}.get(kind)
        if not app or not isinstance(iid, int) or iid <= 0 or size not in ("250", "500"): return None
        fn = os.path.join(self.cache_dir, f"{kind}-{iid}-{size}.jpg") if self.cache_dir else ""
        if fn and os.path.exists(fn) and time.time() - os.path.getmtime(fn) < POSTER_TTL:
            with open(fn, "rb") as f: return f.read(), "image/jpeg"
        url = f"{self.arr_base(app)}/api/v3/mediacover/{iid}/poster-{size}.jpg"
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={"X-Api-Key": self.apikey(app)}), timeout=8)
            data = r.read(); ctype = r.headers.get("Content-Type") or "image/jpeg"
        except Exception:
            if fn and os.path.exists(fn):
                with open(fn, "rb") as f: return f.read(), "image/jpeg"
            return None
        if fn:
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                tmp = fn + ".tmp"
                with open(tmp, "wb") as f: f.write(data)
                os.replace(tmp, fn); self._trim_cache()
            except Exception: pass
        return data, ctype
    def _trim_cache(self):
        try:
            files = [os.path.join(self.cache_dir, f) for f in os.listdir(self.cache_dir) if f.endswith(".jpg")]
            if len(files) <= POSTER_CACHE_MAX: return
            files.sort(key=os.path.getmtime)
            for f in files[:len(files) - POSTER_CACHE_MAX]: os.remove(f)
        except Exception: pass
