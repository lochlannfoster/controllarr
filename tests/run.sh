#!/usr/bin/env bash
# The validation pipeline for Controllarr. One command, deterministic
# stages, concise output: each stage's full log goes to $LOG_DIR/<stage>.log and only the verdict —
# plus the failing lines on a failure — reaches the terminal.
#
#   tests/run.sh quick        lint + unit + api                       (seconds; after any code change)
#   tests/run.sh e2e          browser tests (Playwright, headless)     (when app/static/ or the HTTP layer changed)
#   tests/run.sh a11y         axe-core accessibility pass only
#   tests/run.sh compose      docker compose config on the shipped file
#   tests/run.sh archive      the COMMITTED app/ boots and answers
#   tests/run.sh py312        unit + api under python:3.12-alpine — the interpreter the panel runs on
#   tests/run.sh all          quick + compose + e2e + archive + py312 (py312 skipped without docker)
#   tests/run.sh serve        keep the panel + fake stack up for a browser / the Playwright MCP
#   tests/run.sh lint|unit|api  one stage
#
# Exit codes: 0 pass, 1 fail, 2 usage. Set TEST_LEDGER to a script — directly, or in an untracked
# tests/local.env — and each run's outcome is recorded through it; it may turn a fail into exit 3.
set -u
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$R/tests"
STATE_BASE="${XDG_STATE_HOME:-$HOME/.local/state}/mediastack-tests"
LOG_DIR="$STATE_BASE/logs"; mkdir -p "$LOG_DIR"
export PYTHONWARNINGS=ignore::ResourceWarning PYTHONDONTWRITEBYTECODE=1
cd /tmp || exit 2   # never run python from the repo root (never the repo root: a scratch module must not shadow the stdlib)

failed=(); fingerprint=()
t_start() { _t0=$(date +%s); }
t_end() { echo $(( $(date +%s) - _t0 )); }

# run <stage> <command...>: full output to the log, verdict to the terminal, failing lines on failure.
run() {
  local stage="$1"; shift; local log="$LOG_DIR/$stage.log"; t_start
  "$@" > "$log" 2>&1; local rc=$?
  if [ $rc -eq 0 ]; then printf '  %-8s PASS  (%ss)\n' "$stage" "$(t_end)"; return 0; fi
  printf '  %-8s FAIL  rc=%s (%ss)  log: %s\n' "$stage" "$rc" "$(t_end)" "$log"
  failed+=("$stage")
  # what failed, concisely: unittest ids, playwright titles, or the last lines
  local ids
  ids=$(grep -E '^(FAIL|ERROR): ' "$log" | sed 's/^[A-Z]*: //' | sort -u)
  [ -z "$ids" ] && ids=$(grep -E '^\s+[0-9]+\) \[' "$log" | sed -E 's/^\s+[0-9]+\) //' | sort -u)
  if [ -n "$ids" ]; then
    printf '%s\n' "$ids" | sed 's/^/           /' | head -20
    fingerprint+=("$stage:$(printf '%s' "$ids" | sha1sum | cut -c1-12)")
  else
    fingerprint+=("$stage:rc$rc")
  fi
  echo "           ---- $log (tail) ----"
  grep -vE '^\s*$' "$log" | grep -vE 'ResourceWarning|tracemalloc' | tail -${TAIL:-25} | cut -c1-220 | sed 's/^/           /'
  return $rc
}

# ---------------------------------------------------------------- stages
stage_lint() {
  local rc=0
  echo "== bash -n"; bash -n "$R"/install.sh "$R"/uninstall.sh "$R"/lib/*.sh "$T"/run.sh || rc=1
  if command -v shellcheck >/dev/null; then echo "== shellcheck"; shellcheck -S warning "$R"/install.sh "$R"/uninstall.sh "$R"/lib/*.sh || rc=1
  else echo "== shellcheck: not installed (apt install shellcheck) — skipped"; fi
  echo "== ast.parse"; python3 -I -c 'import ast,sys
for p in sys.argv[1:]:
    ast.parse(open(p, encoding="utf-8").read(), p)' "$R"/app/*.py "$T"/*.py "$T"/lib/*.py "$T"/unit/*.py "$T"/api/*.py || rc=1
  local ruff=""
  if command -v ruff >/dev/null; then ruff="ruff"; elif command -v uvx >/dev/null; then ruff="uvx ruff"; elif command -v pipx >/dev/null; then ruff="pipx run ruff"; fi
  if [ -n "$ruff" ]; then echo "== $ruff check"; (cd "$R" && $ruff check --quiet --config ruff.toml app tests) || rc=1
  else echo "== ruff: not installed (apt install ruff, or uvx/pipx) — skipped"; fi
  echo "== node --check (ES modules through stdin)"
  for f in "$R"/app/static/app.js "$R"/app/static/settings.js "$R"/app/static/modules/*.js "$R"/playwright.config.mjs "$T"/e2e/*.mjs; do
    node --input-type=module --check < "$f" || { echo "$f"; rc=1; }
  done
  echo "== markdown links"; for f in "$R"/README.md "$R"/docs/*.md "$R"/docs/services/*.md; do [ -f "$f" ] && { python3 -I "$T/lib/md-links.py" "$f" || rc=1; }; done
  echo "== json"; python3 -I -c 'import json,sys; json.load(open(sys.argv[1]))' "$R/package.json" || rc=1
  return $rc
}
stage_unit() { (cd "$T" && python3 -I -m unittest discover -s unit -t . -v 2>&1); }
stage_api()  { (cd "$T" && python3 -I -m unittest discover -s api -t . -v 2>&1); }
stage_compose() {
  # the shipped file must be valid on its own: install.sh only ever writes an override on top of it
  (cd "$R" && CONTROLLARR_DATA=./config docker compose --env-file .env.example -f docker-compose.yml config -q) && echo "compose: docker-compose.yml is valid"
}
stage_e2e()  { need_node || return 1; (cd "$R" && npx playwright test "$@"); }
stage_a11y() { stage_e2e tests/e2e/a11y.spec.mjs; }
stage_archive() {
  # the committed app/ only (git archive never includes uncommitted changes), booted the way the container
  # boots it: python3 <dir>/controllarr.py with that directory on sys.path.
  local tmp; tmp=$(mktemp -d /tmp/ca-archive-XXXXXX)
  (cd "$R" && git archive HEAD app | tar -x -C "$tmp") || return 1
  if [ -n "$(cd "$R" && git status --porcelain -- app)" ]; then echo "note: app/ has uncommitted changes — the archive under test is the last COMMIT, not the working tree"; fi
  (cd "$T" && python3 -I -c '
import sys; sys.path.insert(0, ".")
import harness
with harness.Harness(app_dir=sys.argv[1]) as h:
    c = h.admin_cookie()
    for p in ("/", "/api/attention", "/api/live", "/api/system", "/api/reference", "/api/board", "/settings"):
        st = h.get(p, c)[0]; assert st == 200, (p, st)
    st, hd, raw = h.get("/static/app.js?v=1"); assert st == 200 and b"?v=" in raw
    print("archive: the committed app/ boots and answers every section")
' "$tmp/app")
  local rc=$?; rm -rf "$tmp"; return $rc
}
stage_py312() {
  command -v docker >/dev/null || { echo "docker not available — py312 skipped"; return 0; }
  docker run --rm -v "$R":/repo:ro -w /repo/tests -e PYTHONWARNINGS=ignore::ResourceWarning -e PYTHONDONTWRITEBYTECODE=1 python:3.12-alpine \
    sh -c 'python3 -I -m unittest discover -s unit -t . -v 2>&1 && python3 -I -m unittest discover -s api -t . -v 2>&1'
}
need_node() {
  command -v node >/dev/null && command -v npx >/dev/null || { echo "node/npm missing: sudo apt install nodejs npm"; return 1; }
  [ -d "$R/node_modules/@playwright/test" ] || { echo "Playwright not installed: (cd $R && npm ci && npx playwright install --with-deps chromium)"; return 1; }
}

# ---------------------------------------------------------------- driver
usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
[ $# -ge 1 ] || usage
cmd="$1"; shift
case "$cmd" in
  serve)      exec python3 -I "$T/harness.py" serve "$@" ;;
  lint|unit|api|compose|e2e|a11y|archive|py312) stages=("$cmd") ;;
  quick)      stages=(lint unit api) ;;
  all)        stages=(lint unit api compose e2e archive py312) ;;
  *) usage ;;
esac
echo "validation: ${stages[*]}   (logs: $LOG_DIR)"
for s in "${stages[@]}"; do
  case "$s" in
    e2e) run e2e stage_e2e "$@" ;;
    *)   run "$s" "stage_$s" ;;
  esac
done
[ -f "$T/local.env" ] && . "$T/local.env"   # optional, untracked: this checkout's own settings
LEDGER="${TEST_LEDGER:-}"
if [ ${#failed[@]} -eq 0 ]; then
  echo "RESULT: PASS (${stages[*]})"
  [ -n "$LEDGER" ] && [ -f "$LEDGER" ] && python3 -I "$LEDGER" record --status pass; exit 0
fi
echo "RESULT: FAIL — ${failed[*]}"
if [ -n "$LEDGER" ] && [ -f "$LEDGER" ]; then python3 -I "$LEDGER" record --status fail --fingerprint "${fingerprint[*]}"; rc=$?; [ $rc -eq 3 ] && exit 3; fi
exit 1
