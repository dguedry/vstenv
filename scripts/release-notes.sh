#!/usr/bin/env bash
# Print the GitHub release page for a tag: what to download and install, what
# the other attached files are (the app fetches them itself), and the change
# note recorded for that version in the metainfo. CI passes the result to
# `gh release create --notes-file`.
#
#   scripts/release-notes.sh v0.1.3
set -euo pipefail
TAG="${1:?tag, e.g. v0.1.3}"
VERSION="${TAG#v}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
METAINFO="$HERE/data/io.github.dguedry.vstenv.metainfo.xml"
NOTE="$(python3 - "$METAINFO" "$VERSION" <<'PY'
import re, sys, html
text = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r'<release version="%s"[^>]*>(.*?)</release>' % re.escape(sys.argv[2]), text, re.S)
m = m and re.search(r'<p>(.*?)</p>', m.group(1), re.S)
print(html.unescape(m.group(1).strip()) if m else "")
PY
)"
PREV="$(git -C "$HERE" tag --list 'v*' --sort=-version:refname | grep -vx "$TAG" | head -1 || true)"
WINE="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from vstenv.wine import WINE_BUILD; print(WINE_BUILD["name"])' "$HERE")"
cat <<MD
## Install

**Download \`vstenv.flatpak\`** below, then:

\`\`\`sh
flatpak install --user vstenv.flatpak
flatpak run io.github.dguedry.vstenv
\`\`\`

That is the whole app. It needs the Flathub remote for the GNOME runtime; on
first run it downloads the rest by itself.

## The other files

You do not need to download these. The app fetches them from this page when
it sets up; they are attached here so that download is versioned and verified:

- \`$WINE.tar.xz\` — the Wine build the app runs everything with
- \`wine-fixes-*.tar.gz\` — the patched Wine DLLs some vendors' programs need (see \`patches/wine/README.md\`)
- \`yabridge-*.tar.gz\` — yabridge built against that Wine, which bridges the plugins to Linux DAWs

The \`Source code\` archives are GitHub's automatic copies of the repository.

## What changed

${NOTE:-See the commits below.}
MD
if [ -n "$PREV" ]; then
  echo
  echo "**Full Changelog**: https://github.com/dguedry/vstenv/compare/$PREV...$TAG"
fi
