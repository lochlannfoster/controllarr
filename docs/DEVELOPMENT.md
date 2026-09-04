# Development

How Controllarr is put together, the invariants that are easy to break, and how to test it without touching a real \*arr stack.

Related: [DASHBOARD.md](DASHBOARD.md) (what each control does) · [CONFIGURATION.md](CONFIGURATION.md)

## 1. Layout

| Path | What |
|---|---|
| `install.sh`, `uninstall.sh`, `lib/common.sh` | the entry points; interactive (`ask` reads `/dev/tty`) |
| `docker-compose.yml` | one service; `install.sh` generates the gitignored `docker-compose.override.yml` (networking, optional mounts) |
| `app/controllarr.py` | the server: routing, auth, actions, settings |
| `app/panel_data.py` | every source the page reads, behind one cache |
| `app/board_gen.py` | the library classifier: seven stages |
| `app/settings_ops.py` | the single writer of app settings: the TRaSH sync, language, seeders, media management, client and subtitles |
| `app/trash.py`, `app/trash-guides/` | the vendored TRaSH Guides data and the diff against what an arr holds — reads only |
| `app/library_import.py` | adopting media the arrs do not know about |
| `app/services.py` | configuration, API keys, one HTTP helper |
| `app/action_log.py` | the bounded record of every write, behind Settings ▸ Action log |
| `app/static/` | pages, ES modules, tokens, vendored fonts — no build step |
| `tests/` | the validation pipeline (§4) |

`app/` is mounted read-only into the container, so `git pull` plus a restart is the whole upgrade.

## 2. The server

A stdlib `ThreadingHTTPServer` (`Handler`), sibling modules only; vanilla ES modules in the browser.

- **Boot:** `CONTROLLARR_PORT`, `_REFRESH`, `_PASSWORD` from the environment; everything else from `CONTROLLARR_ENV` through `services.load_env`, with `settings.local` overlaid. `SERVICES()` is what this install connects to; `_host()` resolves each app.
- **Data:** a thread regenerates the library every `REFRESH` s or when `_WAKE` is set (`board_gen.generate`: seven stages, ≤ 6 searches per pass, verdicts cached 3 h). Sections come from `panel_data.Panel` with every accessor injected; `Sources` is the per-source cache (TTL, short fail TTL, last good value kept, `ok`/`age_s`/`err` reported per source).
- **Absence is not failure:** `Panel.has(name)` consults the configured service list; a service you do not run is never polled and never reported. `Panel(docker=False)` does the same for the container table, VPN check and health roll-up.
- **Actions:** every write is `POST /api/action` → `do_action` (one entry through `action_log.record`) → `_do_action`. Privileged actions are in `_CAP_FOR`, `_can` decides, the client's `data-cap` hiding is cosmetic. Download actions take `hash` as one hash or `a|b|c`. Purges resolve through `_title_hashes` (queue ∪ the arr's history ∩ what the client still holds) and `_tv_scope` / `_purge_tv_scope`, which cascades to the whole show when nothing on disk and nothing tracked is left.

### 2.1 Invariants

- **HTTP/1.1** (`protocol_version`, 30 s idle): every response carries `Content-Length` (bodiless → `0`), `do_POST` reads its body before any early return — otherwise browsers never revalidate and a leftover body is parsed as the next request.
- **`?v=`:** `asset_ver()` hashes the names, sizes and mtimes under `static/` per request; `_version_imports` rewrites bare relative `./x.js` imports. Anything else is served unversioned.
- **Files Controllarr writes** (`users.json`, `sessions.json`, `settings.local`, `actions.log` 0600; `cache/`) are chowned back to the directory owner (`_own_like_dir`).
- **The action log is a ring.** `action_log` keeps the newest `LOG_RING` entries ([the number an operator sees](DASHBOARD.md#settings)) and rewrites the file once it runs `_SLACK` past that, so it cannot fill the disk the panel is monitoring. Every entry has the same keys (`action_log.FIELDS`) and a `type` saying which funnel it came through, because it is the event record, not a formatted line; `record` is the only writer and it prints the stdout line too, so the two can never disagree. `redact` scrubs every text field, and no request body is ever recorded. `GET /api/log` is the whole read side, admin-only and with no write route facing it.
- **Secrets** live in `services.py` and nowhere else: `config_problems` refuses to start the panel on a config anyone else can read (called first in `__main__`), and `redact` scrubs every secret from any text on its way out — `_send`, the `/api/board` body, the `do_action` log line and every error print. A key read from another app's own file registers itself with `add_secret`. Values shorter than `MIN_SECRET` are left alone, or scrubbing would mangle unrelated text. They are deliberately not encrypted; the reasoning is in the module comment and in [CONFIGURATION.md ▸ Your keys](CONFIGURATION.md#your-keys).
- `settings_ops.py` is the single writer of app settings — the installer of a bigger stack and this panel must agree.
- **Quality correctness is the guide's, not ours** (§3.2). A save of the Movies or TV group must never touch a custom format, a profile's qualities or a quality definition, or it would silently undo a sync; the tests assert exactly that.
- The Docker socket is **read-only by design**: Controllarr never starts or stops a container.
- Privileged actions live in `_CAP_FOR`; unknown `/api/` paths are a JSON 404, never the page shell; `HEAD` is 501.

## 3. Design system

Values live only in `app/static/tokens.css`. Six core tokens and four semantic ones; status is always a colour **and** a word or glyph. Five states per widget: loading (skeletons), empty (the good news said out loud), partial (one row per failed source), error, stale (an age chip). The floor: 44 px targets on coarse pointers, 16 px inputs, a visible focus ring, focus traps with `Esc`, WCAG AA in both themes, axe clean with no exclusions. DOM through `h()` / `append()` only, never `innerHTML` with data; a row handler that re-renders on click tests `e.composedPath()`.

### 3.1 Incognito (the render layer)

`app/static/modules/incognito.js` is the switch behind the header's **Incognito** button ([what it covers](DASHBOARD.md#incognito)). It masks what a module *draws* and nothing else — no payload is rewritten, `panel_data` substitutes nothing, and every action body keeps the real id and the real title, so `do_action` logs the real target.

- `mask(real, key)` / `who(real)` / `yr(year)` / `poster(url)` / `epLabel(label)` at each render site; `alias(key)` hashes (FNV-1a) into two words from its own lists and a number, so the pseudonym is stable, carries nothing of the input, and is keyed by `kind:id` wherever two views mean one thing.
- `sig()` belongs in **every keyed-render signature** (`patchList` skips a row whose signature has not moved, so a flip would otherwise leave real names on screen). `onChange` in `app.js` redraws from the data the page is already holding rather than waiting for a poll.
- Text the **server** composes needs its help: an attention item lists the real names its sentences carry in `subjects` (`Panel._subject`), and the client replaces exactly those; `/api/consequence` takes `incognito=1`, which tells `Panel.consequence` to leave out the names it would add from qBittorrent while every count stays.
- A new render site is covered by `tests/e2e/incognito.spec.mjs`: it sweeps the rendered text plus every `title` / `aria-label` / `alt` / `placeholder` and fails on any name the fake stack owns.

### 3.2 TRaSH Guides (data, diff, writer)

Three pieces, and the split between them is the invariant: **`app/trash-guides/`** is data, **`app/trash.py`**
reads and diffs it and writes nothing, **`settings_ops.apply_trash`** is the only thing that writes
([what a person sees](DASHBOARD.md#trash-guides), [where the data came from](CONFIGURATION.md#the-quality-guide)).

- `trash.load(app)` prefers a refreshed copy under the state directory over the vendored one (`trash.VENDOR_DIR`
  is the shipped copy, `trash.configure` points at the other); `trash._compile`
  turns the guide's file-per-format tree into that one document and is shared by `python3 app/trash.py vendor`
  and by `refresh()`, so a refreshed file and a vendored one are the same shape by construction.
- `trash.plan(app, name, arr, default)` is the whole preview: formats to create or update (compared on their
  *specifications*, never their name or id), the profile's score, quality and field changes, the per-quality
  size limits, and whether the profile titles are added with would move. `build_items` turns the guide's
  best-first list into the arrs' worst-first one against `GET /qualityprofile/schema`, gives each group an id
  from 1000 up, and appends any quality the guide does not mention as not-allowed — the arrs reject a profile
  that does not list every quality they know. `plan` issues GETs only, and an api test asserts it.
- `apply_trash` writes in the order the preview showed: formats first (a profile can only score a format that
  exists), then the profile, then the global quality definitions. A format whose pattern has moved on is
  updated **in place** so every profile already scoring it follows; nothing is ever duplicated.
- **Rollback is the existing snapshot, not a second mechanism.** `export_config` carries an `arr` section from
  `settings_ops.arr_state` — every profile's allowed qualities, format scores, cutoff and score floors, plus
  the quality definitions, all keyed by *name* so a snapshot still means something on another box, and with
  zero scores dropped so two identical profiles compare equal. `_write_rollback` takes that snapshot before
  every apply; `apply_arr_state` puts it back and leaves a profile the sync created in place, naming it.
  Because it *is* the settings snapshot, rolling back restores the whole of it — `apply_config` applies every
  one of `_SNAP_TABS` (Downloads, Movies, TV, Subtitles, Notifications) before `apply_arr_state` — so a
  setting changed after the sync is undone with it. The confirmation says so; it is the price of one
  mechanism rather than two.
- **`refresh()` is the only outbound request in the panel**, it is reached only from a button, and it reads
  tarball members by name rather than extracting, so a hostile archive cannot write outside the state directory.

## 4. Validation

`tests/run.sh` boots Controllarr from the checkout against **`tests/fake_stack.py`** — one stdlib server per app plus a fake Docker socket, each implementing exactly the API subset Controllarr calls (unknown → 404) — through `tests/harness.py` (temp directories, its own config file, the server as a subprocess). No real service is ever touched. Its root folders carry two unmapped entries, `ADOPT_NAME` (holding media) and `GHOST_NAME` (emptied, as a purge leaves a directory behind), because the only way adoption can tell them apart is the arr's own scan of the folder.

| Stage | What | Time |
|---|---|---|
| `lint` | `bash -n` (+ shellcheck), `ast.parse`, `ruff`, `node --check` of every ES module via stdin, Markdown links (they must exist, and — from a tracked file — be tracked, so a published doc cannot point at something only this checkout has), JSON | 2 s |
| `unit` | helpers, the config loader, `board_gen`, `Sources`, the VPN namespace check, consequence text, `settings_ops` readers, the action log's ring and redaction, the vendored guide and the diff it produces against a dictionary-shaped arr | 1 s |
| `api` | the auth gate, HTTP/1.1 invariants, 404 JSON, ETags, every section's contract, source failures, **what an install without a given service or without a Docker socket does**, capabilities, presets, the action log (its entries, who may read it, that it outlives the process), the TRaSH sync (that a preview writes nothing, that the snapshot exists before the first write, the write order, and that a rollback restores exactly), every action's wiring and the three purge scopes — asserted on the fake's call log | 50 s |
| `compose` | `docker compose config` on the shipped file | 1 s |
| `e2e` / `a11y` | Playwright, headless Chromium: sign-in, the Dash, every section, the library, the season tree and episode dialog, groups, the system strip, Settings, the five widget states through the fake's `/_control`, confirmations, roles, the action log, incognito (the pseudonym's own properties, and a sweep for any real name left on the page), and the phone floor. **Three `controls-*` specs press every control in turn and assert the request it makes** (below). Every test fails on any console error or 4xx/5xx. axe on every surface | 220 s |
| `archive` | the committed `app/` boots and answers every section | 5 s |
| `py312` | unit + api inside `python:3.12-alpine` | 15 s |

```bash
tests/run.sh quick     # lint + unit + api — after any change
tests/run.sh e2e       # when app/static/ or the HTTP layer changed
tests/run.sh all       # everything
tests/run.sh serve     # keep the panel + fake up for a browser (prints URL and login)
```

Setup: `npm ci && npx playwright install --with-deps chromium` (Node ≥ 18). Driving the fake: `POST {"down": [...]}` / `{"up": [...]}` / `{"scenario": "default|empty|container_down|backup_stale"}` / `{"clear_calls": true}` / `{"reset": true}`.

### 4.1 Every control, and the call it makes

The other browser specs check what a screen *says*; `tests/e2e/controls-dash.spec.mjs` (the header, the Dash,
Live, Needs attention, the library tools and the bulk bar), `controls-item.spec.mjs` (the drawer, the season
tree, the episode dialog and the episode toolbar) and `controls-settings.spec.mjs` (all thirteen groups)
check what a control *does*. Each one clears the fake's call log, presses the control, and asserts the exact
request that reached Radarr, Sonarr, Bazarr, Jellyfin, Jellyseerr, Prowlarr, ntfy or qBittorrent — the id or
hash included, because acting on the wrong title is the failure that matters.

The other half is asserted just as carefully: a control that is only meant to change this browser (theme,
density, incognito, a filter, a station, folding a season) must reach **no** backend at all, a cancelled
confirmation must write nothing, and a TRaSH preview or a snapshot export must be GETs only. A view control
that quietly writes is the worse bug.

Two habits these specs depend on, both of which cost an afternoon to learn:

- **Reset the fake *and* the panel.** `{"reset": true}` restores the fake's data, but the panel is still
  holding the board it read a moment ago, so a title an earlier test unmonitored or purged is still missing.
  `POST /api/refresh` — what the header's re-scan control posts — drops that cache.
- **Address a row by its key, never its position.** Torrent rows are ordered by queue position, which a
  reset reshuffles; `article.tor[data-key="<hash>"]` is stable, and it also says which torrent the assertion
  is about.

## 5. Adding things

- **A service:** teach `fake_stack.py` its API subset first — a call the fake does not know answers 404 and the test fails loudly. Then `_SVC` in `controllarr.py`, an accessor and a source in `panel_data.py`, a prompt in `install.sh`, a row in the README table, and a page section in `docs/DASHBOARD.md`.
- **An action:** a branch in `_do_action` returning `(ok, msg)`; register privileged ones in `_CAP_FOR`; anything destructive needs consequence text with real counts. It lands in the action log by itself — `do_action` is the funnel.
- **A render site:** anything drawing a title, a poster, a person or a file name goes through `incognito.js` (§3.1), and its signature carries `sig()`.
- **A route:** admin-only POSTs go in `_ADMIN_POST`; unknown `/api/` stays a JSON 404.
- **A quality rule:** you do not add one. It is the guide's (§3.2) — re-vendor with `python3 app/trash.py vendor` and record the commit and date in [CONFIGURATION.md ▸ The quality guide](CONFIGURATION.md#the-quality-guide).

## 6. Conventions

- **Commits:** `<area>: <lowercase statement>` — areas `app`, `installer`, `compose`, `docs`, `tests`, `repo` — no trailing full stop; the body says why and how it was verified.
- **Docs:** one home per fact; every other mention is a sentence and a link. British spelling, sentence-case headings, placeholders never a real host. Cite function and module names, never line numbers.
- **Vocabulary:** the Dash, System, Needs attention, stations, rows, the season tree, the episode dialog, the drawer, Settings groups, presets, the guide (never "TRaSH sync" as a noun for the feature — it is *preview then apply*).
