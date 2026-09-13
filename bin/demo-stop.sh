#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/demo-stop.sh: stop the resident the runbook-correct way and prove the
# stop. Stops the segment 2 user unit if it exists and waits for systemd to
# report it inactive; sends SIGTERM to any resident of this checkout's runs
# that is still in the process table (an interrupted segment 1) and waits for
# it to leave; then scans for anything left (a stub carrying a run tag, the
# host named by run-child.sh, the unit's cgroup, a resident) and exits 1 if
# any is found. Never signals the child directly: the resident's own shutdown
# quiesces it. Exit 0 only when nothing is left.
set -uo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

UNIT="kiwi-cake-demo.service"
DEADLINE="${KC_STOP_DEADLINE:-75}"   # above the unit's TimeoutStopSec=60
STOPPED=0
case "${1:-}" in
  "") ;;
  -h | --help)
    echo "usage: bin/demo-stop.sh"
    echo "Stops the kiwi-cake-demo user unit and any resident of this checkout, then proves nothing is left."
    echo "KC_STOP_DEADLINE: seconds to wait for the unit to report inactive (default 75)."
    exit 0 ;;
  *) kc_fail "unknown argument '$1'; this script takes none (-h for help)" 5 ;;
esac
case "$DEADLINE" in "" | *[!0-9]*) kc_fail "KC_STOP_DEADLINE must be a whole number of seconds, got '$DEADLINE'" 5 ;; esac

motor_note() { kc_say "motor state is your host's own disconnect behaviour and is not visible to this script; check the robot"; }
stop_fail() { motor_note; kc_fail "$1" 1; }
have_unit() { command -v systemctl >/dev/null 2>&1 && systemctl --user list-unit-files "$UNIT" >/dev/null 2>&1; }
unit_state() { systemctl --user is-active "$UNIT" 2>/dev/null | head -n 1; }

pids_matching() {
  # pids_matching GLOB: pids (never this script's) with one whole argument matching GLOB.
  local glob="$1" entry pid arg hit out=""
  for entry in /proc/[0-9]*; do
    pid="${entry#/proc/}"
    [ "$pid" = "$$" ] && continue
    hit=0
    while IFS= read -r -d '' arg; do
      # shellcheck disable=SC2254
      case "$arg" in $glob) hit=1; break ;; esac
    done 2>/dev/null <"$entry/cmdline"
    [ "$hit" -eq 1 ] && out="$out $pid"
  done
  printf '%s' "${out# }"
}
resident_pids() {
  # A resident of this checkout names */cake-resident and a configuration under this state directory.
  local entry pid arg res ours out=""
  for entry in /proc/[0-9]*; do
    pid="${entry#/proc/}"; res=0; ours=0
    while IFS= read -r -d '' arg; do
      case "$arg" in
        */cake-resident) res=1 ;;
        "$KC_STATE"/runs/* | "$KC_STATE"/segment2/*) ours=1 ;;
      esac
    done 2>/dev/null <"$entry/cmdline"
    [ "$res" -eq 1 ] && [ "$ours" -eq 1 ] && out="$out $pid"
  done
  printf '%s' "${out# }"
}
host_entry() {
  # The filled @@HOST_ENTRY@@ of the operator's run-child.sh: the third word
  # (exec, interpreter, entry) of its first non-comment line starting with 'exec '.
  # The live host never carries the run tag, so this path is how it is found.
  local f="$KC_SCRIPTS_DIR/run-child.sh" line entry=""
  [ -f "$f" ] || return 0
  line="$(grep -vE '^[[:space:]]*#' "$f" | grep -E '^[[:space:]]*exec ' | head -n 1)"
  [ -n "$line" ] && read -r _ _ entry _ <<<"$line"
  case "$entry" in /*) printf '%s' "$entry" ;; esac
}
cgroup_pids() {
  [ -n "$UNIT_CGROUP" ] && [ -r "/sys/fs/cgroup$UNIT_CGROUP/cgroup.procs" ] || return 0
  tr '\n' ' ' <"/sys/fs/cgroup$UNIT_CGROUP/cgroup.procs"
}
leftovers() {
  local pids p
  pids="$(pids_matching 'kiwi-cake-child-*') $(cgroup_pids) $(resident_pids)"
  [ -n "$HOST_ENTRY" ] && pids="$pids $(pids_matching "$HOST_ENTRY")"
  for p in $pids; do [ -d "/proc/$p" ] && echo "$p"; done | sort -un | paste -sd ' '
}

# --- 1. the segment 2 user unit -------------------------------------------------------------
UNIT_CGROUP=""
if have_unit; then
  UNIT_CGROUP="$(systemctl --user show -p ControlGroup --value "$UNIT" 2>/dev/null | head -n 1)"
  STATE="$(unit_state)"
  case "$STATE" in
    active | activating | deactivating)
      # systemctl stop blocks for the unit's own stop job; the poll below is the
      # bound on top of it, so 'stopped' is only ever printed for a unit systemd
      # itself reports inactive or failed.
      systemctl --user stop "$UNIT" || stop_fail "systemctl --user stop $UNIT failed"
      SETTLED=0
      for _ in $(seq 1 $((DEADLINE * 20))); do
        STATE="$(unit_state)"
        case "$STATE" in inactive | failed) SETTLED=1; break ;; esac
        sleep 0.05
      done
      [ "$SETTLED" -eq 1 ] || stop_fail "$UNIT is still $STATE after $DEADLINE s"
      [ "$STATE" = "failed" ] && kc_warn "$UNIT ended in the failed state: systemd ended it by force after its stop timeout, so the host may not have run its disconnect (docs/stopping-and-cleanup.md)"
      kc_say "stopped $UNIT"; STOPPED=$((STOPPED + 1)) ;;
    *) kc_say "$UNIT is installed and not active" ;;
  esac
fi

# --- 2. residents of this checkout outside the unit (an interrupted segment 1) ---------------
# SIGTERM only, never SIGKILL: a resident ended by force does not quiesce its
# child, and a child left behind is exactly what this script exists to prevent.
for pid in $(resident_pids); do
  kill -TERM "$pid" 2>/dev/null || stop_fail "cannot send SIGTERM to resident $pid"
  kc_say "sent SIGTERM to resident $pid"
  kc_wait_gone "$pid" 600 || stop_fail "resident $pid is still in the process table after SIGTERM"
  kc_say "resident $pid ended"; STOPPED=$((STOPPED + 1))
done

# --- 3. what is left -------------------------------------------------------------------------
HOST_ENTRY="$(host_entry)"
LEFT=""
for _ in $(seq 1 100); do LEFT="$(leftovers)"; [ -z "$LEFT" ] && break; sleep 0.05; done
SOCKS_LEFT=0
for s in "$KC_RUNTIME"/admin.sock "$KC_RUNTIME"/admin-*.sock; do
  [ -S "$s" ] || continue
  # admin.sock is shared by every checkout of this user, so a stale file is
  # removed only when no cake-resident process at all is running.
  if [ -z "$(pids_matching '*/cake-resident')" ]; then
    rm -f -- "$s" && kc_say "removed the stale socket file $s (no cake-resident process remains)"
  else
    kc_say "left the socket file $s alone: a cake-resident process is still running"; SOCKS_LEFT=$((SOCKS_LEFT + 1))
  fi
done
if [ -n "$LEFT" ]; then
  N=0
  for p in $LEFT; do echo "still alive: $p $(tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null)"; N=$((N + 1)); done
  stop_fail "$N process(es) still in the process table after the stop"
fi
[ "$SOCKS_LEFT" -eq 0 ] || stop_fail "$SOCKS_LEFT socket file(s) still present after the stop"
motor_note
kc_say "done ($STOPPED stop action(s)): no resident, no supervised child, no socket"
