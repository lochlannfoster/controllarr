"""Configuration and service access for Controllarr.

Everything the panel needs to reach an *arr stack it did not install: the config file (CONTROLLARR_ENV,
`KEY=value`, values may be POSIX-quoted), the live overrides the Settings page writes next to it, API keys
(read from a service's own config directory when Controllarr can see it, else taken from the config file),
and one small HTTP helper. Stdlib only.
"""
import json, os, shlex, socket, urllib.request, urllib.parse, urllib.error
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
