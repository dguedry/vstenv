# Wine patches vstenv ships

Patched DLLs that go over the pinned Wine build (see `vstenv/winefixes.py`).
CI builds them with `scripts/build-wine-fixes.sh <wine version>`, which applies
every `*-<version>.patch` here on top of the full wine-staging set and builds
the DLLs it lists, and attaches `wine-fixes-<version>.tar.gz` to every
release; setup installs the DLLs into `lib/wine/x86_64-windows/` of the app's
Wine, keeping the originals as `.orig`.

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
