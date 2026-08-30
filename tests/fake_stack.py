#!/usr/bin/env python3
"""A fake *arr stack for testing Controllarr without touching a real service.

One stdlib HTTP server per backend (Radarr, Sonarr, Prowlarr, qBittorrent, Jellyseerr, Jellyfin,
Bazarr, ntfy) plus a fake Docker socket (AF_UNIX, HTTP/1.0). Each server implements exactly the
subset of its app's API that app/controllarr.py, panel_data.py, board_gen.py, settings_ops.py
and library_import.py call; anything else answers 404 so a new call in the panel fails loudly here
instead of silently on the box.

Every server also answers `/_control` (any service, any port):
  GET  /_control            -> {"scenario", "down": [...], "calls": [[svc, method, path, body], ...]}
  POST /_control  {"down": ["radarr"]}      take services down (connection refused-like: 503 + drop)
                  {"up": ["radarr"]}        bring them back
                  {"scenario": "default" | "empty" | "container_down" | "backup_stale"}
                  {"clear_calls": true}     forget the recorded backend calls
                  {"reset": true}           scenario default, nothing down, calls cleared

`calls` is the deterministic record a test asserts against ("Retry posted MoviesSearch to Radarr").
Data lives in module state; the harness (tests/harness.py) owns the ports and the temp directory.
Stdlib only, so it runs under the same python3 as the panel — including python:3.12-alpine.
"""
import base64, json, os, socket, socketserver, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# A 1x1 transparent PNG: the poster proxy has something real to serve.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
KEYS = {"radarr": "radarr-test-key", "sonarr": "sonarr-test-key", "prowlarr": "prowlarr-test-key",
        "jellyseerr": "jellyseerr-test-key", "jellyfin": "jellyfin-test-key", "bazarr": "bazarr-test-key"}
QBIT_USER, QBIT_PASS = "admin", "fake-qbit-pass"
STALLED_HASH = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
IMPORT_HASH = "0011223344556677889900aabbccddeeff001122"
EP2_HASH = "1122334455667788990011223344556677889900"
SERVICES = ["radarr", "sonarr", "bazarr", "jellyfin", "jellyseerr", "prowlarr", "qbittorrent", "ntfy"]   # what Controllarr connects to
EXPECTED = ["jellyfin", "qbittorrent", "radarr", "sonarr", "prowlarr", "flaresolverr", "jellyseerr", "autoheal", "bazarr", "ntfy", "controllarr"]   # what the fake Docker socket knows

_LOCK = threading.Lock()
STATE = {"scenario": "default", "down": set(), "calls": [], "data": {}}


def _iso(offset_s=0):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_s))


def build_data(scenario="default"):
    """The dataset for a scenario. 'default' exercises every attention kind the fake can produce."""
    img = lambda n: [{"coverType": "poster", "remoteUrl": f"http://posters.invalid/{n}.jpg"}]
    movies = [
        {"id": 1, "tmdbId": 101, "title": "Arrival", "year": 2016, "monitored": True, "hasFile": True, "isAvailable": True, "runtime": 116,
         "sizeOnDisk": 5_300_000_000, "qualityProfileId": 1, "minimumAvailability": "released", "rootFolderPath": "/data/media/movies",
         "path": "/data/media/movies/Arrival (2016)", "images": img("arrival")},
        {"id": 2, "tmdbId": 102, "title": "Blade Runner 2049", "year": 2017, "monitored": True, "hasFile": False, "isAvailable": True, "runtime": 164,
         "sizeOnDisk": 0, "qualityProfileId": 1, "minimumAvailability": "released", "rootFolderPath": "/data/media/movies",
         "path": "/data/media/movies/Blade Runner 2049 (2017)", "images": img("br2049")},
        {"id": 3, "tmdbId": 103, "title": "Coherence", "year": 2013, "monitored": True, "hasFile": False, "isAvailable": True,
         "sizeOnDisk": 0, "qualityProfileId": 1, "minimumAvailability": "released", "rootFolderPath": "/data/media/movies",
         "path": "/data/media/movies/Coherence (2013)", "images": img("coherence")},
        {"id": 4, "tmdbId": 104, "title": "Dune Part Three", "year": 2027, "monitored": True, "hasFile": False, "isAvailable": False,
         "sizeOnDisk": 0, "qualityProfileId": 1, "minimumAvailability": "released", "rootFolderPath": "/data/media/movies",
         "path": "/data/media/movies/Dune Part Three (2027)", "images": []},
    ]
    series = [
        {"id": 11, "tvdbId": 201, "title": "Severance", "year": 2022, "monitored": True, "qualityProfileId": 1, "seriesType": "standard", "runtime": 55,
         "path": "/data/media/tv/Severance", "rootFolderPath": "/data/media/tv", "images": img("severance"),
         "statistics": {"episodeFileCount": 9, "episodeCount": 9, "sizeOnDisk": 20_000_000_000},
         "seasons": [{"seasonNumber": 1, "monitored": True, "statistics": {"episodeFileCount": 9, "episodeCount": 9, "totalEpisodeCount": 9, "sizeOnDisk": 20_000_000_000}}]},
        {"id": 12, "tvdbId": 202, "title": "The Expanse", "year": 2015, "monitored": True, "qualityProfileId": 1, "seriesType": "standard", "runtime": 42,
         "path": "/data/media/tv/The Expanse", "rootFolderPath": "/data/media/tv", "images": img("expanse"),
         "statistics": {"episodeFileCount": 10, "episodeCount": 23, "sizeOnDisk": 30_000_000_000},
         "seasons": [{"seasonNumber": 1, "monitored": True, "statistics": {"episodeFileCount": 10, "episodeCount": 10, "totalEpisodeCount": 10, "sizeOnDisk": 30_000_000_000}},
                     {"seasonNumber": 2, "monitored": True, "statistics": {"episodeFileCount": 0, "episodeCount": 13, "totalEpisodeCount": 13, "sizeOnDisk": 0}}]},
    ]
    # The Expanse: S01 on disk (ten files), S02 missing (three episodes, two of them downloading); Severance: one episode,
    # on disk, subtitled — the smallest show that a single purge can empty (the cascade case).
    episodes = [{"id": 1100 + n, "seriesId": 12, "seasonNumber": 1, "episodeNumber": n, "title": f"Episode {n}", "monitored": True,
                 "hasFile": True, "airDate": "2015-12-14", "episodeFileId": 7100 + n} for n in range(1, 11)]
    episodes += [{"id": 1200 + n, "seriesId": 12, "seasonNumber": 2, "episodeNumber": n, "title": f"Episode {n}", "monitored": True,
                  "hasFile": False, "airDate": "2017-02-01", "episodeFileId": 0} for n in range(1, 4)]
    episodes += [{"id": 1501, "seriesId": 11, "seasonNumber": 1, "episodeNumber": 1, "title": "Good News About Hell", "monitored": True,
                  "hasFile": True, "airDate": "2022-02-18", "episodeFileId": 7501}]
    episodefiles = [{"id": 7100 + n, "seriesId": 12, "seasonNumber": 1, "size": 3_000_000_000} for n in range(1, 11)] + [{"id": 7501, "seriesId": 11, "seasonNumber": 1, "size": 2_200_000_000}]
    torrents = [
        {"hash": STALLED_HASH, "name": "Blade.Runner.2049.2017.1080p.BluRay.x264-GROUP", "state": "stalledDL", "progress": 0.42,
         "dlspeed": 0, "upspeed": 0, "num_seeds": 0, "num_leechs": 3, "num_complete": 0, "availability": 0.42, "ratio": 0.0,
         "eta": 8640000, "size": 9_000_000_000, "category": "movies", "priority": 1, "dl_limit": 0, "up_limit": 0, "force_start": False},
        {"hash": "ffee0011223344556677889900aabbccddeeff00", "name": "Arrival.2016.1080p.WEB-DL", "state": "uploading", "progress": 1.0,
         "dlspeed": 0, "upspeed": 120_000, "num_seeds": 0, "num_leechs": 2, "num_complete": 40, "availability": 1.0, "ratio": 1.4,
         "eta": 8640000, "size": 5_300_000_000, "category": "movies", "priority": 0, "dl_limit": 0, "up_limit": 0, "force_start": False},
        {"hash": IMPORT_HASH, "name": "The.Expanse.S02E01.1080p", "state": "uploading", "progress": 1.0,
         "dlspeed": 0, "upspeed": 0, "num_seeds": 0, "num_leechs": 0, "num_complete": 12, "availability": 1.0, "ratio": 0.1,
         "eta": 8640000, "size": 2_000_000_000, "category": "tv", "priority": 0, "dl_limit": 0, "up_limit": 0, "force_start": False},
        {"hash": EP2_HASH, "name": "The.Expanse.S02E02.1080p", "state": "downloading", "progress": 0.3,
         "dlspeed": 2_500_000, "upspeed": 10_000, "num_seeds": 8, "num_leechs": 4, "num_complete": 30, "availability": 3.0, "ratio": 0.0,
         "eta": 600, "size": 2_000_000_000, "category": "tv", "priority": 2, "dl_limit": 0, "up_limit": 0, "force_start": False},
    ]
    queue = {"radarr": [{"id": 501, "movieId": 2, "title": torrents[0]["name"], "downloadId": STALLED_HASH.upper(), "status": "warning",
                         "trackedDownloadStatus": "ok", "trackedDownloadState": "downloading", "statusMessages": [{"messages": ["stalled"]}],
                         "size": 9_000_000_000, "sizeleft": 5_220_000_000, "timeleft": "01:20:00"}],
             "sonarr": [{"id": 601, "seriesId": 12, "episodeId": 1201, "title": "The.Expanse.S02E01.1080p", "downloadId": IMPORT_HASH.upper(), "status": "completed",
                         "trackedDownloadStatus": "warning", "trackedDownloadState": "importPending",
                         "statusMessages": [{"messages": ["No files found are eligible for import"]}], "size": 2_000_000_000, "sizeleft": 0},
                        {"id": 602, "seriesId": 12, "episodeId": 1202, "title": "The.Expanse.S02E02.1080p", "downloadId": EP2_HASH.upper(), "status": "downloading",
                         "trackedDownloadStatus": "ok", "trackedDownloadState": "downloading", "statusMessages": [], "size": 2_000_000_000, "sizeleft": 1_400_000_000,
                         "episode": {"id": 1202, "seasonNumber": 2, "episodeNumber": 2, "title": "Doors & Corners"}}]}
    requests_ = [
        {"id": 31, "type": "movie", "status": 1, "createdAt": _iso(-3600), "updatedAt": _iso(-3600), "media": {"id": 900, "tmdbId": 103, "status": 2},
         "requestedBy": {"jellyfinUsername": "sam", "displayName": "Sam"}, "seasons": []},
        {"id": 32, "type": "tv", "status": 2, "createdAt": _iso(-86400), "updatedAt": _iso(-80000), "media": {"id": 901, "tvdbId": 202, "status": 3},
         "requestedBy": {"jellyfinUsername": "alex", "displayName": "Alex"}, "seasons": [{"seasonNumber": 2}]},
    ]
    # the arr's history: every torrent a title ever grabbed, downloading or long since imported and seeding (the queue forgets those)
    history = {"radarr": {1: [{"downloadId": "FFEE0011223344556677889900AABBCCDDEEFF00", "eventType": "downloadFolderImported"}],
                          2: [{"downloadId": STALLED_HASH.upper(), "eventType": "grabbed"}]},
               "sonarr": {12: [{"downloadId": IMPORT_HASH.upper(), "eventType": "grabbed"}, {"downloadId": EP2_HASH.upper(), "eventType": "grabbed"}], 11: []}}
    health = {"radarr": [{"type": "warning", "source": "IndexerStatusCheck", "message": "Indexers unavailable due to failures: Indexer B"}],
              "sonarr": [], "prowlarr": []}
    containers = {n: "running" for n in EXPECTED}
    sessions = [{"UserName": "sam", "Client": "Jellyfin Web", "DeviceName": "Firefox",
                 "NowPlayingItem": {"Name": "Arrival", "Type": "Movie", "Id": "jf-arrival", "RunTimeTicks": 70_000_000_000},
                 "PlayState": {"PlayMethod": "Transcode", "PositionTicks": 7_000_000_000, "IsPaused": False},
                 "TranscodingInfo": {"TranscodeReasons": ["VideoCodecNotSupported"], "VideoCodec": "h264", "AudioCodec": "aac", "Bitrate": 8_000_000}}]
    backups_enabled = False
    if scenario == "empty":
        torrents = []; queue = {"radarr": [], "sonarr": []}; requests_ = [r for r in requests_ if r["status"] != 1]
        health = {"radarr": [], "sonarr": [], "prowlarr": []}; sessions = []
        for m in movies: m["hasFile"] = True; m["isAvailable"] = True; m["sizeOnDisk"] = m["sizeOnDisk"] or 4_000_000_000
        series[1]["statistics"]["episodeFileCount"] = 23
        series[1]["seasons"][1]["statistics"]["episodeFileCount"] = 13
    elif scenario == "container_down":
        containers["radarr"] = "exited"
    elif scenario == "backup_stale":
        backups_enabled = True
    return {"movies": movies, "series": series, "episodes": episodes, "episodefiles": episodefiles, "history": history, "torrents": torrents, "queue": queue, "requests": requests_,
            "health": health, "containers": containers, "sessions": sessions, "backups_enabled": backups_enabled,
            "prefs": {"dl_limit": 0, "up_limit": 2 * 1048576, "alt_dl_limit": 1048576, "alt_up_limit": 524288, "scheduler_enabled": False,
                      "schedule_from_hour": 8, "schedule_to_hour": 23, "max_active_downloads": 2, "max_active_uploads": 3,
                      "max_ratio_enabled": False, "max_ratio": 0, "use_alt_speed_limits": False},
            "indexers": [{"id": 1, "name": "Indexer A", "enable": True, "fields": []}, {"id": 2, "name": "Indexer B", "enable": False, "fields": []}],
            "appprofiles": [{"id": 1, "name": "Standard", "minimumSeeders": 1, "enableRss": True}],
            "releases": {"radarr:2": [{"guid": "g-1", "indexerId": 1, "title": "Blade.Runner.2049.2017.2160p", "size": 30e9, "seeders": 12, "rejected": True,
                                       "rejections": ["Size 30 GB is larger than maximum allowed"], "quality": {"quality": {"name": "Remux-2160p"}}}],
                         "radarr:3": [],
                         "sonarr:12:2": [{"guid": "g-2", "indexerId": 1, "title": "The.Expanse.S02.1080p", "size": 14e9, "seeders": 40, "rejected": True,
                                          "rejections": ["Existing file meets cutoff"], "quality": {"quality": {"name": "WEBDL-1080p"}}}]},
            "gluetun_started": _iso(-7200)}


def set_scenario(name):
    with _LOCK:
        STATE["scenario"] = name; STATE["data"] = build_data(name)


def record(svc, method, path, body):
    with _LOCK:
        STATE["calls"].append([svc, method, path, body if isinstance(body, (dict, list, str)) else None])
        del STATE["calls"][:-500]


set_scenario("default")


# ---------------------------------------------------------------- HTTP servers
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    # -- plumbing
    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        ct = (self.headers.get("Content-Type") or "").lower()
        if not raw: return None
        if "json" in ct:
            try: return json.loads(raw)
            except Exception: return raw.decode(errors="replace")
        if "x-www-form-urlencoded" in ct: return {k: (v[0] if len(v) == 1 else v) for k, v in urllib.parse.parse_qs(raw.decode(errors="replace")).items()}   # a repeated field (Bazarr providers) stays a list
        return raw.decode(errors="replace")
    def _send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        elif isinstance(body, str): body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)
    def _drop(self):
        """A service that is 'down': close the connection without a response (what a dead container looks like)."""
        try: self.connection.shutdown(socket.SHUT_RDWR)
        except Exception: pass
        self.close_connection = True

    def do_GET(self): self._route("GET")
    def do_POST(self): self._route("POST")
    def do_PUT(self): self._route("PUT")
    def do_DELETE(self): self._route("DELETE")

    def _route(self, method):
        svc = self.server.svc
        path, _, qs = self.path.partition("?")
        q = {k: v[0] for k, v in urllib.parse.parse_qs(qs).items()}
        body = self._body()
        if path == "/_control": return self._control(method, body)
        with _LOCK: down = svc in STATE["down"]; d = STATE["data"]
        record(svc, method, self.path, body)
        if down: return self._drop()
        fn = getattr(self, "svc_" + svc)
        try:
            out = fn(method, path, q, body, d)
        except KeyError:
            return self._send(404, {"message": "not found"})
        if out == "__sent__": return
        if out is None: return self._send(404, {"message": f"fake {svc}: no route for {method} {path}"})
        code, payload = out if isinstance(out, tuple) else (200, out)
        if isinstance(payload, bytes): return self._send(code, payload, "image/png")
        if isinstance(payload, str): return self._send(code, payload, "text/plain")
        return self._send(code, payload)

    def _control(self, method, body):
        if method == "GET":
            with _LOCK: return self._send(200, {"scenario": STATE["scenario"], "down": sorted(STATE["down"]), "calls": list(STATE["calls"])})
        b = body if isinstance(body, dict) else {}
        if b.get("reset"):
            set_scenario("default")
            with _LOCK: STATE["down"] = set(); STATE["calls"] = []
        if b.get("scenario"): set_scenario(b["scenario"])
        with _LOCK:
            for s in b.get("down", []): STATE["down"].add(s)
            for s in b.get("up", []): STATE["down"].discard(s)
            if b.get("clear_calls"): STATE["calls"] = []
            return self._send(200, {"ok": True, "scenario": STATE["scenario"], "down": sorted(STATE["down"])})

    # -- helpers shared by radarr / sonarr
    def _arr(self, app, method, path, q, body, d):
        if self.headers.get("X-Api-Key") != KEYS[app]: return 401, {"message": "Unauthorized"}
        if not path.startswith("/api/v3/"): return None
        p = path[len("/api/v3"):]
        items, key = (d["movies"], "movie") if app == "radarr" else (d["series"], "series")
        if p == "/ping": return {"status": "OK"}
        if p == "/system/status": return {"version": "5.0.0-fake" if app == "radarr" else "4.0.0-fake", "appName": app}
        if p == "/health": return d["health"][app]
        if p == "/queue": return {"records": d["queue"][app], "totalRecords": len(d["queue"][app])}
        if p in ("/history/movie", "/history/series"):   # a plain list, keyed by movieId / seriesId (what the real apps answer)
            return d["history"][app].get(int(q.get("movieId") or q.get("seriesId") or 0), [])
        if p == "/wanted/missing": return {"totalRecords": 3 if app == "radarr" else 13, "records": []}
        if p == "/history":
            return {"records": [{"date": _iso(-5400), "sourceTitle": "Arrival.2016.1080p.WEB-DL", "eventType": "downloadFolderImported"}] if app == "radarr"
                    else [{"date": _iso(-90000), "sourceTitle": "Severance.S01E09.1080p", "eventType": "downloadFolderImported"}]}
        if p == "/calendar":
            if app == "radarr": return [{"id": 4, "title": "Dune Part Three", "digitalRelease": _iso(3 * 86400), "hasFile": False}]
            return [{"seriesId": 12, "seasonNumber": 2, "episodeNumber": 4, "title": "Episode 4", "airDateUtc": _iso(2 * 86400), "hasFile": False,
                     "series": {"title": "The Expanse"}}]
        if p == "/qualityprofile":
            if app == "radarr":
                return [{"id": 1, "name": "HD-1080p", "language": {"id": -2, "name": "Original"}, "formatItems": [], "cutoff": 7,
                         "items": [{"quality": {"id": 0, "name": "Unknown"}, "allowed": False}, {"quality": {"id": 7, "name": "Bluray-1080p"}, "allowed": True}]}]
            return [{"id": 1, "name": "HD-1080p", "formatItems": [{"format": 7, "score": 100}], "cutoff": 7,
                     "items": [{"quality": {"id": 0, "name": "Unknown"}, "allowed": False}, {"quality": {"id": 7, "name": "Bluray-1080p"}, "allowed": True}]}]
        if p == "/qualitydefinition": return [{"id": 1, "quality": {"name": "Bluray-1080p"}, "preferredSize": 20, "maxSize": 50, "minSize": 0}]
        if p == "/customformat": return [{"id": 7, "name": "Original-language"}]
        if p == "/language": return [{"id": 1, "name": "English"}, {"id": -2, "name": "Original"}]
        if p == "/indexer": return [{"id": 1, "name": "Indexer A (Prowlarr)", "enableRss": True, "fields": [{"name": "minimumSeeders", "value": 5}]}]
        if p == "/config/mediamanagement":
            return {"downloadPropersAndRepacks": "preferAndUpgrade", "copyUsingHardlinks": True, "recycleBin": "", "recycleBinCleanupDays": 7, "minimumFreeSpaceWhenImporting": 1000}
        if p == "/config/naming": return {"renameMovies": True, "renameEpisodes": True}
        if p == "/rootfolder": return [{"id": 1, "path": "/data/media/movies" if app == "radarr" else "/data/media/tv", "freeSpace": 800e9}]
        if p == "/downloadclient": return [{"id": 1, "name": "qBittorrent", "removeCompletedDownloads": False}]
        if p == "/release":
            if method == "POST": return 200, {"guid": (body or {}).get("guid")}
            k = f"radarr:{q.get('movieId')}" if app == "radarr" else f"sonarr:{q.get('seriesId')}:{q.get('seasonNumber')}"
            return d["releases"].get(k, [])
        if p == "/command":
            if method == "GET": return [{"id": 70, "name": "RssSync", "status": "started"}, {"id": 71, "name": "MoviesSearch" if app == "radarr" else "SeriesSearch", "status": "queued"}, {"id": 69, "name": "Backup", "status": "completed"}]
            return 201, {"id": 77, "name": (body or {}).get("name"), "status": "queued"}
        if p.startswith("/queue/") and method == "DELETE":
            qid = int(p.rsplit("/", 1)[1]); d["queue"][app][:] = [r for r in d["queue"][app] if r["id"] != qid]; return 200, {}
        if p == f"/{key}/lookup": return []
        if p == f"/{key}":
            if method == "GET": return items
            if method == "POST": new = dict(body or {}); new["id"] = max(i["id"] for i in items) + 1; items.append(new); return 201, new
        if p.startswith(f"/{key}/"):
            iid = int(p.rsplit("/", 1)[1])
            it = next((i for i in items if i["id"] == iid), None)
            if not it: return 404, {"message": "not found"}
            if method == "PUT" and isinstance(body, dict): it.update(body); return it
            if method == "DELETE": items.remove(it); return 200, {}
            return it
        if p.startswith("/mediacover/"): return PNG
        if app == "sonarr":
            if p == "/episode": return [e for e in d["episodes"] if str(e["seriesId"]) == q.get("seriesId")]
            if p == "/episodefile": return [f for f in d["episodefiles"] if str(f["seriesId"]) == q.get("seriesId")]
            if p == "/episode/monitor":   # Sonarr applies it at once; so does the fake, so a purge that empties a show can be seen to
                ids = set((body or {}).get("episodeIds") or []); mon = bool((body or {}).get("monitored"))
                for e in d["episodes"]:
                    if e["id"] in ids: e["monitored"] = mon
                return {}
            if p.startswith("/episodefile/") and method == "DELETE":
                fid = int(p.rsplit("/", 1)[1]); d["episodefiles"][:] = [f for f in d["episodefiles"] if f["id"] != fid]
                for e in d["episodes"]:
                    if e.get("episodeFileId") == fid: e["hasFile"] = False; e["episodeFileId"] = 0
                return {}
        return None

    def svc_radarr(self, *a): return self._arr("radarr", *a)
    def svc_sonarr(self, *a): return self._arr("sonarr", *a)

    def svc_prowlarr(self, method, path, q, body, d):
        if self.headers.get("X-Api-Key") != KEYS["prowlarr"]: return 401, {"message": "Unauthorized"}
        if not path.startswith("/api/v1/"): return None
        p = path[len("/api/v1"):]
        if p == "/health": return d["health"]["prowlarr"]
        if p == "/system/status": return {"version": "1.30.0-fake"}
        if p == "/indexer": return d["indexers"]
        if p == "/indexer/test": return 200, {}
        if p == "/appprofile": return d["appprofiles"]
        if p.startswith("/appprofile/") and method == "PUT" and isinstance(body, dict):
            for ap in d["appprofiles"]:
                if str(ap["id"]) == p.rsplit("/", 1)[1]: ap.update(body); return ap
            return 404, {}
        if p.startswith("/indexer/"):
            it = next((i for i in d["indexers"] if str(i["id"]) == p.rsplit("/", 1)[1]), None)
            if not it: return 404, {}
            if method == "PUT" and isinstance(body, dict): it.update(body)
            return it
        if p == "/command":
            if method == "GET": return [{"id": 5, "name": "ApplicationIndexerSync", "status": "started"}]
            return 201, {"name": (body or {}).get("name")}
        return None

    def svc_qbittorrent(self, method, path, q, body, d):
        if not path.startswith("/api/v2/"): return None
        p = path[len("/api/v2"):]
        if p == "/auth/login":
            f = body if isinstance(body, dict) else {}
            if f.get("username") == QBIT_USER and f.get("password") == QBIT_PASS:
                self._send(200, "Ok.", "text/plain", {"Set-Cookie": "SID=fake-sid; HttpOnly; Path=/"}); return "__sent__"
            return 200, "Fails."
        if "SID=fake-sid" not in (self.headers.get("Cookie") or ""): return 403, "Forbidden"
        if p == "/torrents/info": return d["torrents"]
        if p == "/transfer/info":
            return {"dl_info_speed": sum(t["dlspeed"] for t in d["torrents"]), "up_info_speed": sum(t["upspeed"] for t in d["torrents"]),
                    "dl_rate_limit": d["prefs"]["dl_limit"], "up_rate_limit": d["prefs"]["up_limit"], "use_alt_speed_limits": d["prefs"]["use_alt_speed_limits"],
                    "dht_nodes": 312, "connection_status": "connected"}
        if p == "/app/preferences": return d["prefs"]
        if p == "/app/setPreferences":
            try: d["prefs"].update(json.loads((body or {}).get("json", "{}")))
            except Exception: pass
            return 200, "Ok."
        if p == "/transfer/toggleSpeedLimitsMode": d["prefs"]["use_alt_speed_limits"] = not d["prefs"]["use_alt_speed_limits"]; return 200, "Ok."
        if p == "/transfer/speedLimitsMode": return 200, "1" if d["prefs"]["use_alt_speed_limits"] else "0"
        if p.startswith("/torrents/"):
            cmd = p.rsplit("/", 1)[1]
            hashes = (body or {}).get("hashes", "") if isinstance(body, dict) else ""
            if cmd == "delete":
                keep = [] if hashes == "all" else [h.lower() for h in hashes.split("|")]
                d["torrents"][:] = [t for t in d["torrents"] if t["hash"].lower() not in keep]
            elif cmd == "stop" or cmd == "pause":
                for t in d["torrents"]:
                    if hashes == "all" or t["hash"].lower() in hashes.lower(): t["state"] = "stoppedDL" if t["progress"] < 1 else "stoppedUP"
            elif cmd in ("start", "resume"):
                for t in d["torrents"]:
                    if hashes == "all" or t["hash"].lower() in hashes.lower(): t["state"] = "downloading" if t["progress"] < 1 else "uploading"
            elif cmd == "setForceStart":
                for t in d["torrents"]:
                    if t["hash"].lower() in hashes.lower(): t["force_start"] = str((body or {}).get("value")).lower() == "true"
            return 200, "Ok."
        return None

    def svc_jellyseerr(self, method, path, q, body, d):
        if self.headers.get("X-Api-Key") != KEYS["jellyseerr"]: return 403, {"message": "Forbidden"}
        if not path.startswith("/api/v1/"): return None
        p = path[len("/api/v1"):]
        if p == "/status": return {"version": "2.1.0-fake", "updateAvailable": True}
        if p == "/request":
            rs = d["requests"]
            if q.get("filter") == "pending": rs = [r for r in rs if r["status"] == 1]
            return {"pageInfo": {"results": len(rs)}, "results": rs[: int(q.get("take", 50))]}
        if p.startswith("/request/"):
            parts = p.split("/"); rid = int(parts[2]); r = next((x for x in d["requests"] if x["id"] == rid), None)
            if not r: return 404, {"message": "not found"}
            if len(parts) == 4 and parts[3] in ("approve", "decline"): r["status"] = 2 if parts[3] == "approve" else 3; return r
            if method == "DELETE": d["requests"].remove(r); return 204, {}
            return r
        if p.startswith("/media/"):
            if method == "DELETE": return 204, {}
            return {"id": int(p.rsplit("/", 1)[1])}
        if p == "/settings/jobs": return [{"id": "plex-full-scan", "name": "Plex Full Library Scan", "running": False}, {"id": "download-sync", "name": "Download Sync", "running": True}]
        if p in ("/settings/radarr", "/settings/sonarr"):
            return [{"id": 0, "isDefault": True, "name": p.rsplit("/", 1)[1], "activeProfileId": 1,
                     "activeDirectory": "/data/media/movies" if p.endswith("radarr") else "/data/media/tv"}]
        if p.startswith("/settings/") and method == "PUT":
            if isinstance(body, dict) and "id" in body: return 400, {"message": "request.body.id is read-only"}   # Jellyseerr's OpenAPI validation
            d["js_settings_put"] = body; return body or {}
        return None

    def svc_jellyfin(self, method, path, q, body, d):
        if path == "/health": return 200, "Healthy"
        if path == "/System/Info/Public": return {"Version": "10.10.0-fake", "ServerName": "fake"}
        if f'Token="{KEYS["jellyfin"]}"' not in (self.headers.get("Authorization") or ""): return 401, {}
        if path == "/Sessions": return d["sessions"]
        if path == "/ScheduledTasks": return [{"Name": "Scan Media Library", "State": "Running", "CurrentProgressPercentage": 42.5}, {"Name": "Clean Transcode Directory", "State": "Idle"}]
        if path == "/Library/Refresh": return 204, {}
        return None

    def svc_bazarr(self, method, path, q, body, d):
        if self.headers.get("X-API-KEY") != KEYS["bazarr"]: return 401, {}
        if path == "/api/system/ping": return {"status": "ok"}
        if path == "/api/system/settings":
            if method == "POST":
                if "application/json" not in (self.headers.get("Accept") or ""): return 406, {"message": "Not Acceptable"}   # flask-restx: the write happened, the reply cannot be negotiated
                bad = [k for k, v in (body or {}).items() if k.startswith("settings-general-") and v in ("True", "False")]
                if bad: return 406, f"{bad[0].split('-')[-1]} must be bool"   # dynaconf validator: only lowercase true/false are cast
                prof = (body or {}).get("languages-profiles") if isinstance(body, dict) else None
                if prof and any("audio_only_include" not in it for p in json.loads(prof) for it in p.get("items", [])):
                    return 500, {"message": "KeyError: 'audio_only_include'"}   # Bazarr 1.6: list_missing_subtitles crashes before save_settings runs
                d["bazarr_saved"] = body; return 204, {}
            return {"general": {"enabled_providers": ["opensubtitlescom"], "upgrade_subs": True, "days_to_upgrade_subs": 7, "minimum_score": 90,
                                "minimum_score_movie": 70, "adaptive_searching": False, "use_embedded_subs": True, "embedded_subs_show_desired": True,
                                "ignore_pgs_subs": False, "ignore_vobsub_subs": False}}
        if path == "/api/system/languages/profiles":
            return [{"profileId": 1, "name": "Default", "items": [{"id": 1, "language": "en", "hi": "False", "forced": "False"}]}]
        if path == "/api/movies": return {"data": [{"radarrId": m["id"], "title": m["title"]} for m in d["movies"] if m["hasFile"]]}
        if path == "/api/movies/wanted": return {"data": []}
        if path == "/api/episodes/wanted": return {"data": [{"sonarrSeriesId": 12, "sonarrEpisodeId": 1201}]}
        if path == "/api/episodes":   # ?seriesid[]=N — every episode Bazarr knows for the show: S01E10 of The Expanse still wants its English subtitle
            sid = q.get("seriesid[]")
            return {"data": [{"sonarrSeriesId": e["seriesId"], "sonarrEpisodeId": e["id"], "subtitles": [] if e["id"] == 1110 else [{"code2": "en"}],
                              "missing_subtitles": [{"code2": "en"}] if e["id"] == 1110 else []}
                             for e in d["episodes"] if str(e["seriesId"]) == sid and e["hasFile"]]}
        if path in ("/api/providers/movies", "/api/providers/episodes"):
            if method == "POST": return 204, {}
            return {"data": [{"language": "en", "provider": "opensubtitlescom", "score": 95, "release_info": ["Arrival.2016.1080p"], "subtitle": "s-1", "hearing_impaired": False, "forced": False}]}
        if path == "/api/system/tasks":
            if method == "GET": return {"data": [{"name": "Search for Missing Movies Subtitles", "job_running": True}, {"name": "Upgrade Previously Downloaded Subtitles", "job_running": False}]}
            return 204, {}
        return None

    def svc_flaresolverr(self, method, path, q, body, d):
        if path == "/": return 200, "FlareSolverr is ready!"
        return None

    def svc_ntfy(self, method, path, q, body, d):
        if method == "POST" and len(path) > 1: return {"id": "fake", "topic": path[1:]}
        return None


class Server(ThreadingHTTPServer):
    daemon_threads = True; allow_reuse_address = True
    def __init__(self, svc):
        super().__init__(("127.0.0.1", 0), Handler); self.svc = svc


# ---------------------------------------------------------------- fake Docker socket
class DockerHandler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline().decode(errors="replace")
        while True:   # drain headers
            h = self.rfile.readline()
            if not h or h in (b"\r\n", b"\n"): break
        parts = line.split()
        path = parts[1] if len(parts) > 1 else "/"
        with _LOCK: d = STATE["data"]; down = "docker" in STATE["down"]
        record("docker", "GET", path, None)
        if down: return
        body, code = self.route(path, d)
        raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.wfile.write(f"HTTP/1.0 {code} OK\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\n\r\n".encode() + raw)

    def route(self, path, d):
        p = path.split("?")[0]
        def cont(name, state):
            healthy = state == "running"
            return {"Id": ("%040x" % (abs(hash(name)) % (16 ** 40))), "Names": ["/" + name], "State": state,
                    "Status": "Up 2 hours (healthy)" if healthy else "Exited (1) 5 minutes ago"}
        if p == "/info": return {"Name": "fakehost", "ServerVersion": "29.0.0"}, 200
        if p == "/containers/json": return [cont(n, s) for n, s in d["containers"].items()], 200
        if p.startswith("/containers/") and p.endswith("/json"):
            name = p.split("/")[2]
            if name == "gluetun":
                return {"Id": "gluetunid", "State": {"Running": True, "Health": {"Status": "healthy"}, "StartedAt": d["gluetun_started"]},
                        "HostConfig": {"NetworkMode": "bridge"}}, 200
            st = d["containers"].get(name)
            if st is None: return {"message": "No such container"}, 404
            c = cont(name, st)
            return {"Id": c["Id"], "State": {"Running": st == "running", "Health": {"Status": "healthy"} if st == "running" else None, "StartedAt": _iso(-3600)},
                    "HostConfig": {"NetworkMode": "container:gluetunid" if name in ("qbittorrent", "prowlarr", "flaresolverr") else "bridge"}}, 200
        if p.startswith("/containers/gluetun/logs"): return b"", 200
        if p.startswith("/containers/") and p.endswith("/stats"):
            cid = p.split("/")[2]; name = next((n for n, s in d["containers"].items() if cont(n, s)["Id"].startswith(cid)), cid)
            tick = int(time.time() * 1e9)   # monotonic-ish counters so the panel's delta CPU is positive and stable
            return {"cpu_stats": {"cpu_usage": {"total_usage": tick // 50}, "system_cpu_usage": tick, "online_cpus": 4},
                    "memory_stats": {"usage": 150 * 1048576 + len(name) * 1048576, "stats": {"inactive_file": 20 * 1048576}, "limit": 8 * 1024 ** 3}}, 200
        if p.startswith("/containers/") and "/logs" in p:
            cid = p.split("/")[2]; name = next((n for n, s in d["containers"].items() if cont(n, s)["Id"].startswith(cid)), cid)   # by id or by name
            line = f"[Info] {name} is running\n".encode()
            return b"\x01\x00\x00\x00" + len(line).to_bytes(4, "big") + line, 200
        return {"message": "not found"}, 404


class DockerServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    # Panel.container_stats fans out stats + logs for every container over eight threads, so a poll opens
    # ~20 connections at once. The default backlog of 5 drops some, the panel swallows the failure as "no
    # log line", and its 10 s cache holds that empty value long enough for a browser test to see it.
    request_queue_size = 128


# ---------------------------------------------------------------- lifecycle
class FakeStack:
    SERVICES = ("radarr", "sonarr", "prowlarr", "qbittorrent", "jellyseerr", "jellyfin", "bazarr", "ntfy", "flaresolverr")
    def __init__(self, sock_path):
        self.servers = {s: Server(s) for s in self.SERVICES}
        self.ports = {s: srv.server_address[1] for s, srv in self.servers.items()}
        self.sock_path = sock_path
        try: os.unlink(sock_path)
        except FileNotFoundError: pass
        self.docker = DockerServer(sock_path, DockerHandler)
        self.threads = [threading.Thread(target=srv.serve_forever, daemon=True) for srv in list(self.servers.values()) + [self.docker]]
    def start(self):
        for t in self.threads: t.start()
        return self
    def stop(self):
        for srv in list(self.servers.values()) + [self.docker]:
            try: srv.shutdown(); srv.server_close()
            except Exception: pass
        try: os.unlink(self.sock_path)
        except Exception: pass
    @property
    def control_url(self): return f"http://127.0.0.1:{self.ports['radarr']}/_control"


if __name__ == "__main__":
    import tempfile
    fs = FakeStack(os.path.join(tempfile.gettempdir(), "fake-docker.sock")).start()
    print(json.dumps({"ports": fs.ports, "docker": fs.sock_path, "control": fs.control_url}), flush=True)
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt:
        fs.stop()
