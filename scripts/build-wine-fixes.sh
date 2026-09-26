#!/bin/bash
# Build the Wine DLLs that vstenv patches, for the pinned Wine, and package them.
#
#   scripts/build-wine-fixes.sh <wine-version> [out-dir]
#
# Why: Steinberg's current products (HALion Sonic 7 and that generation of
# Cubase/Dorico) draw through DirectComposition. Wine implements it only as
# stubs; wine-staging carries a near-complete implementation whose last gaps
# (device-level surfaces, virtual surfaces, Direct2D rendering devices) are
# closed by patches/wine/dcomp-steinberg-<version>.patch. This builds just
# dcomp.dll from the Wine sources with those patches, using the MinGW cross
# compiler, and produces wine-fixes-<version>.tar.gz: the DLL plus a manifest.
# The app installs it into its Wine build (winefixes.py).
#
# Needs: x86_64-w64-mingw32-gcc, flex, bison, gcc, make, autoconf, perl, curl, tar, xz, python3.
set -euo pipefail
VERSION="${1:?wine version, e.g. 11.17}"
OUT="${2:-$PWD}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PATCH="$HERE/patches/wine/dcomp-steinberg-$VERSION.patch"
[ -f "$PATCH" ] || { echo "no patch for Wine $VERSION: $PATCH" >&2; exit 1; }
# WINE_FIXES_WORK=<dir> keeps the patched tree (unstripped DLL for symbols).
if [ -n "${WINE_FIXES_WORK:-}" ]; then WORK="$WINE_FIXES_WORK"; mkdir -p "$WORK"; else WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT; fi
cd "$WORK"

echo "== sources"
curl -sSL -o wine.tar.xz "https://dl.winehq.org/wine/source/${VERSION%%.*}.x/wine-$VERSION.tar.xz"
curl -sSL -o staging.tar.gz "https://github.com/wine-staging/wine-staging/archive/refs/tags/v$VERSION.tar.gz"
tar -xJf wine.tar.xz && tar -xzf staging.tar.gz
SRC="$WORK/wine-$VERSION"

# The whole staging set, not just DirectComposition: the pinned build is
# wine-staging, and dcomp talks to its wineserver by request numbers that only
# match when the tree has every staging change to server/protocol.def. This
# also regenerates configure and the request headers.
echo "== wine-staging: every patch (the pinned build is a staging build)"
python3 "wine-staging-$VERSION/staging/patchinstall.py" DESTDIR="$SRC" --backend=patch --all >/dev/null
echo "== vstenv patch"
patch -d "$SRC" -p1 < "$PATCH"

echo "== configure (tools + PE modules only)"
cd "$SRC"
./configure --enable-win64 --disable-tests --without-x --without-freetype --without-fontconfig --without-alsa --without-pulse \
    --without-vulkan --without-gstreamer --without-opengl --without-wayland --without-dbus --without-usb --without-udev \
    --without-gnutls --without-krb5 --without-sane --without-gphoto --without-cups --without-netapi \
    --without-opencl --without-pcap --without-pcsclite --without-capi --without-v4l2 --without-oss --without-ffmpeg \
    --without-inotify --without-sdl >/dev/null
for i in include/d2d1*.idl include/dwrite*.idl; do make "${i%.idl}.h" >/dev/null; done
echo "== build dcomp.dll"
make -j"$(nproc)" dlls/dcomp/x86_64-windows/dcomp.dll >/dev/null
DLL="$SRC/dlls/dcomp/x86_64-windows/dcomp.dll"

echo "== package"
PKG="$WORK/wine-fixes"; mkdir -p "$PKG"
cp "$DLL" "$PKG/dcomp.dll.unstripped"; x86_64-w64-mingw32-strip --strip-unneeded -o "$PKG/dcomp.dll" "$DLL" 2>/dev/null || cp "$DLL" "$PKG/dcomp.dll"
rm -f "$PKG/dcomp.dll.unstripped"
python3 - "$PKG" "$VERSION" "$(basename "$PATCH")" <<'EOF'
import hashlib, json, sys
from pathlib import Path
pkg, version, patch = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
files = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(pkg.glob("*.dll"))}
(pkg / "manifest.json").write_text(json.dumps({"wine_version": version, "files": files, "patches": [patch],
    "note": "dcomp: wine-staging's DirectComposition plus device-level/virtual surfaces and Direct2D rendering devices (Steinberg)"}, indent=2))
EOF
mkdir -p "$OUT"
tar -czf "$OUT/wine-fixes-$VERSION.tar.gz" -C "$WORK" wine-fixes
ls -la "$OUT/wine-fixes-$VERSION.tar.gz"
