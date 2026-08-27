#!/usr/bin/env bash
# Bump manifest.json, commit, tag, and push so GitHub Actions can create the release.
#
# Usage:
#   ./scripts/release.sh              # patch bump
#   ./scripts/release.sh minor
#   ./scripts/release.sh major
#   ./scripts/release.sh 0.22.0
#   ./scripts/release.sh patch --dry-run

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DRY_RUN=0
SPEC="patch"
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,11p' "$0"
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
PRINT_FLAG=()
if [ "$DRY_RUN" -eq 1 ]; then
  PRINT_FLAG=(--print)
fi
VERSION="$(python3 scripts/bump_version.py "$SPEC" "${PRINT_FLAG[@]}")"
TAG="v${VERSION}"

if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "Tag $TAG already exists." >&2
  exit 1
fi

run git add "$MANIFEST"
run git commit -m "Bump version to ${VERSION}" -- "$MANIFEST"
run git tag "$TAG"
run git push "$REMOTE" "$BRANCH"
run git push "$REMOTE" "$TAG"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry run: would release $TAG on $REMOTE/$BRANCH"
else
  echo "Released $TAG on $REMOTE/$BRANCH"
fi
