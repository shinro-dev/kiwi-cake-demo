#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# tools/publish.sh: the maintainer's publish runbook for one release, run from
# the repository root on the maintainer's machine. Every irreversible step
# (push, release creation, visibility change) asks for a typed yes first.
#
#   bash tools/publish.sh KEYID STAGING_DIR SOURCE_REVISION [VERSION]
#
#   KEYID            the GPG key that signs the release (keys/README.md)
#   STAGING_DIR      a directory with pi5-aarch64/ and pi4-aarch64/, each
#                    holding the four gate-clean binaries
#   SOURCE_REVISION  the 40-character private source commit the binaries
#                    were built from (recorded in MANIFEST.txt only)
#   VERSION          default v0.1.0
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
KEYID="${1:?usage: bash tools/publish.sh KEYID STAGING_DIR SOURCE_REVISION [VERSION]}"
STAGING="${2:?STAGING_DIR is required}"
SRCREV="${3:?SOURCE_REVISION is required}"
VERSION="${4:-v0.1.0}"
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
[ -z "$(git status --porcelain)" ] || { echo "the working tree is not clean"; git status --short; exit 1; }
git rev-parse --verify "$BRANCH" >/dev/null 2>&1 || { echo "branch $BRANCH does not exist"; exit 1; }
[ "$(git branch --show-current)" = "$BRANCH" ] || git switch "$BRANCH" || exit 1
grep -q 'PLACEHOLDER' keys/shinro-release-signing.pub.asc && { echo "keys/shinro-release-signing.pub.asc is still the placeholder; export the release key there first (keys/README.md)"; exit 1; }
grep -q 'PLACEHOLDER_FINGERPRINT' keys/README.md bin/lib/common.sh docs/verifying-a-release.md && { echo "the fingerprint placeholder is still present in keys/README.md, bin/lib/common.sh or docs/verifying-a-release.md"; exit 1; }
grep -q '\[PLACEHOLDER' LICENSE && { echo "LICENSE still carries counsel placeholders; set them first (LICENSE-NOTE.md)"; exit 1; }
echo "ok"

step "1. tests"
tests/run-all.sh || exit 1

step "2. the strings gate over the staged binaries"
tools/strings-gate.sh "$STAGING"/pi5-aarch64/{cake-resident,admin-probe,demo-plan,demo-preflight} \
  "$STAGING"/pi4-aarch64/{cake-resident,admin-probe,demo-plan,demo-preflight} || exit 1

step "3. assemble and sign"
tools/build-release.sh --staging "$STAGING" --source-revision "$SRCREV" --version "$VERSION" --sign "$KEYID" || exit 1
bin/verify.sh --version "$VERSION" dist || exit 1
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

confirm "6. Create the GitHub Release $VERSION with the tarballs, signatures and SHA256SUMS."
gh release create "$VERSION" dist/kiwi-cake-demo-"$VERSION"-*.tar.gz dist/kiwi-cake-demo-"$VERSION"-*.tar.gz.asc dist/SHA256SUMS dist/SHA256SUMS.asc \
  --repo "$REPO" --title "kiwi-cake-demo $VERSION" --notes-file RELEASE_NOTES.md || exit 1

confirm "7. Make $REPO PUBLIC. This cannot be quietly undone: anything pushed is public from here on."
gh repo edit "$REPO" --visibility public --accept-visibility-change-consequences || exit 1

step "8. verify the live release from scratch"
TMP="$(mktemp -d)"
git clone -q "https://github.com/$REPO.git" "$TMP/clone" && cd "$TMP/clone" && bin/fetch-release.sh --target pi5-aarch64 --version "$VERSION" && echo "live release verified from a fresh clone"
