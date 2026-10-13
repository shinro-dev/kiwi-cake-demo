#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/runtime-release.sh SUBCOMMAND: pinned releases of the demo_runtime daemon for the ROBOT-SIDE deployment, and the
# user unit that runs one (bin/templates/kiwi-cake-runtime.service.in). SEPARATE from bin/pi/lekiwi-deploy.sh and its
# APP_FILES: that script deploys the Cake resident, the host and the capability, and nothing here touches them.
#
#   stage COMMIT   builds ONE release from a `git archive` of COMMIT in this checkout, under $KC_RUNTIME_RELEASES, in a
#                  staging directory first. Every file of the extracted tree is compared with the commit's own tree
#                  (git ls-tree: every blob's hash, every symlink's target, and no extra file), the release records the
#                  commit it was built from, the release is made read-only, and only then is it renamed into place.
#                  A refusal leaves no release behind. The working tree's state does not matter: the archive is of the
#                  commit object, never of the files on disk.
#   render RELEASE UNIT_FILE
#                  writes the user unit for RELEASE (a directory stage built) to UNIT_FILE, every placeholder filled
#                  from the environment below. It never runs systemctl, never enables anything and never writes the
#                  runtime configuration. The unit starts the runtime WITHOUT --enable-hardware-commands unless
#                  KC_RUNTIME_HARDWARE_COMMANDS=1 names it.
#   status         read-only: the releases under $KC_RUNTIME_RELEASES with the commit each was built from and whether it
#                  verified, and what the configuration says about mission.execution (unset, as shipped, or set).
#
# THIS DEPLOYS NOTHING. It builds a directory and writes one file where it is told to. Copying a release to the robot,
# placing the unit under ~/.config/systemd/user, daemon-reload and starting it are the operator's steps.
#
# Environment (paths absolute; none is printed except as the operator's own arguments):
#   KC_RUNTIME_RELEASES   the directory releases are built under (stage, status)
#   KC_RUNTIME_PYTHON     the python the unit runs the daemon with (render)
#   KC_RUNTIME_DATA_DIR   the runtime's PRIVATE data directory (render)
#   KC_RUNTIME_CONFIG     the runtime's PRIVATE configuration file (render, status)
#   KC_RUNTIME_HARDWARE_COMMANDS  1 to render the unit with --enable-hardware-commands; anything else leaves it off
#
# Exit status: 0 done; 3 a verification refused (the REFUSE line says which); 5 usage or a missing or unsafe value.
set -euo pipefail

usage() { echo "usage: $0 stage COMMIT | render RELEASE UNIT_FILE | status" >&2; exit 5; }
[ $# -ge 1 ] || usage
CMD="$1"; shift
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$ROOT/bin/templates/kiwi-cake-runtime.service.in"
MARKER="RELEASE"

refuse() { echo "REFUSE: $1" >&2; exit "${2:-3}"; }
# An absolute path of a plain alphabet. Each value is substituted into a unit file, where a space, a quote or a '%' would
# change its meaning, so anything else is refused rather than escaped.
need_path() {
  local name="$1" value="${!1:-}"
  [ -n "$value" ] || refuse "$name is not set" 5
  case "$value" in
    /*) ;;
    *) refuse "$name must be an absolute path" 5 ;;
  esac
  case "$value" in
    *[!A-Za-z0-9_./-]* | */../* | */./* | */.. | */. | *//*) refuse "$name must be a normalized absolute path of letters, digits, '_', '.', '/' or '-'" 5 ;;
  esac
}

mission_execution_state() {  # prints unset | set | unreadable for the configuration file $1
  python3 - "$1" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        document = json.load(handle)
except (OSError, ValueError):
    print("unreadable"); raise SystemExit(0)
mission = document.get("mission") if isinstance(document, dict) else None
print("set" if isinstance(mission, dict) and mission.get("execution") is not None else "unset")
PY
}

verify_tree() {  # verify_tree DIR COMMIT: every path of the commit's tree, and nothing else, with the same content
  local dir="$1" commit="$2" mode type hash path listed actual
  listed="$(mktemp)"; actual="$(mktemp)"
  # The loop runs in a pipeline's subshell, so its refusals end that subshell only: the pipeline's own status (pipefail)
  # is what is checked below, never left to errexit, which a caller's `||` would switch off.
  if ! git -C "$ROOT" ls-tree -r -z --full-tree "$commit" | while IFS= read -r -d '' entry; do
    mode="${entry%% *}"; rest="${entry#* }"; type="${rest%% *}"; rest="${rest#* }"; hash="${rest%%$'\t'*}"; path="${rest#*$'\t'}"
    printf '%s\n' "$path" >>"$listed"
    [ "$type" = blob ] || { echo "REFUSE: the commit holds a $type at a path; only files are released" >&2; exit 3; }
    if [ "$mode" = 120000 ]; then
      [ -L "$dir/$path" ] && [ "$(printf '%s' "$(readlink "$dir/$path")" | git hash-object --stdin)" = "$hash" ] \
        || { echo "REFUSE: a link of the release differs from the commit" >&2; exit 3; }
    else
      [ -f "$dir/$path" ] && [ ! -L "$dir/$path" ] && [ "$(git hash-object --no-filters "$dir/$path")" = "$hash" ] \
        || { echo "REFUSE: a file of the release differs from the commit" >&2; exit 3; }
    fi
  done; then
    rm -f -- "$listed" "$actual"
    return 3
  fi
  (cd "$dir" && find . \( -type f -o -type l \) ! -path "./$MARKER" -printf '%P\n') | LC_ALL=C sort >"$actual"
  LC_ALL=C sort -o "$listed" "$listed"
  if ! cmp -s "$listed" "$actual"; then rm -f -- "$listed" "$actual"; echo "REFUSE: the release holds a file the commit does not" >&2; exit 3; fi
  rm -f -- "$listed" "$actual"
}

case "$CMD" in
  stage)
    [ $# -eq 1 ] || usage
    need_path KC_RUNTIME_RELEASES
    COMMIT="$(git -C "$ROOT" rev-parse --verify --quiet "$1^{commit}")" || refuse "$1 names no commit of this checkout" 5
    case "$COMMIT" in *[!0-9a-f]* | '') refuse "the commit id is not hexadecimal" 5 ;; esac
    [ -d "$KC_RUNTIME_RELEASES" ] || { mkdir -p -- "$KC_RUNTIME_RELEASES"; chmod 700 -- "$KC_RUNTIME_RELEASES"; }
    NAME="${COMMIT:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
    FINAL="$KC_RUNTIME_RELEASES/$NAME"; STAGING="$KC_RUNTIME_RELEASES/.staging-$NAME"
    [ ! -e "$FINAL" ] && [ ! -e "$STAGING" ] || refuse "a release of that name already exists"
    mkdir -m 700 -- "$STAGING"
    trap 'chmod -R u+w -- "$STAGING" 2>/dev/null || true; rm -rf -- "$STAGING"' EXIT
    git -C "$ROOT" archive --format=tar "$COMMIT" | tar -x -C "$STAGING" -f -
    ( verify_tree "$STAGING" "$COMMIT" ) || exit 3
    printf 'commit %s\ntree %s\nbuilt_utc %s\nverified yes\n' "$COMMIT" "$(git -C "$ROOT" rev-parse "$COMMIT^{tree}")" \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$STAGING/$MARKER"
    chmod -R a-w -- "$STAGING"
    chmod u+w -- "$STAGING"   # the directory itself, so the rename below can move it; its contents stay read-only
    mv -T -- "$STAGING" "$FINAL"
    chmod a-w -- "$FINAL"
    trap - EXIT
    echo "staged $NAME (commit $COMMIT), verified against the commit's tree"
    ;;
  render)
    [ $# -eq 2 ] || usage
    RELEASE="$1"; UNIT_FILE="$2"
    need_path RELEASE; need_path UNIT_FILE; need_path KC_RUNTIME_PYTHON; need_path KC_RUNTIME_DATA_DIR; need_path KC_RUNTIME_CONFIG
    [ -f "$RELEASE/$MARKER" ] && grep -qx 'verified yes' "$RELEASE/$MARKER" || refuse "$RELEASE is not a verified release"
    [ -f "$RELEASE/packages/demo-runtime/demo_runtime/daemon.py" ] || refuse "the release holds no demo_runtime daemon"
    case "$UNIT_FILE" in */kiwi-cake-runtime.service) ;; *) refuse "the unit file must be named kiwi-cake-runtime.service" 5 ;; esac
    FLAG=""
    [ "${KC_RUNTIME_HARDWARE_COMMANDS:-}" = 1 ] && FLAG=" --enable-hardware-commands"
    TMP="$(mktemp "${UNIT_FILE}.XXXXXX")"
    sed -e "s|@@RELEASE_DIR@@|$RELEASE|g" -e "s|@@PYTHON@@|$KC_RUNTIME_PYTHON|g" -e "s|@@DATA_DIR@@|$KC_RUNTIME_DATA_DIR|g" \
      -e "s|@@CONFIG@@|$KC_RUNTIME_CONFIG|g" -e "s|@@HARDWARE_FLAG@@|$FLAG|g" "$TEMPLATE" >"$TMP"
    if grep -vE '^[[:space:]]*#' "$TMP" | grep -qE '@@[A-Z_]+@@'; then rm -f -- "$TMP"; refuse "a placeholder was left unfilled"; fi
    mv -f -- "$TMP" "$UNIT_FILE"
    echo "rendered the unit for release $(basename "$RELEASE"); hardware commands: $([ -n "$FLAG" ] && echo ENABLED || echo not enabled)"
    [ -f "$KC_RUNTIME_CONFIG" ] && echo "mission.execution: $(mission_execution_state "$KC_RUNTIME_CONFIG")"
    echo "nothing was installed, enabled or started"
    ;;
  status)
    [ $# -eq 0 ] || usage
    need_path KC_RUNTIME_RELEASES
    found=0
    for dir in "$KC_RUNTIME_RELEASES"/*/; do
      [ -d "$dir" ] || continue
      found=1
      if [ -f "$dir$MARKER" ]; then
        echo "release $(basename "$dir"): $(sed -n 's/^commit //p' "$dir$MARKER"), verified $(sed -n 's/^verified //p' "$dir$MARKER")"
      else
        echo "release $(basename "$dir"): NOT VERIFIED (no $MARKER record)"
      fi
    done
    [ "$found" -eq 1 ] || echo "no release under the releases directory"
    if [ -n "${KC_RUNTIME_CONFIG:-}" ]; then
      need_path KC_RUNTIME_CONFIG
      echo "mission.execution: $(mission_execution_state "$KC_RUNTIME_CONFIG")"
    fi
    ;;
  *) usage ;;
esac
