#!/bin/bash
# Build the patched Wine files from this checkout's patches and install them into the vstenv Wine runtime.
#
#   scripts/install-from-source.sh [--version 11.17] [--work DIR] [--keep-work] [--force] [--dry-run]
#
# --version       Wine version to build (default: the pinned one in vstenv/wine.py)
# --work DIR      build in DIR (kept) instead of a fresh temporary directory
# --keep-work     keep the temporary directory
# --force         go on although Cubase14.exe or a wineserver is running
# --dry-run       check the tools and print the steps; build and install nothing
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=""; WORK=""; KEEP=0; FORCE=0; DRY=0
die() { echo "install-from-source: $*" >&2; exit 1; }
while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="${2:?--version needs a value}"; shift 2;;
        --work) WORK="${2:?--work needs a directory}"; shift 2;;
        --keep-work) KEEP=1; shift;;
        --force) FORCE=1; shift;;
        --dry-run) DRY=1; shift;;
        -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0;;
        *) die "unknown option $1 (see --help)";;
    esac
done
[ -n "$VERSION" ] || VERSION="$(cd "$HERE" && python3 -c 'from vstenv.winefixes import wine_version; print(wine_version())')"

# (a) tools and headers, mirroring build-wine-fixes.sh (its feature checks need these -dev packages)
missing=()
for t in x86_64-w64-mingw32-gcc i686-w64-mingw32-gcc flex bison gcc make autoconf perl curl tar xz python3 patch pkg-config; do
    command -v "$t" >/dev/null || missing+=("$t")
done
if command -v pkg-config >/dev/null; then
    # winex11.so: X, OpenGL, Vulkan; win32u.so: FreeType and fontconfig
    for p in x11 xext xcursor xi xrandr xrender xfixes xinerama xcomposite xxf86vm gl vulkan freetype2 fontconfig; do
        pkg-config --exists "$p" || missing+=("headers: $p (pkg-config)")
    done
fi
[ ${#missing[@]} -eq 0 ] || die "missing: ${missing[*]}"
# WineASIO loads libjack at run time: no JACK headers are needed to build it.

# (b) a running Windows program may be using the runtime
if [ "$DRY" -eq 1 ]; then echo "== would check: Cubase14.exe and wineserver are not running"
else
if pgrep -x Cubase14.exe >/dev/null && [ "$FORCE" -eq 0 ]; then die "Cubase14.exe is running; close it (or --force)"; fi
if pgrep -x wineserver >/dev/null; then
    [ "$FORCE" -eq 1 ] || die "a wineserver is running (a Windows program may use the runtime); close it or pass --force"
    echo "warning: a wineserver is running" >&2
fi
fi

# (c) launcher: vstenv on PATH, else the flatpak, else this checkout
if command -v vstenv >/dev/null; then LAUNCH=(vstenv); FLATPAK=0
elif command -v flatpak >/dev/null && flatpak info io.github.dguedry.vstenv >/dev/null 2>&1; then LAUNCH=(flatpak run --command=vstenv io.github.dguedry.vstenv); FLATPAK=1
else LAUNCH=(env "PYTHONPATH=$HERE" python3 -m vstenv); FLATPAK=0; fi

if [ -z "$WORK" ]; then
    # the flatpak only sees the home directory, not /tmp
    mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}/vstenv"
    OUT="$(mktemp -d "${XDG_CACHE_HOME:-$HOME/.cache}/vstenv/wine-fixes-build.XXXXXX")"
    [ "$KEEP" -eq 1 ] || trap 'rm -rf "$OUT"' EXIT
else OUT="$(mkdir -p "$WORK" && cd "$WORK" && pwd)"; fi
TARBALL="$OUT/wine-fixes-$VERSION.tar.gz"
echo "== wine $VERSION; work dir $OUT; launcher: ${LAUNCH[*]}"
if [ "$DRY" -eq 1 ]; then
    echo "== would run: $HERE/scripts/build-wine-fixes.sh $VERSION $OUT"
    echo "== would run: VSTENV_WINE_FIXES=$TARBALL ${LAUNCH[*]} wine-fixes install --force"
    echo "dry run: nothing built or installed"; exit 0
fi

"$HERE/scripts/build-wine-fixes.sh" "$VERSION" "$OUT"
[ -f "$TARBALL" ] || die "the build left no $TARBALL"
if [ "$FLATPAK" -eq 1 ]; then
    flatpak run --env="VSTENV_WINE_FIXES=$TARBALL" --filesystem="$OUT:ro" --command=vstenv io.github.dguedry.vstenv wine-fixes install --force
else VSTENV_WINE_FIXES="$TARBALL" "${LAUNCH[@]}" wine-fixes install --force; fi
# (d)
if [ "$FLATPAK" -eq 1 ]; then flatpak run --command=vstenv io.github.dguedry.vstenv wine-fixes status; else "${LAUNCH[@]}" wine-fixes status; fi
echo "Restart any running Windows program: it keeps the files it loaded before."
