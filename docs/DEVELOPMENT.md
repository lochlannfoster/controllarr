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
| `app/settings_ops.py` | the single writer of the apps' quality, size, language, client and subtitle settings |
| `app/library_import.py` | adopting media the arrs do not know about |
| `app/services.py` | configuration, API keys, one HTTP helper |
| `app/static/` | pages, ES modules, tokens, vendored fonts — no build step |
| `tests/` | the validation pipeline (§4) |

`app/` is mounted read-only into the container, so `git pull` plus a restart is the whole upgrade.

## 2. The server

A stdlib `ThreadingHTTPServer` (`Handler`), sibling modules only; vanilla ES modules in the browser.

- **Boot:** `CONTROLLARR_PORT`, `_REFRESH`, `_PASSWORD` from the environment; everything else from `CONTROLLARR_ENV` through `services.load_env`, with `settings.local` overlaid. `SERVICES()` is what this install connects to; `_host()` resolves each app.
- **Data:** a thread regenerates the library every `REFRESH` s or when `_WAKE` is set (`board_gen.generate`: seven stages, ≤ 6 searches per pass, verdicts cached 3 h). Sections come from `panel_data.Panel` with every accessor injected; `Sources` is the per-source cache (TTL, short fail TTL, last good value kept, `ok`/`age_s`/`err` reported per source).
- **Absence is not failure:** `Panel.has(name)` consults the configured service list; a service you do not run is never polled and never reported. `Panel(docker=False)` does the same for the container table, VPN check and health roll-up.
- **Actions:** every write is `POST /api/action` → `do_action` (one log line) → `_do_action`. Privileged actions are in `_CAP_FOR`, `_can` decides, the client's `data-cap` hiding is cosmetic. Download actions take `hash` as one hash or `a|b|c`. Purges resolve through `_title_hashes` (queue ∪ the arr's history ∩ what the client still holds) and `_tv_scope` / `_purge_tv_scope`, which cascades to the whole show when nothing on disk and nothing tracked is left.

### 2.1 Invariants

- **HTTP/1.1** (`protocol_version`, 30 s idle): every response carries `Content-Length` (bodiless → `0`), `do_POST` reads its body before any early return — otherwise browsers never revalidate and a leftover body is parsed as the next request.
- **`?v=`:** `asset_ver()` hashes the names, sizes and mtimes under `static/` per request; `_version_imports` rewrites bare relative `./x.js` imports. Anything else is served unversioned.
- **Files Controllarr writes** (`users.json`, `sessions.json` 0600; `settings.local`; `cache/`) are chowned back to the directory owner (`_own_like_dir`).
- `settings_ops.py` is the single writer of app settings — the installer of a bigger stack and this panel must agree.
- The Docker socket is **read-only by design**: Controllarr never starts or stops a container.
- Privileged actions live in `_CAP_FOR`; unknown `/api/` paths are a JSON 404, never the page shell; `HEAD` is 501.

## 3. Design system

Values live only in `app/static/tokens.css`. Six core tokens and four semantic ones; status is always a colour **and** a word or glyph. Five states per widget: loading (skeletons), empty (the good news said out loud), partial (one row per failed source), error, stale (an age chip). The floor: 44 px targets on coarse pointers, 16 px inputs, a visible focus ring, focus traps with `Esc`, WCAG AA in both themes, axe clean with no exclusions. DOM through `h()` / `append()` only, never `innerHTML` with data; a row handler that re-renders on click tests `e.composedPath()`.

## 4. Validation

`tests/run.sh` boots Controllarr from the checkout against **`tests/fake_stack.py`** — one stdlib server per app plus a fake Docker socket, each implementing exactly the API subset Controllarr calls (unknown → 404) — through `tests/harness.py` (temp directories, its own config file, the server as a subprocess). No real service is ever touched.

| Stage | What | Time |
|---|---|---|
| `lint` | `bash -n` (+ shellcheck), `ast.parse`, `ruff`, `node --check` of every ES module via stdin, Markdown links (they must exist, and — from a tracked file — be tracked, so a published doc cannot point at something only this checkout has), JSON | 2 s |
| `unit` | helpers, the config loader, `board_gen`, `Sources`, the VPN namespace check, consequence text, `settings_ops` readers | 1 s |
| `api` | the auth gate, HTTP/1.1 invariants, 404 JSON, ETags, every section's contract, source failures, **what an install without a given service or without a Docker socket does**, capabilities, presets, every action's wiring and the three purge scopes — asserted on the fake's call log | 45 s |
| `compose` | `docker compose config` on the shipped file | 1 s |
| `e2e` / `a11y` | Playwright, headless Chromium: sign-in, the Dash, every section, the library, the season tree and episode dialog, groups, the system strip, Settings, the five widget states through the fake's `/_control`, confirmations, roles, and the phone floor. Every test fails on any console error or 4xx/5xx. axe on every surface | 90 s |
| `archive` | the committed `app/` boots and answers every section | 5 s |
| `py312` | unit + api inside `python:3.12-alpine` | 15 s |

```bash
tests/run.sh quick     # lint + unit + api — after any change
tests/run.sh e2e       # when app/static/ or the HTTP layer changed
tests/run.sh all       # everything
tests/run.sh serve     # keep the panel + fake up for a browser (prints URL and login)
```

Setup: `npm ci && npx playwright install --with-deps chromium` (Node ≥ 18). Driving the fake: `POST {"down": [...]}` / `{"up": [...]}` / `{"scenario": "default|empty|container_down|backup_stale"}` / `{"clear_calls": true}` / `{"reset": true}`.

## 5. Adding things

- **A service:** teach `fake_stack.py` its API subset first — a call the fake does not know answers 404 and the test fails loudly. Then `_SVC` in `controllarr.py`, an accessor and a source in `panel_data.py`, a prompt in `install.sh`, a row in the README table, and a page section in `docs/DASHBOARD.md`.
- **An action:** a branch in `_do_action` returning `(ok, msg)`; register privileged ones in `_CAP_FOR`; anything destructive needs consequence text with real counts.
- **A route:** admin-only POSTs go in `_ADMIN_POST`; unknown `/api/` stays a JSON 404.

## 6. Conventions

- **Commits:** `<area>: <lowercase statement>` — areas `app`, `installer`, `compose`, `docs`, `tests`, `repo` — no trailing full stop; the body says why and how it was verified.
- **Docs:** one home per fact; every other mention is a sentence and a link. British spelling, sentence-case headings, placeholders never a real host. Cite function and module names, never line numbers.
- **Vocabulary:** the Dash, System, Needs attention, stations, rows, the season tree, the episode dialog, the drawer, Settings groups, presets.
