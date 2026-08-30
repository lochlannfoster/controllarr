#!/usr/bin/env bash
# Shared logging + prompt helpers for the Controllarr installer/uninstaller.
# Design goal: the TERMINAL stays clean (prompts + one line per step); the LOG FILE
# gets everything (commands, API chatter, errors) so an issue can carry it.

# ---- colours (disabled if not a tty) ----
if [ -t 1 ]; then
  C_RESET='\033[0m'; C_DIM='\033[2m'; C_GRN='\033[32m'; C_YEL='\033[33m'; C_RED='\033[31m'; C_BLU='\033[34m'; C_BOLD='\033[1m'; C_CLR='\033[K'
else
  C_RESET=''; C_DIM=''; C_GRN=''; C_YEL=''; C_RED=''; C_BLU=''; C_BOLD=''; C_CLR=''
fi
LAST_RUN_SECS=""   # elapsed seconds of the most recent run(); consumed by step_ok/step_warn/step_fail

# LOGFILE must be set by the caller before sourcing actions; default to cwd.
: "${LOGFILE:=./install-$(date +%Y%m%d-%H%M%S).log}"

_ts() { date '+%Y-%m-%d %H:%M:%S'; }

# log LEVEL MESSAGE  -> always to logfile; INFO/WARN/ERROR also to terminal (short)
log() {
  local level="$1"; shift
  printf '%s [%s] %s\n' "$(_ts)" "$level" "$*" >> "$LOGFILE"
}

# step "Doing thing"  -> terminal shows a live line; log records it
step() {
  STEP_MSG="$*"
  printf "${C_BLU}[ ]${C_RESET} %s" "$STEP_MSG"
  log INFO "STEP: $STEP_MSG"
}
# _elapsed -> " (12s)" if the last run() recorded a duration, else ""; clears the marker
_elapsed() { if [ -n "${LAST_RUN_SECS:-}" ]; then printf " ${C_DIM}(%ss)${C_RESET}" "$LAST_RUN_SECS"; LAST_RUN_SECS=""; fi; }
step_ok()   { printf "\r${C_CLR}${C_GRN}[✓]${C_RESET} %s%b\n" "${1:-$STEP_MSG}" "$(_elapsed)"; log INFO "OK: ${1:-$STEP_MSG}"; }
step_warn() { printf "\r${C_CLR}${C_YEL}[!]${C_RESET} %s%b\n" "${1:-$STEP_MSG}" "$(_elapsed)"; log WARN "WARN: ${1:-$STEP_MSG}"; }
step_fail() { printf "\r${C_CLR}${C_RED}[✗]${C_RESET} %s%b\n" "${1:-$STEP_MSG}" "$(_elapsed)"; log ERROR "FAIL: ${1:-$STEP_MSG}"; }

info()  { printf "${C_DIM}    %s${C_RESET}\n" "$*"; log INFO "$*"; }
warn()  { printf "${C_YEL}    ! %s${C_RESET}\n" "$*"; log WARN "$*"; }
die() {
  step_fail "$STEP_MSG"
  printf "\n${C_RED}${C_BOLD}Error:${C_RESET} %s\n" "$*"
  printf "See the full log: ${C_BOLD}%s${C_RESET}\n" "$LOGFILE"
  printf "${C_DIM}--- last 20 log lines ---${C_RESET}\n"; tail -n 20 "$LOGFILE" 2>/dev/null
  exit 1
}

# run "description" cmd args...   -> runs a command, all output to the log only.
# On a terminal it shows a live spinner + elapsed timer so long steps (image
# pulls, wiring) don't look frozen. Records the duration in LAST_RUN_SECS so the
# following step_ok/step_warn prints "(NNs)". Returns the command's exit code.
run() {
  local desc="$1"; shift
  log DEBUG "RUN: $desc :: $*"
  local start=$SECONDS rc
  if [ -t 1 ]; then
    "$@" >>"$LOGFILE" 2>&1 &
    local pid=$! sp='|/-\' i=0
    while kill -0 "$pid" 2>/dev/null; do
      printf "\r${C_CLR}${C_BLU}[%s]${C_RESET} %s ${C_DIM}(%ds)${C_RESET}" "${sp:i++%4:1}" "${STEP_MSG:-$desc}" "$((SECONDS-start))"
      sleep 0.5
    done
    wait "$pid"; rc=$?
    printf "\r${C_CLR}"   # clear the spinner line; the caller's step_ok prints the final line
  else
    "$@" >>"$LOGFILE" 2>&1; rc=$?
  fi
  LAST_RUN_SECS=$((SECONDS - start))
  if [ "$rc" -eq 0 ]; then log DEBUG "RUN-OK: $desc (${LAST_RUN_SECS}s)"; else log ERROR "RUN-FAIL($rc): $desc :: $*"; fi
  return "$rc"
}

# progress_bar CURRENT TOTAL "label"  -> redraws an in-place [####----] NN% bar (terminal only)
progress_bar() {
  [ -t 1 ] || return 0
  local cur=$1 tot=$2 label="${3:-}" width=22
  [ "$tot" -gt 0 ] || tot=1; [ "$cur" -gt "$tot" ] && cur=$tot
  local fill=$(( cur * width / tot )) pct=$(( cur * 100 / tot )) bar="" i=0
  while [ $i -lt "$width" ]; do [ $i -lt "$fill" ] && bar+="#" || bar+="."; i=$((i+1)); done
  printf "\r${C_CLR}  ${C_BLU}[%s]${C_RESET} %3d%%  ${C_DIM}%s${C_RESET}" "$bar" "$pct" "$label"
}

# ---- prompt helpers (read from /dev/tty so they work even when stdin is a pipe) ----
_tty() { if [ -e /dev/tty ]; then cat >/dev/tty; fi; }

# ask VAR "Question" "default"
ask() {
  local __var="$1" q="$2" def="${3:-}" ans
  if [ -n "$def" ]; then printf "${C_BOLD}%s${C_RESET} [%s]: " "$q" "$def" >/dev/tty
  else printf "${C_BOLD}%s${C_RESET}: " "$q" >/dev/tty; fi
  read -r ans </dev/tty
  ans="${ans:-$def}"
  printf -v "$__var" '%s' "$ans"
  log INFO "PROMPT: $q => ${ans}"
}

# ask_path VAR "Question" default fallback  -> like ask, but the answer must be an absolute path: a stray "y" typed at a
# directory prompt once became BACKUP_DIR=y and an invalid volume specification at compose up. A default that is not
# absolute (a previous run's bad answer) is replaced by the fallback, so it is never offered back.
ask_path() {
  local __var="$1" q="$2" def="$3" fallback="${4:-}" ans
  case "$def" in /*) ;; *) def="$fallback";; esac
  while :; do
    ask "$__var" "$q" "$def"; ans="${!__var}"
    case "$ans" in /*) return 0;; esac
    warn "'$ans' is not an absolute path (it must start with /) — try again"
  done
}

# ask_secret VAR "Question"  (input hidden; if blank, generates a strong random password)
ask_secret() {
  local __var="$1" q="$2" ans
  printf "${C_BOLD}%s${C_RESET} ${C_DIM}(blank = auto-generate)${C_RESET}: " "$q" >/dev/tty
  read -rs ans </dev/tty; printf "\n" >/dev/tty
  if [ -z "$ans" ]; then
    ans="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16)"
    printf "  ${C_DIM}generated a random password${C_RESET}\n" >/dev/tty
  fi
  printf -v "$__var" '%s' "$ans"
  log INFO "PROMPT(secret): $q => ******"
}

# ask_hidden VAR "Question"  (input hidden; blank STAYS blank — for optional secrets like a login password
# or a VPN key where "blank" has a meaning and a random value would be wrong)
ask_hidden() {
  local __var="$1" q="$2" ans
  printf "${C_BOLD}%s${C_RESET}: " "$q" >/dev/tty
  read -rs ans </dev/tty; printf "\n" >/dev/tty
  printf -v "$__var" '%s' "$ans"
  log INFO "PROMPT(hidden): $q => $([ -n "$ans" ] && echo '******' || echo '(blank)')"
}

# yesno "Question" default(y|n)  -> returns 0 for yes, 1 for no
yesno() {
  local q="$1" def="${2:-y}" ans hint
  [ "$def" = "y" ] && hint="[Y/n]" || hint="[y/N]"
  printf "${C_BOLD}%s${C_RESET} %s: " "$q" "$hint" >/dev/tty
  read -r ans </dev/tty; ans="${ans:-$def}"
  log INFO "PROMPT(yesno): $q => $ans"
  case "$ans" in [Yy]*) return 0;; *) return 1;; esac
}

# sq VALUE -> POSIX single-quoted, safe for both `. file` (bash) and python shlex.
sq() { local s=${1//\'/\'\\\'\'}; printf "'%s'" "$s"; }

# section "Title"  -> a visual divider in the terminal + log
section() {
  printf "\n${C_BOLD}${C_BLU}== %s ==${C_RESET}\n" "$*"
  log INFO "==== $* ===="
}
