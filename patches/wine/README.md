# Wine patches vstenv ships

Patched DLLs that go over the pinned Wine build (see `vstenv/winefixes.py`).
CI builds them with `scripts/build-wine-fixes.sh <wine version>`, which applies
every `*-<version>.patch` here on top of the full wine-staging set and builds
the DLLs it lists, and attaches `wine-fixes-<version>.tar.gz` to every
release; setup installs the DLLs into `lib/wine/x86_64-windows/` of the app's
Wine, keeping the originals as `.orig`.

To build them from this checkout and install them into your runtime in one
command: `scripts/install-from-source.sh` (see `--help`; needs the MinGW
compiler, flex, bison, and the X, OpenGL, Vulkan, FreeType and fontconfig headers).

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

## iphlpapi-adapter-listing-<version>.patch

KORG's TRITON (1.4.7) took most of a minute to load, every time, and hosts
gave up on it: a scan that allows a plugin 30 seconds never listed it, and a
host that allows a window 15 seconds to open took it for hung. Its copy
protection reads the network adapters' addresses about 2800 times as the
plugin starts, each time loading `netapi32.dll`, calling `Netbios` and
`GetAdaptersAddresses` (twice), and freeing the library again. On Windows
that is nothing. In Wine each round cost 20 ms, for three reasons, all
general:

- **The interface table only grew** (`nsiproxy.sys`, `ndis.c`).
  `update_if_table` added interfaces it had not seen and never removed one, so
  every interface that had existed since the prefix's Wine started was still an
  adapter: a container's veth pair, a VPN, a USB adapter unplugged. On the
  machine this was found on, 26 interfaces existed and Wine listed 168, four
  days into a session with Docker running. Everything that lists adapters paid
  for all of them. The patch drops the ones that are gone at each update (their
  entries are kept aside, not freed: `convert_luid_to_unix_name` hands out a
  pointer into one).
- **A listing cost the square of the interface count** (same file).
  `ifinfo_fill_dynamic` opened a socket and read all of `/proc/net/dev` for
  each interface, to find its one line. A listing now opens one socket and
  reads the file once.
- **One driver request per adapter for a GUID it already had**
  (`iphlpapi_main.c`). `adapters_addresses_alloc` called
  `ConvertInterfaceLuidToGuid` for each adapter, a round trip to the driver,
  for a value that is in the table it had just fetched.

And one that is for programs like this one: `iphlpapi.dll` pins itself when
loaded, so loading and freeing it thousands of times is a reference count, not
three libraries (`iphlpapi`, `nsi`, `dnsapi`) mapped and unmapped each time.

Measured 2026-11-11 on Wine 11.17 with TRITON 1.4.7, time to scan the plugin
(load it, read its description, unload): 58 s in the live prefix (168 listed
adapters); 26.8 s in a fresh prefix with 26; **11-14 s** with the patch,
whatever the uptime. In a prefix of its own, in a network namespace: 44
adapters listed after 40 of them were deleted, 12.0 ms a round, without the
patch; 4 listed and 4.3 ms with it.

`nsiproxy.so` is the driver's unix half and is loaded by the prefix's
`winedevice`: it takes effect when the prefix's Wine next starts (every Wine
program closed), `iphlpapi.dll` in the next program started. Restarting Wine
alone also shrinks an unpatched table back to the interfaces that exist, until
it grows again. Not upstream (checked 11.17's sources); the first three are
general and good upstream candidates, the pin is not Windows' behaviour and
would stay here.

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

## dxgi-composition-resize-<version>.patch

On top of `dcomp-swapchain-present`: a composition swapchain draws into a
hidden `WineDCompSwapchain` backing window, which is created at the
swapchain's size and was never resized again. wined3d's GDI present takes
the device window's client rect as its destination. So after the application
called `ResizeBuffers()` with a new size, every frame was stretched
into the old-sized composition buffer, and dcomp blitted that buffer at the
old size. Seen on IK Multimedia's TONEX 2.2.0 (standalone and VST3,
github.com/dguedry/vstenv/issues/21): after a window resize the interface was
pixelated, cropped, or showed a shrunken copy of itself in the corner. The
patch resizes the backing window to the new buffer size once `ResizeBuffers()`
succeeds (asynchronously when called from another thread). That is how a
composition swapchain behaves on Windows: it is as large as its buffers.
Verified 2026-11-15 by A/B on TONEX 2.2.0 at 1200x700 and 1600x900: the
shipped DLL draws the interface too large and clipped, the patched one fits
the window. Janggo (JUCE 8, relays out on resize) rendered correctly either way.
TONEX also logs `Something's still holding back buffer 0` from
`wined3d_swapchain_resize_buffers` on some resize steps, with and without the
patch. A later step succeeds, so it does no visible harm; it is not fixed here.
giang17's d2d1-dcomp-11.21 branch avoids this differently: its compositor reads the swapchain's buffers back at their own size.

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

## user32-foreign-children-dce-<version>.patch

SINE Player (JUCE 8 with a bundled WebView2) shows its Store and My Licenses
pages in a browser window: `Chrome_WidgetWin_0` (SINE's process) >
`Chrome_WidgetWin_1` (the browser process) > "Intermediate D3D Window" (the
browser's GPU process). Going back to Library hides the first two, and the page
stayed painted over the Library until SINE was restarted (issue #37).

The GPU process presents through a cached window DC (`client_surface_present()`
in win32u, `DCX_CACHE | DCX_USESTYLE`). A cached DC keeps its visible region
until `invalidate_dce()` marks it, and that runs only in the process that showed,
hid or resized a window ("cross-process invalidation is not supported yet" in
win32u/dce.c). The hide happened in two other processes, so the GPU process kept
its old region and painted the last page into the host's window. No browser flag
changes this (software rendering, no partial swap, no DirectComposition were all
tried).

The patch gives `ShowWindow` and `SetWindowPos` real bodies in `user32` (they
were forwards to win32u). When a call changes a window's visibility or size, the
descendants that other processes own get
`SetWindowPos(SWP_FRAMECHANGED | SWP_ASYNCWINDOWPOS | no move/size/z-order)`,
which runs in the owner's thread and invalidates its DCEs. For a visibility
change it then waits for those threads (a `WM_NULL` with a 100 ms timeout, queued
behind the request) and invalidates the area, so whatever the other process drew
in between is painted over. A window without descendants in another process
costs one child enumeration and nothing else.

Verified on SINE Player 1.4.3 (Library back after My Licenses and after Store,
the page shown again on the next visit) and, for regressions, on Minimal Hub
(Tauri + WebView2) and Softube Central (Electron). The real fix belongs in
win32u's unix side (invalidate across processes); drop this when Wine has it.
Rebuilds the 64-bit `user32` only: `EndDeferWindowPos`, `SetParent` and
`ShowWindowAsync` still go straight to win32u.

## d2d1-primitive-blend-copy-<version>.patch

`SetPrimitiveBlend(D2D1_PRIMITIVE_BLEND_COPY)` was stored and then ignored: every
draw blended. JUCE 8 clears a cached component image with a transparent fill in
copy mode, and blending transparent paint changes nothing, so each repaint landed
on the previous one. Minimal Audio's Squash and Morph EQ showed it: a value turned
into a white smear as soon as it changed, and shadows darkened into black patches.
With the patch, a brush draw in copy mode runs without the blend state and replaces
the destination. The same line is in
[giang17/wine](https://github.com/giang17/wine) (`d2d1-dcomp-*`), inside a much larger
d2d1 rework that this does not take. Only the 64-bit `d2d1` is rebuilt. It applies to
every program, but only changes a draw when the program asked for copy mode.

## sensapi-isnetworkalive-last-error-<version>.patch

KORG Software Pass opened on "No Internet Connection" with the network working.
It calls `IsNetworkAlive` and then `GetLastError`, and counts itself online only
when the call returns TRUE *and* the last error is 0, as Microsoft's documentation
tells callers to check. Windows clears the last error on success; Wine's
`IsNetworkAlive` returned TRUE and left whatever error an earlier call had set, so
the app always saw itself offline and never made a request. The patch sets
`ERROR_SUCCESS` before returning. Only the 64-bit `sensapi` is rebuilt.

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

## dcomp-swapchain-present-offscreen-<version>.patch

Cubase 14's project cursor line (the line under the downward triangle in the
ruler, down the track area) flickered, with the project stopped. A 60 fps
recording of just that line showed it in one of two states, a bright line or
only the faint bar line, two frames each, repeating about every four frames.
The compositor of `dcomp-steinberg` drew every visual of a tick straight onto
the target window's DC, one after another, so the window could reach the screen
with the base visual drawn and the overlay visual (the cursor line) not yet on
top of it. The patch composes each tick into an off-screen bitmap (the window's
current content copied in first, so what no visual draws is kept), then copies
the result to the window once. The bitmap lives on the target while the window
size holds; if it cannot be made the old direct drawing is used. Tried and
ruled out first: skipping the reblit timer's `Present(0,0)` of
`dcomp-swapchain-present` did not change the flicker and left parts of the
window unpainted. With this patch the line no longer flickers (Cubase 14,
wine-staging 11.17) and idle CPU right after start was lower (about 6.3 cores
against 8.6 in a 10 s sample; both include start-up scanning). Layers on
`dcomp-steinberg`; a candidate for upstreaming with it.

## dcomp-zz-compose-thread-keeps-running-<version>.patch

Cubase 14, after minimize and restore, kept the window as it was when it
went away: no redraw until something else forced one. The compositor thread
of `dcomp-steinberg` ends the first time a tick draws nothing (a failed
tick, or a target with no root) and only `IDCompositionDevice::Commit()`
starts it again, which a program with nothing new to say never calls. The
patch counts a target for as long as its window exists, regardless of the
tick's result, so the thread ends only when all windows of the device are
gone; and it skips drawing while the target's top-level window is
minimized or destroyed (the first tick after the restore redraws every
visual). Candidate fix from reading the source; built, not yet run in Cubase.
Layers on `dcomp-swapchain-present-offscreen`.

## dcomp-zz-swapchain-alpha-blend-<version>.patch

Cubase 14's start-up splash showed a black strip, about 20 px tall, across
the bottom of the window (cutting the steinberg logo) where the progress bar
and the loading text belong. That overlay is a composition swapchain with
premultiplied alpha, transparent around the bar and text, and the compositor
copied its buffer with SRCCOPY, so transparent pixels came out black.
wined3d now sets the window property `__wine_dcomp_comp_alpha` when the
swapchain is premultiplied and the compositor then uses AlphaBlend. Checked
with a test program (a blue window, a premultiplied composition swapchain
cleared to 0,0,0,0 as an overlay visual, headless kwin Xwayland): the overlay
area was black (0,0,0) with the stock DLLs and blue (0,0,255) with the patched
dcomp.dll and wined3d.dll. Not yet seen in Cubase itself. A translucent overlay
is re-blended each tick over the window content if no base visual redraws it.
Layers on `dcomp-zz-compose-thread-keeps-running`.

## dcomp-zz-recompose-on-restore-<version>.patch

After a minimize and restore of Cubase 14's project window (with
`dcomp-zz-compose-thread-keeps-running` and `winex11-zz-wm-restore-follows`)
the transport row, the track area and the channel and inspector bodies were
black. X drops the window's content when it is unmapped, and the compositor
read the window into its off-screen frame at every tick, so a tick after the
restore began from black. A first version kept the frame only when a tick saw
the window minimized; in Cubase it worked for the first cycle only (the
cycles are fast, the size passes through odd values and the frame was thrown
away on every size change). Now the window is read once, for the first frame,
and the frame is carried over on resize. Test (MinGW program, headless KWin
Xwayland :3: a GDI-painted window painted once plus a composition-swapchain
visual, WM_CHANGE_STATE Iconic then _NET_ACTIVE_WINDOW after 0.05, 0.1, 0.3, 1
and 3 s, whole window compared to the first snapshot): 84111 pixels differ in
every cycle with the compose-thread dcomp, 0 with this patch. Limit: what a
program paints onto the top-level window itself after the first frame is
overwritten by the retained frame (child windows are not touched). Layers on
`dcomp-zz-swapchain-alpha-blend`.

The same patch carries the child-window fix for Cubase 14's splash. The
splash is a 750x407 window whose image is one dcomp target; the progress bar
and the loading text are a child ("Plugin View" with a 750x21 child of its
own) with a second device and target, a premultiplied virtual surface redrawn
every 33 ms. The parent's device context has WS_CLIPCHILDREN, so nothing ever
painted the image under the child, and the child's transparent pixels showed
the black window: a black strip over the lower edge of the image (the xwd of
the splash window itself showed it, so not a compositor or KWin issue). A
child's target now takes its base from the nearest composed ancestor's frame.
Test (MinGW program `sp.cpp`: a WS_CLIPCHILDREN popup with an image target, a
child with its own device, target and virtual surface redrawn every 33 ms by
another thread; headless KWin Xwayland :3): the strip row without the bar was
(0,0,0) with the live dcomp.dll and (42,46,51), the image, with this patch;
the bar stayed visible. In Cubase 14 (Xwayland, KDE Plasma) the splash's progress bar still does not show.

## d2d1-zzz-multithread-protect-<version>.patch

Cubase 14 hung when it opened a project with DXVK as `d3d11`. The hung process had one
worker thread inside `d2d_device_context_draw` (`SwapDeviceContextState`) that had raised an
access violation in DXVK's `d3d11.dll`, and Cubase's exception handler then blocked in `dbghelp`
while holding d2d1's factory lock, so the 12 other image-generator threads queued behind it. The
faulting instruction appends a command to DXVK's command-stream chunk (`mov 0x8(%rdx),%r8` with
`rdx` = the context's current chunk = NULL): two threads were inside the same immediate context at
once. Cubase's threads use a multi-threaded D2D factory, whose lock covers drawing only: bitmap
`Map`, `Unmap` and copies, and the DirectComposition compositor thread, reach the context without it.
Wine's own `d3d11` tolerates that, DXVK does not unless multithread protection is on. A device
context of a multi-threaded factory now turns `ID3D11Multithread` protection on, and
`d2d_device_context_draw` also holds the device lock, so the swap of the context state is atomic.
Test: with this patch Cubase opens its Hub and project window and the hang signature (a thread in
`dbghelp`, threads queued in `d2d_device_context_draw`) is gone, in three runs; without it the hang
happened in every run.

## d2d1-zz-layer-opacity-mask-<version>.patch

Cubase 14's context-menu icons drew as solid near-black squares. It builds each
icon with Direct2D: PushLayer with an opacity brush (the icon's alpha as a
bitmap brush), a solid-colour FillRectangle, PopLayer. Wine's PushLayer and
PopLayer are stubs, so the fill covered the whole 13x13 icon (colour
0xff010200). The icon bitmaps were already wrong on arrival in the menu code;
the black is not a D3D readback or `dxgi_surface_GetDC` problem (GetDC returns
the real UI pixels). A debug print in `d2d_wic_render_target_present` gave 36 of
36 uniform ff010200 icons before and 0 after the patch (alpha shapes instead).
The device context now keeps the opacity brush of a layer (non-command-list
targets only) until PopLayer, and `FillGeometry` (so `FillRectangle`) uses it
when the caller passes no opacity brush. Geometric masks and other layer
parameters are still ignored; layers are counted, so only the outermost
layer's brush applies and an inner PopLayer leaves it. The brush is released in
PopLayer, in EndDraw (a layer left open) and when the context is destroyed. An
earlier version released it inside `SetTextRenderingParams` without clearing it,
which crashed a test that set text rendering params inside a layer (checked:
the old build crashes in that scenario, the new one passes it, together with a
nested layer, a layer left open and a layer that is released with the context;
the icon mask renders the same 88 transparent / 81 painted pixels). Named `zz` so it sorts after
`d2d1-primitive-blend-copy` (same file). Not fixed here: the slow context menu
(about 55 ms per item, spent creating a Direct2D device and WIC render target
per icon).

## d2d1-zz-shader-cache-<version>.patch

Cubase 14 opens a context menu with ~20 icons in ~1 s because every 13x13 icon
gets its own Direct2D device (about 55 ms each). `d2d_device_init` ran
`D3DCompile` on the shape vertex shaders and the pixel shader for every device,
and `CreateWicBitmapRenderTarget` makes a device too. A mingw micro-benchmark
of the icon sequence (WIC 13x13 target or D2D device + context, draw,
CopyFromBitmap/Map, GetDC, ReleaseDC; under the host runtime on a private Xvfb)
measured 20.0 ms per icon steady state, 16 ms of it device creation. The
patch compiles once per process (critical section, blobs shared by reference),
giving 3.4 ms per icon. Left: a new D3D11 device per icon costs another ~32 ms
(wined3d device/context creation), only if Cubase does that. Named `zz` to sort
after `d2d1-zz-layer-opacity-mask` (same file).

## dxgi-zz-shared-wined3d-<version>.patch

`D3D11CreateDevice` makes a new DXGI factory, and every factory made its own
wined3d object, which probes the GL adapter again (a throw-away GL context,
capability tables, format probing). Cubase 14 creates a D3D11 device per
context-menu icon. In a traced creation 30 of 32 ms were
`wined3d_adapter_gl_create`; `D3D11CreateDevice` went from 29 ms to 8.5 ms with
the patch (host runtime, Xvfb, so llvmpipe; the probing is driver work, the
number on NVIDIA will differ). One wined3d object is now kept per process
(created on first use, never released, one reference per factory) and replaced
when the virtual screen rectangle changes, since the outputs are read at
creation. The first device in a process still pays about 220 ms (driver load
and GL context, about 140 ms of it the first GL context; software-GL
specific in size). What is left per device (about 8 ms create, 8 ms release, 20 ms
first draw, 12 ms first readback on a fresh device) is the GL context and
wined3d's per-device GLSL program cache.

## winex11-zz-close-ends-menu-<version>.patch

With a context menu open in Cubase 14 (right-click in the track area), clicking
the window manager's close button showed "Do you want to save this project?"
while the menu was still open, and the dialog could not be clicked: the menu
kept the capture (Esc freed it). The driver turns `WM_DELETE_WINDOW` into a
posted `WM_SYSCOMMAND`/`SC_CLOSE`; the menu's modal loop dispatches it and the
dialog runs nested inside the loop. On Windows the click on the close button
ends the menu first. The driver now calls `NtUserEndMenu` when the thread is in
menu mode, before posting `SC_CLOSE`; its `WM_CANCELMODE` is queued first, so
the menu ends before the close is handled. Only `winex11.so` is rebuilt. Named
`zz` so it sorts after `winex11-xdnd-deferred-drop` (same file, later hunk is
independent). Tested in Cubase 14 under Xwayland on KDE Plasma.

## winex11-zz-wm-restore-follows-<version>.patch

Restoring the minimized Cubase 14 window from the taskbar took many clicks.
A restore by the window manager arrives as an unexpected WM_STATE
NormalState; the Win32 window only loses WS_MINIMIZE later, when the posted
WM_WINE_WINDOW_STATE_CHANGED is handled. Any SetWindowPos in between makes
`X11DRV_WindowPosChanged` derive IconicState from the still-minimized style
and `XIconifyWindow` the window again. The patch sets `wm_restored` on such a
restore and derives NormalState while the style still has WS_MINIMIZE. It
also activates after the maximized restore (SC_MAXIMIZE branch, via the
foreground out-parameter) and restores a disabled window directly with
`ShowWindow(SW_RESTORE)`, since WM_SYSCOMMAND is ignored by disabled windows
and the window stayed WS_MINIMIZE with X showing it Normal. Upstream master
has no change here. Test (MinGW program, headless KWin): the disabled-while-
minimized case reproduces and is fixed; the re-iconify race did not reproduce
in a plain run, so that part is from reading the source. Tested in Cubase 14 under Xwayland on KDE Plasma.

Fix to the above: the first build removed WS_MINIMIZE from the style it passed to `get_desired_wm_state`. The minimized window's rectangle is unmapped, so a SetWindowPos landing in the race window made the driver request WithdrawnState and the window vanished from the taskbar. It now takes IconicState from the unmodified style and turns that into NormalState, and drops the flag when X is no longer Normal.

## winex11-xdnd-deferred-drop-<version>.patch

Dragging a file from a file manager onto Cubase 14 opened its "Import Options"
dialog, and then nothing in the desktop could be clicked: Cubase had to be
killed. The dialog is shown from inside `IDropTarget::Drop`, and the X11 driver
called the drop target from its X event handler. `X11DRV_ProcessEvents` does not
process nested events (`current_event` is set, so `mask = 0`), so the dialog's
own message loop got no pointer or keyboard input for as long as `Drop` ran.
The `XdndDrop` handler now only posts a driver message
(`WM_X11DRV_XDND_DROP`); the message loop runs the drop after the handler has
returned. A copy or link drop is also finished early (`XdndFinished` with the
effect accepted at the last `XdndPosition`; the data was imported when the drag
entered), because a Wayland file manager behind XWayland holds its drag until it
gets the answer. A move still waits for the drop's result, since the source
deletes what it moved when it sees `XdndFinished`. Only `winex11.so` is rebuilt.
Verified with an mp3 dragged from Dolphin onto a Cubase 14 audio track on
wine-staging 11.17; a candidate for upstreaming to Wine.

## win32u.so (rebuilt unix library, no patch of its own)

`dcomp-swapchain-present-<version>.patch` changes `dlls/win32u/menu.c` so that a
32-bit menu bitmap is drawn with `NtGdiAlphaBlend` (per-pixel alpha); a plain
BitBlt turns the transparent, premultiplied pixels of Cubase's context-menu
icons into black squares. But the menu, window and GDI code lives in the unix
half of win32u, `win32u.so`; the PE `win32u.dll` is only syscall thunks. The
build script rebuilt only the PE half, so the hunk never reached the runtime
and every icon stayed a black square even with the Direct2D layer fix
(`d2d1-zz-layer-opacity-mask`). `scripts/build-wine-fixes.sh` now also builds
`win32u.so` (in the same X-enabled second configure as `winex11.so`). That
configure no longer passes `--without-freetype --without-fontconfig`: without
them the rebuilt `win32u.so` has no font support (it came out 2.3 MB against the
stock 2.9 MB). The script checks for both
SONAME defines. Tested in Cubase 14 under Xwayland on KDE Plasma.

This switches on more than the menu hunk: every `dlls/win32u` unix-side change
in `dcomp-swapchain-present` was dormant until now (cursoricon, font.c, dce.c
with its cross-process `has_foreign_ancestor()` handling, window.c, OpenGL,
input, sysparams, the SC_MAXIMIZE geometry), about 1,100 lines, and they now
run in every program. Checked on Cinnamon (X11) with the rebuilt library
against the stock one: notepad draws pixel-identical (fonts), menus, minimize
and restore, a file drop; SINE Player's tabs and Store page, Minimal Hub and
Softube Central; and in REAPER the editors of Squash, SINE Player, Serum 2 and
Kontakt 8 at unchanged CPU (121, 60, 17 and 16 percent of a core). If something
in window, cursor or font behaviour changes after this release, restoring
`win32u.so.orig` is the first thing to try.

## win32u-post-move-no-erase-<version>.patch

`dcomp-swapchain-present` invalidates a whole window, with an erase, after
every move or resize (`set_window_pos()` in `dlls/win32u/window.c`). That code
is in the unix half of win32u, so it did nothing until `win32u.so` was shipped
(v0.1.162, above). Once live, the erase painted the class background over
windows that do not draw again when asked: Minimal Hub's WebView2 browser
window renders through OpenGL into its window surface and presents only when
the page changes, so shrinking the window left it black until the next resize.

The patch drops `RDW_ERASE` and keeps the invalidation. Tested with Minimal Hub
in a copy of the prefix (shrink, capture after 10 s): black 6 of 6 with the
shipped `win32u.so`, drawn 3 of 3 with the stock one, drawn 4 of 4 with the
patch; Softube Central unchanged. Not tested: SynthEdit, which the invalidation
was written for in the fork it came from.

## dcomp-zzz-direct-single-visual-<version>.patch

Follow-up to the four Cubase 14 dcomp patches (`dcomp-swapchain-present-offscreen`
and the three `dcomp-zz-*` after it), for what they cost programs that do not
need them. As submitted, the series raised SINE Player (a JUCE 8 plugin in
REAPER, editor open) from 60 % to 71-72 % of a core.

- A visual tree with one drawing visual and no composed window above it is
  drawn straight onto the window, as before the series. The off-screen frame
  exists to hide a partly composed frame and to put a child target over its
  parent's frame; such a tree has neither, and a JUCE 8 editor is exactly that.
- wined3d sets the `__wine_dcomp_comp_alpha` property when the composition
  buffer is created, not on every present (a server call per frame).
- The composed-window property holds a target pointer; the ancestor walk stops
  at the first window of another process, whose pointer would mean nothing here.
- "Target has no root" is a TRACE (it printed a FIXME on every tick).

Percent of one core in REAPER, stock / series as submitted / with this patch:
SINE Player 60 / 71-72 / 60-61, Squash 121 / 124-126 / 119-122, Kontakt 8
16 / 16 / 16. Not tested here: the off-screen path itself (several visuals,
child targets), which is what Cubase uses; there is no Cubase on the test
machine.

## WineASIO (no patch: built from upstream)

The tarball also carries WineASIO 1.3.0 (github.com/wineasio/wineasio), an ASIO
driver over JACK, for low-latency audio in Cubase and other ASIO hosts. It is
built from upstream source with the patched tree's own `winegcc`/`winebuild`
(64-bit only) and installed as `x86_64-unix/wineasio.dll.so` (the only unix name
Wine 11 loads; the AUR's `wineasio64.dll.so` is ignored) and the PE stub
`wineasio.dll`. It is opt-in: `vstenv wineasio enable` (`vstenv/wineasio.py`)
copies the stub into the prefix's system32 as `wineasio64.dll` and runs
`regsvr32 /s` through the app's Wine runner, which creates
`HKLM\Software\ASIO\WineASIO`; setup and `vstenv wine-fixes install` only keep
an existing registration's copy current. Opt-in because every ASIO program in
the prefix then lists it, and Cubase already has Steinberg's built-in ASIO
driver over Wine's normal audio (it opened here at 2 in / 2 out, 480 samples,
48 kHz).

Wine runs on the host, so the driver dlopens the host's `libjack.so.0`. On
Debian, Ubuntu and Mint that is JACK2's library even with PipeWire as the sound
server (PipeWire's sits in `/usr/lib/x86_64-linux-gnu/pipewire-0.3/jack`, which
only `pw-jack` adds), and WineASIO's `init` fails with "Unable to open a JACK
client". While the driver is registered, `Prefix.wine_env` puts PipeWire's JACK
directory first on Wine's `LD_LIBRARY_PATH` when PipeWire's socket exists and
no `jackd` runs. Measured with a small ASIO client on Linux Mint (PipeWire
1.0.5): system libjack, init fails; PipeWire's, 16 in / 16 out, 1024 samples,
48 kHz. In Cubase: Studio > Studio Setup > Audio System > ASIO Driver =
WineASIO.

The driver's Control Panel button runs a host program named `wineasio-settings`
(`execvp`, so from PATH). Upstream's is a PyQt tool under the GPL that edits
`~/.wine` unless `WINEPREFIX` says otherwise and calls whatever `regedit` is on
PATH; it is not shipped. Instead `vstenv wineasio enable` writes a two-line
launcher of that name into `~/.local/share/vstenv/wineasio-bin`, which is put
first on Wine's PATH while the driver is registered, and it opens vstenv's own
settings window (`vstenv/wineasio_gui.py`, also `vstenv wineasio settings`) for
this prefix. The window and `vstenv wineasio set` write the six REG_DWORD values
the driver reads from `HKCU\Software\Wine\WineASIO` when a program opens it.
Checked with the ASIO client: after `inputs 4`, `fixed-buffer 0`, `buffer 256`
the driver reports 4 inputs and a 16 to 8192 range with 256 preferred, and
`ControlPanel()` runs the launcher.
