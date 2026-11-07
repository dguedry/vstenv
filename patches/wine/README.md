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

## hidparse-large-descriptors-<version>.patch

Native Instruments' Komplete Kontrol S-series MK1 keyboards (S88: USB
17cc:1410) expose their buttons, knobs and light guide as one vendor HID
interface (usage page 0xff01). Its 1831-byte report descriptor declares 264
usages in a single main item (the light guide's output report: 88 keys x 3
colours). Wine's HID parser refused it twice, so winebus never created the
HID device and Komplete Kontrol saw no keyboard:

- `array_reserve` grew the value-caps array by half (or to 32) once per call
  and failed when one item needed more than that step:
  `err:hid:parse_new_value_caps HID parser values overflow!`
- the per-item usage list was a fixed 256-entry array:
  `err:hid:parse_local_usage HID parser usages stack overflow!`

Either one ends in `Failed to get device ... report descriptor, status
0xc0110008`. The patch grows the array until the index fits and raises the
usage cap to 1024 (Windows accepts the device). Verified on Wine 11.17 with an
S88 MK1, 2026-10-31: the device then enumerates and opens as
`hid#vid_17cc&pid_1410&mi_02` (input 49, output 265, feature 33 bytes). Not
upstream (checked master the same day); small and general, a good upstream
candidate. Only the 64-bit `hidparse.sys` is needed: winebus and hidclass run
in the 64-bit winedevice.

## powershell.exe (tools/psshim, not a patch)

Wine's `powershell.exe` is a stub: it runs nothing and exits 0 for any command,
`exit 1` included. Most callers are better off for it -- an installer's
"configure the service" script that does nothing and reports success beats one
that fails -- but one question it answers wrongly is "is this program
running?". electron-builder installers ask exactly that (Get-CimInstance
Win32_Process, filtered on the install folder) and read 0 as "yes", so Softube
Central 3.0.0 stopped at "Softube Central cannot be closed" with nothing
running (2026-10-30). Disabling PowerShell for those installers got them
through but broke the Softube Helper's own service scripts, which rely on the
stub's success.

`tools/psshim/powershell.c` replaces the stub in both bitnesses. A command that
queries Win32_Process for executables under a folder
(`$_.Path.StartsWith('<folder>'...)`) is answered from the real process list,
exit 0 if one runs and 1 if none; one that also stops them (Stop-Process,
`.Terminate()`, taskkill) ends them. A service script run with `-File` that
declares `$name = '<service>'` and switches on stop / start <exe> / restart /
remove / <dependency>-running (Softube's Installer Helper `service.ps1`) is
carried out through the service control manager -- the stub's success had
left the old daemon running and the service pointing at a deleted exe ("Installer
Daemon is outdated"). `Start-Process` is carried out too (Minimal Hub,
2026-11-04): the Hub installs its products by writing a script of
`Start-Process -FilePath '<Inno installer>' -ArgumentList '/VERYSILENT ...'
-PassThru` lines followed by `Wait-Process`, and runs it through
`powershell -Command "$p = Start-Process -FilePath '<Hub>' -ArgumentList
'--run-elevated-ps1 "<script>"' -Verb RunAs -Wait -PassThru; exit
$p.ExitCode"`, whereupon the Hub runs `powershell -File <script>`; the stub's
success at the first step meant "installed" with nothing run and the download
deleted. A `-Command` with Start-Process starts that program (waiting for its
exit code with `-Wait`); a `-File` script of Start-Process lines runs each in
turn, waiting, honouring its `New-Item -ItemType Directory` and `Remove-Item`
lines, and exits 1 if any failed. Verified with the Hub's exact chain: Squash
1.4 installed, bridged and loading. Anything else exits 0, as before. Every
call is logged to `%LOCALAPPDATA%\vstenv-powershell.log`. The build tags it
"Wine builtin DLL" at 0x40, as Wine's own modules are, or Wine will not load it
from `lib/wine` as its builtin; it ships in the wine-fixes tarball as
`powershell.exe` (x86_64-windows) and `i386-windows/powershell.exe`.

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

A visual whose content is a composition swapchain is drawn from that
swapchain's published composition buffer when there is one, and composited the
original way when there is not. Without that fallback such a visual stayed
blank for good: the Audio Modeling Software Center and SINE Player hand dcomp a
composition swapchain that never publishes a comp buffer, so the blit had
nothing to copy while the original path had been skipped, and their windows
rendered white.

Touches dxgi (backing window, child mode), wined3d and win32u (keep-back-
buffers swapchains, the dcomp leaf layer, the comp-buffer GDI present), and
dcomp (per-tick blit; `dcomp_layer.h` is new). It also replaces the earlier
`WaitForVBlank` pacing stub with the fork's. Layers on `dcomp-steinberg`.
The largest patch here by far — wined3d and win32u are central DLLs, so re-test
the full Tested table on every Wine bump.

## dcomp-swapchain-present-child-update-<version>.patch

On top of `dcomp-swapchain-present`: a child-mode composition swapchain kept
its first frame for good. A swapchain the dcomp compositor shows straight on
its target (`composite_blit_swapchain_window`, the plugin-editor case) is put
in child mode, and in that mode the GDI present wrote each frame's dirty
rectangles into the per-visual `surface_bits` and skipped the copy into
`comp_bits` -- the buffer behind `comp_dc`, which is what the compositor
blits from. Only the first present, before `surface_bits` existed, reached
`comp_bits`. Seen on Spitfire's BBC Symphony Orchestra (JUCE 8, VST3 under
yabridge in StageRig): the editor drew once and ignored every click, while
Wine's message trace showed the clicks delivered and the plugin presenting
twenty-odd frames a second; a resize, which rebuilds the swapchain, showed one
new frame. The patch copies `surface_bits` into `comp_bits` after a child-mode
present that updated it, so the compositor reads the current frame; a root
swapchain compositing the child reads `surface_bits` as before and is
unchanged. Verified 2026-10-26: articulation clicks, hover text and the
modified-preset marker all appear at once.

(The hunks of `dcomp-swapchain-present` under `dlls/win32u/` touch Wine's
Unix-side win32u, which the PE-only DLL set does not ship; see the note at
the top of that file before relying on any of them.)

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

## user32-dpi-awareness-env-<version>.patch

`WINE_DPI_AWARENESS=0|1|2` sets one process's DPI awareness, with the same values
as Wine's `dpiAwareness` execution option. That option goes by executable name,
and every bridged plugin runs in a `yabridge-host.exe`, so it would change them
all. Jamstix 4 checks its host's awareness and will not embed its editor in an
unaware one (it opens "Your DAW plugin interface is not High-DPI aware" in a form
that is still hidden, and waits for OK). The variable is set for that plugin's
host only (`plugin-host-env`). Rebuilds the 64-bit `user32` only.

## ntdll-hide-wine-version-<version>.patch

JUCE 8.0.13 and later call `GetProcAddress(ntdll, "wine_get_version")` and take a
fallback when it succeeds. Under it Minimal Audio's Squash (JUCE 8.0.13) paints
the card of its activation panel and nothing else: no text, no buttons, no way to
sign in, and the main interface stays out of sight behind it. With
`WINE_HIDE_WINE_VERSION=1` in a process's environment, `LdrGetProcedureAddress`
reports that one export as missing, and the plugin draws everything. The variable
is set per plugin host (`plugin-host-env`, read by the yabridge launcher), never
globally: PACE/iLok needs to find the export ("Error 2000, iLok background
component not running"). The idea and that warning come from
[giang17/wine](https://github.com/giang17/wine), whose `HideWineVersion` registry
switch this reduces to the environment variable. Only the 64-bit `ntdll` is
rebuilt, so 32-bit plugins are not covered.

## winex11-cursor-from-other-process-<version>.patch

The pointer vanished over every WebView2 window (Minimal Audio's Hub, a Tauri
app; Wine bug 58922 reports the same for a plain WebView2 host and Adobe's
installer). WebView2's browser process (`msedgewebview2.exe`) calls
`SetCursor` for the window its host process owns. Wine forwards the handle to
the window's process, and win32u cannot resolve an icon object that lives in
another process (`get_icon_ptr icon handle ... from other process`), so
`create_cursor` fails and the X11 driver returned without touching the window
-- which left it on the cursor set when the pointer entered: the empty one,
because the host's own thread (tao) has no cursor of its own. Measured with
XFixes: a 1x1 fully transparent cursor for as long as the pointer was over
the window.

The patch defines the theme's arrow (Xcursor `left_ptr`, else the X font
arrow) when the cursor cannot be created, instead of leaving the window
alone. The real shape is not known on this side -- a link's hand and a text
field's I-beam show as an arrow -- but the pointer is visible. Same approach
as the workaround attached to the bug (unmerged). The library is the unix
side of the driver, `lib/wine/x86_64-unix/winex11.so`, built from a second,
X-enabled configure of the same patched tree with every optional part the
pinned build has (XInput2, Xcursor, XRandR, Xrender, Xfixes, Xinerama,
Xcomposite, XF86VidMode, OpenGL, Vulkan); the script refuses to package it if
one is missing.

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
