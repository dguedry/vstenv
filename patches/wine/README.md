# Wine patches vstenv ships

Patched DLLs that go over the pinned Wine build (see `vstenv/winefixes.py`).
CI builds them with `scripts/build-wine-fixes.sh <wine version>`, which applies
every `*-<version>.patch` here on top of the full wine-staging set and builds
the DLLs it lists, and attaches `wine-fixes-<version>.tar.gz` to every
release; setup installs the DLLs into `lib/wine/x86_64-windows/` of the app's
Wine, keeping the originals as `.orig`.

## Upstream status (re-check before each Wine bump)

These are a bridge, not a fork to maintain forever. DirectComposition, the piece
most of this rides on, is being implemented upstream, and as it lands the custom
patches should shrink toward nothing:

- **Mainline Wine** has merged the stub `dcomp.dll` (the groundwork); a working
  implementation is in progress, not finished.
- **Wine-Staging** carries the real code today: a ~65-patch DirectComposition
  series from CodeWeavers (Zhiyi Zhang), first in staging 11.6, plus
  `DCompositionCreateDevice2` after it. `dcomp-steinberg-*.patch` here sits on
  top of that staging set, so it is already mostly upstream's work.
- **A dedicated audio fork**, https://github.com/giang17/wine (`d2d1-dcomp-11.*`
  branches), implements DirectComposition, Direct2D, DirectWrite and DXGI
  composition swap chains for the exact frameworks vstenv cares about (JUCE 8,
  VSTGUI, SynthEdit/GMPI), ~30k lines over 40+ DLLs, with several fixes already
  upstreamed. Worth watching as a reference and a possible base.
- **Still missing upstream** before the graphics patches become unnecessary: a
  `dwm.exe` compositor, some graphics-driver integration, and Direct2D 1.3
  (staging ships 1.2). `dxgi-waitforvblank-*.patch` below is small and general
  and is a good upstream candidate on its own.

At each Wine bump, rebuild against the new staging set and re-test HALion Sonic
and a JUCE 8 GUI (Spitfire Audio) *without* each patch; drop any patch the
staging set now covers.

## advapi32-credential-attributes-<version>.patch

Credential Manager attributes. Wine's `CredWriteW` stored everything but
`Attributes` (a `FIXME`), and `CredReadW` / `CredEnumerateW` returned
`AttributeCount = 0`. Steinberg's License Engine writes its refresh token as a
generic credential with one attribute (its format marker); reading it back
without the attribute made it log "1.1 Format refresh token detecting --
resetting" at every restart, delete the token, and sign the user out, after
which every product start made the engine pop the Activation Manager up to ask
for a sign-in. The patch stores each attribute as a `Attribute<n>` binary
value (flags, value size, keyword, value) and reads them back into the
credential's buffer, pointer-aligned. Verified with a round-trip test
(2026-09-26); a candidate for upstreaming as is.

The same patch exports `CloseThreadWaitChainSession` (a no-op, as the
session `OpenThreadWaitChainSession` hands out is NULL). Wine left it as a
commented-out stub, so HALion Sonic, which closes its wait-chain session on
exit, raised a stub exception instead of quitting and showed its crash
reporter on every close.

## dxgi-waitforvblank-<version>.patch

`IDXGIOutput::WaitForVBlank` returned `E_NOTIMPL` (a stub). JUCE 8's Direct2D
window peer paces its painting on that call from a vblank thread whose loop
only checks its exit flag after a successful wait, so on `E_NOTIMPL` the thread
spins forever and the main thread hangs in `stopThread` before the first window
is shown (Spitfire Audio's app; any JUCE 8 GUI). DXVK sleeps for the refresh
period and returns `S_OK`; this patch does the same with Wine's dxgi, reading
the output's refresh rate from wined3d and sleeping to the next period boundary.
Verified 2026-09-28: the Spitfire Audio app draws and signs in. A candidate
for upstreaming.

## dcomp-steinberg-<version>.patch

DirectComposition for Steinberg's current products. Their GUI library
`graphics2d.dll` (HALion Sonic 7; Cubase and Dorico of the same generation)
creates a DirectComposition device from a **Direct2D** device, asks the device
itself for surfaces and virtual surfaces, and draws into them through
`ID2D1DeviceContext`. Stock Wine's dcomp is stubs; wine-staging's
`dcomp-DCompositionCreateDevice2` set implements devices, visuals, targets and
a surface factory for DXGI devices. This patch, on top of that set, adds:

- device-level `CreateSurface` / `CreateVirtualSurface` (via a factory on the
  device's rendering device),
- `IDCompositionVirtualSurface` (Resize, Trim, per-rectangle BeginDraw), and
  empty virtual surfaces: `CreateVirtualSurface(0, 0)` is legal on Windows (a
  virtual surface is sparse) and gets its backing on `Resize`; refusing it
  crashed HALion, which does not check the result (null deref, crash dump
  "HALion Sonic Standalone ... .dmp", 2026-09-26),
- a Direct2D device as rendering device (its DXGI device via
  `ID2D1Device2::GetDxgiDevice`), and `BeginDraw` for `IID_ID2D1DeviceContext`
  (a device context targeting a bitmap over the DXGI surface),
- `EndDraw` accepting every factory kind,
- property objects (`objects.c`): every transform, 3D transform, transform
  group, effect group, rectangle clip and animation is a real object whose
  setters return S_OK; the compositor applies visual offsets (`SetOffsetX/Y`)
  and a matrix transform's translation and scale, nothing else yet. Programs
  keep object pointers without checking HRESULTs, so stubs returning
  E_NOTIMPL were a crash waiting to happen,
- `GetFrameStatistics` (nominal 60 Hz from the performance counter) and
  `CheckDeviceState` (always valid) instead of E_NOTIMPL.

The patch applies to a Wine tree with the whole wine-staging set applied (the
build script does that; the pinned Wine is a staging build, and dcomp talks to
its wineserver by request numbers). Regenerate it for a new Wine version by
re-applying the hunks and renaming the file; `winefixes.wine_version()` picks
the asset by the pinned build's version. Written for Wine 11.17, verified with
HALion Sonic 7 on 2026-09-26; a candidate for upstreaming to wine-staging.
