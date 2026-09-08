#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/telemetry.sh: the telemetry poll. Prints a live table of the resident's
# state over the admin socket using only the served read operations
# get-status, list-slots and read-flight, plus get-health when the resident
# serves it: build identity, session, Plan digest, epoch, every slot with its
# state and generation, the newest flight records decoded by name from
# docs/event-codes.md, and health rows when answered. A resident that does not
# serve get-health is reported as "health surface not served", never as a
# failure.
#
# The socket is a Unix socket, which ssh cannot reach directly, so every query
# goes through `admin-probe request`, either on this host or wrapped in one
# ssh call per query with --remote USER@HOST (then --probe and --socket name
# paths on that host).
#
# No timing figure is printed anywhere. --interval-seconds and --rounds pace
# a terminal; they are never printed as part of a table.
#
# Usage:
#   bin/telemetry.sh [--probe PATH] [--socket PATH] [--registry PATH]
#                    [--remote USER@HOST] [--ssh-opts "OPT OPT"]
#                    [--rounds N] [--interval-seconds N] [--flight-rows N]
# Exit 0 when every round answered the three required queries with
# `status ok`; exit 1 otherwise.
set -uo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

fail() { echo "telemetry: FAIL: $1" >&2; exit 1; }

PROBE=""
SOCKET=""
REGISTRY=""
REMOTE=""
SSH_OPTS_STR="-o BatchMode=yes -o ConnectTimeout=10"
ROUNDS=1
INTERVAL_SECONDS=5
FLIGHT_ROWS=10
while [ $# -gt 0 ]; do
  case "$1" in
    --probe) PROBE="${2:?--probe needs a path}"; shift 2 ;;
    --socket) SOCKET="${2:?--socket needs a path}"; shift 2 ;;
    --registry) REGISTRY="${2:?--registry needs a path}"; shift 2 ;;
    --remote) REMOTE="${2:?--remote needs USER@HOST}"; shift 2 ;;
    --ssh-opts) SSH_OPTS_STR="${2:?--ssh-opts needs a value}"; shift 2 ;;
    --rounds) ROUNDS="${2:?--rounds needs a number}"; shift 2 ;;
    --interval-seconds) INTERVAL_SECONDS="${2:?--interval-seconds needs a number}"; shift 2 ;;
    --flight-rows) FLIGHT_ROWS="${2:?--flight-rows needs a number}"; shift 2 ;;
    -h | --help) sed -n '3,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fail "unrecognised argument '$1'" ;;
  esac
done
if [ -z "$PROBE" ] && [ -z "$REMOTE" ]; then
  PROBE="$(kc_binaries_dir 2>/dev/null)/admin-probe" || fail "--probe is required when no release is installed"
fi
[ -n "$PROBE" ] || fail "--probe is required with --remote"
[ -n "$SOCKET" ] || SOCKET="$KC_RUNTIME/admin.sock"
[ -n "$REGISTRY" ] || REGISTRY="$KC_ROOT/docs/event-codes.md"
case "$ROUNDS" in '' | *[!0-9]*) fail "--rounds must be a non-negative integer" ;; esac
case "$INTERVAL_SECONDS" in '' | *[!0-9]*) fail "--interval-seconds must be a non-negative integer" ;; esac
case "$FLIGHT_ROWS" in '' | *[!0-9]*) fail "--flight-rows must be a non-negative integer" ;; esac
[ -f "$REGISTRY" ] || fail "--registry $REGISTRY does not exist"

TMPFILES=""
cleanup() { local f; for f in $TMPFILES; do rm -f -- "$f"; done; }
trap cleanup EXIT

run_query() {
  local op="$1"
  if [ -n "$REMOTE" ]; then
    # shellcheck disable=SC2086
    ssh $SSH_OPTS_STR "$REMOTE" "$PROBE" request "$SOCKET" "$op"
  else
    "$PROBE" request "$SOCKET" "$op"
  fi
}

decode_event() {
  # decode_event HEX REGISTRY: the name in the `| Code | Name | Meaning |` table, or UNKNOWN_0x<hex>.
  local hexval="$1" reg="$2" lower name
  lower="0x$(printf '%s' "$hexval" | tr 'A-Z' 'a-z')"
  name="$(awk -F'|' -v want="$lower" '
    NF >= 4 {
      code=$2; gsub(/[ `]/, "", code); code=tolower(code)
      if (code == want) { nm=$3; gsub(/[ `]/, "", nm); print nm; found=1; exit }
    }
    END { if (!found) exit 1 }' "$reg")"
  if [ -n "$name" ]; then printf '%s' "$name"; else printf 'UNKNOWN_%s' "$lower"; fi
}

sed_field() { printf '%s\n' "$1" | sed -nE "s/^$2 (.*)\$/\\1/p" | head -n 1; }
status_of() { sed_field "$1" 'status'; }

print_table() {
  local round="$1" answered=0 required=3 status
  echo "=== poll $round/$ROUNDS ==="

  local status_out
  status_out="$(run_query get-status)" || status_out=""
  status="$(status_of "$status_out")"
  if [ "$status" = "ok" ]; then
    answered=$((answered + 1))
    echo "build_identity $(sed_field "$status_out" 'build_identity')"
    echo "session_uuid $(sed_field "$status_out" 'session_uuid')"
    echo "plan_digest $(sed_field "$status_out" 'plan_digest')"
    echo "epoch $(sed_field "$status_out" 'epoch')"
    echo "config_identity $(sed_field "$status_out" 'config_identity')"
  else
    echo "get_status_status ${status:-no_response}"
  fi

  local slot_names_file
  slot_names_file="$(mktemp)" || fail "cannot create a scratch file"
  TMPFILES="$TMPFILES $slot_names_file"
  local slots_out
  slots_out="$(run_query list-slots)" || slots_out=""
  status="$(status_of "$slots_out")"
  if [ "$status" = "ok" ]; then
    answered=$((answered + 1))
    local slot_lines
    slot_lines="$(printf '%s\n' "$slots_out" | grep -E '^slot ')"
    if [ -n "$slot_lines" ]; then
      while IFS= read -r line; do
        local name state generation uuid
        name="$(printf '%s\n' "$line" | sed -nE 's/.* name=([^ ]+) .*/\1/p')"
        state="$(printf '%s\n' "$line" | sed -nE 's/.* state=([^ ]+) .*/\1/p')"
        generation="$(printf '%s\n' "$line" | sed -nE 's/.* generation=([0-9]+) .*/\1/p')"
        uuid="$(printf '%s\n' "$line" | sed -nE 's/.* uuid=([0-9a-fA-F]+)$/\1/p')"
        echo "slot name=$name state=$state generation=$generation"
        [ -n "$uuid" ] && printf '%s %s\n' "$uuid" "$name" >>"$slot_names_file"
      done <<<"$slot_lines"
    fi
  else
    echo "list_slots_status ${status:-no_response}"
  fi

  local flight_out
  flight_out="$(run_query read-flight)" || flight_out=""
  status="$(status_of "$flight_out")"
  if [ "$status" = "ok" ]; then
    answered=$((answered + 1))
    echo "flight_records $(sed_field "$flight_out" 'flight_records')"
    printf '%s\n' "$flight_out" | grep -E '^record ' | tail -n "$FLIGHT_ROWS" |
      while IFS= read -r rec; do
        local arg0 arg1 seq ev name
        arg0="$(printf '%s\n' "$rec" | sed -nE 's/.*arg0=([0-9]+).*/\1/p')"
        arg1="$(printf '%s\n' "$rec" | sed -nE 's/.*arg1=([0-9]+).*/\1/p')"
        seq="$(printf '%s\n' "$rec" | sed -nE 's/.*sequence=([0-9]+).*/\1/p')"
        ev="$(printf '%s\n' "$rec" | sed -nE 's/.*event=0x([0-9a-fA-F]+).*/\1/p')"
        name="$(decode_event "$ev" "$REGISTRY")"
        echo "record event=$name arg0=$arg0 arg1=$arg1 sequence=$seq"
      done
  else
    echo "read_flight_status ${status:-no_response}"
  fi

  local health_out health_status
  health_out="$(run_query get-health)" || health_out=""
  health_status="$(status_of "$health_out")"
  if [ "$health_status" = "ok" ]; then
    echo "health_surface served"
    echo "health_read_epoch $(sed_field "$health_out" 'health_read_epoch')"
    echo "health_rows $(sed_field "$health_out" 'health_rows')"
    printf '%s\n' "$health_out" | grep -E '^health ' |
      while IFS= read -r row; do
        local slot_uuid surface slot_name state state_name row_status
        slot_uuid="$(printf '%s\n' "$row" | sed -nE 's/.*slot=([0-9a-fA-F]+).*/\1/p')"
        surface="$(printf '%s\n' "$row" | sed -nE 's/.*surface=([a-z]+).*/\1/p')"
        slot_name="$(awk -v want="$slot_uuid" '$1==want{print $2; f=1; exit} END{if(!f) print "slot_" want}' "$slot_names_file")"
        case "$surface" in
          reported)
            state="$(printf '%s\n' "$row" | sed -nE 's/.*state=([0-9]+).*/\1/p')"
            state_name="$(printf '%s\n' "$row" | sed -nE 's/.*state_name=([A-Z]+).*/\1/p')"
            if [ -n "$state_name" ]; then
              echo "health slot=$slot_name surface=reported state=$state_name"
            else
              echo "health slot=$slot_name surface=reported state=$state (unnamed numeral)"
            fi ;;
          none) echo "health slot=$slot_name surface=none" ;;
          unreadable)
            row_status="$(printf '%s\n' "$row" | sed -nE 's/.*status=([0-9]+).*/\1/p')"
            echo "health slot=$slot_name surface=unreadable status=$row_status" ;;
          *) echo "health slot=$slot_name surface=unknown" ;;
        esac
      done
  else
    echo "health_surface not served"
  fi

  echo "poll queries answered $answered of $required"
  [ "$answered" -eq "$required" ]
}

round=0
overall_ok=0
while [ "$round" -lt "$ROUNDS" ]; do
  round=$((round + 1))
  if print_table "$round"; then overall_ok=$((overall_ok + 1)); fi
  if [ "$round" -lt "$ROUNDS" ] && [ "$INTERVAL_SECONDS" -gt 0 ]; then sleep "$INTERVAL_SECONDS"; fi
done
echo "poll tables printed $ROUNDS"
[ "$overall_ok" -eq "$ROUNDS" ] || fail "$((ROUNDS - overall_ok)) of $ROUNDS round(s) did not answer all three required queries"
exit 0
