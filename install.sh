#!/usr/bin/env bash
# Controllarr installer — points the panel at an *arr stack you already run. Interactive, idempotent.
# Usage: ./install.sh          (interactive)
#        ./install.sh --help
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGFILE="$HERE/install-$(date +%Y%m%d-%H%M%S).log"
ENV_FILE="$HERE/.env"

# shellcheck source=lib/common.sh
source "$HERE/lib/common.sh"

[ "${1:-}" = "--help" ] && {
  cat <<'H'
Controllarr installer.
  ./install.sh            Interactive install (asks where your apps are).
  ./install.sh --help     This help.
Nothing about your existing stack is changed: Controllarr only reads and, when you press a
control, calls the app's own API. Everything is logged to install-<timestamp>.log.
Re-running is safe — previous answers are the defaults and API keys are kept.
H
  exit 0
}

echo "Controllarr installer — full log: $LOGFILE"
log INFO "installer started"

# ---------------------------------------------------------------------------
# 1. PREFLIGHT
# ---------------------------------------------------------------------------
section "Preflight checks"
step "Checking Docker"
command -v docker >/dev/null 2>&1 || die "Docker is not installed. Install Docker Engine (https://docs.docker.com/engine/install/), then re-run."
run "docker info" docker info || die "Docker is installed but not running, or you lack permission. Try: sudo systemctl start docker, and add yourself to the 'docker' group."
docker compose version >/dev/null 2>&1 || die "The 'docker compose' plugin is missing. Install docker-compose-plugin, then re-run."
command -v python3 >/dev/null 2>&1 || die "python3 is required (the installer parses your answers with it)."
step_ok "Docker, Compose and python3 are available"

# ---------------------------------------------------------------------------
# 2. PREVIOUS ANSWERS
# ---------------------------------------------------------------------------
declare -A OLD
load_old() { [ -f "$1" ] || return 0
  while IFS=$'\t' read -r k v; do [ -n "$k" ] && OLD["$k"]="$v"; done < <(python3 - "$1" <<'PY'
import sys, shlex
for line in open(sys.argv[1]):
    line = line.rstrip("\n")
    if not line or line.lstrip().startswith("#") or "=" not in line: continue
    k, v = line.split("=", 1)
    try: v = shlex.split(v)[0] if v.strip() else ""
    except Exception: v = v.strip()
    print(k.strip() + "\t" + v.replace("\t", " ").replace("\n", " "))
PY
); }
load_old "$ENV_FILE"
OLD_DATA="${OLD[CONTROLLARR_DATA]:-$HERE/config}"
load_old "$OLD_DATA/controllarr.env"
d()   { printf '%s' "${OLD[$1]:-$2}"; }
dyn() { case "${OLD[$1]:-$2}" in true|y|yes|Y) echo y;; *) echo n;; esac; }
[ ${#OLD[@]} -gt 0 ] && info "Existing configuration found — previous answers are the defaults; API keys and the admin password are kept."

declare -A CFG SEC
set_cfg() { CFG["$1"]="$2"; }
set_sec() { SEC["$1"]="$2"; }

section "Basics"
DEF_IP="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' | head -1)"
ask SERVER_HOST "Address people will use to reach this box (used in the links Controllarr shows)" "$(d SERVER_HOST "${DEF_IP:-localhost}")"; set_sec SERVER_HOST "$SERVER_HOST"
DEF_TZ="$( [ -f /etc/timezone ] && cat /etc/timezone || readlink -f /etc/localtime 2>/dev/null | sed 's#.*/zoneinfo/##' )"
ask TZv "Timezone" "$(d TZ "${DEF_TZ:-Etc/UTC}")"; set_cfg TZ "$TZv"
ask CONTROLLARR_PORT "Port for Controllarr on this host" "$(d CONTROLLARR_PORT 3002)"; set_cfg CONTROLLARR_PORT "$CONTROLLARR_PORT"
ask_path CONTROLLARR_DATA "Where Controllarr keeps its own state (accounts, sessions, settings, poster cache)" "$(d CONTROLLARR_DATA "$HERE/config")" "$HERE/config"
set_cfg CONTROLLARR_DATA "$CONTROLLARR_DATA"
set_cfg CONTROLLARR_REFRESH "$(d CONTROLLARR_REFRESH 15)"
set_cfg STACK_NAME "$(d STACK_NAME controllarr)"

if [ -n "${OLD[CONTROLLARR_PASSWORD]:-}" ]; then
  set_sec CONTROLLARR_PASSWORD "${OLD[CONTROLLARR_PASSWORD]}"; info "Admin password: keeping the existing one"
else
  ask_hidden CONTROLLARR_PW "Admin password (hidden; blank = no login at all — only sane on a trusted LAN)"
  set_sec CONTROLLARR_PASSWORD "$CONTROLLARR_PW"
fi

# ---------------------------------------------------------------------------
# 3. HOW CONTROLLARR REACHES YOUR APPS
# ---------------------------------------------------------------------------
section "How Controllarr reaches your apps"
info "Two ways. 'host' shares this machine's network, so addresses like localhost:7878 work — the usual answer"
info "when your apps publish ports on this box. 'network' joins an existing Docker network and uses container names."
NET_MODE="$(d NET_MODE host)"
while :; do
  ask NET_MODE "Reach your apps over [host] or a Docker [network]?" "$NET_MODE"
  case "$NET_MODE" in host|network) break;; *) warn "Answer 'host' or 'network'.";; esac
done
DOCKER_NET=""
if [ "$NET_MODE" = network ]; then
  info "Existing networks: $(docker network ls --format '{{.Name}}' | grep -v -E '^(bridge|host|none)$' | tr '\n' ' ')"
  while :; do
    ask DOCKER_NET "Docker network to join" "$(d DOCKER_NET '')"
    [ -n "$DOCKER_NET" ] && docker network inspect "$DOCKER_NET" >/dev/null 2>&1 && break
    warn "No such Docker network — pick one of the names above."
  done
  DEF_APP_HOST=""   # container names; no sensible single default
else
  DEF_APP_HOST=localhost
fi
set_cfg NET_MODE "$NET_MODE"; set_cfg DOCKER_NET "$DOCKER_NET"

section "Your apps"
info "For each app: the address Controllarr should use, or blank to say you do not run it."
info "An app you skip is simply absent — its Settings group is hidden and nothing reports it as broken."
SERVICES=()
# ask_service NAME LABEL DEFAULT_PORT NEEDS_KEY
ask_service() {
  local name="$1" label="$2" dport="$3" needs_key="$4"
  local U=$(echo "$name" | tr '[:lower:]' '[:upper:]'); [ "$name" = qbittorrent ] && U=QBIT
  local host_def="${OLD[${U}_HOST]:-$DEF_APP_HOST}"
  [ "$NET_MODE" = network ] && [ -z "${OLD[${U}_HOST]:-}" ] && host_def="$name"
  local h p k
  ask h "  $label host (blank = not installed)" "$host_def"
  if [ -z "$h" ]; then info "  $label: skipped"; return 0; fi
  ask p "  $label port" "${OLD[${U}_PORT]:-$dport}"
  set_sec "${U}_HOST" "$h"; set_sec "${U}_PORT" "$p"
  if [ "$needs_key" = y ]; then
    if [ -n "${OLD[${U}_APIKEY]:-}" ]; then
      set_sec "${U}_APIKEY" "${OLD[${U}_APIKEY]}"; info "  $label API key: keeping the existing one"
    else
      ask_hidden k "  $label API key (its Settings ▸ General ▸ Security page)"
      set_sec "${U}_APIKEY" "$k"
    fi
  fi
  SERVICES+=("$name")
}
ask_service radarr     "Radarr (movies)"          7878 y
ask_service sonarr     "Sonarr (TV)"              8989 y
ask_service bazarr     "Bazarr (subtitles)"       6767 y
ask_service jellyfin   "Jellyfin (player)"        8096 y
ask_service jellyseerr "Jellyseerr (requests)"    5055 y
ask_service prowlarr   "Prowlarr (indexers)"      9696 y
ask_service qbittorrent "qBittorrent (downloads)" 8080 n
ask_service ntfy       "ntfy (phone notifications)" 8090 n
if [ " ${SERVICES[*]} " = "  " ]; then die "No apps configured — Controllarr would have nothing to show."; fi
set_sec SERVICES "$(IFS=,; echo "${SERVICES[*]}")"

# qBittorrent authenticates with a login rather than an API key
case " ${SERVICES[*]} " in *" qbittorrent "*)
  if [ -n "${OLD[QBIT_PASS]:-}" ]; then
    set_sec QBIT_USER "${OLD[QBIT_USER]:-admin}"; set_sec QBIT_PASS "${OLD[QBIT_PASS]}"; info "qBittorrent login: keeping the existing one"
  else
    ask qu "  qBittorrent username" "admin"; ask_hidden qp "  qBittorrent password"
    set_sec QBIT_USER "$qu"; set_sec QBIT_PASS "$qp"
  fi
  # Radarr and Sonarr already hold this per indexer; Controllarr reads the same number back when it explains a stuck title
  ask MIN_SEEDERS "The release threshold your arrs are set to (their own indexer setting; Controllarr reports against it)" "$(d MIN_SEEDERS 5)"; set_sec MIN_SEEDERS "$MIN_SEEDERS"
  ask MAX_ACTIVE_DL_CAP "Ceiling Controllarr enforces on simultaneous downloads" "$(d MAX_ACTIVE_DL_CAP 2)"; set_sec MAX_ACTIVE_DL_CAP "$MAX_ACTIVE_DL_CAP"
;; esac

section "Optional extras"
MEDIA_DIR=""; ask MEDIA_DIR "Media library path, to report its free space (blank = skip)" "$(d MEDIA_DIR '')"
case "$MEDIA_DIR" in ""|/*) ;; *) warn "Not an absolute path — skipped."; MEDIA_DIR="";; esac
[ -n "$MEDIA_DIR" ] && set_sec MEDIA_DIR "$MEDIA_DIR"
ARR_CONFIG=""; ask ARR_CONFIG "Your apps' config directory, so keys are read live instead of stored (blank = use the keys above)" "$(d CONFIG_DIR '')"
case "$ARR_CONFIG" in ""|/*) ;; *) warn "Not an absolute path — skipped."; ARR_CONFIG="";; esac
[ -n "$ARR_CONFIG" ] && set_sec CONFIG_DIR "$ARR_CONFIG"
WITH_SOCK=n
if [ -S /var/run/docker.sock ] && yesno "Mount the Docker socket read-only, so Controllarr can also show container state, memory and last log lines?" "$(dyn WITH_SOCK y)"; then
  WITH_SOCK=y; set_sec DOCKER_SOCK /var/run/docker.sock
else
  set_sec DOCKER_SOCK ""
fi
set_cfg WITH_SOCK "$WITH_SOCK"

# ---------------------------------------------------------------------------
# 4. WRITE CONFIG
# ---------------------------------------------------------------------------
section "Writing configuration"
step "Writing .env and controllarr.env"
mkdir -p "$CONTROLLARR_DATA" || die "Cannot create $CONTROLLARR_DATA"
{ echo "# generated by install.sh $(date) — re-run ./install.sh to change; previous values are the defaults"
  for k in $(printf '%s\n' "${!CFG[@]}" | sort); do printf '%s=%s\n' "$k" "$(sq "${CFG[$k]}")"; done; } > "$ENV_FILE"
# The keys are written 600 and never anything else, including for the moment between creating the file and
# chmod'ing it: create it empty at the right mode first, then fill it. Re-running the installer therefore also
# repairs an older install whose config was left readable — the panel refuses to start on one (docs/CONFIGURATION.md).
for f in "$CONTROLLARR_DATA/controllarr.env" "$CONTROLLARR_DATA/settings.local"; do
  [ -e "$f" ] || : > "$f"
  chmod 600 "$f"
done
{ echo "# Controllarr — addresses, keys and live knobs. chmod 600, never committed."
  for k in $(printf '%s\n' "${!SEC[@]}" | sort); do printf '%s=%s\n' "$k" "$(sq "${SEC[$k]}")"; done; } > "$CONTROLLARR_DATA/controllarr.env"
step_ok "Wrote .env and $CONTROLLARR_DATA/controllarr.env (chmod 600)"

step "Writing docker-compose.override.yml"
{
  echo "# generated by install.sh — regenerated on every run; do not edit by hand"
  echo "services:"
  echo "  controllarr:"
  if [ "$NET_MODE" = host ]; then
    # host networking: the panel reaches localhost:<port> apps, and binds its own port directly
    printf '    network_mode: host\n    ports: !override []\n'
    printf '    environment:\n      CONTROLLARR_PORT: "%s"\n' "$CONTROLLARR_PORT"
  else
    printf '    networks: [appnet]\n'
  fi
  vols=()
  [ "$WITH_SOCK" = y ] && vols+=("/var/run/docker.sock:/var/run/docker.sock:ro")
  [ -n "$MEDIA_DIR" ] && vols+=("$MEDIA_DIR:$MEDIA_DIR:ro")
  [ -n "$ARR_CONFIG" ] && vols+=("$ARR_CONFIG:$ARR_CONFIG:ro")
  if [ ${#vols[@]} -gt 0 ]; then
    echo "    volumes:"
    for v in "${vols[@]}"; do echo "      - \"$v\""; done
  fi
  if [ "$NET_MODE" = network ]; then
    printf 'networks:\n  appnet:\n    name: %s\n    external: true\n' "$DOCKER_NET"
  fi
} > "$HERE/docker-compose.override.yml"
step_ok "Wrote docker-compose.override.yml"

# ---------------------------------------------------------------------------
# 5. START
# ---------------------------------------------------------------------------
section "Starting Controllarr"
step "docker compose up -d"
( cd "$HERE" && run "compose up" docker compose --env-file "$ENV_FILE" up -d --remove-orphans ) || die "docker compose failed to start Controllarr."
step_ok "Container started"

step "Waiting for it to answer"
URL="http://localhost:$CONTROLLARR_PORT"
for _ in $(seq 1 40); do
  if curl -fsS -o /dev/null "$URL/health" 2>/dev/null; then OK=y; break; fi
  sleep 1
done
[ "${OK:-n}" = y ] && step_ok "Controllarr is up at $URL" || step_warn "Not answering yet — docker logs controllarr"

section "Done"
cat <<EOT
  Controllarr   http://$SERVER_HOST:$CONTROLLARR_PORT
  Apps          ${SERVICES[*]}
  State         $CONTROLLARR_DATA   (accounts, sessions, settings, poster cache — the one thing to back up)
$( [ -z "${SEC[CONTROLLARR_PASSWORD]:-}" ] && echo "  NOTE: no password was set, so every visitor is an admin. Fine on a trusted LAN, nowhere else." || echo "  Sign in as 'admin' with the password you gave." )

  Re-run ./install.sh any time to change an answer; ./uninstall.sh removes the container.
  Full log: $LOGFILE
EOT
