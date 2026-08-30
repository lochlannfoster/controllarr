#!/usr/bin/env python3
"""Controllarr — one control panel for your *arr stack. Stdlib only, LAN-only.

A background thread regenerates the library data every CONTROLLARR_REFRESH s (board_gen); the page
(app/static/) polls one JSON endpoint per section. Actions, the drawer and the Settings page act on the
services listed in the config file, wherever they live. Optional password gate (CONTROLLARR_PASSWORD)
with persisted sessions. docs/DASHBOARD.md explains operating it."""
import hashlib, html, http.cookiejar, json, os, re, secrets as _secrets, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import board_gen, settings_ops, library_import, panel_data
from services import http as H, apikey, E, CONFIG_DIR, redact, add_secret, config_problems

PORT = int(os.environ.get("CONTROLLARR_PORT", "3002"))
REFRESH = int(os.environ.get("CONTROLLARR_REFRESH", "15"))
PASSWORD = os.environ.get("CONTROLLARR_PASSWORD") or E.get("CONTROLLARR_PASSWORD") or ""
MIN_SEEDERS = int(E.get("MIN_SEEDERS", "5"))
BACKUP_DIR = E.get("BACKUP_DIR", "")
# Where each service lives: <APP>_HOST and <APP>_PORT (or _PORT_INTERNAL) from the config file, so Controllarr
# reaches a stack it did not install — container names on a shared Docker network, or host addresses, or anything
# else routable. A service with no host configured is simply not part of this install (SERVICES below).
_ENVNAME = {"qbittorrent": "QBIT"}   # the config file spells it QBIT_HOST, as qBittorrent's own docs do
def _host(name, default):
    k = _ENVNAME.get(name, name.upper())
    return E.get(f"{k}_HOST", default), int(E.get(f"{k}_PORT_INTERNAL") or E.get(f"{k}_PORT", "0") or 0)
def SERVICES():
    """The services this install connects to (SERVICES in the config file, written by install.sh). A service that is
    not listed is absent, not broken: its Settings group is hidden and nothing reports it as failed. Empty = unknown,
    and the client assumes everything is there."""
    return [x.strip() for x in E.get("SERVICES", "").split(",") if x.strip()]
_SVC = {"radarr": ("radarr", 7878), "sonarr": ("sonarr", 8989), "bazarr": ("bazarr", 6767), "jellyseerr": ("jellyseerr", 5055),
        "jellyfin": ("jellyfin", 8096), "qbittorrent": ("qbittorrent", 8080), "prowlarr": ("prowlarr", 9696)}
for _n, (_h, _p) in list(_SVC.items()):
    _eh, _ep = _host(_n, _h); _SVC[_n] = (_eh, _ep or _p)
def _base(name): return f"http://{_SVC[name][0]}:{_SVC[name][1]}"
JS_BASE = _base("jellyseerr")
# Everything Controllarr WRITES lives in one directory (users.json, sessions.json, settings.local, poster cache):
# its own volume at /config in the container, CONTROLLARR_DIR anywhere else. Never inside another app's config.
SB_DIR = os.environ.get("CONTROLLARR_DIR") or E.get("CONTROLLARR_DIR") or "/config"
LOCAL = os.path.join(SB_DIR, "settings.local")   # writable overrides (notify/quiet-hours)
SESSIONS_FILE = os.path.join(SB_DIR, "sessions.json")
SESSION_TTL = 30 * 86400   # matches the cookie's Max-Age
def _load_sessions():
    try:
        with open(SESSIONS_FILE) as f: d = json.load(f)
        now = time.time()
        return {t: s for t, s in d.items() if isinstance(s, dict) and s.get("exp", 0) > now}
    except Exception: return {}
SESSIONS = _load_sessions()   # token -> {"user", "role", "exp"}; persisted so a panel restart keeps people signed in
_SESS_LOCK = threading.Lock()
def _save_sessions():
    try:
        with _SESS_LOCK:
            now = time.time()
            for t in [t for t, s in SESSIONS.items() if s.get("exp", 0) <= now]: SESSIONS.pop(t, None)
            tmp = SESSIONS_FILE + ".tmp"
            with open(tmp, "w") as f: json.dump(SESSIONS, f)
            os.chmod(tmp, 0o600); os.replace(tmp, SESSIONS_FILE); _own_like_dir(SESSIONS_FILE)
    except Exception as e: print("sessions: could not save:", redact(e), flush=True)

# ---------------- static assets (CSS / JS / fonts / icons) + page templates ----------------
# Served from scripts/static next to this file (the installer copies the directory). Allowlisted
# extensions, no directory listing, no path escape; immutable caching when the URL carries ?v=.
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".woff2": "font/woff2",
                 ".svg": "image/svg+xml", ".png": "image/png", ".txt": "text/plain; charset=utf-8", ".json": "application/json"}
_ASSET = {"stamp": None, "ver": "0"}
def asset_ver():
    """Short hash of static/ (names, sizes, mtimes). Recomputed whenever the directory changes, so a re-copied
    static/ (installer re-run, hot fix) gets a new ?v= without restarting the panel — the old URLs are
    immutable-cached in browsers, so the version must move with the files."""
    h = hashlib.sha1(); stamp = []
    for root, _, files in os.walk(STATIC_DIR):
        for f in sorted(files):
            try: st = os.stat(os.path.join(root, f)); stamp.append((f, st.st_size, int(st.st_mtime)))
            except Exception: pass
    stamp = tuple(stamp)
    if stamp != _ASSET["stamp"]:
        for f, size, mt in stamp: h.update(f"{f}{size}{mt}".encode())
        _ASSET["stamp"], _ASSET["ver"] = stamp, h.hexdigest()[:8]
    return _ASSET["ver"]
def static_file(rel):
    """(bytes, content-type) for a file under STATIC_DIR, or None."""
    rel = urllib.parse.unquote(rel or "")
    if not rel or ".." in rel or rel.startswith(("/", "\\")) or "\\" in rel: return None
    ext = os.path.splitext(rel)[1].lower()
    if ext not in _STATIC_TYPES: return None
    full = os.path.normpath(os.path.join(STATIC_DIR, rel))
    if not full.startswith(STATIC_DIR + os.sep) or not os.path.isfile(full): return None
    with open(full, "rb") as f: return f.read(), _STATIC_TYPES[ext]
_IMPORT_RE = re.compile(rb"""((?:from\s+|import\s*\(\s*)(['"]))(\.{1,2}/[^'"?]+\.js)\2""")
def _version_imports(js):
    """Give bare ES-module specifiers the same ?v= as the page's <script> tags, so modules cache immutably too."""
    v = asset_ver().encode()
    return _IMPORT_RE.sub(lambda m: m.group(1) + m.group(3) + b"?v=" + v + m.group(2), js)
def _safe_next(p):
    """A post-login destination we are willing to follow: a local path, never a URL, never an API/asset path."""
    p = (p or "").strip()
    if not p.startswith("/") or p.startswith("//") or "\\" in p or len(p) > 512: return "/"
    if p.startswith(("/api/", "/static/", "/img/", "/login", "/logout", "/health")): return "/"
    return p
def page(name, **ctx):
    """An HTML template from static/ with __CONFIG__ (JSON for the client) and __VER__ filled in."""
    with open(os.path.join(STATIC_DIR, name + ".html"), encoding="utf-8") as f: tpl = f.read()
    cfg = json.dumps(ctx).replace("</", "<\\/")
    return tpl.replace("__CONFIG__", cfg).replace("__VER__", asset_ver())

# ---------------- users / roles (multi-user access) ----------------
USERS_FILE = os.path.join(SB_DIR, "users.json")
# capabilities an admin can grant/deny to a non-admin role (admins always have all of them)
CONFIGURABLE_CAPS = ["can_purge", "can_delete_files", "can_import", "can_remove", "can_change_root",
                     "can_grab", "can_control_client", "can_manage_requests"]
# action -> capability required (absent = allowed to any logged-in user)
_CAP_FOR = {"purge": "can_purge", "import_library": "can_import", "add": "can_import",
            "q_remove": "can_remove", "blocklist_retry": "can_remove", "t_delete": "can_remove",
            "set_root_folder": "can_change_root", "episode_delete_file": "can_delete_files",
            "grab": "can_grab",
            "qall_pause": "can_control_client", "qall_resume": "can_control_client", "alt_toggle": "can_control_client",
            "t_dllimit": "can_control_client", "t_uplimit": "can_control_client", "t_forcestart": "can_control_client",
            "req_approve": "can_manage_requests", "req_decline": "can_manage_requests", "req_delete": "can_manage_requests",
            "indexers_test_all": "can_control_client", "rss_sync": "can_control_client", "alt_set": "can_control_client",
            "t_purge": "can_purge", "season_purge": "can_purge", "episode_purge": "can_purge", "episode_delete_files": "can_delete_files"}
_USERS_LOCK = threading.Lock()

def hash_pw(pw, salt=None):
    salt = salt or _secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", (pw or "").encode(), bytes.fromhex(salt), 200_000).hex()
    return salt, h
def verify_pw(pw, salt, h):
    try: return _secrets.compare_digest(hashlib.pbkdf2_hmac("sha256", (pw or "").encode(), bytes.fromhex(salt), 200_000).hex(), h)
    except Exception: return False

def _default_store():
    """Bootstrap: seed an 'admin' from CONTROLLARR_PASSWORD so nobody is locked out."""
    users = {}
    if PASSWORD:
        salt, h = hash_pw(PASSWORD); users["admin"] = {"salt": salt, "hash": h, "role": "admin"}
    return {"users": users, "roles": {"user": {c: False for c in CONFIGURABLE_CAPS}}}

def load_users():
    with _USERS_LOCK:
        try:
            with open(USERS_FILE) as f: d = json.load(f)
            if not isinstance(d, dict) or "users" not in d: raise ValueError("bad shape")
        except FileNotFoundError:
            d = _default_store(); _write_users(d)
        except Exception as e:
            # never silently destroy a corrupt store: keep it aside for recovery, then reseed
            try:
                os.replace(USERS_FILE, USERS_FILE + ".bad")
                print("users.json unreadable, moved to users.json.bad:", e, flush=True)
            except Exception: pass
            d = _default_store(); _write_users(d)
        d.setdefault("roles", {}).setdefault("user", {c: False for c in CONFIGURABLE_CAPS})
        return d
def _own_like_dir(path):
    """The panel runs as root inside its container; files it creates would be root-owned and unreadable
    by the host user whose cron runs the nightly backup. Give new files the owner of their directory."""
    try:
        st = os.stat(os.path.dirname(path) or ".")
        if os.geteuid() == 0 and (st.st_uid, st.st_gid) != (0, 0): os.chown(path, st.st_uid, st.st_gid)
    except Exception: pass
def _write_users(d):
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w") as f: json.dump(d, f, indent=2)
    os.replace(tmp, USERS_FILE)
    try: os.chmod(USERS_FILE, 0o600)
    except Exception: pass
    _own_like_dir(USERS_FILE)
def save_users(d):
    with _USERS_LOCK: _write_users(d)

def authenticate(username, password):
    """Return the user's role on success, else None. Falls back to the legacy single password as admin."""
    username = (username or "").strip() or "admin"
    d = load_users(); u = d.get("users", {}).get(username)
    if u and verify_pw(password, u.get("salt", ""), u.get("hash", "")):
        return u.get("role", "user")
    if PASSWORD and password == PASSWORD and not d.get("users"):   # first-run legacy login
        return "admin"
    return None
def role_caps(role):
    if role == "admin": return {c: True for c in CONFIGURABLE_CAPS}
    return {c: bool(v) for c, v in load_users().get("roles", {}).get(role, {}).items()}
def _can(sess, cap):
    if not sess: return False
    if sess.get("role") == "admin": return True
    return bool(role_caps(sess.get("role")).get(cap))
def list_users():
    d = load_users(); return [{"username": u, "role": v.get("role", "user")} for u, v in sorted(d.get("users", {}).items())]
def save_user(b):
    name = (b.get("username") or "").strip()
    if not name or not name.replace("_", "").replace("-", "").isalnum(): return False, "Invalid username"
    d = load_users(); users = d.setdefault("users", {}); ex = users.get(name); pw = b.get("password") or ""
    role = b.get("role") or (ex or {}).get("role", "user")   # omitted role (password reset) keeps the existing one
    if role not in ("admin", "user"): return False, "Invalid role"
    if not ex and not pw: return False, "Password required for a new user"
    if ex and ex.get("role") == "admin" and role != "admin" \
            and not any(v.get("role") == "admin" for u, v in users.items() if u != name):
        return False, "Can't demote the last admin"
    ent = ex or {}
    if pw: ent["salt"], ent["hash"] = hash_pw(pw)
    ent["role"] = role; users[name] = ent; save_users(d)
    return True, ("Updated " + name if ex else "Added " + name)
def delete_user(name):
    name = (name or "").strip(); d = load_users(); users = d.get("users", {})
    if name not in users: return False, "No such user"
    admins = [u for u, v in users.items() if v.get("role") == "admin"]
    if users[name].get("role") == "admin" and len(admins) <= 1: return False, "Can't remove the last admin"
    users.pop(name, None); save_users(d); return True, "Removed " + name
def save_role(b):
    role = b.get("role", "user")
    if role != "user": return False, "Only the 'user' role is editable"
    d = load_users(); d.setdefault("roles", {})[role] = {c: bool(b.get(c)) for c in CONFIGURABLE_CAPS}
    save_users(d); return True, "Permissions saved"

ARR_TIMEOUT = 10   # the arrs answer in milliseconds on this LAN; 60 s only ever hid a dead service
def arr(app, path, method="GET", data=None):
    return H(method, f"{_base(app)}/api/v3{path}", headers={"X-Api-Key": apikey(app)}, data=data, timeout=ARR_TIMEOUT)

PROWLARR_HOST = _SVC["prowlarr"][0]   # "gluetun" when Prowlarr is routed through the VPN
def prowlarr(path, method="GET", data=None):
    return H(method, f"{_base('prowlarr')}/api/v1{path}", headers={"X-Api-Key": apikey("prowlarr")}, data=data, timeout=ARR_TIMEOUT)

QBIT_HOST = _SVC["qbittorrent"][0]   # "gluetun" when qBittorrent is routed through the VPN
_QB = {"op": None, "ts": 0}; _QB_LOCK = threading.Lock()
def qbit(fresh=False):
    """Authenticated qBittorrent opener + base url, or (None, None). The session is cached ~60 s
    (one login per minute instead of one per call); a failed login ("Fails.") is treated as down."""
    url = _base("qbittorrent")
    with _QB_LOCK:
        if not fresh and _QB["op"] is not None and time.time() - _QB["ts"] < 60:
            return _QB["op"], url
        cj = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        try:
            r = op.open(urllib.request.Request(url + "/api/v2/auth/login",
                    data=urllib.parse.urlencode({"username": E.get("QBIT_USER", "admin"), "password": E.get("QBIT_PASS", "")}).encode(),
                    headers={"Referer": url}), timeout=15)
            body = r.read() or b"Ok."
            if b"Ok" not in body:   # qBittorrent answers "Ok." / "Fails." with HTTP 200
                _QB.update(op=None, ts=0); return None, None
            _QB.update(op=op, ts=time.time()); return op, url
        except Exception:
            _QB.update(op=None, ts=0); return None, None

def _bazarr_key():
    try:
        for ln in open(os.path.join(CONFIG_DIR, "bazarr", "config", "config.yaml")):
            if ln.strip().startswith("apikey:"):
                k = ln.split(":", 1)[1].strip().strip("'\"")
                add_secret(k); return k                      # so it can never appear in a response or a log line
    except Exception: pass
    return None

# Bazarr's API (flask-restx) negotiates the response type: a POST without an Accept header is answered 406 AFTER the
# settings were written, which the panel would report as a refusal. Every call therefore says what it accepts.
_BZ_ACCEPT = "application/json"
def bazarr_post(fields):
    k = _bazarr_key()
    if not k: return
    op = urllib.request.build_opener()
    try:
        op.open(urllib.request.Request(_base("bazarr") + "/api/system/settings",
                data=urllib.parse.urlencode(fields).encode(),
                headers={"X-API-KEY": k, "Content-Type": "application/x-www-form-urlencoded", "Accept": _BZ_ACCEPT}), timeout=20)
    except urllib.error.HTTPError as e:   # 406 carries Bazarr's own validation message — the one thing worth showing
        body = ""
        try: body = e.read().decode(errors="replace").strip().strip('"')[:160]
        except Exception: pass
        raise RuntimeError(f"HTTP {e.code}" + (f": {body}" if body else "")) from None

def _bazarr_get(path):
    k = _bazarr_key()
    if not k: return None
    try:
        req = urllib.request.Request(_base("bazarr") + path, headers={"X-API-KEY": k, "Accept": _BZ_ACCEPT})
        return json.load(urllib.request.build_opener().open(req, timeout=15))
    except Exception:
        return None

def bazarr_api(path, method="POST", fields=None, timeout=25):
    """Call a Bazarr /api endpoint (form-encoded). Returns (ok, parsed_json_or_None)."""
    k = _bazarr_key()
    if not k: return False, None
    try:
        data = urllib.parse.urlencode(fields or {}).encode() if fields is not None else None
        req = urllib.request.Request(_base("bazarr") + path, data=data, method=method,
                                     headers={"X-API-KEY": k, "Content-Type": "application/x-www-form-urlencoded", "Accept": _BZ_ACCEPT})
        r = urllib.request.build_opener().open(req, timeout=timeout)
        body = r.read()
        try: return True, json.loads(body) if body else True
        except Exception: return True, None
    except Exception as e:
        print("bazarr_api error:", path, redact(e), flush=True); return False, None

# Optional: with a Docker socket mounted read-only Controllarr can also show every container's state, memory and
# last log line. Set DOCKER_SOCK= (empty) and it simply does not offer that.
DOCKER_SOCK = E.get("DOCKER_SOCK", "/var/run/docker.sock")
def _docker_raw(path):
    """Raw bytes from the Docker socket for `path` (body only). Read-only socket."""
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(6); s.connect(DOCKER_SOCK)
    s.sendall(("GET " + path + " HTTP/1.0\r\nHost: docker\r\n\r\n").encode())
    buf = b""
    while True:
        d = s.recv(65536)
        if not d: break
        buf += d
    s.close()
    return buf.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in buf else buf



_SUBMAP = {"data": {}, "ts": 0}
def sub_map():
    """Per-item English-subtitle status. {('movie',radarrId): bool} and {('tv',seriesId): missing_count}. Cached 5 min."""
    if _SUBMAP["data"] and time.time() - _SUBMAP["ts"] < 300:
        return _SUBMAP["data"]
    out = {}
    def rows(x):
        if isinstance(x, dict): return x.get("data") or []
        return x or []
    try:
        wanted = {m.get("radarrId") for m in rows(_bazarr_get("/api/movies/wanted?start=0&length=1000")) if m.get("radarrId") is not None}
        for m in rows(_bazarr_get("/api/movies?start=0&length=1000")):
            rid = m.get("radarrId")
            if rid is not None: out[("movie", rid)] = rid not in wanted
    except Exception: pass
    try:
        miss = {}
        ew = _bazarr_get("/api/episodes/wanted?start=0&length=2000")
        for e in rows(ew):
            sid = e.get("sonarrSeriesId")
            if sid is not None: miss[sid] = miss.get(sid, 0) + 1
        for sid, n in miss.items(): out[("tv", sid)] = n
        out["_tv_seen"] = ew is not None   # only trust "no missing subs" when Bazarr actually answered
    except Exception: pass
    _SUBMAP.update(data=out, ts=time.time())
    return out

def _js_key():
    try:
        k = json.load(open(os.path.join(CONFIG_DIR, "jellyseerr", "settings.json")))["main"]["apiKey"]
        add_secret(k); return k                              # so it can never appear in a response or a log line
    except Exception: return None
def js(path, method="GET", data=None):
    k = _js_key()
    return H(method, JS_BASE + "/api/v1" + path, headers={"X-Api-Key": k}, data=data, timeout=ARR_TIMEOUT)

# ---------------- overrides file (notify / quiet-hours) ----------------
def read_local():
    d = {}
    try:
        for ln in open(LOCAL):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1); d[k.strip()] = v.strip()
    except Exception: pass
    return d
def write_local(updates):
    cur = read_local(); cur.update({k: str(v) for k, v in updates.items()})
    with open(LOCAL, "w") as f:
        for k, v in cur.items(): f.write(f"{k}={v}\n")
    try: os.chmod(LOCAL, 0o600)                              # it carries the ntfy URL, which can carry a token
    except Exception: pass
    _own_like_dir(LOCAL)

# ---------------- live data (background regeneration) ----------------
_LOCK = threading.Lock()
_STATE = {"status": {"items": [], "summary": {}, "resources": {}, "health": {}, "activity": [], "generated": 0}, "cache": {}}
_WAKE = threading.Event()
def _regen():
    try:
        st, cache = board_gen.generate(arr, qbit, H, JS_BASE, CONFIG_DIR, MIN_SEEDERS, BACKUP_DIR, _STATE["cache"],
                                       data_dir=E.get("MEDIA_DIR") or E.get("DATA_DIR", ""))
        with _LOCK: _STATE["status"] = st; _STATE["cache"] = cache
    except Exception as e:
        print("regen error:", e, flush=True)
def _loop():
    while True:
        _regen(); _WAKE.wait(REFRESH); _WAKE.clear()
def status():
    with _LOCK: return _STATE["status"]

# ---------------- queue helpers ----------------
def _queue_records(app, arr_id):
    st, q = arr(app, "/queue?pageSize=500&includeUnknownSeriesItems=true")
    key = "movieId" if app == "radarr" else "seriesId"
    return [r for r in (q or {}).get("records", []) if r.get(key) == arr_id]
def _hashes(app, arr_id):
    return list({r["downloadId"].lower() for r in _queue_records(app, arr_id) if r.get("downloadId")})
def _title_hashes(app, arr_id):
    """Every torrent qBittorrent still holds for a title: the queue (downloading) plus the arr's history (grabbed and
    imported — a finished torrent that is seeding has left the queue but its files are still on disk), intersected with
    the live torrent list. Without a reachable qBittorrent the queue's hashes are all that is known."""
    hs = set(_hashes(app, arr_id))
    st, hist = arr(app, f"/history/movie?movieId={arr_id}" if app == "radarr" else f"/history/series?seriesId={arr_id}")
    rows = hist if isinstance(hist, list) else (hist or {}).get("records", []) if isinstance(hist, dict) else []
    for r in rows:
        h = (r.get("downloadId") or "").lower()
        if h: hs.add(h)
    live = {t["hash"].lower() for t in _torrents_info()[0]}
    return sorted(hs & live) if live else sorted(hs & set(_hashes(app, arr_id)))
def _qbit_cmd(cmd, hashes):
    if not hashes: return
    op, url = qbit()
    if not op: return
    data = {"hashes": "|".join(hashes)}
    if cmd == "delete": data["deleteFiles"] = "true"
    try: op.open(urllib.request.Request(url + "/api/v2/torrents/" + cmd, data=urllib.parse.urlencode(data).encode(), headers={"Referer": url}), timeout=20)
    except Exception: pass
def _qbit_raw(endpoint, data):
    """POST arbitrary form data to a qBittorrent /api/v2 endpoint (e.g. torrents/setDownloadLimit)."""
    op, url = qbit()
    if not op: return
    try: op.open(urllib.request.Request(url + "/api/v2/" + endpoint, data=urllib.parse.urlencode(data).encode(), headers={"Referer": url}), timeout=20)
    except Exception: pass

_HASH_RE = re.compile(r"^[0-9a-f]{32,64}(\|[0-9a-f]{32,64})*$")
def _hash_arg(a):
    """The torrent hash(es) an action names, lower-cased — 'a|b|c' is allowed everywhere a hash is, so a season's or
    a group's torrents are one call; anything else is refused."""
    h = str(a.get("hash") or "").strip().lower()
    return h if _HASH_RE.match(h) else ""

_QIDX = {"data": {}, "ts": 0}; _QIDX_LOCK = threading.Lock()
_EP_RE = re.compile(r"S(\d{1,2})E(\d{1,3})", re.I)
def queue_index(max_age=5):
    """hash -> what Radarr/Sonarr know about that download: app, kind, arr id, queue-record ids and (TV) the episodes
    it carries. Both queues, includeEpisode/includeMovie, cached a few seconds. The Live section labels torrents from
    it (S10E03 · title instead of ten rows all called Futurama), the series tree maps episodes to torrents, and the
    torrent-level purge finds the episodes behind a hash."""
    with _QIDX_LOCK:
        if time.time() - _QIDX["ts"] < max_age: return _QIDX["data"]
    out = {}
    for app in ("radarr", "sonarr"):
        st, q = arr(app, "/queue?pageSize=500&includeUnknownSeriesItems=true&includeEpisode=true&includeMovie=true")
        for r in (q or {}).get("records", []) if isinstance(q, dict) else []:
            h = (r.get("downloadId") or "").lower()
            if not h: continue
            kind = "movie" if app == "radarr" else "tv"
            e = out.setdefault(h, {"app": app, "kind": kind, "id": r.get("movieId") if kind == "movie" else r.get("seriesId"),
                                   "qids": [], "episodes": [], "title": r.get("title") or ""})
            if r.get("id") is not None: e["qids"].append(r["id"])
            if kind == "tv":
                ep = r.get("episode") or {}
                sn, en = ep.get("seasonNumber"), ep.get("episodeNumber")
                if sn is None or en is None:
                    m = _EP_RE.search(r.get("title") or "")
                    if m: sn, en = int(m.group(1)), int(m.group(2))
                if sn is not None and en is not None:
                    e["episodes"].append({"id": r.get("episodeId") or ep.get("id"), "season": sn, "ep": en, "title": ep.get("title") or ""})
    with _QIDX_LOCK: _QIDX.update(data=out, ts=time.time())
    return out
_SEASON_RE = re.compile(r"\bS(\d{1,2})\b(?!E)", re.I)
_XEP_RE = re.compile(r"\b(\d{1,2})x(\d{2,3})\b")
def _torrent_label(name, episodes, kind=""):
    """(label, season) for a torrent: from the queue's episodes when Sonarr still lists it, else from the release name —
    a finished, seeding episode is no longer in the queue but its name still says S08E03 (or 8x03, or S01 for a pack).
    Movies never get one."""
    if kind == "movie": return "", None
    if episodes:
        seasons = {e["season"] for e in episodes}
        return _ep_label(episodes), (next(iter(seasons)) if len(seasons) == 1 else None)
    n = name or ""
    m = _EP_RE.search(n) or _XEP_RE.search(n)
    if m: return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}", int(m.group(1))
    m = _SEASON_RE.search(n)
    if m: return f"S{int(m.group(1)):02d} (season pack)", int(m.group(1))
    return "", None
def _ep_label(eps):
    """'S02E01 · Title' for one episode, 'S02 · E01–E13 (13 episodes)' for a pack, '' when nothing is known."""
    eps = sorted({(e["season"], e["ep"]): e for e in eps}.values(), key=lambda e: (e["season"], e["ep"]))
    if not eps: return ""
    if len(eps) == 1:
        e = eps[0]; return f"S{e['season']:02d}E{e['ep']:02d}" + (f" · {e['title']}" if e.get("title") else "")
    seasons = sorted({e["season"] for e in eps})
    if len(seasons) == 1: return f"S{seasons[0]:02d} · E{eps[0]['ep']:02d}–E{eps[-1]['ep']:02d} ({len(eps)} episodes)"
    return f"S{seasons[0]:02d}–S{seasons[-1]:02d} ({len(eps)} episodes)"

_LIVE = {}   # hash -> live qBittorrent torrent, refreshed on each /api/board render
_TINFO = {"tors": [], "info": {}, "ts": 0}
def _torrents_info(max_age=3):
    """qBittorrent torrents/info + transfer/info, cached a few seconds so one page poll (board + live +
    drawer) costs one fetch instead of three."""
    if time.time() - _TINFO["ts"] < max_age: return _TINFO["tors"], _TINFO["info"]
    op, url = qbit()
    if not op: return [], {}
    try:
        tors = json.load(op.open(url + "/api/v2/torrents/info?sort=priority", timeout=15))
        info = json.load(op.open(url + "/api/v2/transfer/info", timeout=15))
        _TINFO.update(tors=tors, info=info, ts=time.time())
        return tors, info
    except Exception:
        qbit(fresh=True)   # session may have expired — re-login on the next call
        return [], {}
def _qbit_live():
    tors, info = _torrents_info()
    return {t["hash"].lower(): t for t in tors}, (info.get("dl_info_speed", 0), info.get("up_info_speed", 0))
def _eta(sec):
    if not sec or sec >= 8640000: return "∞"
    m = int(sec) // 60
    return f"{m}m" if m < 60 else f"{m // 60}h{m % 60:02d}m"
def _why(t):
    """Plain-English reason a torrent isn't moving, from the fields torrents/info already carries.
    'Peers' alone is misleading: 30 connected leechers with 0% each is a dead swarm, not a slow one."""
    st = t.get("state", "") or ""
    if (t.get("progress") or 0) >= 1: return ""
    if st == "queuedDL": return f"queued behind the {MAX_ACTIVE_DL_CAP}-download cap"
    if st in ("metaDL", "forcedMetaDL"): return "fetching metadata — no peer has shared the torrent info yet"
    if st.startswith("checking"): return "verifying files on disk"
    if st not in ("stalledDL", "downloading", "forcedDL") or (t.get("dlspeed") or 0) > 0: return ""
    avail = t.get("availability") or 0                      # distributed copies among connected peers (<1 = pieces missing)
    cs, cp = t.get("num_seeds", 0) or 0, t.get("num_leechs", 0) or 0    # connected
    ss = t.get("num_complete", 0) or 0                       # seeds the trackers/DHT report
    if cs == 0 and ss <= 0 and avail < 1:
        return (f"dead swarm — {cp} peers connected but none has the file (0 seeds, {avail * 100:.0f}% available)"
                if cp else "dead — no seeds and no peers")
    if cs == 0 and ss > 0: return f"{ss} seeds reported but none reachable (0 connected)"
    if avail < 1: return f"only {avail * 100:.0f}% of the file exists among connected peers — waiting for a seed"
    if st == "stalledDL": return f"{cs} seeds connected but sending nothing (choked or slow)"
    return ""
def _derive_title(name):
    """Best-effort series/movie title from a release name, for grouping torrents."""
    import re
    n = name or ""
    m = re.search(r'[._ ](S\d{1,2}(E\d{1,3})?|\d{1,2}x\d{2}|Season[._ ]?\d+|COMPLETE)\b', n, re.I) \
        or re.search(r'[._ ](19|20)\d{2}[._ ]', n)
    if m: n = n[:m.start()]
    n = re.sub(r'[._]+', ' ', n).strip(' -')
    return n or (name or "Other")

def qbit_torrents():
    amap = {}   # hash -> arr item (title/poster/kind), for torrents Radarr/Sonarr is tracking
    for it in status().get("items", []):
        for h in (it.get("hashes") or []):
            amap[str(h).lower()] = it
    try:
        tors, _ = _torrents_info(); qi = queue_index()
        out = []
        for t in tors:
            it = amap.get(t["hash"].lower()); q = qi.get(t["hash"].lower()) or {}
            group = (it.get("title") if it else None) or _derive_title(t.get("name"))
            kind = (it.get("kind") if it else "") or q.get("kind") or ""
            iid = (it.get("id") if it else None) or q.get("id")
            label, season = _torrent_label(t.get("name"), q.get("episodes") or [], kind)
            out.append({"hash": t["hash"], "name": t.get("name"), "state": t.get("state"), "progress": round((t.get("progress") or 0) * 100),
                        "dlspeed": t.get("dlspeed", 0), "upspeed": t.get("upspeed", 0), "num_seeds": t.get("num_seeds", 0),
                        "num_leechs": t.get("num_leechs", 0), "ratio": round(t.get("ratio", 0), 2), "eta": _eta(t.get("eta")), "why": _why(t),
                        "size": t.get("size", 0), "category": t.get("category", ""), "priority": t.get("priority", 0),
                        "matched": bool(it or isinstance(iid, int)), "group": group, "kind": kind, "label": label,
                        "episodes": q.get("episodes") or [], "season": season,
                        "poster": (f"/img/poster/{it.get('kind')}/{it.get('id')}" if it and it.get("poster") and isinstance(it.get("id"), int) else ""),
                        "iid": iid, "tmdbId": (it.get("tmdbId") if it else None), "tvdbId": (it.get("tvdbId") if it else None),
                        "arr_stage": (it.get("stage") if it else ""), "arr_detail": (it.get("detail") if it else ""),
                        "arr_reason": (it.get("reason") if it else ""), "arr_year": (it.get("year") if it else "")})
        return out
    except Exception:
        return []
def _poster(obj):
    for im in obj.get("images", []) or []:
        if im.get("coverType") == "poster": return im.get("remoteUrl") or im.get("url") or ""
    return ""
def item_detail(kind, aid):
    """Everything the per-item drawer shows: quality/monitoring/root/subs/seasons + this item's torrents."""
    app = "radarr" if kind == "movie" else "sonarr"
    ep = f"/movie/{aid}" if kind == "movie" else f"/series/{aid}"
    st, obj = arr(app, ep)
    if not isinstance(obj, dict): return None
    profs = [{"id": p["id"], "name": p["name"]} for p in (arr(app, "/qualityprofile")[1] or [])]
    roots = [{"path": r.get("path"), "freeGB": round((r.get("freeSpace") or 0) / 1e9)} for r in (arr(app, "/rootfolder")[1] or [])]
    hashes = set(_title_hashes(app, aid))   # the queue's torrents and the seeding ones the arr's history remembers
    tors = []
    if hashes:
        for t in _torrents_info()[0]:
            if t.get("hash", "").lower() in hashes:
                tors.append({"hash": t["hash"], "name": t.get("name"), "state": t.get("state"),
                    "progress": round((t.get("progress") or 0) * 100), "dlspeed": t.get("dlspeed", 0),
                    "upspeed": t.get("upspeed", 0), "num_seeds": t.get("num_seeds", 0), "ratio": round(t.get("ratio", 0), 2),
                    "eta": _eta(t.get("eta")), "priority": t.get("priority", 0), "size": t.get("size", 0), "why": _why(t),
                    "dl_limit": round((t.get("dl_limit", 0) or 0) / 1e6, 2), "up_limit": round((t.get("up_limit", 0) or 0) / 1e6, 2),
                    "force_start": bool(t.get("force_start"))})
    sm = sub_map()
    out = {"kind": kind, "id": aid, "title": obj.get("title"), "year": obj.get("year"), "poster": _poster(obj),
           "tmdbId": obj.get("tmdbId"), "tvdbId": obj.get("tvdbId"),   # the drawer needs these for Purge (Jellyseerr request)
           "stage": next((i.get("stage") for i in status().get("items", []) if i.get("kind") == kind and i.get("id") == aid), ""),
           "monitored": obj.get("monitored"), "qualityProfileId": obj.get("qualityProfileId"), "profiles": profs,
           "rootFolderPath": obj.get("rootFolderPath") or obj.get("path"), "rootfolders": roots,
           "sizeOnDisk": obj.get("sizeOnDisk"), "torrents": tors}
    if kind == "movie":
        out["minimumAvailability"] = obj.get("minimumAvailability"); out["hasFile"] = obj.get("hasFile")
        out["sub"] = sm.get(("movie", aid))
    else:
        out["seriesType"] = obj.get("seriesType")
        out["seasons"] = [{"season": s2.get("seasonNumber"), "monitored": s2.get("monitored"),
                           "have": (s2.get("statistics") or {}).get("episodeFileCount", 0),
                           "total": (s2.get("statistics") or {}).get("totalEpisodeCount", 0)}
                          for s2 in obj.get("seasons", []) if s2.get("seasonNumber") is not None]
        out["sub_missing"] = sm.get(("tv", aid), 0) if sm.get("_tv_seen") else None
    return out

_EPSUBS = {}   # seriesId -> (ts, {episodeId: has_subs}); Bazarr's per-episode subtitle status, cached 5 min
def _episode_subs(sid):
    """{sonarrEpisodeId: True (nothing missing) | False (a wanted language is missing)} from Bazarr's episode list; an
    episode Bazarr does not know (no file yet) is absent. {} when Bazarr does not answer."""
    ent = _EPSUBS.get(sid)
    if ent and time.time() - ent[0] < 300: return ent[1]
    out = {}
    data = _bazarr_get(f"/api/episodes?seriesid%5B%5D={int(sid)}")
    rows = data.get("data") if isinstance(data, dict) else data
    for r in rows or []:
        eid = r.get("sonarrEpisodeId")
        if eid is not None: out[eid] = not (r.get("missing_subtitles") or [])
    if data is not None: _EPSUBS[sid] = (time.time(), out)
    return out
def series_tree(sid):
    """Seasons + episodes of a show with the torrent (if any) behind each episode, the file's size and its subtitle
    status: the inline season list under a Library row, the drawer's Monitoring section and the episode dialog all
    read this one payload."""
    st, obj = arr("sonarr", f"/series/{sid}")
    if not isinstance(obj, dict): return None
    st, eps = arr("sonarr", f"/episode?seriesId={sid}"); eps = eps if isinstance(eps, list) else []
    st, files = arr("sonarr", f"/episodefile?seriesId={sid}")
    fsize = {f.get("id"): f.get("size") or 0 for f in (files if isinstance(files, list) else [])}
    subs = _episode_subs(sid)
    qi = queue_index(); live, _ = _qbit_live(); by_ep = {}
    for h, q in qi.items():
        if q["kind"] != "tv" or q.get("id") != sid: continue
        t = live.get(h) or {}
        for e in q["episodes"]:
            if e.get("id"): by_ep[e["id"]] = {"hash": h, "state": t.get("state") or "", "progress": round((t.get("progress") or 0) * 100) if t else None,
                                             "eta": _eta(t.get("eta")) if t else "", "why": _why(t) if t else "", "dlspeed": t.get("dlspeed", 0), "force_start": bool(t.get("force_start"))}
    out_eps = [{"id": e.get("id"), "season": e.get("seasonNumber"), "ep": e.get("episodeNumber"), "title": e.get("title"), "monitored": e.get("monitored"),
                "hasFile": e.get("hasFile"), "airDate": e.get("airDate"), "episodeFileId": e.get("episodeFileId"), "torrent": by_ep.get(e.get("id")),
                "size": (fsize.get(e.get("episodeFileId")) or 0) if e.get("hasFile") else 0,
                "sub": subs.get(e.get("id")) if e.get("hasFile") else None}   # None: no file yet, or Bazarr has not seen it
               for e in sorted(eps, key=lambda e: (e.get("seasonNumber") or 0, e.get("episodeNumber") or 0)) if e.get("seasonNumber") is not None]
    seasons = []
    for s2 in obj.get("seasons", []):
        sn = s2.get("seasonNumber")
        if sn is None: continue
        st2 = s2.get("statistics") or {}
        seasons.append({"season": sn, "monitored": s2.get("monitored"), "have": st2.get("episodeFileCount", 0), "total": st2.get("totalEpisodeCount", 0),
                        "size": st2.get("sizeOnDisk", 0) or sum(x["size"] for x in out_eps if x["season"] == sn),
                        "hashes": sorted({x["torrent"]["hash"] for x in out_eps if x["season"] == sn and x.get("torrent")})})
    return {"id": sid, "title": obj.get("title"), "runtime": obj.get("runtime") or 0, "seasons": seasons, "episodes": out_eps}

def _transfer():
    return _torrents_info()[1]
PANEL = panel_data.Panel(arr=arr, prowlarr=prowlarr, js=js, http=H, docker_raw=_docker_raw, torrents=qbit_torrents,
                         transfer=_transfer, status=status, bazarr_get=_bazarr_get, env=E, config_dir=CONFIG_DIR,
                         apikey=apikey, arr_base=_base, cache_dir=os.path.join(SB_DIR, "cache"),
                         jellyfin_url=_base("jellyfin"), qbit_host=QBIT_HOST, services=SERVICES(),
                         docker=bool(DOCKER_SOCK),   # no socket configured: the container table and health simply are not offered
                         flaresolverr_url=E.get("FLARESOLVERR_URL") or None)

# ---------------- actions ----------------
def _ok(st): return isinstance(st, int) and 200 <= st < 300
def _js_forget(tmdb, tvdb):
    """Drop a title from Jellyseerr entirely: its request(s) AND the media record (which otherwise keeps per-season
    'requested' marks). The media delete needs the Jellyseerr owner to be an admin."""
    if not (tmdb or tvdb): return
    try:
        st, reqs = js("/request?take=500"); mids = set()
        for r in (reqs or {}).get("results", []) if isinstance(reqs, dict) else []:
            m = r.get("media", {})
            if (tmdb and m.get("tmdbId") == tmdb) or (tvdb and m.get("tvdbId") == tvdb):
                js(f"/request/{r['id']}", "DELETE")
                if m.get("id"): mids.add(m["id"])
        for mid in mids: js(f"/media/{mid}", "DELETE")
    except Exception: pass
def _jf_auth():
    k = apikey("jellyfin")
    return {"Authorization": f'MediaBrowser Token="{k}", Client="Controllarr", Device="panel", DeviceId="controllarr", Version="1"'} if k else None
def _after_delete(kind):
    """Bazarr and Jellyfin only notice a deletion on their own schedule (Bazarr's Sonarr/Radarr sync, Jellyfin's library
    scan); ask both now so a purged title vanishes from the whole stack at once. Best effort, never fatal."""
    bazarr_api("/api/system/tasks", "POST", {"taskid": "update_movies" if kind == "movie" else "update_series"})
    hdr = _jf_auth()
    if hdr:
        try: H("POST", _base("jellyfin") + "/Library/Refresh", headers=hdr, data=b"", timeout=8, expect_json=False)
        except Exception: pass
def _purge_item(kind, aid, tmdb=None, tvdb=None):
    """The whole-title purge, everywhere at once: every torrent of the title (downloading or seeding) with its data out of
    qBittorrent, the title with its files out of Radarr/Sonarr, its request and media record out of Jellyseerr, then
    Bazarr and Jellyfin are told to rescan so nothing keeps showing it."""
    app = "radarr" if kind == "movie" else "sonarr"; ep = f"/movie/{aid}" if kind == "movie" else f"/series/{aid}"
    if not (tmdb or tvdb):
        st, obj = arr(app, ep)
        if isinstance(obj, dict): tmdb, tvdb = obj.get("tmdbId"), obj.get("tvdbId")
    hs = _title_hashes(app, aid)
    _qbit_cmd("delete", hs)
    st, r = arr(app, ep + "?deleteFiles=true&addImportExclusion=false", "DELETE")
    if not _ok(st): return False, f"{app} refused to delete ({st})"
    _js_forget(tmdb, tvdb); _after_delete(kind); _QIDX["ts"] = 0; _TINFO["ts"] = 0; _WAKE.set()
    return True, f"Purged — {_n(len(hs), 'torrent')} and the files deleted, dropped from {'Radarr' if kind == 'movie' else 'Sonarr'}, Jellyseerr, Bazarr and Jellyfin"
def _tv_scope(sid, episode_ids=None, season=None, hashes=None):
    """Resolve a below-the-series purge without touching anything: the episodes named (by id, by season, or by the
    torrents given — a pack is one file set, so every episode it carries comes along), the torrents behind them and
    the episode files on disk. Returns {"episodes": [...], "hashes": [...], "files": n, "label": 'S02E01–E03'}."""
    st, eps = arr("sonarr", f"/episode?seriesId={sid}"); eps = eps if isinstance(eps, list) else []
    by_id = {e.get("id"): e for e in eps}
    if season is not None: targets = {e["id"]: e for e in eps if e.get("seasonNumber") == season}
    else: targets = {i: by_id[i] for i in (episode_ids or []) if i in by_id}
    qi = queue_index(); hs = {h for h in (hashes or []) if h}
    for h, q in qi.items():
        if q["kind"] == "tv" and q.get("id") == sid and any(e.get("id") in targets for e in q["episodes"]): hs.add(h)
    for h in list(hs):
        for e in (qi.get(h) or {}).get("episodes", []):
            if e.get("id") in by_id: targets.setdefault(e["id"], by_id[e["id"]])
    rows = sorted(targets.values(), key=lambda e: (e.get("seasonNumber") or 0, e.get("episodeNumber") or 0))
    lab = _ep_label([{"season": e.get("seasonNumber"), "ep": e.get("episodeNumber"), "title": ""} for e in rows]).split(" · ")
    # the last of the show? once these episodes are gone and untracked, nothing on disk and nothing tracked remains
    rest = [e for e in eps if e.get("id") not in targets]
    last = bool(rows) and not any(e.get("hasFile") or e.get("monitored") for e in rest)
    return {"episodes": rows, "hashes": sorted(hs), "files": sum(1 for e in rows if e.get("episodeFileId")),
            "label": (lab[0] + (" " + lab[1].split(" (")[0] if len(lab) > 1 else "")) if rows else "", "queue": qi, "last": last}
def _purge_tv_scope(scope, sid, season=None):
    """Act on a resolved _tv_scope: torrents + data out of qBittorrent, queue records dropped, episode files deleted,
    the episodes (and the season, for a season purge) unmonitored so Sonarr will not fetch them again. When that was
    the last of the show — no file left, nothing tracked — the show itself is purged from the whole stack too, so a
    purge never leaves an empty title behind that reads as "Searching". Returns (torrents, files, episodes, gone)."""
    hs, rows, qi = scope["hashes"], scope["episodes"], scope["queue"]
    if hs: _qbit_cmd("delete", hs)
    for h in hs:
        for qid in (qi.get(h) or {}).get("qids", []):
            arr("sonarr", f"/queue/{qid}?removeFromClient=false&blocklist=false", "DELETE")
    for e in rows:
        if e.get("episodeFileId"): arr("sonarr", f"/episodefile/{int(e['episodeFileId'])}", "DELETE")
    ids = [e["id"] for e in rows if e.get("id") is not None]
    if ids: arr("sonarr", "/episode/monitor", "PUT", {"episodeIds": ids, "monitored": False})
    if season is not None:
        st, obj = arr("sonarr", f"/series/{sid}")
        if isinstance(obj, dict):
            for s2 in obj.get("seasons", []):
                if s2.get("seasonNumber") == season: s2["monitored"] = False
            arr("sonarr", f"/series/{sid}", "PUT", obj)
    st, eps = arr("sonarr", f"/episode?seriesId={sid}")
    gone = False
    if isinstance(eps, list) and eps and not any(e.get("hasFile") or e.get("monitored") for e in eps):
        gone, _ = _purge_item("tv", sid)
    else:
        _after_delete("tv")
    _QIDX["ts"] = 0; _TINFO["ts"] = 0; _WAKE.set()
    return len(hs), scope["files"], len(ids), gone
def _n(n, word): return f"{n} {word}{'s' if n != 1 else ''}"
def _ids(a, plural, single):
    """The integer ids an action names: a list, a comma-separated string (query strings), or the single-id key."""
    raw = a.get(plural)
    if raw is None and a.get(single) is not None: raw = [a.get(single)]
    if isinstance(raw, str): raw = raw.split(",")
    return [int(x) for x in (raw or []) if str(x).strip().lstrip("-").isdigit() and int(x) > 0]
def _pin(hashes, on):
    """Tag torrents 'pinned' so queue-optimizer leaves a manual Top alone (Bottom un-pins)."""
    if hashes: _qbit_raw("torrents/" + ("addTags" if on else "removeTags"), {"hashes": "|".join(hashes), "tags": "pinned"})
def do_action(a, sess=None):
    """Every write goes through here. One log line per call (user, action, target, result, ms) to stdout —
    `docker logs controllarr` has it; no new files."""
    t0 = time.time(); action = a.get("action")
    try:
        ok, msg = _do_action(a, sess)
    except Exception as e:
        ok, msg = False, f"{action} failed: {redact(e)[:120]}"
    who = (sess or {}).get("user", "-"); role = (sess or {}).get("role", "-")
    tgt = a.get("hash") or (f"{a.get('kind')}:{a.get('id')}" if a.get("id") is not None else a.get("reqId") or "-")
    print(f"action user={who} role={role} action={action} target={tgt} result={'ok' if ok else 'fail'} ms={round((time.time() - t0) * 1000)} msg={json.dumps(redact(msg)[:100])}", flush=True)
    return ok, msg
def _do_action(a, sess=None):
    action = a.get("action"); kind = a.get("kind"); aid = a.get("id")
    # --- capability gating (admins bypass; None sess = internal/open) ---
    if sess is not None:
        need = _CAP_FOR.get(action)
        if action == "t_delete" and a.get("deleteFiles"): need = "can_delete_files"
        if need and not _can(sess, need):
            return False, "Not permitted (ask an admin)"
    # --- indexers / rss (no item) ---
    if action == "indexers_test_all":
        idx = prowlarr("/indexer")[1] or []; bad = []
        for i in idx:
            if not i.get("enable"): continue
            st = prowlarr("/indexer/test", "POST", i)[0]
            if st not in (200, 201, 202, 204): bad.append(i.get("name") or str(i.get("id")))
        return (True, f"Tested {len([i for i in idx if i.get('enable')])} indexers — all passed") if not bad else (False, f"Failed: {', '.join(bad)}")
    if action == "rss_sync":
        for app in ("radarr", "sonarr"): arr(app, "/command", "POST", {"name": "RssSync"})
        _WAKE.set(); return True, "RSS sync started on Radarr and Sonarr"
    if action == "jf_scan":
        hdr = _jf_auth()
        if not hdr: return False, "No Jellyfin API key configured"
        st, _ = H("POST", _base("jellyfin") + "/Library/Refresh", headers=hdr, data=b"", timeout=8, expect_json=False)
        return (True, "Jellyfin library scan started") if _ok(st) else (False, f"Jellyfin refused ({st})")
    # --- global qBittorrent controls (no item) ---
    if action == "qall_pause": _qbit_cmd("stop", ["all"]); _WAKE.set(); return True, "All paused"
    if action == "qall_resume": _qbit_cmd("start", ["all"]); _WAKE.set(); return True, "All resumed"
    if action in ("alt_toggle", "alt_set"):
        op, url = qbit()
        if op:
            try:
                if action == "alt_set":   # explicit on/off: read the mode, toggle only when it differs (works on every qBittorrent)
                    cur = op.open(url + "/api/v2/transfer/speedLimitsMode", timeout=10).read().strip() == b"1"
                    if cur == _bool(a.get("value")): return True, "Alt-speed " + ("on" if cur else "off")
                op.open(urllib.request.Request(url + "/api/v2/transfer/toggleSpeedLimitsMode", data=b"", headers={"Referer": url}))
            except Exception: pass
        return True, ("Alt-speed " + ("on" if _bool(a.get("value")) else "off")) if action == "alt_set" else "Alt-speed toggled"
    # --- direct qBittorrent torrent controls (by hash; 'a|b|c' addresses several at once) ---
    if action and action.startswith("t_"):
        h = _hash_arg(a)
        if not h: return False, "no hash"
        if action == "t_purge":
            qi = queue_index(); n_t = n_e = 0; gone = []
            for hh in h.split("|"):
                q = qi.get(hh)
                if q and q["kind"] == "movie" and q.get("id"):
                    ok, msg = _purge_item("movie", q["id"])
                    if not ok: return False, msg
                    n_t += 1
                elif q and q["kind"] == "tv" and q.get("id"):
                    if q["id"] in gone: continue   # its show already went with an earlier hash of this call
                    t, f, e, g = _purge_tv_scope(_tv_scope(q["id"], hashes=[hh]), q["id"]); n_t += t; n_e += e
                    if g: gone.append(q["id"])
                else:
                    _qbit_cmd("delete", [hh]); n_t += 1   # unknown to the arrs: the torrent and its files
            _WAKE.set(); return True, f"Purged {_n(n_t, 'torrent')}" + (f", {_n(n_e, 'episode')} unmonitored" if n_e else "") + (" — the show was the last of it and is gone from the stack" if gone else "")
        if action == "t_delete":
            op, url = qbit()
            if op:
                try: op.open(urllib.request.Request(url + "/api/v2/torrents/delete",
                        data=urllib.parse.urlencode({"hashes": h, "deleteFiles": "true" if a.get("deleteFiles") else "false"}).encode(),
                        headers={"Referer": url}))
                except Exception: pass
            _WAKE.set(); return True, "Torrent removed"
        if action in ("t_dllimit", "t_uplimit"):
            try: lim = int(float(a.get("limit") or 0) * 1_000_000)   # MB/s → bytes/s (0 = unlimited)
            except Exception: lim = 0
            _qbit_raw("torrents/" + ("setDownloadLimit" if action == "t_dllimit" else "setUploadLimit"),
                      {"hashes": h, "limit": lim}); _WAKE.set(); return True, "Speed limit set"
        if action == "t_forcestart":
            _qbit_raw("torrents/setForceStart", {"hashes": h, "value": "true" if a.get("value") else "false"})
            _WAKE.set(); return True, "Force-start toggled"
        cmd = {"t_pause": "stop", "t_resume": "start", "t_recheck": "recheck", "t_reannounce": "reannounce",
               "t_top": "topPrio", "t_bottom": "bottomPrio"}.get(action)
        if cmd:
            _qbit_cmd(cmd, [h])
            if action in ("t_top", "t_bottom"): _pin([h], action == "t_top")
            _WAKE.set(); return True, {"t_pause": "Paused", "t_resume": "Resumed", "t_recheck": "Rechecking", "t_reannounce": "Reannounced to trackers",
                                       "t_top": "Moved to top", "t_bottom": "Moved to bottom"}[action]
        return False, "unknown torrent action"
    app = "radarr" if kind == "movie" else "sonarr"
    ep = f"/movie/{aid}" if kind == "movie" else f"/series/{aid}"
    # --- item ---
    if action == "retry":
        st, _ = arr(app, "/command", "POST",
            {"name": "MoviesSearch", "movieIds": [aid]} if kind == "movie" else {"name": "SeriesSearch", "seriesId": aid})
        _WAKE.set(); return (True, "Search triggered") if _ok(st) else (False, f"{app} refused the search ({st})")
    if action == "refresh":
        st, _ = arr(app, "/command", "POST", {"name": "RefreshMovie", "movieIds": [aid]} if kind == "movie" else {"name": "RefreshSeries", "seriesId": aid})
        _WAKE.set(); return (True, "Refresh triggered") if _ok(st) else (False, f"{app} refused the refresh ({st})")
    if action == "grab":
        st, r = arr(app, "/release", "POST", {"guid": a["guid"], "indexerId": a["indexerId"]})
        _WAKE.set(); return (True, "Grabbing release") if _ok(st) else (False, f"Grab failed ({st}): {str(r)[:120]}")
    if action in ("monitor", "monitor_set"):   # monitor = toggle (drawer); monitor_set = explicit (bulk bar)
        st, obj = arr(app, ep)
        if isinstance(obj, dict):
            obj["monitored"] = _bool(a.get("monitored")) if action == "monitor_set" else not obj.get("monitored")
            st, r = arr(app, ep, "PUT", obj)
            if not _ok(st): return False, f"{app} rejected the change ({st})"
            _WAKE.set(); return True, ("Monitored" if obj["monitored"] else "Unmonitored")
        return False, "not found"
    if action == "monitor_all" and kind == "tv":
        st, obj = arr("sonarr", ep)
        if isinstance(obj, dict): obj["monitored"] = True; arr("sonarr", ep, "PUT", obj)
        st, eps = arr("sonarr", f"/episode?seriesId={aid}")
        ids = [e["id"] for e in (eps or [])]
        if ids: arr("sonarr", "/episode/monitor", "PUT", {"episodeIds": ids, "monitored": True})
        arr("sonarr", "/command", "POST", {"name": "MissingEpisodeSearch", "seriesId": aid})
        _WAKE.set(); return True, "Monitoring all + searching gaps"
    if action == "set_quality":
        st, obj = arr(app, ep)
        if isinstance(obj, dict):
            obj["qualityProfileId"] = int(a["profileId"]); st, r = arr(app, ep, "PUT", obj)
            if not _ok(st): return False, f"{app} rejected the profile ({st})"
            _WAKE.set(); return True, "Quality profile changed"
        return False, "not found"
    if action == "set_min_availability" and kind == "movie":
        st, obj = arr("radarr", ep)
        if isinstance(obj, dict):
            obj["minimumAvailability"] = a.get("value", "released"); st, r = arr("radarr", ep, "PUT", obj)
            if not _ok(st): return False, f"Radarr rejected the change ({st})"
            _WAKE.set(); return True, "Minimum availability set"
        return False, "not found"
    if action == "set_series_type" and kind == "tv":
        st, obj = arr("sonarr", ep)
        if isinstance(obj, dict):
            obj["seriesType"] = a.get("value", "standard"); st, r = arr("sonarr", ep, "PUT", obj)
            if not _ok(st): return False, f"Sonarr rejected the change ({st})"
            _WAKE.set(); return True, "Series type set"
        return False, "not found"
    if action == "set_root_folder":
        st, obj = arr(app, ep)
        if isinstance(obj, dict) and a.get("path"):
            obj["rootFolderPath"] = a["path"]; st, r = arr(app, ep + "?moveFiles=false", "PUT", obj)
            if not _ok(st): return False, f"{app} rejected the root folder ({st})"
            _WAKE.set(); return True, "Root folder set (existing files not moved)"
        return False, "not found"
    if action == "set_season_monitor" and kind == "tv":
        st, obj = arr("sonarr", ep)
        if isinstance(obj, dict):
            sn = int(a.get("season")); mon = _bool(a.get("monitored"))
            for s2 in obj.get("seasons", []):
                if s2.get("seasonNumber") == sn: s2["monitored"] = mon
            st, r = arr("sonarr", ep, "PUT", obj)
            if not _ok(st): return False, f"Sonarr rejected the change ({st})"
            if mon: arr("sonarr", "/command", "POST", {"name": "SeasonSearch", "seriesId": aid, "seasonNumber": sn})
            _WAKE.set(); return True, ("Season monitored + searching" if mon else "Season unmonitored")
        return False, "not found"
    # --- per-episode (TV) ---
    if action == "episode_monitor" and kind == "tv":
        ids = _ids(a, "episodeIds", "episodeId"); mon = _bool(a.get("monitored"))
        if not ids: return False, "no episodes"
        arr("sonarr", "/episode/monitor", "PUT", {"episodeIds": ids, "monitored": mon})
        _WAKE.set(); return True, f"{_n(len(ids), 'episode')} {'tracked' if mon else 'untracked'}"
    if action == "episode_search" and kind == "tv":
        ids = _ids(a, "episodeIds", "episodeId")
        if not ids: return False, "no episodes"
        arr("sonarr", "/command", "POST", {"name": "EpisodeSearch", "episodeIds": ids})
        _WAKE.set(); return True, f"Searching {_n(len(ids), 'episode')}"
    if action in ("episode_delete_file", "episode_delete_files") and kind == "tv":
        fids = _ids(a, "episodeFileIds", "episodeFileId")
        if not fids: return False, "no file"
        for fid in fids: arr("sonarr", f"/episodefile/{fid}", "DELETE")
        _WAKE.set(); return True, f"{_n(len(fids), 'episode file')} deleted"
    # --- subtitles (Bazarr) ---
    if action == "fetch_subs":
        task = "wanted_search_missing_subtitles_movies" if kind == "movie" else "wanted_search_missing_subtitles_series"
        ok, _ = bazarr_api("/api/system/tasks", "POST", {"taskid": task})
        return (True, "Bazarr searching missing subtitles…") if ok else (False, "Bazarr unreachable")
    if action == "download_sub":
        # override with a specific subtitle picked from the manual-search list
        if kind == "movie":
            ok, _ = bazarr_api("/api/providers/movies", "POST", {"radarrid": aid, "hi": a.get("hi", "False"),
                "forced": a.get("forced", "False"), "original_format": a.get("original_format", "False"),
                "language": a.get("language"), "subtitle": a.get("subtitle"), "provider": a.get("provider")})
        else:
            ok, _ = bazarr_api("/api/providers/episodes", "POST", {"seriesid": aid, "episodeid": a.get("episodeId"),
                "hi": a.get("hi", "False"), "forced": a.get("forced", "False"), "original_format": a.get("original_format", "False"),
                "language": a.get("language"), "subtitle": a.get("subtitle"), "provider": a.get("provider")})
        return (True, "Subtitle downloaded") if ok else (False, "Download failed")
    if action == "blocklist_retry":
        for r in _queue_records(app, aid):
            arr(app, f"/queue/{r['id']}?removeFromClient=true&blocklist=true", "DELETE")
        arr(app, "/command", "POST", {"name": "MoviesSearch", "movieIds": [aid]} if kind == "movie" else {"name": "SeriesSearch", "seriesId": aid})
        _WAKE.set(); return True, "Blocklisted + re-searching"
    if action == "purge": return _purge_item(kind, aid, a.get("tmdbId"), a.get("tvdbId"))
    if action == "season_purge" and kind == "tv":
        sn = int(a.get("season")); t, f, e, g = _purge_tv_scope(_tv_scope(aid, season=sn), aid, season=sn)
        return True, f"Season {sn} purged — {_n(t, 'torrent')}, {_n(f, 'file')}, {_n(e, 'episode')} unmonitored" + ("; it was the last of the show, which is gone from the stack" if g else "")
    if action == "episode_purge" and kind == "tv":
        raw = a.get("episodeIds") or []; raw = raw.split(",") if isinstance(raw, str) else raw   # a list, or "1201,1202" from a query string
        ids = [int(x) for x in raw if str(x).strip().isdigit()]
        if not ids: return False, "no episodes"
        t, f, e, g = _purge_tv_scope(_tv_scope(aid, episode_ids=ids), aid)
        return True, f"Purged {_n(e, 'episode')} — {_n(t, 'torrent')}, {_n(f, 'file')} deleted, unmonitored" + ("; that was the last of the show, which is gone from the stack" if g else "")
    if action == "season_search" and kind == "tv":
        st, _ = arr("sonarr", "/command", "POST", {"name": "SeasonSearch", "seriesId": aid, "seasonNumber": int(a.get("season"))})
        _WAKE.set(); return (True, f"Searching season {a.get('season')}") if _ok(st) else (False, f"Sonarr refused the search ({st})")
    # --- queue ---
    if action in ("q_pause", "q_resume", "q_recheck", "q_top", "q_bottom"):
        cmd = {"q_pause": "stop", "q_resume": "start", "q_recheck": "recheck",
               "q_top": "topPrio", "q_bottom": "bottomPrio"}[action]
        hs = _hashes(app, aid); _qbit_cmd(cmd, hs)
        if action in ("q_top", "q_bottom"): _pin(hs, action == "q_top")
        _WAKE.set()
        return True, {"q_pause": "Paused", "q_resume": "Resumed", "q_recheck": "Rechecking",
                      "q_top": "Moved to top", "q_bottom": "Moved to bottom"}[action]
    if action == "q_remove":
        for r in _queue_records(app, aid):
            arr(app, f"/queue/{r['id']}?removeFromClient=true&blocklist={'true' if a.get('blocklist') else 'false'}", "DELETE")
        _WAKE.set(); return True, "Removed from queue"
    # --- jellyseerr requests ---
    if action == "req_approve": js(f"/request/{a['reqId']}/approve", "POST", b""); _WAKE.set(); return True, "Approved"
    if action == "req_decline": js(f"/request/{a['reqId']}/decline", "POST", b""); _WAKE.set(); return True, "Declined"
    if action == "req_delete":  js(f"/request/{a['reqId']}", "DELETE"); _WAKE.set(); return True, "Request deleted"
    # --- add ---
    if action == "add":
        root = (arr(app, "/rootfolder")[1] or [{}])[0].get("path")
        qp = (arr(app, "/qualityprofile")[1] or [{}])[0].get("id")
        if kind == "movie":
            arr("radarr", "/movie", "POST", {"title": a["title"], "tmdbId": a["tmdbId"], "year": a.get("year"),
                "qualityProfileId": qp, "rootFolderPath": root, "monitored": True, "minimumAvailability": "released",
                "addOptions": {"searchForMovie": True}})
        else:
            arr("sonarr", "/series", "POST", {"title": a["title"], "tvdbId": a["tvdbId"], "qualityProfileId": qp,
                "rootFolderPath": root, "monitored": True, "seasonFolder": True,
                "addOptions": {"searchForMissingEpisodes": True, "monitor": "all"}})
        _WAKE.set(); return True, f"Added {a.get('title')}"
    if action == "import_library":
        res = library_import.import_existing(arr); _WAKE.set()
        return True, f"Imported movies {res.get('movies')}, series {res.get('series')}"
    return False, "unknown action"

# ---------------- settings ----------------
def _bool(v):
    """Truthy for True/"true"/"True"/"on"/"1"/"yes" — settings.local round-trips str(True) as "True"."""
    return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "on", "1", "yes")
# Guardrail: a single-SSD box saturates on iowait beyond a couple of concurrent downloads.
# Configurable via MAX_ACTIVE_DL_CAP in .env / app.env (default 2); every write path clamps to it.
MAX_ACTIVE_DL_CAP = max(1, int(E.get("MAX_ACTIVE_DL_CAP", "2") or 2))
def put_qbit(f):
    q = {k: f.get(k) for k in ("dl_limit", "up_limit", "alt_dl_limit", "alt_up_limit", "sched_from", "sched_to",
                               "max_active_downloads", "max_active_uploads", "max_ratio")}
    raw = q.get("max_active_downloads")
    try: v = int(float(raw)) if raw not in (None, "") else MAX_ACTIVE_DL_CAP
    except Exception: v = MAX_ACTIVE_DL_CAP
    if v <= 0: v = MAX_ACTIVE_DL_CAP           # 0 / negative would mean "unlimited" in qBittorrent → snap to the cap
    q["max_active_downloads"] = min(v, MAX_ACTIVE_DL_CAP)
    q["scheduler_enabled"] = _bool(f.get("scheduler_enabled")); q["seed_after_complete"] = _bool(f.get("seed_after_complete"))
    q["remove_completed"] = _bool(f.get("remove_completed"))
    settings_ops.apply_qbit(q, arr, qbit, log=lambda m: print("qbit:", m, flush=True)); _WAKE.set()

# ---- per-app settings tabs (load + save) ----
def read_jellyseerr():
    out = {}
    for kind, app in (("movie", "radarr"), ("series", "sonarr")):
        servers = js(f"/settings/{app}")[1] or []
        srv = next((s for s in servers if s.get("isDefault")), servers[0] if servers else None)
        profs = [{"id": p["id"], "name": p["name"]} for p in (arr(app, "/qualityprofile")[1] or [])]
        roots = [r.get("path") for r in (arr(app, "/rootfolder")[1] or [])]
        out[kind] = {"serverId": (srv or {}).get("id"), "profile": (srv or {}).get("activeProfileId"),
                     "root": (srv or {}).get("activeDirectory"), "profiles": profs, "roots": roots}
    return out
def apply_jellyseerr(f):
    for kind, app in (("movie", "radarr"), ("series", "sonarr")):
        d = f.get(kind) or {}
        if d.get("serverId") is None: continue
        servers = js(f"/settings/{app}")[1] or []
        srv = next((s for s in servers if s.get("id") == d.get("serverId")), None)
        if not srv: continue
        sid = srv["id"]; body = {k: v for k, v in srv.items() if k != "id"}   # Jellyseerr's OpenAPI marks id read-only: a body carrying it is a 400 and nothing is saved
        if d.get("profile") is not None:
            body["activeProfileId"] = int(d["profile"])
            name = next((p["name"] for p in (arr(app, "/qualityprofile")[1] or []) if p.get("id") == int(d["profile"])), None)
            if name: body["activeProfileName"] = name
        if d.get("root"): body["activeDirectory"] = d["root"]
        st, r = js(f"/settings/{app}/{sid}", "PUT", body)
        if not _ok(st): return False, f"Jellyseerr refused the {kind} defaults ({st}): {str((r or {}).get('message', r))[:120]}"
    _WAKE.set(); return True, "Jellyseerr defaults saved"
def read_tab(tab):
    if tab in ("radarr", "sonarr"): return settings_ops.read_content(arr, tab)
    if tab == "qbit":
        d = settings_ops.read_qbit(qbit)
        try:
            dcs = arr("radarr", "/downloadclient")[1] or []
            d["remove_completed"] = bool(dcs[0].get("removeCompletedDownloads")) if dcs else False
        except Exception: d["remove_completed"] = False
        d["cap"] = MAX_ACTIVE_DL_CAP; return d
    if tab == "bazarr":
        if _bazarr_get("/api/system/settings") is None: raise RuntimeError("Bazarr unreachable")   # never show defaults as if they were live values
        return settings_ops.read_bazarr(_bazarr_get)
    if tab == "prowlarr":
        idx = prowlarr("/indexer")[1] or []
        fs, fm = PANEL.flaresolverr()
        return {"indexers": [{"id": i.get("id"), "name": i.get("name"), "enable": bool(i.get("enable"))} for i in idx], "flaresolverr": bool(fm and fm.get("ok"))}
    if tab == "jellyfin":
        v, _ = PANEL.versions()
        return {"key_present": bool(apikey("jellyfin")), "version": (v or {}).get("jellyfin")}
    if tab == "backup":
        hp = status().get("health", {}) or {}
        return {"last_backup_h": hp.get("last_backup_h"), "dir": BACKUP_DIR, "visible": bool(BACKUP_DIR and os.path.isdir(BACKUP_DIR))}
    if tab == "jellyseerr": return read_jellyseerr()
    if tab == "notify":
        loc = read_local()
        return {"quiet_start": loc.get("NOTIFY_QUIET_START", "0"), "quiet_end": loc.get("NOTIFY_QUIET_END", "9"),
                "topic_media": loc.get("NTFY_TOPIC_MEDIA", "media"), "topic_admin": loc.get("NTFY_TOPIC_ADMIN", "admin"),
                "ntfy_url": loc.get("NTFY_URL", E.get("NTFY_URL", ""))}
    return {}
def apply_tab(tab, f):
    global MIN_SEEDERS
    lg = lambda m: print(f"set/{tab}:", m, flush=True)
    if tab in ("radarr", "sonarr"):
        content = {"size_cap": f.get("size_cap"), "size_max": f.get("size_max"), "min_seeders": f.get("min_seeders"),
                   "audio_language": f.get("audio_language", "Any"), "allow_unknown": _bool(f.get("allow_unknown")),
                   "prefer_h264": _bool(f.get("prefer_h264")), "propers": _bool(f.get("propers")), "rename": _bool(f.get("rename")),
                   "copy_hardlinks": _bool(f.get("copy_hardlinks")), "recycle_bin": f.get("recycle_bin", ""),
                   "recycle_days": f.get("recycle_days") or 0, "min_free_mb": f.get("min_free_mb") or 100}
        errs = settings_ops.apply_content(content, arr, apps=(tab,), log=lg)
        # Prowlarr's application sync writes ITS app profile's minimumSeeders back into both arrs, undoing the value
        # above within minutes — the profile has to agree (on the box it had drifted from the installed 5 to 1)
        try:
            if f.get("min_seeders") not in (None, ""):
                n = int(float(f["min_seeders"]))
                for p in prowlarr("/appprofile")[1] or []:
                    if isinstance(p, dict) and p.get("id") is not None and p.get("minimumSeeders") != n:
                        p["minimumSeeders"] = n; prowlarr(f"/appprofile/{p['id']}", "PUT", p)
        except Exception as e: lg(f"prowlarr app profile not updated: {e}")
        # keep the library classifier — and anything else on the box that reads settings.local — in step
        try:
            upd = {}
            if f.get("size_cap") not in (None, ""): upd["SIZE_CAP_MBPM"] = int(float(f.get("size_cap")))
            if f.get("min_seeders") not in (None, ""):
                MIN_SEEDERS = int(float(f.get("min_seeders"))); upd["MIN_SEEDERS"] = MIN_SEEDERS
            if upd: write_local(upd)
        except Exception: pass
        _WAKE.set()
        if errs: return False, f"{tab.title()}: {'; '.join(errs)[:200]}"
        return True, tab.title() + " settings saved"
    if tab == "qbit": put_qbit(f); return True, "qBittorrent settings saved"
    if tab == "bazarr":
        b = dict(f)
        for k in ("hearing_impaired", "forced", "upgrade_subs", "adaptive_searching", "use_embedded_subs",
                  "embedded_subs_show_desired", "ignore_pgs_subs", "ignore_vobsub_subs"):
            b[k] = _bool(f.get(k))
        errs = settings_ops.apply_bazarr(b, bazarr_post, log=lg)
        if errs: return False, "Bazarr refused the settings — " + "; ".join(errs)[:200] + " (its log has the traceback: docker logs bazarr)"
        write_local({"SUBTITLE_LANGS": f.get("subtitle_langs", "en"), "HEARING_IMPAIRED": b["hearing_impaired"]})
        return True, "Bazarr settings saved"
    if tab == "jellyseerr": return apply_jellyseerr(f)
    if tab == "notify":
        write_local({"NOTIFY_QUIET_START": f.get("quiet_start", "0"), "NOTIFY_QUIET_END": f.get("quiet_end", "9"),
                     "NTFY_TOPIC_MEDIA": f.get("topic_media", "media"), "NTFY_TOPIC_ADMIN": f.get("topic_admin", "admin"),
                     "NTFY_URL": f.get("ntfy_url", "")})
        return True, "Notification settings saved"
    return False, "unknown tab"
def prowlarr_action(b):
    act = b.get("act")
    if act == "sync":
        prowlarr("/command", "POST", {"name": "ApplicationIndexerSync"}); return True, "Syncing indexers to Radarr/Sonarr"
    idx = prowlarr(f"/indexer/{int(b['id'])}")[1]
    if not isinstance(idx, dict): return False, "indexer not found"
    if act == "toggle":
        idx["enable"] = not idx.get("enable"); prowlarr(f"/indexer/{idx['id']}", "PUT", idx)
        return True, ("Enabled " if idx["enable"] else "Disabled ") + (idx.get("name") or "")
    if act == "test":
        st = prowlarr("/indexer/test", "POST", idx)[0]
        return (True, "Test passed") if st in (200, 201, 202, 204) else (False, "Test failed")
    return False, "unknown action"
def ntfy_test():
    loc = read_local(); topic = loc.get("NTFY_TOPIC_ADMIN", "admin")
    base = (loc.get("NTFY_URL") or E.get("NTFY_URL") or "http://ntfy:80").rstrip("/")
    try:
        urllib.request.urlopen(urllib.request.Request(f"{base}/{topic}", data=b"Test from Controllarr",
                               headers={"Title": "Controllarr test", "Tags": "white_check_mark"}), timeout=8)
        return True, f"Test notification sent to {topic}"
    except Exception:
        return False, f"ntfy unreachable at {base}"

# ---- config snapshot / presets (per app, through the same read/apply path as the tabs) ----
_SNAP_TABS = ("radarr", "sonarr", "qbit", "bazarr", "notify")
_SNAP_SKIP = {"providers", "cap",   # derived / read-only keys, not settings
               "ntfy_url"}          # a credential (a token can be in the URL); a snapshot is made to be shared
_ARR_DEF = {"size_cap": 20, "size_max": 50, "min_seeders": 5, "audio_language": "Original", "allow_unknown": False,
            "prefer_h264": False, "propers": True, "rename": True, "copy_hardlinks": True, "recycle_days": 7, "min_free_mb": 1000}
DEFAULTS = {   # sensible baseline = the installer's defaults (the download guardrail is respected)
    "radarr": dict(_ARR_DEF), "sonarr": dict(_ARR_DEF),
    "qbit":   {"dl_limit": 0, "up_limit": 0, "alt_dl_limit": 0, "alt_up_limit": 0, "sched_from": 8, "sched_to": 23,
               "max_active_downloads": MAX_ACTIVE_DL_CAP, "max_active_uploads": 3, "max_ratio": 0, "scheduler_enabled": False,
               "seed_after_complete": True, "remove_completed": False},
    "bazarr": {"subtitle_langs": "en", "hearing_impaired": False, "forced": False, "upgrade_subs": True, "days_to_upgrade_subs": 7,
               "minimum_score": 90, "minimum_score_movie": 70, "adaptive_searching": False, "use_embedded_subs": True,
               "embedded_subs_show_desired": True, "ignore_pgs_subs": False, "ignore_vobsub_subs": False},
    "notify": {"quiet_start": 0, "quiet_end": 9, "topic_media": "media", "topic_admin": "admin"},
}
def _both(d): return {"radarr": dict(d), "sonarr": dict(d)}
_FULL = {"dl_limit": 0, "up_limit": 0, "alt_dl_limit": 0, "alt_up_limit": 0, "scheduler_enabled": False, "max_active_downloads": MAX_ACTIVE_DL_CAP,
         "seed_after_complete": True}
# One-click tuning. Each preset is a partial overlay per app (merged onto the CURRENT values, then applied through the
# same path as the Settings groups) plus optional actions run afterwards. `group` orders the Settings page and the
# dashboard's Tune menu: throughput presets change what the box does right now, quality presets what it looks for.
PRESETS = {
    "Everything paused": {"group": "throughput", "desc": "Stops every download and every seed until you resume. The box goes quiet.",
                          "actions": ["qall_pause"]},
    "Upload off":        {"group": "throughput", "desc": "No seeding: finished torrents stop at once and uploads are capped to a trickle. Downloads carry on (peers still need a little upload).",
                          "qbit": {"seed_after_complete": False, "max_active_uploads": 0, "up_limit": 0.05, "max_ratio": 0}},
    "Balanced":          {"group": "throughput", "desc": "Unlimited downloads, upload capped at 1 MB/s, seed to ratio 2, alt-speed and the schedule off, everything resumed. The everyday setting.",
                          "qbit": dict(_FULL, up_limit=1, max_active_uploads=3, max_ratio=2), "actions": ["alt_off", "qall_resume"]},
    "Overclock":         {"group": "throughput", "desc": f"No speed limits at all, {max(8, MAX_ACTIVE_DL_CAP * 3)} seeding slots, seed forever, alt-speed and the schedule off, everything resumed. The download cap ({MAX_ACTIVE_DL_CAP}) still applies — a single-disk box saturates beyond it.",
                          "qbit": dict(_FULL, max_active_uploads=max(8, MAX_ACTIVE_DL_CAP * 3), max_ratio=0), "actions": ["alt_off", "qall_resume"]},
    "Off-peak only":     {"group": "throughput", "desc": "Use the alternative limits (Downloads ▸ Alternative speed) from 01:00 to 08:00 — full speed only at night.",
                          "qbit": {"scheduler_enabled": True, "sched_from": 1, "sched_to": 8}},
    "4K quality":        {"group": "quality", "desc": "Prefer 40 MB/min, allow up to 120 MB/min, x265 welcome, 3 seeders is enough. Big files, best picture.",
                          **_both({"size_cap": 40, "size_max": 120, "prefer_h264": False, "allow_unknown": False, "min_seeders": 3})},
    "1080p balanced":    {"group": "quality", "desc": "Prefer 20 MB/min, allow up to 50, at least 5 seeders. The installer's default.",
                          **_both({"size_cap": 20, "size_max": 50,  "prefer_h264": False, "allow_unknown": False, "min_seeders": 5})},
    "Data-saver":        {"group": "quality", "desc": "Prefer 8 MB/min, allow up to 20, x264 first (cheap to decode), unknown-quality releases allowed. Smallest files that still play.",
                          **_both({"size_cap": 8,  "size_max": 20,  "prefer_h264": True,  "allow_unknown": True,  "min_seeders": 5})},
}
def preset_list(): return [{"name": n, "desc": p.get("desc", ""), "group": p.get("group", "quality")} for n, p in PRESETS.items()]
def export_config():
    out = {}
    for t in _SNAP_TABS:
        try: out[t] = {k: v for k, v in read_tab(t).items() if k not in _SNAP_SKIP}
        except Exception as e: out[t] = {"_error": str(e)}
    return out
_PRESET_ACTIONS = {"qall_pause": {"action": "qall_pause"}, "qall_resume": {"action": "qall_resume"}, "alt_off": {"action": "alt_set", "value": False}}
def apply_config(cfg):
    """Apply a per-app snapshot/preset: each app's current values overlaid with the given keys, then the preset's
    actions (pause/resume all, alt-speed off) in order."""
    msgs = []; ok_all = True
    for t in _SNAP_TABS:
        part = (cfg or {}).get(t)
        if not isinstance(part, dict): continue
        cur = {k: v for k, v in read_tab(t).items() if k not in _SNAP_SKIP}
        cur.update({k: v for k, v in part.items() if not str(k).startswith("_")})
        ok, msg = apply_tab(t, cur); ok_all = ok_all and ok; msgs.append(f"{t}: {msg}")
    for name in (cfg or {}).get("actions") or []:
        body = _PRESET_ACTIONS.get(name)
        if not body: continue
        ok, msg = do_action(dict(body), None); ok_all = ok_all and ok; msgs.append(str(msg))
    return ok_all, ("; ".join(msgs) if msgs else "nothing to apply")
def consequence_local(a):
    """Confirmation text for the actions the app itself resolves (the arr-aware purges and presets); None hands the
    rest to panel_data.Panel.consequence."""
    act = a.get("action")
    if act == "config_preset":
        p = PRESETS.get(a.get("name") or "")
        if not p: return None
        return (f"Apply preset {a.get('name')}", (p.get("desc") or "") + " Applied to the apps now; the values show up under Settings.")
    if act == "t_purge":
        qi = queue_index(); parts = []; n_files = 0
        for hh in (_hash_arg(a) or "").split("|"):
            q = qi.get(hh)
            if q and q["kind"] == "movie": parts.append("the whole movie — its files, the torrent, the Radarr entry and the Jellyseerr request")
            elif q and q["kind"] == "tv" and q.get("id"):
                sc = _tv_scope(q["id"], hashes=[hh]); n_files += sc["files"]
                parts.append(f"{sc['label'] or 'its episodes'} ({_n(len(sc['episodes']), 'episode')}) — torrent and data deleted, episodes unmonitored so Sonarr won't fetch them again"
                             + ("; that is the last of the show, so the show itself goes from Sonarr, Jellyseerr, Bazarr and Jellyfin too" if sc["last"] else ""))
            elif hh: parts.append("the torrent and its downloaded files (not tracked by Radarr/Sonarr)")
        txt = "Deletes " + ("; ".join(parts) if parts else "the torrent and its files") + (f". {_n(n_files, 'episode file')} on disk go too" if n_files else "") + ". Can't be undone."
        return ("Purge " + (a.get("name") or "torrent")[:60], txt)
    if act in ("season_purge", "episode_purge"):
        try: aid = int(a.get("id"))
        except Exception: return None
        if act == "season_purge":
            try: sn = int(a.get("season"))
            except Exception: return None
            sc = _tv_scope(aid, season=sn); what = f"Purge season {sn} of {a.get('title') or 'this show'}"
            tail = " The season is unmonitored so Sonarr won't fetch it again."
        else:
            raw = a.get("episodeIds") or []; raw = raw.split(",") if isinstance(raw, str) else raw
            ids = [int(x) for x in raw if str(x).strip().isdigit()]
            sc = _tv_scope(aid, episode_ids=ids); what = f"Purge {sc['label'] or _n(len(ids), 'episode')} of {a.get('title') or 'this show'}"
            tail = " The episodes are unmonitored so Sonarr won't fetch them again."
        if sc["last"]: tail += " That is the last of the show: the show itself is removed from Sonarr, Jellyseerr, Bazarr and Jellyfin as well."
        return (what, f"Deletes {_n(sc['files'], 'episode file')} on disk and removes {_n(len(sc['hashes']), 'torrent')} with its data from qBittorrent; {_n(len(sc['episodes']), 'episode')} affected." + tail + " Can't be undone.")
    return None

# ---------------- rendering ----------------
STAGES = board_gen.STAGES   # single source of truth (board_gen) for sections / filter / badges / tallies
def board_json():
    """The library as data: every board item with its live torrent overlay and subtitle status."""
    global _LIVE
    _LIVE, (gdl, gup) = _qbit_live()
    d = status(); sm = sub_map(); out = []
    for i in d.get("items", []):
        it = {k: i.get(k) for k in ("kind", "id", "title", "year", "stage", "reason", "detail", "who", "size", "runtime", "have", "total", "tmdbId", "tvdbId")}
        it["poster"] = f"/img/poster/{i.get('kind')}/{i.get('id')}" if i.get("poster") and isinstance(i.get("id"), int) else None
        ts = [_LIVE[h] for h in (i.get("hashes") or []) if h in _LIVE]
        if ts:
            dlspeed = sum(t.get("dlspeed", 0) for t in ts); tot = sum(t.get("size", 0) for t in ts) or 1
            etas = [t["eta"] for t in ts if t.get("dlspeed", 0) > 0 and 0 < (t.get("eta") or 0) < 8640000]
            prios = [t.get("priority") for t in ts if t.get("priority")]
            it["live"] = {"pct": round(100 * sum((t.get("size", 0)) * (t.get("progress", 0) or 0) for t in ts) / tot),
                          "dlspeed": dlspeed, "seeds": max((t.get("num_seeds", 0) for t in ts), default=0),
                          "eta": _eta(min(etas)) if etas else None, "ratio": max((t.get("ratio", 0) for t in ts), default=0),
                          "prio": min(prios) if prios else None,
                          "why": next((w for w in (_why(t) for t in ts) if w), "") if dlspeed == 0 else "", "n": len(ts)}
        kind, iid = i.get("kind"), i.get("id")
        if kind == "movie" and i.get("stage") == "Available" and ("movie", iid) in sm: it["sub"] = "ok" if sm[("movie", iid)] else "missing"
        elif kind == "tv" and sm.get("_tv_seen") and i.get("stage") in ("Available", "Partial"):
            miss = sm.get(("tv", iid), 0); it["sub"] = "ok" if not miss else {"missing": miss}
        out.append(it)
    return {"generated": d.get("generated", 0), "summary": d.get("summary", {}), "items": out,
            "speed": {"dl": gdl, "up": gup}, "searches": d.get("searches", 0), "stages": STAGES}


def _links():
    """The apps' own addresses on the LAN, for the shortcuts under Needs attention and in Settings."""
    host = E.get("SERVER_HOST", "localhost")
    return {"jellyfin": f"http://{host}:{E.get('JELLYFIN_PORT', '8096')}", "jellyseerr": f"http://{host}:{E.get('JELLYSEERR_PORT', '5055')}",
            "prowlarr": f"http://{host}:{E.get('PROWLARR_PORT', '9696')}"}
def main_page(sess):
    """The redesigned single page: everything the client needs to boot is in window.MC."""
    role = sess.get("role", "user"); host = E.get("SERVER_HOST", "localhost")
    cfg = dict(refresh=REFRESH, role=role, user=sess.get("user"), caps=role_caps(role), auth=bool(PASSWORD), stages=STAGES,
               vpn=(QBIT_HOST == "gluetun"), host=host, hostname=PANEL.hostname(), maxActive=MAX_ACTIVE_DL_CAP,
               links=_links(), services=SERVICES(), ver=asset_ver())
    return page("index", **cfg).replace("__DOCS__", DOCS_INNER)



# ---------------- HTTP ----------------
def _session(handler):
    """Return the session record {user, role} for this request, or None."""
    if not PASSWORD: return {"user": "guest", "role": "admin"}   # auth disabled → open (full access)
    c = handler.headers.get("Cookie", "")
    for part in c.split(";"):
        if part.strip().startswith("sb="):
            s = SESSIONS.get(part.strip()[3:])
            return s if s and s.get("exp", 0) > time.time() else None
    return None
def _authed(handler): return _session(handler) is not None
def _is_admin(handler):
    s = _session(handler); return bool(s and s.get("role") == "admin")

class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1: keep-alive (one connection for the page's ~25 requests) and — the reason it matters — browsers only
    # revalidate with If-None-Match against HTTP/1.1 responses, so ETag/304 is dead on HTTP/1.0. Every response
    # therefore carries a Content-Length (bodiless ones send 0); idle connections are dropped after `timeout` s.
    protocol_version = "HTTP/1.1"
    timeout = 30
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="text/html; charset=utf-8", cookie=None):
        # the one place every text response passes: no secret leaves here, whatever composed it
        b = redact(body).encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        if cookie: self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(b))); self.end_headers()
        try: self.wfile.write(b)
        except Exception: pass
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health": return self._send(200, "ok", "text/plain")
        # /health is the whole unauthenticated surface
        if path.startswith("/static/"):
            res = static_file(path[len("/static/"):])
            if not res: return self._send(404, "not found", "text/plain")
            data, ctype = res
            if ctype.startswith("text/javascript"): data = _version_imports(data)
            cc = "public, max-age=31536000, immutable" if ("v=" in self.path or self.path.endswith(".woff2")) else "no-cache"   # fonts are content-named, so cache them like versioned assets
            etag = '"' + hashlib.sha1(data).hexdigest()[:16] + '"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304); self.send_header("ETag", etag); self.send_header("Cache-Control", cc); self.send_header("Content-Length", "0"); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("ETag", etag); self.send_header("Cache-Control", cc)
            self.send_header("Content-Length", str(len(data))); self.end_headers()
            try: self.wfile.write(data)
            except Exception: pass
            return
        if path == "/login":
            nxt = _safe_next(_q(self.path).get("next", ""))
            return self._send(200, page("login", auth=bool(PASSWORD)).replace("__ERR__", "").replace("__NEXT__", html.escape(nxt if nxt != "/" else "")))
        if path == "/logout":
            c = self.headers.get("Cookie", "")
            for part in c.split(";"):
                if part.strip().startswith("sb="): SESSIONS.pop(part.strip()[3:], None)
            _save_sessions()
            self.send_response(302); self.send_header("Location", "/login"); self.send_header("Set-Cookie", "sb=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"); self.send_header("Content-Length", "0"); self.end_headers(); return
        if not _authed(self):
            nxt = _safe_next(self.path)
            self.send_response(302); self.send_header("Location", "/login" + (("?next=" + urllib.parse.quote(nxt, safe="")) if nxt != "/" else "")); self.send_header("Content-Length", "0"); self.end_headers(); return
        if path == "/dashboard":   # the old overview page; its content is the top of / now
            self.send_response(302); self.send_header("Location", "/#live"); self.send_header("Content-Length", "0"); self.end_headers(); return
        if path == "/docs":        # the glossary is the collapsed section at the end of /
            self.send_response(302); self.send_header("Location", "/#reference"); self.send_header("Content-Length", "0"); self.end_headers(); return
        if path == "/settings":
            if not _is_admin(self):
                self.send_response(302); self.send_header("Location", "/"); self.send_header("Content-Length", "0"); self.end_headers(); return
            sess = _session(self) or {}
            return self._send(200, page("settings", role="admin", user=sess.get("user"), auth=bool(PASSWORD), maxActive=MAX_ACTIVE_DL_CAP,
                                        links=_links(), services=SERVICES()))
        # ---- redesign data layer: one JSON payload per section, each with per-source ok/age/err ----
        if path == "/api/board":
            obj = board_json(); core = {k: v for k, v in obj.items() if k != "generated"}
            etag = '"' + hashlib.sha1(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()[:16] + '"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304); self.send_header("ETag", etag); self.send_header("Cache-Control", "private, no-cache"); self.send_header("Content-Length", "0"); self.end_headers(); return
            b = redact(json.dumps(obj)).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("ETag", etag); self.send_header("Cache-Control", "private, no-cache")
            self.send_header("Content-Length", str(len(b))); self.end_headers()
            try: self.wfile.write(b)
            except Exception: pass
            return
        if path == "/api/attention": return self._send(200, json.dumps(PANEL.attention()), "application/json")
        if path == "/api/live": return self._send(200, json.dumps(PANEL.live()), "application/json")
        if path == "/api/reference": return self._send(200, json.dumps(PANEL.reference()), "application/json")
        if path == "/api/system": return self._send(200, json.dumps(PANEL.system()), "application/json")
        if path == "/api/series-tree":
            try: d = series_tree(int(_q(self.path).get("seriesId")))
            except Exception: d = None
            return self._send(200, json.dumps(d or {}), "application/json")
        if path == "/api/consequence":
            q = _q(self.path)
            try: q["id"] = int(q["id"]) if q.get("id") else None
            except Exception: q["id"] = None
            t, txt = consequence_local(q) or PANEL.consequence(q, item_detail)
            return self._send(200, json.dumps({"title": t, "text": txt}), "application/json")
        if path.startswith("/img/poster/"):   # /img/poster/<movie|tv>/<id>[?size=250|500] — allowlisted proxy, key stays server-side
            parts = path.split("/")
            try: kind, iid = parts[3], int(parts[4])
            except Exception: return self._send(404, "not found", "text/plain")
            res = PANEL.poster(kind, iid, _q(self.path).get("size", "250"))
            if not res: return self._send(404, "", "image/jpeg")
            data, ctype = res
            self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "private, max-age=86400")
            self.send_header("Content-Length", str(len(data))); self.end_headers()
            try: self.wfile.write(data)
            except Exception: pass
            return
        if path == "/api/item":
            q = _q(self.path)
            try: d = item_detail(q.get("kind"), int(q.get("id")))
            except Exception: d = None
            return self._send(200, json.dumps(d or {}), "application/json")
        if path == "/api/rootfolders":
            kind = _q(self.path).get("kind", "movie"); app = "radarr" if kind == "movie" else "sonarr"
            rs = [{"path": r.get("path"), "freeGB": round((r.get("freeSpace") or 0) / 1e9)} for r in (arr(app, "/rootfolder")[1] or [])]
            return self._send(200, json.dumps(rs), "application/json")
        if path == "/api/episodes":
            try: sid = int(_q(self.path).get("seriesId"))
            except Exception: return self._send(200, json.dumps([]), "application/json")
            eps = arr("sonarr", f"/episode?seriesId={sid}")[1] or []
            out = [{"id": e.get("id"), "season": e.get("seasonNumber"), "ep": e.get("episodeNumber"),
                    "title": e.get("title"), "monitored": e.get("monitored"), "hasFile": e.get("hasFile"),
                    "airDate": e.get("airDate"), "episodeFileId": e.get("episodeFileId")}
                   for e in eps if e.get("seasonNumber") is not None]   # keep season 0 (Specials)
            out.sort(key=lambda e: (e["season"] or 0, e["ep"] or 0))
            return self._send(200, json.dumps(out), "application/json")
        if path == "/api/sub-search":
            q = _q(self.path); kind = q.get("kind")
            if kind == "movie":
                data = _bazarr_get(f"/api/providers/movies?radarrid={q.get('id')}")
            else:
                data = _bazarr_get(f"/api/providers/episodes?episodeid={q.get('episodeId')}")
            rows = data.get("data") if isinstance(data, dict) else data
            out = [{"language": r.get("language"), "provider": r.get("provider"), "score": r.get("score"),
                    "release": r.get("release_info") or r.get("release"), "subtitle": r.get("subtitle"),
                    "hi": r.get("hearing_impaired") or r.get("hi"), "forced": r.get("forced"),
                    "original_format": r.get("original_format")} for r in (rows or [])][:40]
            return self._send(200, json.dumps({"available": data is not None, "results": out}), "application/json")
        if path == "/api/me":
            s = _session(self) or {"user": "?", "role": "user"}
            return self._send(200, json.dumps({"user": s.get("user"), "role": s.get("role"),
                "caps": role_caps(s.get("role")), "auth": bool(PASSWORD)}), "application/json")
        if path == "/api/users":
            if not _is_admin(self): return self._send(403, json.dumps([]), "application/json")
            return self._send(200, json.dumps(list_users()), "application/json")
        if path == "/api/roles":
            if not _is_admin(self): return self._send(403, json.dumps({}), "application/json")
            return self._send(200, json.dumps(load_users().get("roles", {})), "application/json")
        if path.startswith("/api/set/"):
            if not _is_admin(self): return self._send(403, json.dumps({}), "application/json")
            try: d = read_tab(path.rsplit("/", 1)[-1])
            except Exception as e: d = {"error": str(e)}
            return self._send(200, json.dumps(d), "application/json")
        if path == "/api/config/export":
            if not _is_admin(self): return self._send(403, json.dumps({}), "application/json")
            return self._send(200, json.dumps(export_config(), indent=2), "application/json")
        if path == "/api/config/presets":
            if not _is_admin(self): return self._send(403, json.dumps([]), "application/json")
            return self._send(200, json.dumps(preset_list()), "application/json")
        if path == "/api/qualityprofiles":
            kind = _q(self.path).get("kind", "movie"); app = "radarr" if kind == "movie" else "sonarr"
            return self._send(200, json.dumps([{"id": p["id"], "name": p["name"]} for p in (arr(app, "/qualityprofile")[1] or [])]), "application/json")
        if path == "/api/releases":
            q = _q(self.path); kind = q.get("kind"); aid = int(q.get("id"))
            app = "radarr" if kind == "movie" else "sonarr"
            if kind == "movie": pathq = f"/release?movieId={aid}"
            else:   # the requested season, else the first monitored season that is still missing episodes
                try: season = int(q.get("season") or 0)
                except Exception: season = 0
                if season <= 0:
                    st, ser = arr("sonarr", f"/series/{aid}")
                    season = board_gen.first_missing_season(ser if isinstance(ser, dict) else {})
                pathq = f"/release?seriesId={aid}&seasonNumber={season}"
            rel = arr(app, pathq)[1] or []
            out = [{"title": r.get("title"), "size": r.get("size", 0), "seeders": r.get("seeders", 0),
                    "quality": (r.get("quality", {}).get("quality", {}) or {}).get("name", "?"),
                    "rejected": r.get("rejected"), "rejections": r.get("rejections", []),
                    "guid": r.get("guid"), "indexerId": r.get("indexerId")} for r in rel][:60]
            return self._send(200, json.dumps(out), "application/json")
        if path == "/api/lookup":
            q = _q(self.path); kind = q.get("kind"); term = q.get("term", "")
            app = "radarr" if kind == "movie" else "sonarr"
            res = arr(app, f"/{'movie' if kind=='movie' else 'series'}/lookup?term=" + urllib.parse.quote(term))[1] or []
            key = "tmdbId" if kind == "movie" else "tvdbId"
            return self._send(200, json.dumps([{"title": r.get("title"), "year": r.get("year"), key: r.get(key)} for r in res if r.get(key)]), "application/json")
        if path.startswith("/api/"):   # an unknown API path is a 404, not the page shell
            return self._send(404, json.dumps({"ok": False, "message": "not found"}), "application/json")
        return self._send(200, main_page(_session(self) or {}))
    def do_POST(self):
        path = self.path.split("?")[0]
        # Read the body up front, before any early return: on a keep-alive connection an unread body would be
        # parsed as the next request line (and answered 501).
        try: n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError: n = 0
        raw = self.rfile.read(n) if n > 0 else b""
        if path == "/login":
            form = urllib.parse.parse_qs(raw.decode(errors="replace"))
            user = form.get("username", [""])[0]; pw = form.get("password", [""])[0]
            nxt = _safe_next(form.get("next", [""])[0])
            role = authenticate(user, pw)
            if role:
                tok = _secrets.token_hex(16); SESSIONS[tok] = {"user": (user.strip() or "admin"), "role": role, "exp": time.time() + SESSION_TTL}
                _save_sessions()
                self.send_response(302); self.send_header("Location", nxt)
                self.send_header("Set-Cookie", f"sb={tok}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Lax"); self.send_header("Content-Length", "0"); self.end_headers(); return
            return self._send(200, page("login", auth=bool(PASSWORD)).replace("__ERR__", "Wrong username or password").replace("__NEXT__", html.escape(nxt if nxt != "/" else "")))
        sess = _session(self)
        if not sess: return self._send(401, json.dumps({"ok": False, "message": "auth"}), "application/json")
        admin = sess.get("role") == "admin"
        if path == "/api/refresh": _regen(); return self._send(200, json.dumps({"ok": True}), "application/json")
        # CSRF defence for a cookie-authenticated LAN app: JSON endpoints only accept a JSON content type
        # (a cross-site form post can't set it), on top of the SameSite=Lax cookie.
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            return self._send(415, json.dumps({"ok": False, "message": "JSON body required"}), "application/json")
        try:
            body = json.loads(raw or b"{}")
            # ---- admin-only: global settings + config + user/role management ----
            _ADMIN_POST = ("/api/config/import", "/api/config/defaults", "/api/config/preset",
                           "/api/users", "/api/users/delete", "/api/roles", "/api/prowlarr", "/api/ntfy-test")
            if (path in _ADMIN_POST or path.startswith("/api/set/")) and not admin:
                return self._send(403, json.dumps({"ok": False, "message": "Admin only"}), "application/json")
            if path.startswith("/api/set/"):
                ok, msg = apply_tab(path.rsplit("/", 1)[-1], body); return self._send(200 if ok else 400, json.dumps({"ok": ok, "message": msg}), "application/json")
            if path == "/api/prowlarr":
                ok, msg = prowlarr_action(body); return self._send(200 if ok else 400, json.dumps({"ok": ok, "message": msg}), "application/json")
            if path == "/api/ntfy-test":
                ok, msg = ntfy_test(); return self._send(200 if ok else 400, json.dumps({"ok": ok, "message": msg}), "application/json")
            if path == "/api/config/import":
                ok, msg = apply_config(body); return self._send(200 if ok else 400, json.dumps({"ok": ok, "message": "Config loaded — " + msg}), "application/json")
            if path == "/api/config/defaults":
                ok, msg = apply_config(DEFAULTS); return self._send(200 if ok else 400, json.dumps({"ok": ok, "message": "Defaults restored — " + msg}), "application/json")
            if path == "/api/config/preset":
                p = PRESETS.get(body.get("name"))
                if not p: return self._send(400, json.dumps({"ok": False, "message": "unknown preset"}), "application/json")
                ok, msg = apply_config(p); return self._send(200 if ok else 400, json.dumps({"ok": ok, "message": f"{body.get('name')} applied — {msg}"}), "application/json")
            if path in ("/api/users", "/api/users/delete", "/api/roles"):   # account and permission changes are logged like every other write
                ok, msg = save_user(body) if path == "/api/users" else delete_user(body.get("username")) if path == "/api/users/delete" else save_role(body)
                print(f"action user={sess.get('user', '-')} role=admin action={path.rsplit('/', 1)[-1] if path != '/api/users' else 'user_save'} target={body.get('username') or body.get('role') or '-'} result={'ok' if ok else 'fail'} msg={json.dumps(str(msg)[:100])}", flush=True)
                return self._send(200 if ok else 400, json.dumps({"ok": ok, "message": msg}), "application/json")
            if path == "/api/action":
                ok, msg = do_action(body, sess); return self._send(200 if ok else (403 if "permitted" in str(msg) else 400), json.dumps({"ok": ok, "message": msg}), "application/json")
        except Exception as e:
            print(f"error path={path} {type(e).__name__}: {redact(e)[:200]}", flush=True)
            return self._send(500, json.dumps({"ok": False, "message": "The panel hit an error handling that — see the controllarr logs"}), "application/json")
        self._send(404, "not found", "text/plain")

def _q(path): return {k: v[0] for k, v in urllib.parse.parse_qs(path.split("?", 1)[1] if "?" in path else "").items()}
DOCS_INNER = """<div class=doc>
<h2>How this stack works</h2>
<p>You request a title in <code>Jellyseerr</code>; <code>Radarr</code> / <code>Sonarr</code> pick a release from your indexers, the download client fetches it, the arr imports it into the library, <code>Bazarr</code> adds subtitles and <code>Jellyfin</code> plays it. Controllarr watches all of that and shows what still needs you — whichever of those apps you actually run.</p>

<h2>Pipeline stages</h2>
<table><tr><th>Stage</th><th>Meaning</th></tr>
<tr><td><b>Available</b></td><td>On disk and playable.</td></tr>
<tr><td><b>Downloading</b></td><td>In qBittorrent, actively fetching.</td></tr>
<tr><td><b>Importing</b></td><td>Download done; the arr is moving/hardlinking it into the library.</td></tr>
<tr><td><b>Partial</b></td><td>(TV) some episodes on disk, still getting the rest.</td></tr>
<tr><td><b>Searching</b></td><td>A usable release exists and is being grabbed.</td></tr>
<tr><td><b>Waiting</b></td><td>Not released yet (future dated).</td></tr>
<tr><td><b>Unavailable</b></td><td>No usable release right now: <span class=k>Nothing found</span>, <span class=k>Only low-seed</span>, <span class=k>too big for size cap</span>, <span class=k>quality not allowed</span>, <span class=k>can't match releases</span>. The verdict is re-checked every few hours.</td></tr></table>
<p>TV shows are classified against the <b>first tracked season that still has missing episodes</b>; the drawer lists <b>tracked (monitored) seasons</b> by default — <i>Show all seasons</i> shows the rest.</p>

<h2>qBittorrent states</h2>
<table><tr><th>State</th><th>What it means</th></tr>
<tr><td><code>downloading</code></td><td>Actively downloading from peers.</td></tr>
<tr><td><code>stalledDL</code></td><td>Wants to download but no peer is giving data. The amber note next to the torrent says why: <i>dead swarm</i> (peers connected, but every one is another leecher at 0 % — "peers" are not "seeds"), <i>seeds reported but none reachable</i>, or <i>only N % of the file exists</i> among connected peers. Stalled for 12 h with no progress → the queue-cleaner removes and blocklists it so a better release can be grabbed.</td></tr>
<tr><td><code>metaDL</code></td><td>Fetching torrent metadata (magnet) — no peers for the metadata yet. Also cleaned after 12 h if stuck.</td></tr>
<tr><td><code>forcedDL</code></td><td>Downloading, ignoring queue limits (Force-start).</td></tr>
<tr><td><code>queuedDL</code></td><td>Waiting its turn — the active-download cap is full.</td></tr>
<tr><td><code>uploading</code> / <code>stalledUP</code></td><td>Complete, now <b>seeding</b>. <code>stalledUP</code> only means nobody is leeching from you right now — normal, not an error.</td></tr>
<tr><td><code>forcedUP</code></td><td>Seeding, ignoring queue/limits.</td></tr>
<tr><td><code>stoppedDL</code> / <code>stoppedUP</code></td><td>Stopped by you (incomplete / complete). Older qBittorrent versions say <code>paused</code>.</td></tr>
<tr><td><code>queuedUP</code></td><td>Complete and waiting for a seeding slot.</td></tr>
<tr><td><code>checkingDL</code> / <code>checkingUP</code></td><td>Re-checking files on disk (after a recheck or restart).</td></tr>
<tr><td><code>error</code> / <code>missingFiles</code></td><td>Disk error, or the files were moved or deleted underneath it.</td></tr></table>

<h2>Tips</h2>
<ul>
<li><b>Unavailable with "Only low-seed"?</b> Not enough seeders for the minimum. Open the title → <b>Search…</b> and grab a better-seeded release, or lower Min seeders in Settings ▸ Quality &amp; size.</li>
<li><b>Download won't start?</b> <code>queuedDL</code> means it is behind the active-download cap; <code>stalledDL</code> means a dead torrent — Blocklist &amp; retry.</li>
<li><b>Ratio 0 / everything crawls?</b> You are not connectable: with the VPN on, the Dash should end in a <code>:port</code>. Providers without port forwarding cannot fix this — see docs/VPN.md.</li>
<li><b>Wrong audio language (a dub)?</b> Settings ▸ Quality &amp; size ▸ Audio language = <b>Original</b> penalises dubs; use the drawer's Search… to grab an original-audio release.</li>
<li><b>No subtitles?</b> Open the title → Subtitles → <b>Fetch subs</b>, or <b>Manual search…</b> to pick one.</li>
</ul>
<p>Full documentation — install, configuration, services, automation, VPN, backups, troubleshooting — is the <code>docs/</code> directory of the repository.</p>
</div>"""

if __name__ == "__main__":
    for _p, _m in config_problems():
        print(f"controllarr: refusing to start — {_p} is mode {_m:04o} and holds your API keys, so anyone with "
              f"an account on this box can read them.\n"
              f"             fix it with:  chmod 600 {_p}\n"
              f"             (or re-run ./install.sh, which writes it that way)", flush=True)
        raise SystemExit(1)
    threading.Thread(target=_loop, daemon=True).start()
    print(f"control panel on :{PORT} (refresh {REFRESH}s, auth={'on' if PASSWORD else 'off'})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
