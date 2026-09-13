#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-journal.sh: kc_unit_journal against a fake journalctl. The
# per-user read serves when it has entries; the system-journal read under the
# four matches --user -u applies takes over when the per-user read exits
# nonzero, finds no journal files or has no entries; a journal neither read can
# open is unavailable (status 1, no file content), never an empty journal.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-test-journal.XXXXXX")"; trap 'rm -rf -- "$T"' EXIT
FAILS=0
mkdir -p "$T/bin"
cat >"$T/bin/journalctl" <<'FAKE'
#!/usr/bin/env bash
# fake journalctl for tests/test-journal.sh: a read carrying --user answers as
# KC_FAKE_JOURNAL_USER says, any other read as KC_FAKE_JOURNAL_SYSTEM says;
# every argv is appended to KC_FAKE_JOURNAL_CALLS as one line, each argument in brackets.
{ printf '[%s]' "$@"; echo; } >>"$KC_FAKE_JOURNAL_CALLS"
mode="$KC_FAKE_JOURNAL_SYSTEM"; which=system
for a in "$@"; do [ "$a" = "--user" ] && { mode="$KC_FAKE_JOURNAL_USER"; which=user; }; done
case "$mode" in
  entries) printf '[10.000000] pi systemd[900]: Stopping kiwi-cake-demo.service...\n[10.100000] pi cake-resident[901]: cake-resident: stopped (%s journal)\n[10.200000] pi systemd[900]: Stopped kiwi-cake-demo.service.\n' "$which" ;;
  empty) echo "-- No entries --" ;;
  nofiles) echo "No journal files were found." >&2; echo "-- No entries --"; exit 0 ;;
  nonzero) echo "Failed to open journal: Permission denied" >&2; exit 1 ;;
  *) echo "fake journalctl: unknown mode '$mode'" >&2; exit 2 ;;
esac
FAKE
chmod 755 "$T/bin/journalctl"
UNIT="kiwi-cake-demo.service"; SINCE="2026-09-13 12:00:00.123456"; ME="$(id -u)"
# argline ARG...: the fake's record of one call, so that every argument boundary is compared.
argline() { printf '[%s]' "$@"; echo; }
USER_READ="$(argline --user -u "$UNIT" --since "$SINCE" -o short-monotonic --no-pager)"
FALLBACK="$(argline "_SYSTEMD_USER_UNIT=$UNIT" "_UID=$ME" + "USER_UNIT=$UNIT" "_UID=$ME" + "COREDUMP_USER_UNIT=$UNIT" _UID=0 "_UID=$ME" + "OBJECT_SYSTEMD_USER_UNIT=$UNIT" _UID=0 "_UID=$ME" --since "$SINCE" -o short-monotonic --no-pager)"
FALLBACK_SOURCE="journalctl _SYSTEMD_USER_UNIT=$UNIT _UID=$ME + USER_UNIT=$UNIT _UID=$ME + COREDUMP_USER_UNIT=$UNIT _UID=0 _UID=$ME + OBJECT_SYSTEMD_USER_UNIT=$UNIT _UID=0 _UID=$ME"
# read_journal USER_MODE SYSTEM_MODE: kc_unit_journal under the fake; status in RC, entries in $T/journal, calls in $T/calls.
read_journal() {
  : >"$T/calls"; rm -f -- "$T/journal"
  PATH="$T/bin:$PATH" KC_FAKE_JOURNAL_USER="$1" KC_FAKE_JOURNAL_SYSTEM="$2" KC_FAKE_JOURNAL_CALLS="$T/calls" kc_unit_journal "$UNIT" "$SINCE" "$T/journal"
  RC=$?
}
served_from() { grep -q "stopped ($1 journal)" "$T/journal" 2>/dev/null; }
calls() { wc -l <"$T/calls"; }
called_with() { grep -qxF -- "$1" "$T/calls"; }
# verdict WHAT OK: one ok/FAIL line; a FAIL shows the status, the source, the error, the calls and the entries.
verdict() {
  if [ "$2" -eq 1 ]; then echo "ok   $1"; return; fi
  echo "FAIL $1 (rc $RC, source '$KC_JOURNAL_SOURCE', err '$KC_JOURNAL_ERR'); calls and entries follow"
  sed 's/^/     call: /' "$T/calls"; sed 's/^/     line: /' "$T/journal" 2>/dev/null; FAILS=$((FAILS + 1))
}
# 1. the per-user read has entries: used as is, the system journal never asked
read_journal entries entries; OK=1
[ "$RC" -eq 0 ] && served_from user && [ "$(calls)" -eq 1 ] || OK=0
called_with "$USER_READ" || OK=0
[ "$KC_JOURNAL_SOURCE" = "journalctl --user -u $UNIT" ] || OK=0
verdict "per-user entries: served from journalctl --user -u alone" "$OK"
# 2. the per-user read exits nonzero: the system journal, under both matches for this uid, without --user
read_journal nonzero entries; OK=1
[ "$RC" -eq 0 ] && served_from system && ! served_from user && [ "$(calls)" -eq 2 ] || OK=0
called_with "$FALLBACK" || OK=0
sed -n 2p "$T/calls" | grep -qF -- '[--user]' && OK=0
[ "$KC_JOURNAL_SOURCE" = "$FALLBACK_SOURCE" ] || OK=0
verdict "per-user read exits nonzero: served from the system journal under the four matches" "$OK"
# 3. the per-user read prints "No journal files were found" and exits 0: the same fallback, on the words alone
read_journal nofiles entries; OK=1
[ "$RC" -eq 0 ] && served_from system && ! grep -q "No journal files" "$T/journal" && called_with "$FALLBACK" || OK=0
verdict "per-user read finds no journal files (exit 0): served from the system journal" "$OK"
# 4. the per-user read is readable but empty: the system journal is still consulted
read_journal empty entries; OK=1
[ "$RC" -eq 0 ] && served_from system && [ "$(calls)" -eq 2 ] || OK=0
verdict "per-user read has no entries: the system journal is consulted and served" "$OK"
# 5. both readable, both empty: a readable journal with no entries, status 0
read_journal empty empty; OK=1
[ "$RC" -eq 0 ] && [ -n "$KC_JOURNAL_SOURCE" ] || OK=0
kc_journal_has_entries "$(cat "$T/journal")" && OK=0
verdict "both reads empty: status 0, no entry, a readable journal" "$OK"
# 6. neither read can open a journal: unavailable, status 1, nothing written, the complaint kept
read_journal nofiles nofiles; OK=1
[ "$RC" -eq 1 ] && [ ! -s "$T/journal" ] && [ -z "$KC_JOURNAL_SOURCE" ] || OK=0
printf '%s' "$KC_JOURNAL_ERR" | grep -q "No journal files were found" || OK=0
verdict "no journal files on either read: status 1, unavailable, not empty" "$OK"
read_journal nonzero nonzero; OK=1
[ "$RC" -eq 1 ] && [ ! -s "$T/journal" ] || OK=0
printf '%s' "$KC_JOURNAL_ERR" | grep -q "Permission denied" || OK=0
verdict "both reads exit nonzero: status 1, the error kept" "$OK"
# 7. the helpers behind the decision
kc_journal_unreadable "$(printf 'No journal files were found.\n-- No entries --')" && echo "ok   kc_journal_unreadable sees the no-files answer" || { echo "FAIL kc_journal_unreadable"; FAILS=$((FAILS + 1)); }
kc_journal_unreadable "-- No entries --" && { echo "FAIL kc_journal_unreadable takes an empty journal for an unreadable one"; FAILS=$((FAILS + 1)); } || echo "ok   kc_journal_unreadable leaves an empty journal alone"
kc_journal_has_entries "-- No entries --" && { echo "FAIL kc_journal_has_entries counts the marker"; FAILS=$((FAILS + 1)); } || echo "ok   kc_journal_has_entries ignores '-- No entries --'"
kc_journal_has_entries "" && { echo "FAIL kc_journal_has_entries counts nothing"; FAILS=$((FAILS + 1)); } || echo "ok   kc_journal_has_entries ignores an empty read"
kc_journal_has_entries "$(printf -- '-- Boot abc --\n[1.0] pi cake-resident[1]: cake-resident: stopped')" && echo "ok   kc_journal_has_entries sees an entry past a boot marker" || { echo "FAIL kc_journal_has_entries misses an entry"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "test-journal: PASS" || { echo "test-journal: $FAILS failure(s)"; exit 1; }
