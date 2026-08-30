#!/usr/bin/env python3
"""Boot Controllarr against tests/fake_stack.py in a throwaway directory.

    python3 -I tests/harness.py serve [--port N] [--state FILE]   # foreground; Ctrl-C stops it
    from harness import Harness; with Harness() as h: h.get("/health")

Everything the panel reads or writes lives under one temp dir: a CONFIG_DIR with the per-app
config files the panel reads API keys from, Controllarr's own CONTROLLARR_DIR, the media directory,
the fake Docker socket and the config file (CONTROLLARR_ENV). The panel is started as a subprocess of the given
interpreter from a neutral cwd (never the repo root: a scratch module must not shadow the stdlib), by absolute script
path, so nothing here can touch a real stack. `--state` writes {panel, control, password, ...} as JSON
for the Playwright specs; default tests/.harness.json (gitignored).
"""
import http.client, http.server, json, os, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP = os.path.join(REPO, "app")
PASSWORD = "harness-admin-pw"

sys.path.insert(0, HERE)
import fake_stack  # noqa: E402


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def write_config_dir(root, ports, sock_path, extra_env=None, backups_enabled=False):
    """The files the panel reads keys and state from, plus the config file. Returns its path."""
    cfg = os.path.join(root, "config"); sb = os.path.join(cfg, "controllarr"); data = os.path.join(root, "data"); bk = os.path.join(root, "backups")
    for d in (cfg, sb, data, bk, os.path.join(cfg, "bazarr", "config"), os.path.join(cfg, "jellyseerr")): os.makedirs(d, exist_ok=True)
    for app in ("radarr", "sonarr", "prowlarr"):
        os.makedirs(os.path.join(cfg, app), exist_ok=True)
        with open(os.path.join(cfg, app, "config.xml"), "w") as f: f.write(f"<Config><ApiKey>{fake_stack.KEYS[app]}</ApiKey></Config>")
    with open(os.path.join(cfg, "bazarr", "config", "config.yaml"), "w") as f: f.write(f"auth:\n  apikey: '{fake_stack.KEYS['bazarr']}'\n")
    with open(os.path.join(cfg, "jellyseerr", "settings.json"), "w") as f: json.dump({"main": {"apiKey": fake_stack.KEYS["jellyseerr"]}}, f)
    env = {"SERVER_HOST": "127.0.0.1", "CONFIG_DIR": cfg, "DATA_DIR": data, "BACKUP_DIR": bk, "ENABLE_BACKUPS": "true" if backups_enabled else "false",
           "CONTROLLARR_PASSWORD": PASSWORD, "JELLYFIN_APIKEY": fake_stack.KEYS["jellyfin"], "QBIT_USER": fake_stack.QBIT_USER, "QBIT_PASS": fake_stack.QBIT_PASS,
           "SERVICES": ",".join(fake_stack.SERVICES), "EXPECTED_CONTAINERS": ",".join(fake_stack.EXPECTED), "MAX_ACTIVE_DL_CAP": "2", "MIN_SEEDERS": "5", "DOCKER_SOCK": sock_path,
           "NTFY_URL": f"http://127.0.0.1:{ports['ntfy']}", "FLARESOLVERR_URL": f"http://127.0.0.1:{ports['flaresolverr']}/",
           "RECONCILE_GRACE_DAYS": "14",
           "RADARR_PORT": "7878", "SONARR_PORT": "8989", "PROWLARR_PORT": "9696", "QBIT_PORT": "8080", "BAZARR_PORT": "6767", "JELLYSEERR_PORT": "5055", "JELLYFIN_PORT": "8096", "NTFY_PORT": "8090"}
    for app, port in ports.items():
        if app in ("ntfy", "flaresolverr"): continue
        k = "QBIT" if app == "qbittorrent" else app.upper()
        env[f"{k}_HOST"] = "127.0.0.1"; env[f"{k}_PORT_INTERNAL"] = str(port)
    env.update(extra_env or {})
    app_env = os.path.join(root, "app.env")
    with open(app_env, "w") as f:
        for k, v in env.items(): f.write(f"{k}={v}\n")
    os.chmod(app_env, 0o600)   # as install.sh writes it; the panel refuses to start on anything world-readable
    return app_env


class Harness:
    def __init__(self, port=None, app_dir=None, python=None, refresh=2, backups_enabled=False, extra_env=None, keep=False):
        self.port = port or free_port(); self.app_dir = app_dir or APP; self.python = python or sys.executable
        self.refresh = refresh; self.backups_enabled = backups_enabled; self.extra_env = extra_env; self.keep = keep
        self.root = None; self.fake = None; self.proc = None; self.log = None

    # -- lifecycle
    def start(self, wait_board=True):
        self.root = tempfile.mkdtemp(prefix="mc-harness-")
        self.fake = fake_stack.FakeStack(os.path.join(self.root, "docker.sock")).start()
        self.app_env = write_config_dir(self.root, self.fake.ports, self.fake.sock_path, self.extra_env, self.backups_enabled)
        self.sb_dir = os.path.join(self.root, "config", "controllarr")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CONTROLLARR_ENV": self.app_env, "CONTROLLARR_DIR": self.sb_dir,
               "CONTROLLARR_PORT": str(self.port), "CONTROLLARR_REFRESH": str(self.refresh), "CONTROLLARR_PASSWORD": PASSWORD,
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "TZ": "UTC"}
        self.log_path = os.path.join(self.root, "panel.log"); self.log = open(self.log_path, "w"); self.env = env
        self._spawn(); self._wait_health()
        if wait_board: self.wait_board()
        return self
    def restart(self):
        """Replace the panel process (same port, same files): a fresh in-memory cache for a new scenario. Sessions survive (on disk)."""
        self._stop_panel(); self.log = open(self.log_path, "a"); self._spawn(); self._wait_health(); self.wait_board()
    def _stop_panel(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try: self.proc.wait(5)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait()
        if self.log: self.log.close(); self.log = None
    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try: self.proc.wait(5)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait()
        if self.log: self.log.close()
        if self.fake: self.fake.stop()
        if self.root and not self.keep: shutil.rmtree(self.root, ignore_errors=True)
    def _spawn(self):
        # no -I: like production (`python3 /app/controllarr.py`) the script's own directory must be on sys.path
        # for its sibling modules; the neutral cwd + the bare env keep everything else out.
        self.proc = subprocess.Popen([self.python, os.path.join(self.app_dir, "controllarr.py")], cwd=self.root, env=self.env,
                                     stdout=self.log, stderr=subprocess.STDOUT)
    def __enter__(self): return self.start()
    def __exit__(self, *a): self.stop()
    def panel_log(self):
        try:
            with open(self.log_path) as f: return f.read()
        except Exception: return ""

    # -- HTTP (stdlib; one fresh connection per call unless conn is given)
    @property
    def url(self): return f"http://127.0.0.1:{self.port}"
    def request(self, method, path, body=None, headers=None, cookie=None, conn=None):
        c = conn or http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = dict(headers or {})
        if cookie: h["Cookie"] = cookie
        data = None
        if body is not None:
            if isinstance(body, (dict, list)): data = json.dumps(body).encode(); h.setdefault("Content-Type", "application/json")
            elif isinstance(body, str): data = body.encode()
            else: data = body
        c.request(method, path, body=data, headers=h)
        r = c.getresponse(); raw = r.read()
        if conn is None: c.close()
        return r.status, dict(r.getheaders()), raw
    def get(self, path, cookie=None, **kw): return self.request("GET", path, cookie=cookie, **kw)
    def json(self, path, cookie=None, **kw):
        st, hd, raw = self.request("GET", path, cookie=cookie, **kw)
        return st, (json.loads(raw) if raw else None)
    def post(self, path, body, cookie=None, **kw):
        st, hd, raw = self.request("POST", path, body=body, cookie=cookie, **kw)
        try: return st, json.loads(raw)
        except Exception: return st, raw.decode(errors="replace")
    def login(self, user="admin", password=PASSWORD, nxt=""):
        """Returns the `sb=...` cookie value on success, else None."""
        form = urllib.parse.urlencode({"username": user, "password": password, "next": nxt})
        st, hd, raw = self.request("POST", "/login", body=form, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if st != 302: return None
        return hd.get("Set-Cookie", "").split(";")[0]
    def admin_cookie(self):
        c = self.login()
        assert c, "admin login failed: " + self.panel_log()[-500:]
        return c
    def add_user(self, name, password, role="user"):
        st, j = self.post("/api/users", {"username": name, "password": password, "role": role}, cookie=self.admin_cookie())
        assert st == 200 and j.get("ok"), j
    # -- fake control
    def control(self, **cmd):
        u = urllib.parse.urlparse(self.fake.control_url); c = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
        if cmd: c.request("POST", "/_control", body=json.dumps(cmd).encode(), headers={"Content-Type": "application/json"})
        else: c.request("GET", "/_control")
        r = c.getresponse(); out = json.loads(r.read()); c.close(); return out
    def calls(self, svc=None, method=None, contains=None):
        out = []
        for s, m, p, b in self.control()["calls"]:
            if svc and s != svc: continue
            if method and m != method: continue
            if contains and contains not in p and contains not in json.dumps(b): continue
            out.append((s, m, p, b))
        return out
    def refresh_board(self, cookie=None):
        """Wake the regenerator and wait for a newer board."""
        cookie = cookie or self.admin_cookie()
        st, before = self.json("/api/board", cookie); g0 = (before or {}).get("generated", 0)
        self.post("/api/refresh", {}, cookie)
        for _ in range(100):
            st, b = self.json("/api/board", cookie)
            if b and b.get("generated", 0) > g0: return b
            time.sleep(0.05)
        raise AssertionError("board did not regenerate: " + self.panel_log()[-800:])

    # -- readiness
    def _wait_health(self, timeout=20):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None: raise RuntimeError("panel exited: " + self.panel_log()[-2000:])
            try:
                st, hd, raw = self.get("/health")
                if st == 200 and raw == b"ok": return
            except Exception: pass
            time.sleep(0.1)
        raise RuntimeError("panel never answered /health: " + self.panel_log()[-2000:])
    def wait_board(self, timeout=20):
        cookie = self.admin_cookie(); t0 = time.time()
        while time.time() - t0 < timeout:
            st, b = self.json("/api/board", cookie)
            if st == 200 and b and b.get("generated"): return b
            time.sleep(0.1)
        raise RuntimeError("first library scan never finished: " + self.panel_log()[-2000:])
    def state(self):
        du = shutil.disk_usage(self.root)
        return {"panel": self.url, "control": self.fake.control_url, "harness": getattr(self, "harness_url", None), "password": PASSWORD,
                "user": "admin", "root": self.root, "disk_pct": round(100 * du.used / du.total), "ports": self.fake.ports}


class _HarnessControl(http.server.BaseHTTPRequestHandler):
    """`serve` mode only: POST /_harness {"restart": true} restarts the panel process (used by the browser specs
    to start a scenario with a cold cache); GET /_harness returns the state."""
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _reply(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): self._reply(200, self.server.harness.state())
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        if body.get("restart"):
            try: self.server.harness.restart()
            except Exception as e: return self._reply(500, {"ok": False, "error": str(e)[:300]})
        self._reply(200, {"ok": True})


def load_panel_module(tmp_root, name="sbapp"):
    """Import app/controllarr.py as a module for unit tests, with its environment pointed at
    tmp_root (no fake needed: importing the panel makes no network call). Returns the module."""
    import importlib.util
    sb = os.path.join(tmp_root, "controllarr"); os.makedirs(sb, exist_ok=True)
    app_env = os.path.join(tmp_root, "app.env")
    with open(app_env, "w") as f: f.write(f"CONFIG_DIR={tmp_root}\nCONTROLLARR_PASSWORD={PASSWORD}\nMAX_ACTIVE_DL_CAP=2\n")
    os.environ["CONTROLLARR_ENV"] = app_env; os.environ["CONTROLLARR_DIR"] = sb; os.environ["CONTROLLARR_PASSWORD"] = PASSWORD
    if APP not in sys.path: sys.path.insert(0, APP)
    for m in ("services", "panel_data", "board_gen", "settings_ops", "library_import"): sys.modules.pop(m, None)
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP, "controllarr.py"))
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    import argparse, signal
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["serve"]); ap.add_argument("--port", type=int); ap.add_argument("--state")
    ap.add_argument("--app", help="boot the panel from this app/ directory instead of the checkout")
    ap.add_argument("--python", help="interpreter for the panel process")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    h = Harness(port=a.port, app_dir=a.app, python=a.python).start()
    ctl = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HarnessControl); ctl.harness = h; ctl.daemon_threads = True
    threading.Thread(target=ctl.serve_forever, daemon=True).start(); h.harness_url = f"http://127.0.0.1:{ctl.server_address[1]}/_harness"
    st = h.state(); state_file = a.state or os.path.join(HERE, ".harness.json")
    with open(state_file, "w") as f: json.dump(st, f)
    if not a.quiet:
        print(f"panel   {st['panel']}   (admin / {PASSWORD})\ncontrol {st['control']}\nharness {st['harness']}\nstate   {state_file}\nroot    {st['root']}", flush=True)
    stop = lambda *a: (h.stop(), os.path.exists(state_file) and os.unlink(state_file), sys.exit(0))
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    while True:
        if h.proc.poll() is not None: print("panel exited:\n" + h.panel_log()[-2000:], file=sys.stderr); stop()
        time.sleep(0.5)
