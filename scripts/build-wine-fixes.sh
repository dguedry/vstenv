#!/bin/bash
# Build the Wine DLLs that vstenv patches, for the pinned Wine, and package them.
#
#   scripts/build-wine-fixes.sh <wine-version> [out-dir]
#
# Why: a few Wine DLLs need patches for the vendors' programs (see
# patches/wine/README.md): dcomp.dll (DirectComposition for Steinberg's
# products) and advapi32.dll (credential attributes, which Steinberg's
# License Engine needs to keep its sign-in) and dxgi.dll (a WaitForVBlank that
# returns, for JUCE 8 GUIs). This applies every
# patches/wine/*-<version>.patch to the Wine sources (on top of the full
# wine-staging set, since the pinned build is a staging build), builds just
# those DLLs with the MinGW cross compiler, and produces
# wine-fixes-<version>.tar.gz: the DLLs plus a manifest. The app installs
# them into its Wine build (winefixes.py).
#
# Needs: x86_64-w64-mingw32-gcc, flex, bison, gcc, make, autoconf, perl, curl, tar, xz, python3.
set -euo pipefail
VERSION="${1:?wine version, e.g. 11.17}"
OUT="${2:-$PWD}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PATCHES=("$HERE"/patches/wine/*-"$VERSION".patch)
[ -f "${PATCHES[0]}" ] || { echo "no patches for Wine $VERSION under $HERE/patches/wine" >&2; exit 1; }
DLLS=(dcomp advapi32 dxgi)
# WINE_FIXES_WORK=<dir> keeps the patched tree (unstripped DLL for symbols).
if [ -n "${WINE_FIXES_WORK:-}" ]; then WORK="$WINE_FIXES_WORK"; mkdir -p "$WORK"; else WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT; fi
cd "$WORK"

echo "== sources"
# development releases live under source/<major>.x/, stable <major>.0 releases under source/<major>.0/
case "$VERSION" in *.0) SRCDIR="${VERSION%%.*}.0";; *) SRCDIR="${VERSION%%.*}.x";; esac
curl -fsSL -o wine.tar.xz "https://dl.winehq.org/wine/source/$SRCDIR/wine-$VERSION.tar.xz"
curl -fsSL -o staging.tar.gz "https://github.com/wine-staging/wine-staging/archive/refs/tags/v$VERSION.tar.gz"
tar -xJf wine.tar.xz && tar -xzf staging.tar.gz
SRC="$WORK/wine-$VERSION"

# The whole staging set, not just DirectComposition: the pinned build is
# wine-staging, and dcomp talks to its wineserver by request numbers that only
# match when the tree has every staging change to server/protocol.def. This
# also regenerates configure and the request headers.
echo "== wine-staging: every patch (the pinned build is a staging build)"
python3 "wine-staging-$VERSION/staging/patchinstall.py" DESTDIR="$SRC" --backend=patch --all >/dev/null
for PATCH in "${PATCHES[@]}"; do echo "== vstenv patch: $(basename "$PATCH")"; patch -d "$SRC" -p1 < "$PATCH"; done

echo "== configure (tools + PE modules only)"
cd "$SRC"
./configure --enable-win64 --disable-tests --without-x --without-freetype --without-fontconfig --without-alsa --without-pulse \
    --without-vulkan --without-gstreamer --without-opengl --without-wayland --without-dbus --without-usb --without-udev \
    --without-gnutls --without-krb5 --without-sane --without-gphoto --without-cups --without-netapi \
    --without-opencl --without-pcap --without-pcsclite --without-capi --without-v4l2 --without-oss --without-ffmpeg \
    --without-inotify --without-sdl >/dev/null
for i in include/d2d1*.idl include/dwrite*.idl; do make "${i%.idl}.h" >/dev/null; done
for DLL in "${DLLS[@]}"; do echo "== build $DLL.dll"; make -j"$(nproc)" "dlls/$DLL/x86_64-windows/$DLL.dll" >/dev/null; done

echo "== package"
PKG="$WORK/wine-fixes"; mkdir -p "$PKG"
for DLL in "${DLLS[@]}"; do
    x86_64-w64-mingw32-strip --strip-unneeded -o "$PKG/$DLL.dll" "$SRC/dlls/$DLL/x86_64-windows/$DLL.dll" 2>/dev/null || cp "$SRC/dlls/$DLL/x86_64-windows/$DLL.dll" "$PKG/$DLL.dll"
done
python3 - "$PKG" "$VERSION" "${PATCHES[@]}" <<'EOF'
import hashlib, json, os, sys
from pathlib import Path
pkg, version, patches = Path(sys.argv[1]), sys.argv[2], [os.path.basename(p) for p in sys.argv[3:]]
files = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(pkg.glob("*.dll"))}
(pkg / "manifest.json").write_text(json.dumps({"wine_version": version, "files": files, "patches": patches,
    "note": "dcomp: DirectComposition for Steinberg's products; advapi32: credential attributes for Steinberg's License Engine sign-in"}, indent=2))
EOF
mkdir -p "$OUT"
tar -czf "$OUT/wine-fixes-$VERSION.tar.gz" -C "$WORK" wine-fixes
ls -la "$OUT/wine-fixes-$VERSION.tar.gz"
