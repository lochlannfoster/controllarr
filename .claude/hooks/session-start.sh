#!/usr/bin/env bash
# SessionStart hook — makes a fresh REMOTE checkout able to run the validation pipeline.
#
# Two stages of tests/run.sh degrade to "skipped" rather than failing when their optional
# dependency is missing, which is easy to mistake for a pass:
#   lint  -> shellcheck   (the shell half of the lint stage is silently not run)
#   e2e   -> node_modules (Playwright is not installed)
# This installs both when they are absent. It is idempotent and non-interactive: on a local
# machine it exits immediately, leaving the developer's own environment untouched.
set -uo pipefail

# Local checkouts are the developer's business — only set up the ephemeral remote container.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

cd "${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}" || exit 0

# Never fail the session start: a missing dependency degrades a stage, it does not break the repo.
if ! command -v shellcheck >/dev/null 2>&1; then
  echo "session-start: installing shellcheck"
  if command -v apt-get >/dev/null 2>&1; then
    (sudo -n apt-get update -qq && sudo -n apt-get install -y -qq shellcheck) >/dev/null 2>&1 \
      || (apt-get update -qq && apt-get install -y -qq shellcheck) >/dev/null 2>&1 \
      || echo "session-start: shellcheck install failed — the shell lint will report itself skipped"
  fi
fi

# Playwright: the browser is preinstalled in this image (PLAYWRIGHT_BROWSERS_PATH), so only the
# node packages are needed. `npm install` (not `ci`) so the result caches with the container.
if [ ! -d node_modules/@playwright/test ] && command -v npm >/dev/null 2>&1; then
  echo "session-start: installing Playwright test packages"
  npm install --no-audit --no-fund >/dev/null 2>&1 \
    || echo "session-start: npm install failed — tests/run.sh e2e will report Playwright missing"
fi

# The image preinstalls Chromium, but its build may not be the one this Playwright version expects
# (it would otherwise try to download, which the image forbids). Point the config at what is here:
# playwright.config.mjs reads PW_CHROMIUM_PATH and ignores it when empty.
if [ -n "${PLAYWRIGHT_BROWSERS_PATH:-}" ]; then
  chromium_bin=$(ls -d "$PLAYWRIGHT_BROWSERS_PATH"/chromium-*/chrome-linux/chrome 2>/dev/null | sort -V | tail -1)
  if [ -n "$chromium_bin" ] && [ -x "$chromium_bin" ] && [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    echo "export PW_CHROMIUM_PATH=$chromium_bin" >> "$CLAUDE_ENV_FILE"
    echo "session-start: Playwright will use $chromium_bin"
  fi
fi

# Orient the session: the conventions live in the repo, not in anyone's private notes.
cat <<'NOTE'
Controllarr: read docs/DEVELOPMENT.md before changing code — it carries the layout, the
validation pipeline and the deliberate lint posture (correctness-only; do NOT reformat).
Validate with `tests/run.sh quick` after any change; add `tests/run.sh e2e` when app/static/
or the HTTP layer moved.
NOTE
exit 0
