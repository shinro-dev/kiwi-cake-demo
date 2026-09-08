#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/demo-stop.sh: stop the resident the runbook-correct way. Stops the
# segment 2 user unit if it exists, and any resident of this checkout's runs
# that is still in the process table (an interrupted segment 1). Never
# signals the child directly: the resident's own shutdown quiesces it.
set -uo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

UNIT="kiwi-cake-demo.service"
STOPPED=0
if command -v systemctl >/dev/null 2>&1 && systemctl --user list-unit-files "$UNIT" >/dev/null 2>&1; then
  if systemctl --user is-active --quiet "$UNIT"; then
    systemctl --user stop "$UNIT" && { kc_say "stopped $UNIT"; STOPPED=$((STOPPED + 1)); }
  else
    kc_say "$UNIT is installed and not active"
  fi
fi

# Residents started by this checkout's runs carry a configuration path under
# state/runs/ on their command line.
for entry in /proc/[0-9]*; do
  pid="${entry#/proc/}"
  is_resident=0; ours=0
  while IFS= read -r -d '' arg; do
    case "$arg" in
      */cake-resident) is_resident=1 ;;
      "$KC_STATE"/runs/* | "$KC_STATE"/segment2/*) ours=1 ;;
    esac
  done 2>/dev/null <"$entry/cmdline"
  if [ "$is_resident" -eq 1 ] && [ "$ours" -eq 1 ]; then
    kill -TERM "$pid" 2>/dev/null && { kc_say "sent SIGTERM to resident $pid"; STOPPED=$((STOPPED + 1)); }
  fi
done
sleep 0.5
LEFT=0
for entry in /proc/[0-9]*; do
  pid="${entry#/proc/}"
  while IFS= read -r -d '' arg; do
    case "$arg" in kiwi-cake-child-*) LEFT=$((LEFT + 1)); break ;; esac
  done 2>/dev/null <"$entry/cmdline"
done
[ "$LEFT" -eq 0 ] || kc_warn "$LEFT supervised child process(es) still in the process table; they end with their resident's shutdown"
kc_say "done ($STOPPED stop action(s))"
