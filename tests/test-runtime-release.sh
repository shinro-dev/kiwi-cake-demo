#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-runtime-release.sh: bin/runtime-release.sh and the robot-side runtime unit it renders
# (bin/templates/kiwi-cake-runtime.service.in). The tool is copied into a throwaway git repository with a few files, so
# `stage` archives that repository's commit and nothing of this checkout; nothing is installed, enabled or started, and
# no systemctl is run (systemd-analyze verify only reads the rendered file, and is skipped where it cannot run).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-test-runtime-release.XXXXXX")"
cleanup() { chmod -R u+w -- "$T" 2>/dev/null; rm -rf -- "$T"; }
trap cleanup EXIT
FAILS=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; FAILS=$((FAILS + 1)); }

# The throwaway repository: the tool, the template, a daemon stand-in, a nested file and a symlink.
R="$T/repo"
mkdir -p "$R/bin/templates" "$R/packages/demo-runtime/demo_runtime" "$R/packages/demo-contracts/python"
cp "$ROOT/bin/runtime-release.sh" "$R/bin/"; cp "$ROOT/bin/templates/kiwi-cake-runtime.service.in" "$R/bin/templates/"
echo '# stand-in' >"$R/packages/demo-runtime/demo_runtime/daemon.py"
echo 'first' >"$R/packages/demo-contracts/python/data.txt"
ln -s data.txt "$R/packages/demo-contracts/python/link.txt"
git -C "$R" init -q && git -C "$R" -c user.name=test -c user.email=test@example.invalid add -A \
  && git -C "$R" -c user.name=test -c user.email=test@example.invalid commit -q -m fixture || { echo "test-runtime-release: git fixture failed"; exit 1; }
COMMIT="$(git -C "$R" rev-parse HEAD)"
TOOL="$R/bin/runtime-release.sh"
export KC_RUNTIME_RELEASES="$T/releases"

# stage: a release equal to the COMMIT, whatever the working tree says, read-only, with its record.
echo 'edited, never committed' >"$R/packages/demo-contracts/python/data.txt"
if OUT="$("$TOOL" stage HEAD 2>&1)"; then ok "stage builds a release"; else bad "stage refused: $OUT"; fi
REL="$(find "$KC_RUNTIME_RELEASES" -mindepth 1 -maxdepth 1 -type d ! -name '.*' | head -n 1)"
[ -n "$REL" ] && [ "$(basename "$REL" | cut -c1-12)" = "${COMMIT:0:12}" ] && ok "the release is named after its commit" || bad "release name"
grep -qx "commit $COMMIT" "$REL/RELEASE" && grep -qx 'verified yes' "$REL/RELEASE" && ok "the release records its commit and its verification" || bad "RELEASE record"
[ "$(cat "$REL/packages/demo-contracts/python/data.txt" 2>/dev/null)" = first ] && ok "the release holds the committed content, not the working tree's" || bad "working tree leaked into the release"
[ -L "$REL/packages/demo-contracts/python/link.txt" ] && ok "a committed symlink stays a symlink" || bad "symlink"
[ ! -w "$REL/packages/demo-runtime/demo_runtime/daemon.py" ] && [ ! -w "$REL" ] && ok "the release is read-only" || bad "the release is writable"
[ -z "$(find "$KC_RUNTIME_RELEASES" -mindepth 1 -maxdepth 1 -name '.staging-*')" ] && ok "no staging directory is left behind" || bad "staging left behind"
git -C "$R" checkout -q -- .

# A tree that differs from the commit is refused and leaves nothing: a tar that adds one file after extracting.
mkdir -p "$T/fakebin"
REAL_TAR="$(command -v tar)"
cat >"$T/fakebin/tar" <<EOF
#!/bin/bash
"$REAL_TAR" "\$@" || exit \$?
while [ \$# -gt 0 ]; do [ "\$1" = -C ] && { echo extra >"\$2/not-in-the-commit.txt"; break; }; shift; done
EOF
chmod +x "$T/fakebin/tar"
BEFORE="$(find "$KC_RUNTIME_RELEASES" -mindepth 1 -maxdepth 1 | wc -l)"
sleep 1  # a new second, so the release name cannot collide with the first
if OUT="$(PATH="$T/fakebin:$PATH" "$TOOL" stage HEAD 2>&1)"; then bad "a tree with an extra file was staged"; else
  case "$OUT" in *"REFUSE: the release holds a file the commit does not"*) ok "a tree with an extra file is refused" ;; *) bad "unexpected refusal: $OUT" ;; esac
fi
[ "$(find "$KC_RUNTIME_RELEASES" -mindepth 1 -maxdepth 1 | wc -l)" -eq "$BEFORE" ] && ok "a refused stage leaves nothing behind" || bad "a refused stage left something"
cat >"$T/fakebin/tar" <<EOF
#!/bin/bash
"$REAL_TAR" "\$@" || exit \$?
while [ \$# -gt 0 ]; do [ "\$1" = -C ] && { echo tampered >"\$2/packages/demo-contracts/python/data.txt"; break; }; shift; done
EOF
sleep 1
if OUT="$(PATH="$T/fakebin:$PATH" "$TOOL" stage HEAD 2>&1)"; then bad "a tree with a changed file was staged"; else
  case "$OUT" in *"REFUSE: a file of the release differs from the commit"*) ok "a tree with a changed file is refused" ;; *) bad "unexpected refusal: $OUT" ;; esac
fi
"$TOOL" stage not-a-commit >/dev/null 2>&1; [ $? -eq 5 ] && ok "a name that is no commit is a usage refusal" || bad "bad commit exit status"

# render: every placeholder filled, shipped off, never enabled, the guarded stop first.
export KC_RUNTIME_PYTHON=/usr/bin/python3 KC_RUNTIME_DATA_DIR="$T/data" KC_RUNTIME_CONFIG="$T/config.json"
echo '{"data_root": "/srv/example"}' >"$KC_RUNTIME_CONFIG"
U="$T/unit/kiwi-cake-runtime.service"; mkdir -p "$T/unit"
if OUT="$("$TOOL" render "$REL" "$U" 2>&1)"; then ok "render writes the unit"; else bad "render refused: $OUT"; fi
case "$OUT" in *"hardware commands: not enabled"*"mission.execution: unset"*"nothing was installed, enabled or started"*) ok "render says it ships off and installed nothing" ;; *) bad "render output: $OUT" ;; esac
kc_template_has_placeholder "$U" && bad "the rendered unit still carries a placeholder" || ok "the rendered unit carries no placeholder"
for want in "WorkingDirectory=$REL" "Environment=PYTHONPATH=$REL/packages/demo-runtime:$REL/packages/demo-contracts/python" \
  "ExecStart=/usr/bin/python3 -m demo_runtime.daemon --config $T/config.json --data-dir $T/data" \
  "ExecStop=-/usr/bin/python3 -m demo_runtime.cli --data-dir $T/data stop" 'Restart=no' 'KillMode=process' 'Type=simple'; do
  grep -qxF -- "$want" "$U" && ok "unit has $want" || bad "unit lacks $want"
done
grep -vE '^[[:space:]]*#' "$U" | grep -q -- '--enable-hardware-commands' && bad "the default unit enables hardware commands" || ok "the default unit does not enable hardware commands"
grep -qE '^\[Install\]|^WantedBy=' "$U" && bad "the runtime unit can be enabled" || ok "the runtime unit has no [Install] section"
EXECL="$(grep -n '^ExecStart=' "$U" | cut -d: -f1)"; STOPL="$(grep -n '^ExecStop=' "$U" | cut -d: -f1)"
[ -n "$EXECL" ] && [ -n "$STOPL" ] && ok "the unit names both its start and its guarded stop" || bad "start or stop missing"
KC_RUNTIME_HARDWARE_COMMANDS=1 "$TOOL" render "$REL" "$U" >/dev/null 2>&1 \
  && grep -qxF "ExecStart=/usr/bin/python3 -m demo_runtime.daemon --config $T/config.json --data-dir $T/data --enable-hardware-commands" "$U" \
  && ok "hardware commands are enabled only when named" || bad "the named hardware flag"
if command -v systemd-analyze >/dev/null 2>&1; then
  if VOUT="$(systemd-analyze --user verify "$U" 2>&1)" || VOUT="$(systemd-analyze verify "$U" 2>&1)"; then
    ok "systemd-analyze verify accepts the rendered runtime unit"; [ -z "$VOUT" ] || printf '%s\n' "$VOUT" | sed 's/^/     /'
  else
    case "$VOUT" in
      *"Failed to connect"* | *"No such file"* | *"cgroup"* | *"not executable"*) echo "SKIP systemd-analyze verify of the runtime unit: could not run here ($(printf '%s' "$VOUT" | head -n 1))" ;;
      *) bad "systemd-analyze verify rejected the rendered runtime unit:"; printf '%s\n' "$VOUT" | sed 's/^/     /' ;;
    esac
  fi
else
  echo "SKIP systemd-analyze: not installed; the rendered runtime unit was not verified by systemd"
fi
"$TOOL" render "$T/repo" "$U" >/dev/null 2>&1; [ $? -eq 3 ] && ok "render refuses a directory that is not a verified release" || bad "unverified release rendered"
"$TOOL" render "$REL" "$T/unit/other.service" >/dev/null 2>&1; [ $? -eq 5 ] && ok "render refuses another unit name" || bad "unit name"
KC_RUNTIME_DATA_DIR="relative/data" "$TOOL" render "$REL" "$U" >/dev/null 2>&1; [ $? -eq 5 ] && ok "render refuses a relative path" || bad "relative path"
KC_RUNTIME_CONFIG="$T/with space.json" "$TOOL" render "$REL" "$U" >/dev/null 2>&1; [ $? -eq 5 ] && ok "render refuses a path a unit file would misread" || bad "path with a space"

# status: the releases, and whether mission.execution is set; the shipped example leaves it unset.
OUT="$("$TOOL" status 2>&1)"
case "$OUT" in *"release $(basename "$REL"): $COMMIT, verified yes"*"mission.execution: unset"*) ok "status names the release and the unset execution" ;; *) bad "status: $OUT" ;; esac
echo '{"mission": {"execution": null}}' >"$KC_RUNTIME_CONFIG"
case "$("$TOOL" status 2>&1)" in *"mission.execution: unset"*) ok "a null execution is unset" ;; *) bad "null execution" ;; esac
echo '{"mission": {"execution": "proven_controller"}}' >"$KC_RUNTIME_CONFIG"
case "$("$TOOL" status 2>&1)" in *"mission.execution: set"*) ok "a named execution is reported as set" ;; *) bad "set execution" ;; esac
KC_RUNTIME_CONFIG="$ROOT/packages/demo-runtime/config.example.json" "$TOOL" status 2>&1 | grep -qx 'mission.execution: unset' \
  && ok "the shipped example configuration leaves mission.execution unset" || bad "the shipped example sets mission.execution"

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S warning "$ROOT/bin/runtime-release.sh" && ok "shellcheck accepts bin/runtime-release.sh" || bad "shellcheck on bin/runtime-release.sh"
else
  echo "SKIP shellcheck of bin/runtime-release.sh: not installed"
fi
[ "$FAILS" -eq 0 ] && echo "test-runtime-release: PASS" || { echo "test-runtime-release: $FAILS failure(s)"; exit 1; }
