#!/usr/bin/env bash
# Controllarr uninstaller — removes the container. Your *arr stack is never touched.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGFILE="$HERE/uninstall-$(date +%Y%m%d-%H%M%S).log"
source "$HERE/lib/common.sh"
[ "${1:-}" = "--help" ] && { echo "Removes the Controllarr container and, if you say so, its state directory. Nothing else on this machine changes."; exit 0; }

echo "Controllarr uninstaller — full log: $LOGFILE"
[ -f "$HERE/.env" ] || die "No .env here — run this from the directory you installed from."
# shellcheck disable=SC1090
set -a; . "$HERE/.env"; set +a

printf "This removes the Controllarr container. Your Radarr, Sonarr, Jellyfin and the rest are untouched.\n"
printf "Type 'yes' to continue: "; read -r ans </dev/tty
[ "$ans" = "yes" ] || { echo "aborted."; exit 0; }

section "Stopping"
step "docker compose down"
( cd "$HERE" && run "compose down" docker compose --env-file "$HERE/.env" down --remove-orphans )
step_ok "Container removed"

if yesno "Also delete Controllarr's state (${CONTROLLARR_DATA:-./config}: accounts, sessions, settings, poster cache)?" n; then
  rm -rf "${CONTROLLARR_DATA:?}" && info "Removed ${CONTROLLARR_DATA}."
else
  info "Kept ${CONTROLLARR_DATA:-./config} — a later ./install.sh picks it up again."
fi
rm -f "$HERE/docker-compose.override.yml"
section "Done"
echo "Controllarr removed. Full log: $LOGFILE"
