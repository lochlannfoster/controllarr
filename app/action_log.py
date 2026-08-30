"""Controllarr's own record of every write.

`do_action` already prints one line per write to stdout, and `docker logs controllarr` holds it until the
container is recreated. Because a purge here cascades across the whole stack, "who did that, and when"
deserves an answer that survives a `compose up`, so the same line is teed — as JSON — into a bounded ring
file beside the panel's other state, and Settings reads it back. Stdlib only, like everything under `app/`.

One entry per write, always the same keys (`FIELDS`), because notification channels will subscribe to this
record rather than to a reshaped one:

    {"ts": 1756550000, "type": "action", "user": "sam", "role": "user", "action": "purge",
     "target": "movie:12", "result": "ok", "ms": 412, "msg": "Purged Coherence"}

`type` says which funnel the entry came through — `action` for a write through `/api/action`, `account` for
a change to a user, a password or a role. The file is the newest `LOG_RING` entries and no more, so it
cannot fill the disk the panel is monitoring; it is 0600 and owned like the directory it sits in; and no
secret reaches it, because every text field goes out through `services.redact` and no request body is ever
recorded — a password is not part of an entry. Read-only: nothing here deletes or rewrites an entry, and
the panel offers no undo.
"""
import json, os, threading, time
from services import redact

FIELDS = ("ts", "type", "user", "role", "action", "target", "result", "ms", "msg")
LOG_RING = 2000    # entries kept; the oldest fall off. docs/DASHBOARD.md states the number to the operator.
_SLACK = 200       # how far past LOG_RING the file may run before it is rewritten (a rewrite reads all of it)
_MSG_MAX = 200     # a result message is a sentence; anything longer is a stack trace escaping into the log

_LOCK = threading.Lock()
_S = {"path": "", "own": None, "n": 0}


def configure(path, own=None):
    """Where the ring lives, and how to give a new file the owner of its directory (`_own_like_dir`).
    Called once at boot; until it is, `record` prints its line and keeps nothing."""
    with _LOCK:
        _S["path"], _S["own"], _S["n"] = path, own, _count(path)


def record(event, user, role, action, target, ok, ms=None, msg=""):
    """Compose, print and persist one entry; returns it. The printed line is the one the container log has
    always carried, so `docker logs controllarr` reads the same as it did before."""
    e = {"ts": int(time.time()), "type": event, "user": user or "-", "role": role or "-",
         "action": action or "-", "target": redact(target if target not in (None, "") else "-")[:_MSG_MAX],
         "result": "ok" if ok else "fail", "ms": ms, "msg": redact(msg)[:_MSG_MAX]}
    print(f"action user={e['user']} role={e['role']} action={e['action']} target={e['target']}"
          f" result={e['result']}" + (f" ms={e['ms']}" if ms is not None else "")
          + f" msg={json.dumps(e['msg'])}", flush=True)
    _append(e)
    return e


def view(user=None, action=None, limit=200):
    """What Settings shows: the newest entries first, optionally narrowed to one person or one action, plus
    the values worth offering as filters and how full the ring is."""
    rows = _read()
    try: limit = max(1, min(int(limit), LOG_RING))
    except (TypeError, ValueError): limit = 200
    out = []
    for r in reversed(rows):
        if user and r.get("user") != user: continue
        if action and r.get("action") != action: continue
        out.append(r)
        if len(out) >= limit: break
    return {"entries": out, "total": len(rows), "cap": LOG_RING,
            "users": sorted({r.get("user") for r in rows if r.get("user")}),
            "actions": sorted({r.get("action") for r in rows if r.get("action")})}


def entries(user=None, action=None, limit=200):
    """The entries alone — the shape anything subscribing to this record wants."""
    return view(user, action, limit)["entries"]


# ---------------- the file
def _count(path):
    try:
        with open(path, "rb") as f: return sum(1 for _ in f)
    except OSError: return 0


def _harden(path):
    try: os.chmod(path, 0o600)
    except OSError: pass
    if _S["own"]:
        try: _S["own"](path)
        except Exception: pass


def _append(e):
    path = _S["path"]
    if not path: return
    try:
        with _LOCK:
            fresh = not os.path.exists(path)
            with open(path, "a", encoding="utf-8") as f: f.write(json.dumps(e, separators=(",", ":")) + "\n")
            if fresh: _harden(path)
            _S["n"] += 1
            if _S["n"] > LOG_RING + _SLACK: _trim()
    except OSError as err:
        print("action log: could not write:", redact(err), flush=True)


def _trim():
    """Keep the newest LOG_RING entries and drop the rest. Called holding _LOCK."""
    path = _S["path"]
    with open(path, encoding="utf-8", errors="replace") as f: lines = f.readlines()
    lines = lines[-LOG_RING:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: f.writelines(lines)
    os.replace(tmp, path); _harden(path); _S["n"] = len(lines)


def _read():
    """Every entry on disk, oldest first. A torn last line (a crash mid-write) is skipped, never fatal."""
    path = _S["path"]
    if not path: return []
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try: r = json.loads(line)
                except ValueError: continue
                if isinstance(r, dict): rows.append({k: r.get(k) for k in FIELDS})
    except OSError: return []
    return rows
