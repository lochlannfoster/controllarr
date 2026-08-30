#!/usr/bin/env bash
# Launcher for the two browser-driving MCP servers, so one committed config works both on a
# developer's machine and in an ephemeral remote container.
#
# Locally: hand off to the server untouched — your own Chrome/Chromium and your own display are
# what you want, and a headed browser is the point when you are debugging.
# In the remote container: pin to the Chromium the image preinstalls (it forbids downloading
# another), drop the sandbox because the container runs as root, and force headless because
# there is no display. Each condition is detected, not assumed, so neither case is special-cased
# by hostname or by CLAUDE_CODE_REMOTE.
set -uo pipefail

server="${1:-}"; shift || true
[ -n "$server" ] || { echo "usage: mcp-browser.sh playwright|chrome-devtools [extra args]" >&2; exit 2; }

# The image sets PLAYWRIGHT_BROWSERS_PATH; pick the highest-versioned Chromium under it.
chromium=""
if [ -n "${PLAYWRIGHT_BROWSERS_PATH:-}" ]; then
  chromium=$(printf '%s\n' "$PLAYWRIGHT_BROWSERS_PATH"/chromium-*/chrome-linux/chrome | sort -V | tail -1)
  [ -x "$chromium" ] || chromium=""
fi

headless=0
[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && headless=1

root=0
[ "$(id -u)" = "0" ] && root=1

case "$server" in
  playwright)
    set -- npx -y @playwright/mcp@0.0.79 --browser chromium --isolated "$@"
    [ -n "$chromium" ] && set -- "$@" --executable-path "$chromium"
    [ "$headless" = 1 ] && set -- "$@" --headless
    [ "$root" = 1 ] && set -- "$@" --no-sandbox
    ;;
  chrome-devtools)
    set -- npx -y chrome-devtools-mcp@1.8.0 --isolated "$@"
    [ -n "$chromium" ] && set -- "$@" --executablePath "$chromium"
    [ "$headless" = 1 ] && set -- "$@" --headless
    [ "$root" = 1 ] && set -- "$@" --chrome-arg=--no-sandbox --chrome-arg=--disable-setuid-sandbox
    ;;
  *)
    echo "mcp-browser.sh: unknown server '$server' (want playwright or chrome-devtools)" >&2; exit 2 ;;
esac

exec "$@"
