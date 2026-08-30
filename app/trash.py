"""TRaSH Guides — the vendored quality data, and the difference between it and what an arr holds.

Quality *correctness* is not the panel's to invent: which release groups are tiered, which sources are
upscales, what a BR-DISK is worth. TRaSH Guides (https://trash-guides.info) keeps that current and this
module carries a compiled copy of it. It reads and diffs only — every write goes through `settings_ops`,
which stays the single writer of the apps' settings.

Where the data comes from, in order:
  1. `<data dir>/trash-guides/<app>.json`, written by `refresh()` when someone presses Refresh in Settings;
  2. `app/trash-guides/<app>.json`, vendored in the repository.
Never fetched at boot and never on a schedule — a scheduled sync would make this a worse Recyclarr and break
the line docs/ROADMAP.md opens with. `refresh()` is the only code here that reaches off the LAN, and only a
person pressing a button calls it.

Re-vendor with `python3 app/trash.py vendor` (writes app/trash-guides/). The data is MIT,
Copyright (c) 2021 TRaSH; app/trash-guides/LICENSE ships the notice with it.
"""
import json, os, re, tarfile, tempfile, urllib.request

REPO = "https://github.com/TRaSH-Guides/Guides"
TARBALL = "https://codeload.github.com/TRaSH-Guides/Guides/tar.gz/refs/heads/master"
VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trash-guides")
APPS = ("radarr", "sonarr")

# The profiles we carry. TRaSH publishes German and French localised sets and the SQP series (five advanced
# streaming profiles with quality-size sets of their own); neither belongs in a household control surface, and
# `base-profile` is a template with no formats at all. Everything else is vendored, with exactly the custom
# formats those profiles score — carrying all ~240 formats would be data nothing reads.
_SKIP_PROFILE = re.compile(r"^(german|french|sqp|base-profile)")
# Which quality-size set a profile's sizes come from. TRaSH pairs its anime profiles with anime.json and
# everything else with movie.json / series.json.
def _size_set(name): return "anime" if name.startswith("[Anime]") else "default"


# ---------------------------------------------------------------- loading
_CACHE = {}          # (path, mtime) -> compiled dict, so a plan does not re-read 250 KB of JSON per request
_DATA_DIR = None     # set by configure(); where a refresh writes and is read back from

def configure(data_dir):
    """Point the refreshed copy at the panel's own data directory (never inside another app's config)."""
    global _DATA_DIR
    _DATA_DIR = os.path.join(data_dir, "trash-guides") if data_dir else None

def _paths(app):
    out = []
    if _DATA_DIR: out.append(os.path.join(_DATA_DIR, f"{app}.json"))
    out.append(os.path.join(VENDOR_DIR, f"{app}.json"))
    return out

def load(app):
    """The newest available set for `app`: the refreshed copy if there is one, else the vendored one."""
    for p in _paths(app):
        try: key = (p, os.path.getmtime(p))
        except OSError: continue
        if key not in _CACHE:
            if len(_CACHE) > 4: _CACHE.clear()           # one entry per app per revision; a refresh strands the old one
            with open(p) as f: _CACHE[key] = json.load(f)
        return _CACHE[key]
    raise FileNotFoundError(f"no TRaSH data for {app}")

def version(app="radarr"):
    """Provenance, for the Settings header: where the data came from, at which commit, and when."""
    try: d = load(app)
    except Exception: return {}
    return {"source": d.get("source", REPO), "commit": d.get("commit", ""), "fetched": d.get("fetched", ""),
            "vendored": not (_DATA_DIR and os.path.exists(os.path.join(_DATA_DIR, f"{app}.json"))),
            "profiles": len(d.get("profiles", [])), "formats": len(d.get("formats", {}))}

def profiles(app):
    """The profiles on offer for `app`, in the order TRaSH groups them (best-known first)."""
    return [{"name": p["name"], "desc": p.get("description", ""), "url": p.get("url", "")} for p in load(app)["profiles"]]

def profile(app, name):
    for p in load(app)["profiles"]:
        if p["name"] == name: return p
    return None


# ---------------------------------------------------------------- comparing against an arr
def _fields(spec):
    """A specification's fields as a plain dict. TRaSH writes `{"value": x}`; the arrs answer
    `[{"name": "value", "value": x, …}]` with a dozen presentation keys we must not compare on."""
    f = spec.get("fields")
    if isinstance(f, dict): return {k: v for k, v in f.items()}
    return {x.get("name"): x.get("value") for x in (f or []) if isinstance(x, dict)}

def _spec_key(spec):
    return (spec.get("implementation"), bool(spec.get("negate")), bool(spec.get("required")),
            json.dumps(_fields(spec), sort_keys=True, default=str))

def _cf_key(cf):
    """What makes two custom formats the same format: their specifications, not their name or id."""
    return sorted(_spec_key(s) for s in (cf.get("specifications") or []))

def format_body(fmt, name=None):
    """A custom format as the arrs' POST/PUT wants it (fields back into a list)."""
    return {"name": name or fmt["name"], "includeCustomFormatWhenRenaming": bool(fmt.get("includeCustomFormatWhenRenaming")),
            "specifications": [{"name": s.get("name") or fmt["name"], "implementation": s["implementation"],
                                "negate": bool(s.get("negate")), "required": bool(s.get("required")),
                                "fields": [{"name": k, "value": v} for k, v in _fields(s).items()]}
                               for s in fmt.get("specifications", [])]}

def _flatten(items, out=None):
    """name -> the arr's own quality object, from a profile (or the profile schema), groups included."""
    out = {} if out is None else out
    for it in items or []:
        q = it.get("quality")
        if isinstance(q, dict) and q.get("name"): out[q["name"]] = q
        if it.get("items"): _flatten(it["items"], out)
    return out

def _allowed(items, out=None):
    """name -> allowed, for every quality AND every group in a profile as the arr holds it."""
    out = {} if out is None else out
    for it in items or []:
        q = it.get("quality")
        nm = q.get("name") if isinstance(q, dict) else it.get("name")
        if nm: out[nm] = bool(it.get("allowed"))
        if it.get("items"): _allowed(it["items"], out)
    return out

def build_items(tp, catalogue):
    """TRaSH's item list (best first, groups nested) as the arr's own list (worst first).

    `catalogue` is name -> quality object from GET /qualityprofile/schema. A quality the guide does not
    mention still has to appear — the arrs reject a profile that does not list every quality they know — so
    anything left over is appended, not allowed. Group ids start at 1000, above every real quality id, which
    is the convention the arrs' own UI uses."""
    used, out, gid = set(), [], 1000
    for it in reversed(tp.get("items", [])):
        names = it.get("items") or []
        if names:                                          # a group of qualities
            members = [catalogue[n] for n in reversed(names) if n in catalogue]
            if not members: continue
            used.update(q["name"] for q in members)
            out.append({"id": gid, "name": it["name"], "allowed": bool(it.get("allowed")),
                        "items": [{"quality": q, "items": [], "allowed": bool(it.get("allowed"))} for q in members]})
            gid += 1
        elif it["name"] in catalogue:
            used.add(it["name"])
            out.append({"quality": catalogue[it["name"]], "items": [], "allowed": bool(it.get("allowed"))})
    for nm, q in catalogue.items():
        if nm not in used: out.insert(0, {"quality": q, "items": [], "allowed": False})
    return out

def _cutoff_id(tp, items):
    """The arr wants the cutoff as an id — a group's id when the guide names a group, else a quality's."""
    want = tp.get("cutoff")
    for it in items:
        if it.get("name") == want and it.get("id") is not None: return it["id"]
        q = it.get("quality")
        if isinstance(q, dict) and q.get("name") == want: return q["id"]
    return None

def plan(app, name, arr, default_profile_name=None):
    """What applying TRaSH profile `name` to `app` would change, as data the page can render.

    Nothing is written. The three things that change are custom formats (created, or their regex updated),
    one quality profile (its allowed qualities, its format scores and its cutoff) and the global quality
    definitions (min/preferred/max size per quality — what the panel's own MB-per-minute cap used to do,
    done per quality instead)."""
    data = load(app); tp = profile(app, name)
    if not tp: return {"error": f"no TRaSH profile named {name!r} for {app}"}
    score_set = tp.get("score_set") or "default"
    warnings = []

    # -- custom formats
    st, have = arr(app, "/customformat")
    by_name = {c["name"]: c for c in (have or []) if isinstance(c, dict)}
    formats, create, update = [], [], []
    for fname, fid in sorted(tp.get("formats", {}).items()):
        fmt = data["formats"].get(fid)
        if not fmt: warnings.append(f"the guide names {fname!r} but carries no definition for it"); continue
        score = fmt.get("scores", {}).get(score_set, fmt.get("scores", {}).get("default", 0))
        cur = by_name.get(fmt["name"])
        if cur is None: create.append(fmt["name"])
        elif _cf_key(cur) != _cf_key(fmt): update.append(fmt["name"])
        # `tid` keeps the guide's own id on the entry: a profile lists its formats by name, and a name is the
        # one thing TRaSH may change under us without changing what the format means.
        formats.append({"name": fmt["name"], "score": score, "id": (cur or {}).get("id"), "tid": fid})

    # -- the profile itself
    st, profs = arr(app, "/qualityprofile")
    cur = next((p for p in (profs or []) if isinstance(p, dict) and p.get("name") == name), None)
    st, schema = arr(app, "/qualityprofile/schema")
    catalogue = _flatten((schema or {}).get("items") if isinstance(schema, dict) else None)
    if not catalogue and cur: catalogue = _flatten(cur.get("items"))
    if not catalogue: warnings.append(f"{app} would not say which qualities it knows; the profile's quality list is left as it is")
    items = build_items(tp, catalogue) if catalogue else (cur or {}).get("items", [])

    want_allowed = _allowed(items); cur_allowed = _allowed((cur or {}).get("items"))
    # a profile the arr does not have yet is a creation, not two dozen quality changes: the preview names what
    # it will allow instead of diffing against nothing.
    qualities = [] if cur is None else [{"name": n, "from": cur_allowed.get(n), "to": a}
                                        for n, a in want_allowed.items() if n in cur_allowed and cur_allowed[n] != a]
    allowed = [n for n, a in want_allowed.items() if a]
    cur_scores = {}
    if cur:
        by_id = {c["id"]: c["name"] for c in (have or []) if isinstance(c, dict)}
        for fi in cur.get("formatItems", []):
            nm = fi.get("name") or by_id.get(fi.get("format"))
            if nm: cur_scores[nm] = fi.get("score") or 0
    scores = [{"name": f["name"], "from": cur_scores.get(f["name"]), "to": f["score"]}
              for f in formats if cur_scores.get(f["name"], 0) != f["score"]]

    lang = tp.get("language")
    fields = []
    def _fld(key, to, frm):
        if frm != to: fields.append({"key": key, "from": frm, "to": to})
    if cur is not None:
        _fld("cutoff", tp.get("cutoff"), _cutoff_name(cur, catalogue))
        for k in ("minFormatScore", "cutoffFormatScore", "minUpgradeFormatScore", "upgradeAllowed"):
            _fld(k, tp.get(k), cur.get(k))
        if lang and app == "radarr": _fld("language", lang, (cur.get("language") or {}).get("name"))

    # -- quality definitions (global: they are not per profile, and this is what replaces the panel's size cap)
    sizes = []
    st, defs = arr(app, "/qualitydefinition")
    want_sizes = {q["quality"]: q for q in data["sizes"].get(_size_set(name), data["sizes"].get("default", []))}
    for q in (defs or []):
        w = want_sizes.get((q.get("quality") or {}).get("name"))
        if not w: continue
        frm = {"min": q.get("minSize"), "preferred": q.get("preferredSize"), "max": q.get("maxSize")}
        to = {"min": w["min"], "preferred": w["preferred"], "max": w["max"]}
        if frm != to: sizes.append({"quality": w["quality"], "from": frm, "to": to})

    # -- the one setting that stays the panel's: which profile a title added here is put on
    default = {"current": default_profile_name or "", "to": name,
               "change": bool(default_profile_name != name)}
    out = {"app": app, "profile": name, "description": tp.get("description", ""), "url": tp.get("url", ""),
           "exists": cur is not None, "version": version(app),
           "formats": {"create": create, "update": update, "same": len(formats) - len(create) - len(update),
                       "total": len(formats)},
           "scores": scores, "qualities": qualities, "allowed": allowed, "fields": fields, "sizes": sizes,
           "default_profile": default, "warnings": warnings,
           # everything the writer needs, so settings_ops does not re-derive (and possibly re-decide) any of it
           "apply": {"formats": formats, "items": items, "cutoff": _cutoff_id(tp, items), "language": lang,
                     "profile": {k: tp.get(k) for k in ("upgradeAllowed", "minFormatScore", "cutoffFormatScore", "minUpgradeFormatScore")},
                     "sizes": [want_sizes[k] for k in sorted(want_sizes)],
                     "bodies": {f["name"]: format_body(data["formats"][f["tid"]]) for f in formats}}}
    out["empty"] = is_empty(out)
    return out

def _cutoff_name(prof, catalogue):
    """The name behind a profile's cutoff id, so the diff reads 'Any → Remux-1080p' and not '7 → 1002'."""
    cid = prof.get("cutoff")
    for it in prof.get("items", []):
        if it.get("id") == cid and it.get("name"): return it["name"]
        q = it.get("quality")
        if isinstance(q, dict) and q.get("id") == cid: return q["name"]
    for nm, q in (catalogue or {}).items():
        if q.get("id") == cid: return nm
    return cid

def is_empty(p):
    """True when applying this plan would change nothing — the page says so rather than offering a button."""
    return not (p.get("formats", {}).get("create") or p.get("formats", {}).get("update") or p.get("scores")
                or p.get("qualities") or p.get("fields") or p.get("sizes")
                or p.get("default_profile", {}).get("change") or not p.get("exists"))


# ---------------------------------------------------------------- compiling the guide
def _compile(app, read, names):
    """The guide's file-per-format tree as one document. `read(path)` returns bytes for a path under
    docs/json/; `names` is every path in the source tree, so this works the same over a tarball and a
    checkout. Kept beside `refresh` on purpose: the vendored file and a refreshed one must be the same shape."""
    pre = f"docs/json/{app}/"
    def _jsons(sub):
        return sorted(n for n in names if n.startswith(pre + sub + "/") and n.endswith(".json"))
    formats = {}
    for n in _jsons("cf"):
        d = json.loads(read(n))
        formats[d["trash_id"]] = {"name": d["name"], "includeCustomFormatWhenRenaming": bool(d.get("includeCustomFormatWhenRenaming")),
                                  "specifications": d.get("specifications", []), "scores": d.get("trash_scores", {})}
    sizes = {}
    for n in _jsons("quality-size"):
        base = os.path.basename(n)[:-5]
        if base not in ("anime", "movie", "series"): continue     # the SQP size sets belong to profiles we do not carry
        d = json.loads(read(n))
        sizes["anime" if base == "anime" else "default"] = [
            {"quality": q["quality"], "min": q["min"], "preferred": q["preferred"], "max": q["max"]} for q in d.get("qualities", [])]
    profiles_, used = [], set()
    for n in _jsons("quality-profiles"):
        if _SKIP_PROFILE.match(os.path.basename(n)): continue
        d = json.loads(read(n))
        fmt = {k: v for k, v in (d.get("formatItems") or {}).items() if v in formats}
        used |= set(fmt.values())
        profiles_.append({"name": d["name"], "description": _plain(d.get("trash_description", "")), "url": d.get("trash_url", ""),
                          "score_set": d.get("trash_score_set") or "default",
                          "upgradeAllowed": bool(d.get("upgradeAllowed", True)), "cutoff": d.get("cutoff"),
                          "minFormatScore": d.get("minFormatScore", 0), "cutoffFormatScore": d.get("cutoffFormatScore", 10000),
                          "minUpgradeFormatScore": d.get("minUpgradeFormatScore", 1), "language": d.get("language"),
                          "items": d.get("items", []), "formats": fmt, "group": d.get("group", 50)})
    # TRaSH's own grouping, which puts the profiles it recommends first and the anime set last — the order the
    # Settings picker offers them in.
    profiles_.sort(key=lambda p: (p.pop("group"), p["name"]))
    return {"app": app, "source": REPO, "licence": "MIT, Copyright (c) 2021 TRaSH — see LICENSE beside this file",
            "sizes": sizes, "formats": {k: v for k, v in formats.items() if k in used}, "profiles": profiles_}

_TAGS = re.compile(r"<[^>]+>")
def _plain(s):
    """TRaSH writes its descriptions with <br> in them; the panel renders text, never markup."""
    return " ".join(_TAGS.sub(" ", s or "").split())

def _fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "controllarr", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return r.read()

def compile_from_tarball(dest, timeout=180):
    """Download the guide once and write <dest>/<app>.json for each app. The only outbound fetch in the panel,
    and only a person pressing Refresh reaches it. Members are read by name — never extracted — so a hostile
    archive cannot write outside `dest`."""
    commit = ""
    try: commit = (json.loads(_fetch("https://api.github.com/repos/TRaSH-Guides/Guides/commits/master", 30)) or {}).get("sha", "")[:12]
    except Exception: pass          # provenance is nice to have; the data is the point
    os.makedirs(dest, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz") as tmp:
        with urllib.request.urlopen(urllib.request.Request(TARBALL, headers={"User-Agent": "controllarr"}), timeout=timeout) as r:
            while True:
                chunk = r.read(1 << 20)
                if not chunk: break
                tmp.write(chunk)
        tmp.flush()
        with tarfile.open(tmp.name) as tar:
            root = tar.getnames()[0].split("/")[0] + "/"
            names = [n[len(root):] for n in tar.getnames() if n.startswith(root)]
            read = lambda p: tar.extractfile(root + p).read()
            out = {}
            for app in APPS:
                d = _compile(app, read, names)
                d["commit"] = commit; d["fetched"] = _today()
                out[app] = d
            try: licence = read("LICENSE").decode()
            except Exception: licence = ""
    for app, d in out.items():
        _write(os.path.join(dest, f"{app}.json"), json.dumps(d, indent=1, sort_keys=True) + "\n")
    if licence: _write(os.path.join(dest, "LICENSE"), licence)
    return {app: {"profiles": len(d["profiles"]), "formats": len(d["formats"]), "commit": commit} for app, d in out.items()}

def _today():
    import datetime
    return datetime.date.today().isoformat()

def _write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: f.write(text)
    os.replace(tmp, path)

def refresh(own=None):
    """Re-fetch the guide into the panel's data directory, where `load` prefers it over the vendored copy.
    Returns (ok, message). `own` chowns what it writes like every other panel-written file."""
    if not _DATA_DIR: return False, "no data directory configured"
    try:
        got = compile_from_tarball(_DATA_DIR)
    except Exception as e:
        return False, f"could not reach {REPO}: {type(e).__name__}"
    _CACHE.clear()
    if own:
        own(_DATA_DIR)                      # the directory too: the host user's nightly backup reads this tree
        for n in APPS + ("LICENSE",):
            p = os.path.join(_DATA_DIR, n + (".json" if n in APPS else ""))
            if os.path.exists(p): own(p)
    n = sum(v["profiles"] for v in got.values())
    return True, f"Guide refreshed — {n} profiles, {sum(v['formats'] for v in got.values())} custom formats" + \
                 (f" at {got['radarr']['commit']}" if got.get("radarr", {}).get("commit") else "")


if __name__ == "__main__":                      # `python3 app/trash.py vendor` — re-vendors app/trash-guides/
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "vendor":
        print(json.dumps(compile_from_tarball(VENDOR_DIR), indent=2))
    else:
        print(__doc__)
