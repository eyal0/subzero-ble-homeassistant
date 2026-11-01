#!/usr/bin/env bash
# Bump manifest.json, commit, tag, and push so GitHub Actions can create the release.
#
# Usage:
#   ./scripts/release.sh                 # patch bump
#   ./scripts/release.sh minor
#   ./scripts/release.sh major
#   ./scripts/release.sh 0.22.0
#   ./scripts/release.sh patch --pre     # 0.59.0 becomes 0.59.1b1
#   ./scripts/release.sh current --pre   # 0.59.1b1 becomes 0.59.1b2
#   ./scripts/release.sh patch --dry-run
#
# A version ending in bN is tagged vX.Y.ZbN. The release workflow publishes
# that tag as a GitHub pre-release. current without --pre on a beta version
# publishes the stable tag (0.59.1b2 becomes 0.59.1).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DRY_RUN=0
PRE=0
SPEC="patch"
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --pre) PRE=1 ;;
    -h|--help)
      sed -n '2,/^$/p' "$0"
      exit 0
      ;;
    *) SPEC="$arg" ;;
  esac
done

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a git repository." >&2
  exit 1
fi

BRANCH="$(git branch --show-current)"
if [ -z "$BRANCH" ]; then
  echo "Not on a branch (detached HEAD). Checkout main first." >&2
  exit 1
fi

if git remote get-url eyal0 >/dev/null 2>&1; then
  REMOTE=eyal0
elif git remote get-url origin >/dev/null 2>&1; then
  REMOTE=origin
else
  echo "No git remote named eyal0 or origin." >&2
  exit 1
fi

MANIFEST="custom_components/subzero_ble/manifest.json"
manifest_version() {
  python3 -c "import json; print(json.load(open('$MANIFEST'))['version'])"
}
OLD="$(manifest_version)"
BUMP_ARGS=("$SPEC")
if [ "$PRE" -eq 1 ]; then
  BUMP_ARGS+=(--pre)
fi
VERSION="$(python3 scripts/bump_version.py "${BUMP_ARGS[@]}" --print)"
TAG="v${VERSION}"

if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "Tag $TAG already exists." >&2
  exit 1
fi

if [ "$VERSION" != "$OLD" ]; then
  if [ "$DRY_RUN" -eq 0 ]; then
    python3 scripts/bump_version.py "${BUMP_ARGS[@]}"
  fi
  run git add "$MANIFEST"
  run git commit -m "Bump version to ${VERSION}" -- "$MANIFEST"
fi
run git tag "$TAG"
run git push "$REMOTE" "$BRANCH"
run git push "$REMOTE" "$TAG"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry run: would release $TAG on $REMOTE/$BRANCH"
else
  echo "Released $TAG on $REMOTE/$BRANCH"
fi
