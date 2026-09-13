#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# tools/publish.sh: the maintainer's publish runbook for one release, run from
# the repository root on the maintainer's machine. Every irreversible step
# (push, release creation, visibility change) asks for a typed yes first; the
# from-scratch verification runs before the visibility change so that leaving
# the repository private never skips it.
#
#   bash tools/publish.sh KEYID STAGING_DIR SOURCE_REVISION
#
#   KEYID            the GPG key that signs the release (keys/README.md)
#   STAGING_DIR      a directory with pi5-aarch64/, holding the four
#                    gate-clean binaries (one tarball is published)
#   SOURCE_REVISION  the 40-character private source commit the binaries
#                    were built from (recorded in MANIFEST.txt only)
#
# The version is always the VERSION file at the repository root; it is not
# a command-line argument.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
cd "$ROOT" || exit 1
[ $# -eq 3 ] || { echo "usage: bash tools/publish.sh KEYID STAGING_DIR SOURCE_REVISION (the version is the VERSION file)"; exit 1; }
KEYID="$1"; STAGING="$2"; SRCREV="$3"
VERSION="$(tr -d '[:space:]' <VERSION)"
printf '%s' "$VERSION" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+$' || { echo "VERSION is '$VERSION', not vX.Y.Z"; exit 1; }
REPO="shinro-dev/kiwi-cake-demo"
BRANCH="release/$VERSION"

confirm() {
  printf '%s\nType yes to continue: ' "$1"
  IFS= read -r a
  [ "$a" = "yes" ] || { echo "stopped."; exit 1; }
}
step() { printf '\n=== %s ===\n' "$1"; }

step "0. preconditions"
for t in git gh gpg tar sha256sum; do command -v "$t" >/dev/null 2>&1 || { echo "$t is not on PATH"; exit 1; }; done
gh auth status >/dev/null 2>&1 || { echo "gh is not logged in (gh auth login)"; exit 1; }
# Exactly one expected untracked entry at this point: the checksum list
# tools/build-release.sh just wrote under releases/$VERSION/, which step 3
# below is what commits it. That is the pipeline's own intended handoff, not
# a dirty tree; anything else here still fails the check as before.
DIRTY="$(git status --porcelain | kc_dirty_entries "?? releases/$VERSION/")"
[ -z "$DIRTY" ] || { echo "the working tree is not clean"; printf '%s\n' "$DIRTY"; exit 1; }
git rev-parse --verify "$BRANCH" >/dev/null 2>&1 || { echo "branch $BRANCH does not exist"; exit 1; }
[ "$(git branch --show-current)" = "$BRANCH" ] || git switch "$BRANCH" || exit 1
grep -q 'PLACEHOLDER' keys/shinro-release-signing.pub.asc && { echo "keys/shinro-release-signing.pub.asc is still the placeholder; export the release key there first (keys/README.md)"; exit 1; }
grep -q 'PLACEHOLDER_FINGERPRINT' keys/README.md bin/lib/common.sh docs/verifying-a-release.md && { echo "the fingerprint placeholder is still present in keys/README.md, bin/lib/common.sh or docs/verifying-a-release.md"; exit 1; }
grep -q '\[PLACEHOLDER' LICENSE && { echo "LICENSE still carries counsel placeholders; set them first (LICENSE-NOTE.md)"; exit 1; }
echo "ok"

step "1. tests"
tests/run-all.sh || exit 1

step "2. the strings gate over the staged binaries"
tools/strings-gate.sh "$STAGING"/pi5-aarch64/{cake-resident,admin-probe,demo-plan,demo-preflight} || exit 1

step "3. assemble and sign"
tools/build-release.sh --staging "$STAGING" --source-revision "$SRCREV" --version "$VERSION" --sign "$KEYID" || exit 1
# Verify this version's tarball by path, not the whole dist/ directory: an
# older version's tarballs left there would otherwise fail the directory
# form, which checks every tarball it finds against one SHA256SUMS.
bin/verify.sh --version "$VERSION" dist/kiwi-cake-demo-"$VERSION"-pi5-aarch64.tar.gz || exit 1
git add "releases/$VERSION/SHA256SUMS"
git diff --cached --quiet || git commit -q -m "Record the $VERSION release checksums" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
git log --oneline -1

step "4. merge to main (local)"
git fetch origin || exit 1
git switch main && git merge --ff-only origin/main && git merge --ff-only "$BRANCH" || exit 1
git log --oneline -1

confirm "5. Push main and the signed tag $VERSION to origin ($REPO). This is public-facing once the repository is public."
if git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null; then
  [ "$(git rev-parse "$VERSION^{commit}")" = "$(git rev-parse HEAD)" ] || { echo "tag $VERSION exists and does not point at HEAD"; exit 1; }
else
  git tag -s -u "$KEYID" "$VERSION" -m "kiwi-cake-demo $VERSION" || exit 1
fi
git push origin main "$VERSION" || exit 1

step "6. the release notes"
NOTES="$(mktemp "${TMPDIR:-/tmp}/kc-notes.XXXXXX")" || exit 1
trap 'rm -f -- "$NOTES"' EXIT
# Only this version's section of RELEASE_NOTES.md is the release body: from
# the '## <version>' heading to the line before the next '## ' heading.
awk -v h="## $VERSION" '$0 == h {p=1; next} p && /^## / {exit} p' RELEASE_NOTES.md >"$NOTES"
grep -q '[^[:space:]]' "$NOTES" || { echo "RELEASE_NOTES.md has no text under '## $VERSION'"; exit 1; }
echo "release body ($(wc -l <"$NOTES") lines):"; sed 's/^/  /' "$NOTES"
confirm "6. Create the GitHub Release $VERSION with the tarballs, signatures, SHA256SUMS and the notes above."
gh release create "$VERSION" dist/kiwi-cake-demo-"$VERSION"-*.tar.gz dist/kiwi-cake-demo-"$VERSION"-*.tar.gz.asc dist/SHA256SUMS dist/SHA256SUMS.asc \
  --repo "$REPO" --title "kiwi-cake-demo $VERSION" --notes-file "$NOTES" || exit 1

step "7. verify the live release from scratch"
# Runs before the visibility change so that declining it never skips this
# check; gh clones with its own credentials while the repository is private.
TMP="$(mktemp -d)"
( gh repo clone "$REPO" "$TMP/clone" -- -q --branch main && cd "$TMP/clone" && bin/fetch-release.sh --target pi5-aarch64 --version "$VERSION" ) ||
  { echo "the live release did not verify from a fresh clone"; exit 1; }
echo "live release verified from a fresh clone"

printf '\n8. Make %s PUBLIC. This cannot be quietly undone: anything pushed is public from here on.\nType yes to continue, or anything else to leave the repository private: ' "$REPO"
IFS= read -r a
if [ "$a" = "yes" ]; then
  gh repo edit "$REPO" --visibility public || exit 1
  echo "the repository is public"
else
  echo "visibility unchanged; when ready: gh repo edit $REPO --visibility public"
fi
exit 0
