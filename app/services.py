"""Configuration and service access for Controllarr.

Everything the panel needs to reach an *arr stack it did not install: the config file (CONTROLLARR_ENV,
`KEY=value`, values may be POSIX-quoted), the live overrides the Settings page writes next to it, API keys
(read from a service's own config directory when Controllarr can see it, else taken from the config file),
and one small HTTP helper. Stdlib only.
"""
import json, os, shlex, socket, stat, time, urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
socket.setdefaulttimeout(30)   # no call may hang a request thread forever (opener.open() has no per-call timeout)

def load_env(path=None):
    """The config file, with the panel's own live overrides (settings.local) merged on top."""
    path = path or os.environ.get("CONTROLLARR_ENV", "")
    d = {}
    if path and os.path.exists(path):
        for line in open(path):
            line = line.rstrip("\n")
            if line and not line.lstrip().startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                try:
                    v = shlex.split(v)[0] if v.strip() else ""
                except Exception:
                    v = v.strip()
                d[k.strip()] = v
    if not d: return d   # no config file: nothing to overlay onto
    base = d.get("CONTROLLARR_DIR") or os.environ.get("CONTROLLARR_DIR") or os.path.dirname(path)
    local = os.path.join(base, "settings.local") if base else ""
    if local and os.path.exists(local):
        for line in open(local):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); d[k.strip()] = v.strip()
    return d

E = load_env()
HOST = E.get("SERVER_HOST", "localhost")
CONFIG_DIR = E.get("CONFIG_DIR", "")   # the *arr stack's config tree, when Controllarr can read it; else API keys come from E

def apikey(app):
    """A service's API key: from its own config.xml when readable, else <APP>_APIKEY from the config file."""
    if CONFIG_DIR:
        try:
            k = ET.parse(os.path.join(CONFIG_DIR, app, "config.xml")).getroot().findtext("ApiKey")
            if k: return k
        except Exception:
            pass
    return E.get(f"{app.upper()}_APIKEY", "")

# ---------------- secrets at rest ----------------
# The keys live in exactly two places: the config file above, and the service's own config directory
# (`apikey`). Nothing here encrypts them, deliberately. The panel starts unattended, so the password to
# unscramble them would have to sit on the same disk, readable by the same process — a lock with its key
# taped to the door. And when Controllarr can see the stack's config tree it does not hold a copy at all:
# it reads Radarr's own `config.xml`, which Radarr keeps in cleartext and which is not ours to change.
# What is enforced instead: the file is the owner's alone (`config_problems`, checked before the panel
# serves anything), and no secret reaches a response body, a log line or an error message (`redact`).
# docs/CONFIGURATION.md states what that does and does not protect against.
_SECRETISH = ("APIKEY", "API_KEY", "PASSWORD", "PASS", "TOKEN", "SECRET")
_SECRET_APPS = ("radarr", "sonarr", "prowlarr")   # the ones `apikey` reads from a config.xml
MIN_SECRET = 12   # a shorter value is left alone: replacing a four-character string would mangle unrelated text
_extra = set()
_known = {"vals": frozenset(), "ts": 0.0}

def add_secret(value):
    """Register a secret this module does not read itself — Bazarr's and Jellyseerr's keys live in their own files."""
    v = str(value or "")
    if len(v) >= MIN_SECRET: _extra.add(v)

def secrets():
    """Every secret value the panel holds. Re-read periodically: a key can be rotated under a running panel."""
    now = time.monotonic()
    if now - _known["ts"] > 60 or not _known["ts"]:
        vals = {v for k, v in E.items() if any(t in k.upper() for t in _SECRETISH)}
        vals |= {apikey(a) for a in _SECRET_APPS}
        _known["vals"] = frozenset(v for v in vals if v and len(v) >= MIN_SECRET); _known["ts"] = now
    return _known["vals"] | frozenset(_extra)

def redact(text):
    """Text on its way out of the panel — a response, a log line, an error — with every secret replaced."""
    s = str(text)
    for v in secrets():
        if v in s: s = s.replace(v, "***")
    return s

def config_problems(path=None):
    """Why the panel must not start: the file holding the keys is readable or writable by anyone with an
    account on this box. Returns [(path, mode)], empty being the good state. Group permission is not refused —
    a shared group is a legitimate way to run this — but docs/CONFIGURATION.md advises against it."""
    path = path or os.environ.get("CONTROLLARR_ENV", "")
    if not path or not os.path.exists(path): return []
    try: mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError: return []
    return [(path, mode)] if mode & 0o006 else []

def http(method, url, headers=None, data=None, opener=None, timeout=60, expect_json=True):
    h = dict(headers or {}); body = None
    if data is not None:
        if isinstance(data, (dict, list)): body = json.dumps(data).encode(); h.setdefault("Content-Type", "application/json")
        elif isinstance(data, bytes): body = data
        else: body = str(data).encode()
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    op_open = opener.open if opener is not None else urllib.request.urlopen
    try:
        with op_open(req, timeout=timeout) as r:
            raw = r.read().decode(); return r.status, (json.loads(raw) if expect_json and raw else raw)
    except urllib.error.HTTPError as e:
        # HTTP error: None as the body when JSON was expected, so a caller that iterates does not crash on a str
        return e.code, (None if expect_json else e.read().decode()[:300])
    except Exception as e:
        return None, (None if expect_json else str(e))
