#!/bin/bash
# Build the Wine DLLs that vstenv patches, for the pinned Wine, and package them.
#
#   scripts/build-wine-fixes.sh <wine-version> [out-dir]
#
# Why: a few Wine DLLs need patches for the vendors' programs (see
# patches/wine/README.md): dcomp.dll (DirectComposition for Steinberg's
# products) and advapi32.dll (credential attributes, which Steinberg's
# License Engine needs to keep its sign-in), dxgi.dll (a WaitForVBlank that
# returns, for JUCE 8 GUIs) and ole32.dll (drag-drop teardown that survives a
# stale target, for WebView2 apps), and hidparse.sys (HID descriptors with
# hundreds of usages, for Komplete Kontrol S-series MK1 keyboards). This applies every
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
# PE modules to rebuild, by file name. Wine keeps a DLL in dlls/<name> and a
# driver in dlls/<name>.sys, so the source folder is the name minus a .dll suffix.
MODULES=(dcomp.dll advapi32.dll dxgi.dll ole32.dll wined3d.dll win32u.dll hidparse.sys)
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
for M in "${MODULES[@]}"; do echo "== build $M"; make -j"$(nproc)" "dlls/${M%.dll}/x86_64-windows/$M" >/dev/null; done

echo "== package"
PKG="$WORK/wine-fixes"; mkdir -p "$PKG"
for M in "${MODULES[@]}"; do
    x86_64-w64-mingw32-strip --strip-unneeded -o "$PKG/$M" "$SRC/dlls/${M%.dll}/x86_64-windows/$M" 2>/dev/null || cp "$SRC/dlls/${M%.dll}/x86_64-windows/$M" "$PKG/$M"
done
# powershell.exe in place of Wine's stub (tools/psshim): it answers "is this program
# running?" honestly and exits 0 for anything else, as the stub did. Built for both
# bitnesses and tagged "Wine builtin DLL" at 0x40, as Wine's own builds are, or Wine
# will not load it from lib/wine as its builtin.
echo "== build powershell.exe (psshim)"
mkdir -p "$PKG/i386-windows"
x86_64-w64-mingw32-gcc -O2 -municode -mconsole -s -o "$PKG/powershell.exe" "$HERE/tools/psshim/powershell.c" -lcrypt32 -ladvapi32 -lshell32
i686-w64-mingw32-gcc -O2 -municode -mconsole -s -o "$PKG/i386-windows/powershell.exe" "$HERE/tools/psshim/powershell.c" -lcrypt32 -ladvapi32 -lshell32
python3 - "$PKG/powershell.exe" "$PKG/i386-windows/powershell.exe" <<'EOF'
import sys
for f in sys.argv[1:]:
    b = bytearray(open(f, "rb").read())
    assert b[:2] == b"MZ" and int.from_bytes(b[0x3c:0x40], "little") >= 0x60, f
    tag = b"Wine builtin DLL"; b[0x40:0x60] = tag + b"\0" * (0x20 - len(tag))
    open(f, "wb").write(bytes(b))
EOF
python3 - "$PKG" "$VERSION" "${PATCHES[@]}" <<'EOF'
import hashlib, json, os, sys
from pathlib import Path
pkg, version, patches = Path(sys.argv[1]), sys.argv[2], [os.path.basename(p) for p in sys.argv[3:]]
files = {f.relative_to(pkg).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
         for f in sorted(pkg.rglob("*")) if f.is_file() and f.suffix in (".dll", ".exe", ".sys")}
(pkg / "manifest.json").write_text(json.dumps({"wine_version": version, "files": files, "patches": patches,
    "note": "dcomp: DirectComposition for Steinberg's products; advapi32: credential attributes for Steinberg's License Engine sign-in"}, indent=2))
EOF
mkdir -p "$OUT"
tar -czf "$OUT/wine-fixes-$VERSION.tar.gz" -C "$WORK" wine-fixes
ls -la "$OUT/wine-fixes-$VERSION.tar.gz"
