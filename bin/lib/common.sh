# shellcheck shell=bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# Shared functions for the kiwi-cake-demo scripts. Sourced by every script in
# bin/ (repository layout) or scripts/ (release tarball layout); never run.

export KC_REPO="shinro-dev/kiwi-cake-demo"
export KC_RELEASE_KEY_FINGERPRINT="5AB8730BB8420601F11965F46E6F9B724BB8EED2"
export KC_SEGMENT2_ACK="ROBOT ON STAND, WHEELS OFF GROUND, ARM PARKED, HAND NEAR POWER"

KC_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KC_SCRIPTS_DIR="$(dirname "$KC_LIB_DIR")"
KC_ROOT="$(dirname "$KC_SCRIPTS_DIR")"
# The release version lives in one place: the VERSION file at the root of
# the checkout or of the tarball. Every script reads it from there.
if [ -r "$KC_ROOT/VERSION" ]; then
  KC_VERSION="$(tr -d '[:space:]' <"$KC_ROOT/VERSION")"
else
  KC_VERSION=""
fi
case "$KC_VERSION" in
  v[0-9]*.[0-9]*.[0-9]*) : ;;
  *) printf 'kiwi-cake: FAIL: %s/VERSION is missing or is not vX.Y.Z\n' "$KC_ROOT" >&2; exit 1 ;;
esac
export KC_VERSION
KC_STATE="${KC_STATE_DIR:-$KC_ROOT/state}"
KC_KEYS_DIR="${KC_KEYS_DIR:-$KC_ROOT/keys}"
KC_RUNTIME="${XDG_RUNTIME_DIR:-/tmp/kiwi-cake-demo-$(id -u)}/kiwi-cake-demo"

kc_say() { printf 'kiwi-cake: %s\n' "$1"; }
kc_warn() { printf 'kiwi-cake: WARNING: %s\n' "$1" >&2; }
kc_fail() { printf 'kiwi-cake: FAIL: %s\n' "$1" >&2; exit "${2:-1}"; }
kc_need_tool() { command -v "$1" >/dev/null 2>&1 || kc_fail "$1 is not on PATH; $2"; }

# Every admin-probe request runs under this many seconds of coreutils timeout.
case "${KC_PROBE_TIMEOUT:-10}" in '' | *[!0-9]*) kc_fail "KC_PROBE_TIMEOUT must be a whole number of seconds" 5 ;; esac

# --- board facts ------------------------------------------------------------

kc_version_ge() {
  # kc_version_ge A B: true when dotted version A is at or above B.
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | head -n 1)" = "$2" ]
}

kc_facts() {
  KC_ARCH="$(uname -m)"
  KC_KERNEL="$(uname -r)"
  KC_MODEL="unknown"
  if [ -r /proc/device-tree/model ]; then
    KC_MODEL="$(tr -d '\0' </proc/device-tree/model)"
  fi
  KC_PAGE_SIZE="$(getconf PAGE_SIZE 2>/dev/null || echo unknown)"
  KC_GLIBC="$(ldd --version 2>/dev/null | head -n 1 | awk '{print $NF}')"
  [ -n "$KC_GLIBC" ] || KC_GLIBC="unknown"
  if [ -r /proc/sys/vm/memfd_noexec_scope ]; then
    KC_MEMFD="$(tr -d '[:space:]' </proc/sys/vm/memfd_noexec_scope)"
  else
    KC_MEMFD="absent"
  fi
  KC_OS="$(sed -nE 's/^PRETTY_NAME="?([^"]*)"?$/\1/p' /etc/os-release 2>/dev/null | head -n 1)"
  export KC_ARCH KC_KERNEL KC_MODEL KC_PAGE_SIZE KC_GLIBC KC_MEMFD KC_OS
}

kc_classify() {
  # Prints one of: pi5-tested, pi5-untested-os, pi4, x86_64, other.
  # The tested class is the embedded record demo-preflight compares against:
  # a Raspberry Pi 5, 16384-byte pages, glibc at or above 2.41.
  [ -n "${KC_ARCH:-}" ] || kc_facts
  case "$KC_ARCH" in
    aarch64)
      case "$KC_MODEL" in
        *"Raspberry Pi 5"*)
          if [ "$KC_PAGE_SIZE" = "16384" ] && [ "$KC_GLIBC" != "unknown" ] && kc_version_ge "$KC_GLIBC" "2.41"; then
            echo pi5-tested
          else
            echo pi5-untested-os
          fi
          ;;
        *"Raspberry Pi 4"*) echo pi4 ;;
        *) echo other ;;
      esac
      ;;
    x86_64) echo x86_64 ;;
    *) echo other ;;
  esac
}

kc_target_slug() {
  # The release tarball slug for this board, or empty when none is published.
  # One tarball is published; a Raspberry Pi 4 uses the Pi 5 bytes under
  # --unsupported-target (docs/targets.md).
  case "$(kc_classify)" in
    pi5-tested | pi5-untested-os | pi4) echo pi5-aarch64 ;;
    *) echo "" ;;
  esac
}

# --- layout -----------------------------------------------------------------

kc_binaries_dir() {
  # Where the four shipped binaries are: KC_RELEASE_DIR, the tarball's own
  # bin/, or release/<target>/bin under the repository.
  if [ -n "${KC_RELEASE_DIR:-}" ]; then
    [ -x "$KC_RELEASE_DIR/bin/cake-resident" ] || kc_fail "KC_RELEASE_DIR=$KC_RELEASE_DIR holds no bin/cake-resident"
    echo "$KC_RELEASE_DIR/bin"
    return
  fi
  if [ -x "$KC_ROOT/bin/cake-resident" ]; then
    echo "$KC_ROOT/bin"
    return
  fi
  local slug
  slug="$(kc_target_slug)"
  if [ -n "$slug" ] && [ -x "$KC_ROOT/release/$slug/bin/cake-resident" ]; then
    echo "$KC_ROOT/release/$slug/bin"
    return
  fi
  local found="" count=0 d
  for d in "$KC_ROOT"/release/*/bin; do
    [ -d "$d" ] || continue
    found="$d"
    count=$((count + 1))
  done
  if [ "$count" -eq 1 ] && [ -x "$found/cake-resident" ]; then
    echo "$found"
    return
  fi
  kc_fail "no release binaries found; run bin/fetch-release.sh first (or set KC_RELEASE_DIR)"
}

kc_check_binaries() {
  local dir="$1" b
  for b in cake-resident admin-probe demo-plan demo-preflight; do
    [ -x "$dir/$b" ] || kc_fail "$dir/$b is missing or not executable"
  done
}

kc_new_run_dir() {
  local id
  id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  { mkdir -p "$KC_STATE/runs" && chmod 700 "$KC_STATE" "$KC_STATE/runs"; } || kc_fail "cannot create $KC_STATE/runs"
  KC_RUN="$KC_STATE/runs/$id"
  { mkdir -p "$KC_RUN" && chmod 700 "$KC_RUN"; } || kc_fail "cannot create $KC_RUN"
  { mkdir -p "$KC_RUNTIME" && chmod 700 "$KC_RUNTIME"; } || kc_fail "cannot create $KC_RUNTIME"
  export KC_RUN
}

# --- the demo key -----------------------------------------------------------

kc_generate_keypair() {
  # kc_generate_keypair PATH: the two-line key file demo-plan reads, made
  # with openssl exactly as the runbook's own recipe does. Mode 600.
  local path="$1" dir
  dir="$(dirname "$path")"
  { mkdir -p "$dir" && chmod 700 "$dir"; } || kc_fail "cannot create $dir"
  local der="$path.der" pub="$path.pub.der"
  ( umask 077
    openssl genpkey -algorithm ed25519 -outform DER -out "$der" 2>/dev/null &&
    openssl pkey -inform DER -in "$der" -pubout -outform DER -out "$pub" 2>/dev/null &&
    {
      printf 'ed25519-seed %s\n' "$(tail -c 32 "$der" | od -An -tx1 | tr -d ' \n')"
      printf 'ed25519-public %s\n' "$(tail -c 32 "$pub" | od -An -tx1 | tr -d ' \n')"
    } >"$path"
  ) || kc_fail "openssl could not generate an Ed25519 key"
  rm -f -- "$der" "$pub"
  chmod 600 "$path"
  if ! grep -qE '^ed25519-seed [0-9a-f]{64}$' "$path" || ! grep -qE '^ed25519-public [0-9a-f]{64}$' "$path"; then
    kc_fail "the generated key file does not have the expected two lines"
  fi
}

kc_keypair_public() { sed -nE 's/^ed25519-public ([0-9a-f]{64})$/\1/p' "$1" | head -n 1; }

# --- manifest and admin helpers ----------------------------------------------

kc_manifest_value() { sed -nE "s/^$2 (.*)\$/\\1/p" "$1" | head -n 1; }
kc_field() { printf '%s\n' "$1" | sed -nE "s/^$2 (.*)\$/\\1/p" | head -n 1; }

KC_QUERY_ERR=""
kc_query() {
  # kc_query PROBE SOCKET OP: one admin-probe request under KC_PROBE_TIMEOUT
  # seconds (default 10). The shipped probe gives up a single read after 5 s
  # on its own, so 10 s leaves a slow but answering resident alone and cuts
  # only a probe stuck on connect or on a read that never returns. Returns
  # the probe's status, 124 on timeout. The probe's stderr is kept in
  # KC_QUERY_ERR for the caller and printed, prefixed, only on a timeout:
  # the readiness loops call this hundreds of times before a resident
  # answers, and a refused connect there is expected, not news.
  local err rc t="${KC_PROBE_TIMEOUT:-10}"
  err="$(mktemp "${TMPDIR:-/tmp}/kc-probe-err.XXXXXX")" || return 1
  timeout -k 2 "$t" "$1" request "$2" "$3" 2>"$err"
  rc=$?
  KC_QUERY_ERR="$(cat "$err")"
  rm -f -- "$err"
  if [ "$rc" -eq 124 ]; then
    [ -z "$KC_QUERY_ERR" ] || printf '%s\n' "$KC_QUERY_ERR" | sed 's/^/kiwi-cake: admin-probe: /' >&2
    printf 'kiwi-cake: admin-probe: %s timed out after %ss\n' "$3" "$t" >&2
  fi
  return "$rc"
}

kc_deadline_passed() {
  # kc_deadline_passed START LIMIT: true once LIMIT seconds have passed since START, a $SECONDS reading.
  [ $((SECONDS - $1)) -ge "$2" ]
}

# --- reply shapes -----------------------------------------------------------------
kc_hex_field() {
  # kc_hex_field TEXT NAME LEN: the value of NAME when it is exactly LEN lowercase hex characters.
  local v
  v="$(kc_field "$1" "$2")"
  printf '%s\n' "$v" | grep -qE "^[0-9a-f]{$3}\$" || return 1
  printf '%s\n' "$v"
}

kc_int_field() {
  # kc_int_field TEXT NAME: the value of NAME when it is digits only.
  local v
  v="$(kc_field "$1" "$2")"
  printf '%s\n' "$v" | grep -qE '^[0-9]+$' || return 1
  printf '%s\n' "$v"
}

kc_status_fields_ok() {
  # kc_status_fields_ok TEXT: a get-status reply with status ok and every declared identity well-formed.
  local text="$1" n
  [ "$(kc_field "$text" status)" = "ok" ] || { echo "missing or malformed: status"; return 1; }
  for n in build_identity:64 session_uuid:32 plan_digest:64 config_identity:64 target_profile_digest:64; do
    kc_hex_field "$text" "${n%%:*}" "${n##*:}" >/dev/null || { echo "missing or malformed: ${n%%:*}"; return 1; }
  done
  kc_int_field "$text" epoch >/dev/null || { echo "missing or malformed: epoch"; return 1; }
}

kc_identities_match() {
  # kc_identities_match TEXT_A TEXT_B NAME...: every NAME well-formed on both sides and equal.
  local a="$1" b="$2" n len va vb
  shift 2
  for n in "$@"; do
    len=64; [ "$n" = "session_uuid" ] && len=32
    va="$(kc_hex_field "$a" "$n" "$len")" || { echo "identity $n: missing or malformed before"; return 1; }
    vb="$(kc_hex_field "$b" "$n" "$len")" || { echo "identity $n: missing or malformed after"; return 1; }
    [ "$va" = "$vb" ] || { echo "identity $n: $va before, $vb after"; return 1; }
  done
}

kc_write_resident_conf() {
  # kc_write_resident_conf PATH STORE_ROOT SOCKET PUBLIC_KEY PLAN_ARTIFACT
  {
    echo "store_root = $2"
    echo "admin_socket_path = $3"
    echo "admin_socket_group = $(id -gn)"
    echo "admin_socket_mode = 0660"
    echo "admin_max_connections = 4"
    echo "signature_policy = require"
    echo "trusted_keys = $4"
    echo "flight_ring_capacity = 256"
    echo "target_profile = lekiwi-mvp"
    echo "plan_artifact = $5"
  } >"$1"
}

# --- process table ------------------------------------------------------------
# /proc/<pid>/cmdline and nothing else, comparing whole arguments, so a
# supervised child is found by the unique run tag in its own argument list.

kc_pids_carrying() {
  local tag="$1" entry pid arg out="" hit
  for entry in /proc/[0-9]*; do
    pid="${entry#/proc/}"
    hit=0
    while IFS= read -r -d '' arg; do
      if [ "$arg" = "$tag" ]; then hit=1; break; fi
    done 2>/dev/null <"$entry/cmdline"
    [ "$hit" -eq 1 ] && out="$out $pid"
  done
  printf '%s' "${out# }"
}

kc_children_of() {
  # kc_children_of PID: pids whose parent is PID, from /proc/<pid>/stat (field 4).
  local ppid="$1" entry pid stat out=""
  for entry in /proc/[0-9]*; do
    pid="${entry#/proc/}"
    stat="$(cat "$entry/stat" 2>/dev/null)" || continue
    stat="${stat##*) }"
    set -- $stat
    [ "${2:-}" = "$ppid" ] && out="$out $pid"
  done
  printf '%s' "${out# }"
}

kc_wait_for_child_of() {
  # kc_wait_for_child_of PID TICKS: wait up to TICKS x 50 ms for PID to have a child.
  local ppid="$1" ticks="$2" _
  for _ in $(seq 1 "$ticks"); do
    [ -n "$(kc_children_of "$ppid")" ] && return 0
    sleep 0.05
  done
  return 1
}

kc_wait_gone() {
  # kc_wait_gone PID TICKS: wait up to TICKS x 50 ms for PID to leave the process table.
  local pid="$1" ticks="$2" _
  for _ in $(seq 1 "$ticks"); do
    [ -d "/proc/$pid" ] || return 0
    sleep 0.05
  done
  return 1
}

kc_template_has_placeholder() {
  # kc_template_has_placeholder FILE: true when a non-comment line still carries @@NAME@@.
  grep -vE '^[[:space:]]*#' "$1" | grep -qE '@@[A-Z_]+@@'
}

kc_wait_for_tag() {
  # kc_wait_for_tag TAG TICKS: wait up to TICKS x 50 ms for a process carrying TAG.
  local tag="$1" ticks="$2" _
  for _ in $(seq 1 "$ticks"); do
    [ -n "$(kc_pids_carrying "$tag")" ] && return 0
    sleep 0.05
  done
  return 1
}

kc_settle_absent() {
  # kc_settle_absent TAG TICKS: true only after five consecutive scans found no process carrying TAG.
  local tag="$1" ticks="$2" clear=0 _
  for _ in $(seq 1 "$ticks"); do
    if [ -z "$(kc_pids_carrying "$tag")" ]; then
      clear=$((clear + 1))
      [ "$clear" -ge 5 ] && return 0
    else
      clear=0
    fi
    sleep 0.05
  done
  return 1
}

# --- the preflight roster ---------------------------------------------------------
# demo-preflight runs seven checks in order and stops at the first refusal. In
# the public build the sixth (observer) always refuses because the module it
# checks is built from a source tree that is not part of this release, so the
# accepted shape is: the five earlier check lines present, then exactly that
# one refusal. See LIMITATIONS.md.

KC_PREFLIGHT_OBSERVER_REFUSAL='^demo-preflight: REFUSED: check observer: --observer-module was not given, so the module this board builds for itself would first be exercised by the resident; that is the divergence this check exists to close$'

kc_preflight_five_green() {
  # kc_preflight_five_green STDOUT_FILE: true when all five check lines are present.
  local f="$1"
  grep -qE '^demo-preflight: page size [0-9]+$' "$f" &&
    grep -qE '^demo-preflight: glibc [0-9.]+$' "$f" &&
    grep -qE '^demo-preflight: vm\.memfd_noexec_scope ' "$f" &&
    grep -qE '^demo-preflight: check admission the staged capsule is admitted against the active profile$' "$f" &&
    grep -qE '^demo-preflight: check load-probe mapped through the sealed loader path with [0-9]+ seals and resolved the entry symbol$' "$f"
}

kc_classify_preflight() {
  # kc_classify_preflight STDOUT_FILE STDERR_FILE RC
  # Prints: pass | five-green-observer-refused | refused:<check>:<message> |
  # refused:usage:<message> | unparsed:no-pass-line | unparsed:no-refusal-line |
  # unparsed:contradictory-refusals | unparsed:abnormal-exit:<rc>
  # demo-preflight exits 0 only after its PASS line and 1 for any refusal;
  # every other status (128 + n is death on signal n) reached no verdict.
  local out="$1" err="$2" rc="$3" line check msg count
  case "$rc" in
    0)
      if grep -qx 'demo-preflight: PASS' "$out"; then echo pass; else echo unparsed:no-pass-line; fi
      return ;;
    1) ;;
    *) echo "unparsed:abnormal-exit:$rc"; return ;;
  esac
  count="$(grep -cE '^demo-preflight: REFUSED: ' "$err" 2>/dev/null)"
  count="${count:-0}"
  if [ "$count" -eq 0 ]; then echo unparsed:no-refusal-line; return; fi
  if [ "$count" -gt 1 ]; then echo unparsed:contradictory-refusals; return; fi
  line="$(grep -E '^demo-preflight: REFUSED: ' "$err")"
  if printf '%s\n' "$line" | grep -qE "$KC_PREFLIGHT_OBSERVER_REFUSAL" && kc_preflight_five_green "$out"; then
    echo five-green-observer-refused
    return
  fi
  if printf '%s\n' "$line" | grep -qE '^demo-preflight: REFUSED: check [a-z-]+: '; then
    check="$(printf '%s\n' "$line" | sed -E 's/^demo-preflight: REFUSED: check ([a-z-]+): .*$/\1/')"
    msg="$(printf '%s\n' "$line" | sed -E 's/^demo-preflight: REFUSED: check [a-z-]+: //')"
    echo "refused:$check:$msg"
    return
  fi
  msg="$(printf '%s\n' "$line" | sed -E 's/^demo-preflight: REFUSED: //')"
  echo "refused:usage:$msg"
}

kc_explain_refusal() {
  # kc_explain_refusal CHECK MESSAGE: plain language for a refusal, on stdout.
  local check="$1" msg="$2"
  echo "demo-preflight refused at check '$check': $msg"
  case "$check" in
    page-size)
      echo "What this means: the kernel on this board uses a page size other than the 16384 bytes of the tested Raspberry Pi 5 kernel. The check requires equality; there is no override inside demo-preflight."
      echo "On a Raspberry Pi 5: boot the default kernel (remove any 'kernel=kernel8.img' line from /boot/firmware/config.txt and reboot)."
      echo "On a Raspberry Pi 4 or any other machine: this refusal is by construction; see docs/targets.md for what --unsupported-target allows."
      ;;
    glibc)
      echo "What this means: the running glibc is below the tested board record (2.41) or below the binaries' floor (2.34). The check requires both."
      echo "Raspberry Pi OS based on Debian 13 (trixie) carries 2.41 and passes; bookworm carries 2.36 and is refused by construction. See docs/targets.md."
      ;;
    memfd-noexec)
      echo "What this means: vm.memfd_noexec_scope is 2 on this kernel, which denies the executable anonymous file the sealed loader needs."
      echo "A value of 0 or 1 passes. Changing it needs root: sudo sysctl vm.memfd_noexec_scope=1 (not persisted across reboots unless written to /etc/sysctl.d/)."
      ;;
    admission)
      echo "What this means: the package you just built was refused by the same admission call the resident runs at start. The reason above is the resident's own; nothing in this demo can proceed with a refused package."
      ;;
    load-probe)
      echo "What this means: the admitted module could not be mapped through the sealed loader path on this kernel, or its entry symbol did not resolve. The reason above names which."
      ;;
    observer)
      echo "What this means: the observer check needs a module this board builds from a source tree that is not part of this release, so it cannot pass here. If the five earlier check lines printed, the runner treats this refusal as the end of the roster (LIMITATIONS.md). If they did not, something earlier did not run."
      ;;
    shipped-binaries)
      echo "What this means: one of the shipped binaries did not start under this board's dynamic loader. Run bin/doctor.sh for the per-binary result."
      ;;
    usage)
      echo "What this means: demo-preflight was invoked without an argument it needs; this is a bug in the runner, not a fact about your board."
      ;;
    *)
      echo "What this means: an unrecognised check name; report the line above."
      ;;
  esac
}

kc_explain_unparsed() {
  # kc_explain_unparsed VERDICT: one line on stdout saying why an 'unparsed:<reason>' verdict is no verdict.
  local reason="${1#unparsed:}" rc
  case "$reason" in
    no-pass-line) echo "What this means: demo-preflight exited 0 without printing 'demo-preflight: PASS'; a pass always prints that line, so this run is not one." ;;
    no-refusal-line) echo "What this means: demo-preflight exited 1 without a 'demo-preflight: REFUSED: ' line, so the check it stopped at is unknown." ;;
    contradictory-refusals) echo "What this means: demo-preflight printed more than one REFUSED line; it stops at the first refusal, so two cannot come from one run." ;;
    abnormal-exit:*)
      rc="${reason#abnormal-exit:}"
      case "$rc" in
        '' | *[!0-9]*) echo "What this means: demo-preflight's exit status '$rc' is not a number; the run did not end the way a process does." ;;
        *)
          if [ "$rc" -ge 128 ]; then
            echo "What this means: demo-preflight exited $rc, which is 128 + $((rc - 128)): it died on signal $((rc - 128)) before reaching a verdict (139 is SIGSEGV, 137 SIGKILL)."
          else
            echo "What this means: demo-preflight exited $rc; it exits only 0 (PASS) or 1 (a refusal), so no verdict was reached."
          fi ;;
      esac ;;
    *) echo "What this means: the classifier returned '$1', which the runner does not know." ;;
  esac
}

# --- no-op probe of a shipped binary ------------------------------------------------
# The same probe demo-preflight's seventh check performs, re-implemented here
# because that check never runs in the public build (LIMITATIONS.md):
# cake-resident is given a configuration path that cannot exist, every other
# binary is run with no arguments, and what is being probed is the dynamic
# loader, which resolves every dependency before the first instruction.

kc_probe_binary() {
  # kc_probe_binary PATH: true when the binary starts and exits on its own usage path.
  local path="$1" rc
  if [ "$(basename "$path")" = "cake-resident" ]; then
    timeout -k 2 30 "$path" --config /nonexistent/kiwi-cake-preflight.conf >/dev/null 2>&1
  else
    timeout -k 2 30 "$path" >/dev/null 2>&1
  fi
  rc=$?
  # A loader failure exits 127 (or is killed by a signal, 128+); a usage path exits 1;
  # a binary that never returns is cut at 30 s (124).
  [ "$rc" -ne 124 ] && [ "$rc" -lt 126 ]
}
