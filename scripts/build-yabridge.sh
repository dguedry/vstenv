#!/bin/bash
# Build yabridge from git against a vstenv Wine build and package it.
#
#   scripts/build-yabridge.sh <wine-build-dir> [out-dir] [git-ref]
#
# Why: yabridge's last release (5.1.1, Nov 2024) predates Wine 9.22's window
# changes; with newer Wine, mouse clicks in bridged plugin GUIs land at the
# wrong place in every DAW. The fix is in yabridge's master branch. This
# builds master with the pinned Wine's own winegcc/headers so plugin host and
# Wine always match, and produces yabridge-<ref>-wine-<version>.tar.gz whose
# layout equals an upstream release tarball (a yabridge/ directory).
#
# Needs: git, meson, ninja, gcc/g++, pkg-config, libxcb1-dev, libdbus-1-dev (Ubuntu names).
# yabridgectl (Rust) is not rebuilt; it is taken from the upstream release.
set -euo pipefail
WINE="${1:?wine build dir, e.g. ~/.local/share/vstenv/wine/wine-11.17-staging-amd64-wow64}"
OUT="${2:-$PWD}"
REF="${3:-master}"
YCTL_RELEASE="${YCTL_RELEASE:-5.1.1}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

# "wine-11.17 (Staging)" -> "11.17"; the build name is the fallback
WINE_VERSION="$("$WINE/bin/wine" --version 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+)+' | head -1 || true)"
WINE_VERSION="${WINE_VERSION:-$(basename "$WINE" | grep -oE '[0-9]+(\.[0-9]+)+' | head -1)}"

# winegcc from a portable Wine build looks for the exact gcc it was built with
# (e.g. gcc-15); point that name at the system compiler.
mkdir -p "$WORK/ccshim"
want="$(strings "$WINE/bin/winegcc" | grep -oE '^gcc-[0-9]+$' | head -1 || true)"; want="${want:-gcc-15}"
ln -sf "$(command -v gcc)" "$WORK/ccshim/$want"
ln -sf "$(command -v g++)" "$WORK/ccshim/${want/gcc/g++}"
ln -sf "$(command -v cpp)" "$WORK/ccshim/${want/gcc/cpp}"
export PATH="$WINE/bin:$WORK/ccshim:$PATH"

echo ">> cloning yabridge ($REF)"
git clone -q --depth 1 --branch "$REF" https://github.com/robbert-vdh/yabridge.git "$WORK/src"
SHA="$(git -C "$WORK/src" rev-parse --short HEAD)"

echo ">> building against Wine $WINE_VERSION with $(winegcc --version 2>&1 | head -1 || echo winegcc)"
( cd "$WORK/src"
  meson setup build --buildtype=release --cross-file=cross-wine.conf --unity=on --unity-size=1000 -Dbitbridge=false > "$WORK/meson.log" 2>&1 || { tail -30 "$WORK/meson.log"; exit 1; }
  ninja -C build > "$WORK/ninja.log" 2>&1 || { tail -30 "$WORK/ninja.log"; exit 1; } )

echo ">> fetching yabridgectl from upstream release $YCTL_RELEASE"
curl -sSL -o "$WORK/rel.tar.gz" "https://github.com/robbert-vdh/yabridge/releases/download/$YCTL_RELEASE/yabridge-$YCTL_RELEASE.tar.gz"
tar -xzf "$WORK/rel.tar.gz" -C "$WORK" yabridge/yabridgectl

PKG="$WORK/pkg/yabridge"; mkdir -p "$PKG"
cp "$WORK/src"/build/libyabridge-{vst2,vst3,clap}.so "$WORK/src"/build/libyabridge-chainloader-{vst2,vst3,clap}.so \
   "$WORK/src"/build/yabridge-host.exe "$WORK/src"/build/yabridge-host.exe.so "$WORK/src"/README.md "$WORK/src"/CHANGELOG.md "$PKG/"
cp "$WORK/yabridge/yabridgectl" "$PKG/"
cat > "$PKG/vstenv-build.json" <<JSON
{"yabridge_ref": "$REF", "yabridge_commit": "$SHA", "wine_version": "$WINE_VERSION", "yabridgectl_release": "$YCTL_RELEASE", "built": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
JSON

NAME="yabridge-$SHA-wine-$WINE_VERSION.tar.gz"
mkdir -p "$OUT"; tar -czf "$OUT/$NAME" -C "$WORK/pkg" yabridge
echo ">> $OUT/$NAME"
