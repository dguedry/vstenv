#!/usr/bin/env bash
# Cut a release: bump the version, add a metainfo entry, commit, tag, push.
# CI (on the v* tag) builds vstenv.flatpak and attaches it to a GitHub release,
# which the README's "latest release" download link then serves.
#
# Usage:  scripts/release.sh <version> "<one-line changelog>"
#         scripts/release.sh 0.1.2 "Share /tmp so DAW plugins reach the NTK daemon"
#
#   --dry-run   make the edits and show them, but do not commit/tag/push
#               (reverts the working-tree edits afterwards)
set -euo pipefail

DRY=0
args=()
for a in "$@"; do
  if [ "$a" = "--dry-run" ]; then DRY=1; else args+=("$a"); fi
done
set -- "${args[@]}"

VERSION="${1:-}"
NOTE="${2:-}"
die() { echo "release: $*" >&2; exit 1; }

[ -n "$VERSION" ] || die "usage: scripts/release.sh <version> \"<changelog>\" [--dry-run]"
[ -n "$NOTE" ]    || die "a one-line changelog is required (it goes in the AppStream release notes)"
echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$' || die "version must be X.Y.Z, got '$VERSION'"

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

INIT="vstenv/__init__.py"
PYPROJECT="pyproject.toml"
METAINFO="data/io.github.dguedry.vstenv.metainfo.xml"

# --- preflight ---------------------------------------------------------------
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "main" ] || die "not on main (on '$BRANCH'); release from main"
if [ "$DRY" = 0 ]; then
  git diff --quiet -- "$INIT" "$PYPROJECT" "$METAINFO" \
    || die "version/metainfo files have uncommitted changes; commit or stash first"
fi
git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null && die "tag v$VERSION already exists"
grep -q "version=\"$VERSION\"" "$METAINFO" && die "$METAINFO already has a $VERSION release entry"

CUR="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$INIT")"
echo "release: $CUR -> $VERSION"

# --- edits (shared with the auto-tag CI job) -----------------------------------
python3 "$ROOT/scripts/bump.py" "$VERSION" "$NOTE"

# --- validate the metainfo ---------------------------------------------------
if command -v flatpak >/dev/null && flatpak info org.flatpak.Builder >/dev/null 2>&1; then
  echo "release: linting metainfo"
  flatpak run --command=flatpak-builder-lint org.flatpak.Builder appstream "$METAINFO" >/dev/null \
    || die "metainfo failed appstream lint (edits left in the working tree for inspection)"
fi

if [ "$DRY" = 1 ]; then
  echo "release: --dry-run, showing diff then reverting"
  git --no-pager diff -- "$INIT" "$PYPROJECT" "$METAINFO"
  git checkout -- "$INIT" "$PYPROJECT" "$METAINFO"
  exit 0
fi

# --- commit, tag, push -------------------------------------------------------
git add "$INIT" "$PYPROJECT" "$METAINFO"
git commit -q -m "Release $VERSION

$NOTE

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git tag -a "v$VERSION" -m "vstenv $VERSION"
git push origin main "v$VERSION"

echo
echo "release: pushed v$VERSION. CI will build and attach vstenv.flatpak to the release."
echo "  watch:   gh run watch \$(gh run list --branch v$VERSION --limit 1 --json databaseId --jq '.[0].databaseId')"
echo "  release: https://github.com/dguedry/vstenv/releases/tag/v$VERSION"
