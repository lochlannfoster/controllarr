# Development

How the code is put together, how to validate a change, and the conventions that are deliberate
rather than accidental. If you change one file and run one command, make it `tests/run.sh quick`.

Related: [ROADMAP.md](ROADMAP.md) (what is intended next) · [DASHBOARD.md](DASHBOARD.md) (what exists today) ·
[CONFIGURATION.md](CONFIGURATION.md) (every setting) · [INSTALL.md](INSTALL.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

## The two constraints that decide a change

Both are stated in the [README](../README.md) and neither is negotiable in review:

1. **Controllarr is a client.** It reads the apps you already run over their own APIs and, on a control
   press, calls the API their own UI would have called. It installs none of them and reconfigures nothing
   you have not asked it to.
2. **It ships no indexers, no sources, and no lists of either** — and never adds one. Which indexers your
   \*arr apps search, and which download client gets a release, is configured in those apps by you.

A change that makes Controllarr act on its own, or that carries a source list into the repo, is out of
scope no matter how well it is written. See [ROADMAP.md](ROADMAP.md) for the longer form of that line.

## Layout

| Path | What it is |
|---|---|
| `app/controllarr.py` | the panel: HTTP layer, routing, session and auth — the bulk of the app |
| `app/panel_data.py` | assembles what each panel shows from the upstream APIs |
| `app/board_gen.py` | renders the Dash |
| `app/services.py` | service definitions; the re-export imports here are intentional |
| `app/settings_ops.py` | writes settings through to the upstream apps |
| `app/library_import.py` | the library import path |
| `app/static/` | front end: `app.js`, `settings.js`, `modules/`, `fonts/` — plain assets, **no build step** |
| `lib/common.sh` | shell helpers shared by `install.sh` and `uninstall.sh` |
| `tests/` | the validation pipeline (below) |
| `docs/` | this file and its siblings |

The panel runs on **Python 3.12** (`python:3.12-alpine` in the container) and uses the standard library
only — there is no `requirements.txt`, and adding a runtime dependency is a design decision, not a detail.
The front end has no bundler: what is in `app/static/` is what the browser gets.

## Validating a change

`tests/run.sh` is the whole pipeline. Stages are deterministic, each stage's full output goes to
`$LOG_DIR/<stage>.log`, and only the verdict — plus the failing lines — reaches the terminal.

```bash
tests/run.sh quick      # lint + unit + api      — seconds; run after ANY code change
tests/run.sh e2e        # Playwright, headless   — when app/static/ or the HTTP layer changed
tests/run.sh a11y       # axe-core accessibility pass
tests/run.sh compose    # docker compose config on the shipped file
tests/run.sh archive    # the COMMITTED app/ boots and answers every section
tests/run.sh py312      # unit + api on python:3.12-alpine, the interpreter that ships
tests/run.sh all        # quick + compose + e2e + archive + py312
tests/run.sh serve      # keep the panel + fake stack up for a browser
```

Exit codes: `0` pass, `1` fail, `2` usage. Setting `TEST_LEDGER` to a script records each outcome and
may turn a failure into exit `3`.

`tests/fake_stack.py` stands in for Radarr, Sonarr and the rest, so nothing in the suite needs a real
\*arr stack or the network; `tests/harness.py` boots the panel against it. `tests/local.env` is read if
present and is untracked — put this checkout's own settings there.

Two stages have optional dependencies and **say so instead of failing**: `shellcheck` for the shell lint,
and `node` plus `npm ci && npx playwright install chromium` for `e2e`. A skipped stage is not a passing
stage — read the output.

If Chromium is already on the machine but is not the build this Playwright version expects — a
preinstalled browser in a container, say — set `PW_CHROMIUM_PATH` to that binary instead of downloading
another. `playwright.config.mjs` uses it when set and ignores it when empty, so a normal checkout is
unaffected. The startup hook below sets it automatically when it applies.

`archive` is the one that catches "works on my machine": it boots the last **commit** of `app/`, not your
working tree, so an uncommitted file that everything depends on shows up here.

## Lint posture — read before "fixing" style

`ruff.toml` is **correctness-only**, and that is deliberate:

- Style families `E1`/`E2`/`E3`/`E7` are **off**. The codebase intentionally uses one-line defs and
  semicolons, and `line-length` is 200. Do not reformat it, and do not add a formatter.
- `F401` is ignored: the re-export imports in `app/services.py` are used by callers.
- Selected: `F`, `E9`, `B006`, `B008`, `B015`, `B018`, `PLE` — undefined names, bare excepts that swallow
  `SystemExit`, mutable default arguments. Real defects, not taste.

The lint stage also runs `bash -n`, `python3 -m ast.parse`, `node --check` on every ES module, a JSON
parse of `package.json`, and a relative-link check over every Markdown file — so a renamed doc cannot
leave a dangling link behind.

## Repo hygiene

`.gitignore` keeps the repo clean for anyone who clones it. Never commit `.env`, `config/`,
`docker-compose.override.yml`, the install/uninstall logs, `node_modules/`, `test-results/`,
`playwright-report/`, `tests/.harness.json`, `tests/local.env`, `.venv/`, `.ruff_cache/` or `__pycache__/`.

`tests/` and `playwright.config.mjs` **are** committed; their outputs and dependencies are not.

### AI assistant files

`CLAUDE.md` and personal assistant configuration stay **out** of this repo — they are per-developer, and
this doc is the shared, public version of anything an assistant would need to know. Two exceptions are
tracked on purpose, because they are project plumbing rather than anyone's private notes:

- `.claude/settings.json` — registers the startup hook below.
- `.claude/hooks/session-start.sh` — installs `shellcheck` and the Playwright dependencies so a fresh
  or remote checkout can run `tests/run.sh quick` and `e2e` without hand-setup.
- `.claude/mcp/mcp-browser.sh` — the launcher the browser MCP servers run through (below).

Everything else under `.claude/` is ignored. If you keep private instructions, point them at this file
rather than duplicating it — a convention recorded in two places is a convention that will disagree.

### MCP servers

`.mcp.json` at the repo root declares the MCP servers this project uses. It is **committed**, so the
same set is available in a local checkout and in an ephemeral remote container — a server registered
only on your own machine does not survive a fresh clone, which is the usual reason one goes missing.

| Server | What it is for |
|---|---|
| `playwright` | drive a browser: navigate, click, snapshot the panel while it runs |
| `chrome-devtools` | the DevTools protocol: network, console, traces, performance |
| `context7` | fetches upstream library documentation on demand |

Both browser servers run through `.claude/mcp/mcp-browser.sh` rather than being invoked directly,
because a container and a laptop need different flags and the config has to suit both. The launcher
**detects** each condition instead of assuming it: it pins `--executable-path` to the Chromium under
`PLAYWRIGHT_BROWSERS_PATH` when that is set (images that preinstall a browser forbid downloading
another), adds `--headless` when there is no `DISPLAY`, and drops the Chrome sandbox when running as
`uid 0`. On a normal developer machine none of those apply and the server is launched untouched, headed
and using your own Chrome — which is what you want when you are watching it work.

Versions are pinned in `.mcp.json`. Bump them deliberately; `npx -y <pkg>@latest` in a config file means
a tool can change under you between two runs of the same checkout.

Two environment limits worth knowing before you debug a server that "does not work":

- **`context7` needs egress to `context7.com`.** A restricted network — the remote container's egress
  policy, for one — refuses that host, and the server's own error is a bare `TypeError: fetch failed`,
  which reads like a bug and is not one. It works from a normal machine. `CONTEXT7_API_KEY` is optional
  and only raises the rate limit; the config passes it through when it is set.
- **`docker` has no MCP server here on purpose.** Docker's own MCP integration is part of Docker
  Desktop's toolkit and needs a reachable daemon; the remote container ships the `docker` client with no
  daemon behind it, so the server would start and then fail every call. The npm packages under similar
  names are unaffiliated third-party ones. Use the `docker` CLI directly where a daemon exists.

There is no MCP server for `ruff`, and nothing is missing for the want of one: Astral ships a language
server, not an MCP server, and `tests/run.sh quick` already runs `ruff check` over `app` and `tests`
with this repo's config. Lint through the pipeline, the same way CI does.
