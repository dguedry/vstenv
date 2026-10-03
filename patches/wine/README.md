# Wine patches vstenv ships

Patched DLLs that go over the pinned Wine build (see `vstenv/winefixes.py`).
CI builds them with `scripts/build-wine-fixes.sh <wine version>`, which applies
every `*-<version>.patch` here on top of the full wine-staging set and builds
the DLLs it lists, and attaches `wine-fixes-<version>.tar.gz` to every
release; setup installs the DLLs into `lib/wine/x86_64-windows/` of the app's
Wine, keeping the originals as `.orig`.

**Licence.** These patches modify Wine and are derivative works of it, so they
and the DLLs built from them are under Wine's licence, LGPL-2.1-or-later, not the
GPL that covers the rest of vstenv.

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

## dcomp-swapchain-present-<version>.patch

Real DXGI composition swap chains, ported from the audio-focused Wine fork
github.com/giang17/wine (branch `d2d1-dcomp-11.17`). Wine's own
`CreateSwapChainForComposition` fakes a hidden window; presenting to it jams in
the GL layer and deadlocks the application's renderer (the Spitfire Audio app
froze on its sign-in screen — its JUCE 8 Direct2D peer waited forever on a
present that never completed).

Each composition swapchain gets a real backing window (`WineDCompSwapchain`).
The swapchain runs in the fork's GDI composition mode
(`WM_WINE_DCOMP_SET_CHILD_MODE`): every present renders into a DIB published as
window properties on the backing window, and the dcomp compositor blits that
buffer into the hosting window on every composite tick. Two designs were tried
and rejected: GL-presenting into the hosting window needs the fork's unix-side
win32u, which a PE-only DLL set cannot ship; and re-pointing the swapchain's
device window at the hosting window let wined3d's window hook run on the app's
message thread, where it blocked cross-thread on activation and wedged the
message loop (the app took no clicks or keys). Messages to the backing window
are posted, never sent — the composite thread holds dcomp_cs and a synchronous
send to the app thread deadlocks.

Touches dxgi (backing window, child mode), wined3d and win32u (keep-back-
buffers swapchains, the dcomp leaf layer, the comp-buffer GDI present), and
dcomp (per-tick blit; `dcomp_layer.h` is new). It also replaces the earlier
`WaitForVBlank` pacing stub with the fork's. Layers on `dcomp-steinberg`.
The largest patch here by far — wined3d and win32u are central DLLs, so re-test
the full Tested table on every Wine bump.

## ole32-dragdrop-stale-target-<version>.patch

`RevokeDragDrop` crashed WebView2-based apps (Audio Modeling's Software Center,
a Rust/wry app) at startup. Wine stores the registered `IDropTarget` pointer in
the public `"OleDropTargetInterface"` window property "for compatibility with
Windows" and blindly `Release`s whatever is there at revoke time. WebView2's
embedded browser registers its own drop target (a Chromium-heap pointer), pokes
that public property, and can free its heap without revoking; the host's revoke
during window destruction then dereferenced a stale pointer (page fault in
ole32, `mov (%rax),%rdx` on the vtable read). Windows never dereferences that
property at revoke, so this never crashes there.

The patch keeps a Wine-private authoritative copy of the pointer in a
`"WineOleDropTargetInterface"` property (the public one stays write-only compat),
resolves the drag-time wrapper through it, removes the properties before the
paired release, and guards the release with `__TRY/__EXCEPT_PAGE_FAULT`, leaking
the object rather than crashing when it is already gone. Verified 2026-09-30:
the Software Center runs past the point it crashed; HALion Sonic (OLE-heavy)
unaffected. A candidate for upstreaming.

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
