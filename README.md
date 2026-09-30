# vstenv — VST Environment for Linux

A managed environment for Windows audio plugins on Linux. It provisions its own
Wine, installs each vendor's manager application and the products it delivers,
applies every fix those need under Wine, and exposes the result to Linux DAWs as
ordinary VST2 / VST3 / CLAP plugins through [yabridge](https://github.com/robbert-vdh/yabridge)
— without you ever dealing with Wine. Installed programs show up in your
desktop's application menu like any other app.

Vendor support lives in **modules**. Native Instruments (Native Access, Kontakt,
the NTK daemon, library registration), IK Multimedia (IK Product Manager) and
Steinberg (Download Assistant, Activation Manager, Library Manager, HALion) are
built in; a separate package can add another vendor without touching the core.
vstenv is the vendor-modular successor to [nilinux](https://github.com/dguedry/nilinux).

Not affiliated with or endorsed by Native Instruments GmbH, IK Multimedia Production srl or Steinberg Media Technologies GmbH.

## What needs help, and why

Wine runs most Windows programs as they are. A few of these vendors' programs
need extra handling under Wine, some of it patched Wine DLLs where the program
relies on parts of Windows that Wine has not finished. Each item below is what
the program does and what the app does about it.

- **Native Instruments (Native Access).** The storefront is an Electron app.
  Under Wine its installer does not run, its GPU process is unstable, its Chromium
  sandbox cannot start child processes, its self-updater reverts the changes
  below, and its helper daemon binds fixed localhost ports. The app extracts the
  files from the installer itself, raises the executable's stack reserve, patches
  the bundle so it does not self-update, launches it with `--disable-gpu
  --no-sandbox`, installs the NTK daemon from NI's own files, and registers the
  `native-access://` browser sign-in handler.
- **Kontakt.** Its InstallAware installer unpacks the payload and then stops
  making progress. The app ends it and copies the already-unpacked files, and
  registers the library entries the daemon does not.
- **IK Multimedia (IK Product Manager).** Also Electron. Its os-info module
  parses the output of `ver` and fails on Wine's format, and it needs GPU
  acceleration disabled; the app makes two edits to its bundle and launches it
  with `--disable-gpu`. Sign-in uses your IK username, not your email. When it
  downloads a product it launches the installer with a command line Wine's shell
  rejects, and each failed attempt opens a browser tab; the app runs the
  downloaded installer through its own installer path instead. Its UI is loaded
  live from ikmultimedia.com and calls out to the system browser for its own
  in-app links (the logo, My Products, the user area), so ordinary clicking opens
  a stream of tabs; the app patches the bundle to keep IK's own links in the
  window, and other links still open in the browser. Its own zip extraction can
  also stop partway under Wine, leaving a truncated installer that fails its
  integrity check ("the setup files are corrupted"); the app re-extracts the
  installer from the downloaded zip and checks its size before running it.
- **Steinberg.** The installer chain is Java/JavaFX and .NET: the Download
  Assistant runs a .NET Install Assistant whose PowerShell steps are signed for a
  Windows trust check that fails under Wine, so it declines to install
  Steinberg's own packages; the app installs Wine Mono and unpacks and runs the
  packages itself. The Activation Manager stores its sign-in token with a
  credential attribute Wine used to discard, so sign-in was lost on each restart;
  a patched advapi32.dll keeps it. HALion, Cubase and Dorico of the current
  generation draw with Direct2D and DirectWrite onto a DirectComposition surface.
  Wine has Direct2D and DirectWrite; DirectComposition is only partly implemented
  and the GUI does not fall back without it. The app ships a patched dcomp.dll,
  gives these programs Wine's own Direct3D, and installs a Segoe UI font family
  so their dialogs have text.
- **JUCE 8 programs.** JUCE 8 defaults to the same Direct2D/DirectComposition
  path on Windows with no fallback, so programs built on it (for example the
  Spitfire Audio app) need the same handling. Its vblank thread also does not
  exit cleanly when the vertical-blank wait is unimplemented, which hangs the
  program at startup under stock Wine. The app detects any program or bridged
  plugin that uses DirectComposition and applies the patched DLLs and Wine's own
  Direct3D; a patched dxgi.dll makes the vblank wait return.

Many plugins need none of this. FabFilter's Total Bundle, Soundtoys 5.5 (with
its iLok licensing), and Valhalla DSP install as plain Windows programs and draw
through GDI or OpenGL, so they run untouched. Xfer's Serum 2 and iZotope's Ozone
install cleanly and draw through Direct2D, so they use the same Wine graphics
handling as the programs above. The Plugins tab labels every product that needed
work and states what it was.

### Known limitation: WebView2 logins

Some vendors build their app's window on Microsoft's WebView2 control (an
embedded Edge/Chromium). Orchestral Tools' SINE Player is one. Its window draws
under Wine, but its text fields do not accept keyboard input: Wine does not route
key messages from the host window into the embedded browser, so a login form
shows a blinking cursor yet takes no typing. This is a Wine limitation in how it
delivers input to a hosted WebView2 (not the same as an ordinary Electron app,
whose Chromium pumps its own input and works, the IK and NI managers sign in
fine). Some of these apps (Audio Modeling's Software Center) go further and show
only a blank window, because WebView2's renderer cannot paint under Wine either.
No app-side flag or runtime swap fixed either problem in testing, and the
affected apps do not officially support Linux.

The app does two things here. It installs the Microsoft WebView2 runtime into
the prefix when it sees an app that needs one, because without it the app does
not just fail, it crashes at startup (`vstenv webview2` installs it by hand;
Health flags it). And a patched ole32.dll makes OLE drag-drop teardown survive
the stale target pointer WebView2's embedded browser leaves behind, which
crashed these apps seconds after launch even with the runtime present. With
both, the app launches and stays up, even if its window then draws blank or
takes no typing.

The practical way around the rest: authorize the app once on a real Windows
machine or a Windows VM. It writes its activation and library registration to
disk, and after that the plugin itself, which vstenv bridges like any other,
plays in a Linux DAW, because playback never uses the WebView2 window. Expect any
WebView2-based login or store window to have this problem, not just SINE.

## Tested

Verified end to end on this setup: Ubuntu 24.04, Linux Mint 22.3 (Cinnamon) and
Fedora 43 hosts, the pinned Wine 11.17 build, plugins loaded in a Linux DAW
through yabridge, including [Performer](https://github.com/dguedry/linux-performer).

| Vendor | Manager | Verified |
|---|---|---|
| Native Instruments | Native Access 3.26 | Sign-in through the browser callback, product installs, library registration; Kontakt 8 standalone and VST3, with its libraries in a DAW |
| IK Multimedia | IK Product Manager 1.1.15 | Sign-in, product installs; IK plugin GUIs draw through DXVK |
| Steinberg | Download Assistant 1.40, Activation Manager 1.9, Library Manager 3.2 | Sign-in that survives restarts, downloads, installs through the Install Assistant, VST Sound library registration; HALion Sonic 7 standalone and VST3 in a DAW, playing and loading libraries, quitting cleanly |
| Arturia (no module: plain Windows installer) | Arturia Software Center 2.12 | Analog Lab V VST3 and VST2 bridged and playing |
| FabFilter (no module: plain Windows installer) | Total Bundle installer, run from the Install tab | All 14 plugins bridged as VST3 and VST2, working out of the box; nothing needed patching |
| Soundtoys (no module: plain Windows installer) | Soundtoys 5.5 bundle installer, iLok License Manager | Every effect bridged as VST3 and VST2, working out of the box; iLok licensing (PACE License Support 5.10) activates and runs unchanged |
| Valhalla DSP (no module: plain Windows installer) | ValhallaFreqEcho 1.2 installer | Bridged as VST3 and VST2, flawless out of the box |
| Xfer Records (no module: plain Windows installer) | Serum 2 (2.1.5) installer | Bridged as VST3 and playing out of the box |
| iZotope (no module: plain Windows installer) | Product Portal 1.4, Ozone 9 Advanced 9.13 installer | Every Ozone module bridged as VST3 and VST2, working out of the box. Its installer hands off to a helper (the app waits for it) and leaves the Electron Product Portal running, which crash-loops under Wine; the app stops that so it cannot starve loaded plugins |
| Spitfire Audio (no module: plain Windows installer) | Spitfire Audio app 3.4 | Draws and signs in only with the app's patched dcomp.dll and dxgi.dll and Wine's own Direct3D, which the app applies to any program that imports DirectComposition (JUCE 8) |

Health lists every check behind these; when one fails it names the fix.

## Install

Download `vstenv.flatpak` from the [latest release](https://github.com/dguedry/vstenv/releases/latest), then:

```sh
flatpak install --user vstenv.flatpak     # needs the flathub remote for the GNOME 50 runtime
flatpak run io.github.dguedry.vstenv
```

Vendor managers are not bundled. On first run the app prepares the environment
(a portable Wine build is downloaded), then the Install tab links to each
vendor's download page; pick the downloaded installer and the rest is automatic.

From source (Python 3.10+, GTK4/libadwaita for the GUI; 7-Zip is fetched if the host's is older than 24):

```sh
pip install -e .
vstenv setup                                          # prefix, fonts, C runtime, patched Wine DLLs, DXVK, yabridge, vendor prerequisites
vstenv manager ni install ~/Downloads/Native-Access-latest.exe
vstenv manager ni launch                              # sign in, install products; plugins are bridged as they appear
vstenv doctor                                         # every fix and prerequisite, with repair hints
```

### Installing something you downloaded

A vendor manager (Native Access, the IK Product Manager, the Steinberg Download
Assistant) installs its own products for you; you rarely touch installers by
hand. But you often download things directly: a plugin installer, or a sound or
content pack (an IK SampleTank library, say). For those:

1. **If it is a `.zip`, unzip it first.** Content packs ship as a zip holding an
   installer (for example `Install SampleTank 4 Sound Content.exe`) next to a
   content folder; the installer only works when it sits beside that folder, so
   extract the whole zip and keep it together. A plain plugin download is often
   already a `.exe` or `.msi` and needs no unzipping.
2. **In the app, use "Install from a Windows installer"** and pick that `.exe`
   or `.msi`. It is on both the **Programs** tab and the **Install** tab (the
   same action, listed in both places); either one works. The installer's own
   window opens, you click through it, and when it finishes the app sets up
   whatever it installed and bridges any plugins to your DAW automatically.
3. **From the command line** the equivalent is `vstenv install <path-to-the.exe>`.

The installer is always the `.exe` or `.msi`, never the `.zip` itself. If you
picked a zip by mistake, unzip it and pick the installer inside.

**If an installer asks where to put the plugin**, choose
`C:\Program Files\VstPlugins` (a VST2 folder the app already scans; older IK
installers ask this). Setup pre-fills that as the default VST2 path, so most
installers propose it. VST3 plugins install to `C:\Program Files\Common
Files\VST3` on their own. Either way the app bridges the plugin to your DAW once
the installer finishes; nothing needs moving by hand.

## What you get

- **Plugins** — what each vendor's manager installed, and what is bridged for DAWs.
  Anything that only works because the app patched something carries a pill
  (*patched*, *limited*, *cannot run*) and expands to one sentence saying what
  the vendor did and what the app does about it; "What needs help, and why" at
  the top gathers them.
- **Programs** — every Windows program in the prefix, run with the environment's
  own Wine (vendor launch fixes and per-program quirks applied); each also gets a
  desktop menu entry with its own icon (`vstenv menu`), except the vendor
  managers and installers (Native Access, the IK Product Manager, the Steinberg
  Download and Install Assistants, the iZotope Product Portal, the Arturia
  Software Center), which have no shortcut because they must be launched from the
  app so their fixes apply.
- **Install** — per vendor: open the manager, get it from the vendor, install or
  update it from a downloaded installer, and (NI, IK) install a product from its
  own installer when the manager cannot. Plus "Install from a Windows installer"
  for any downloaded `.exe`/`.msi` (a plugin, a sound or content pack; unzip a
  `.zip` first, see above), extra plugin folders, and finishing interrupted
  installs. When an installer
  leaves an Electron manager crash-looping under Wine (iZotope's Product Portal),
  the app stops it, so it cannot thrash the prefix and freeze loaded plugins.
- **Health** — every check with a fix, sign-in handler registration, and a
  scrubbed diagnostic report to attach to a bug.

Bridged plugins land in `~/.vst/yabridge`, `~/.vst3/yabridge` and
`~/.clap/yabridge`; point your DAW there if it does not scan them already. They
are ordinary bundles: nothing about how a DAW is started matters.

Each plugin is bridged once per format it ships: a plugin that has both a VST2
and a VST3 build appears as both, and a VST2-only plugin (some older ones, IK's
Sonik Synth) is bridged only as VST2. A host that scans only VST3 (Performer
does; it hosts VST3, LV2 and LADSPA, not VST2) will not list a VST2-only plugin,
that is the host's format support, not a bridging failure. If a host shows a
plugin twice after an update, rescan its plugin folder so it drops stale entries.

For playing them live, [Performer](https://github.com/dguedry/linux-performer)
is a companion project: a live-performance plugin host for Linux that loads the
Windows VST3s vstenv bridges alongside native VST3, LV2 and LADSPA, a program
per sound changed from the keyboard, with each plugin in its own process.

## How it works

**One Wine for everything.** The prefix, the vendor managers, their installers
and the DAW-side plugin hosts all run the same pinned portable Wine build. Mixing
Wines is what breaks prefixes ("prefix updated by a newer Wine", installers
failing at once with mixed DLLs).

**DAWs run plugins with that Wine, with nothing on PATH.** yabridge starts its
Windows-side plugin host through `yabridge-host.exe`, a small launcher script
that runs `$WINELOADER` or else the `wine` on PATH, after setting `WINEPREFIX` to
the prefix it found the plugin in. vstenv installs that script and teaches it one
thing more: a prefix that records its wine in `<prefix>/wineloader` is run with
that wine. Setup writes the record. Every other prefix (`~/.wine`, Proton, a
Bottles bottle) has no record and behaves exactly as upstream; an explicit
`WINELOADER` still wins; uninstall the app and the launcher falls back to `wine`.
So nothing is placed in `~/.local/bin`, no session variable is set, and a Wine
you already have is never touched.

**Wine runs on the host, even from the Flatpak.** wineserver addresses its
clients by pid, and a DAW loads plugins on the host, so the prefix's wineserver
and every Wine process the app starts share the host's pid namespace
(`flatpak-spawn --host`, portal permission `org.freedesktop.Flatpak`). The
sandbox also needs to see every host path Wine reaches through the prefix
(Documents, sample libraries on other disks): Health reports what it cannot.

**Windows behave under your window manager.** Wine activates (raises) its
top-level windows when they receive focus, so with several Wine windows in one
session (a plugin editor in a DAW, a standalone manager) a click can pull a Wine
window to the front. There is no Wine setting for this; the window manager
decides. On Cinnamon, whose default `focus-new-windows = smart` focuses and
raises an activating window without being asked, setup switches it to `strict`
(only when it is still at the default), which stops the jump. Setup also sets
Wine's `UseTakeFocus = N`, which fixes the separate problem of a Wine window
losing keyboard input after Alt-Tab.

**Vendor modules.** `vstenv/vendors/__init__.py` defines the interface; the
core calls it and never names a vendor. A module says what its manager is and
how to install, fix and launch it; which products are installed; which plugin
directories, quirks, launch arguments, URL schemes and log files it has; how its
product installers are driven and rescued; and which health checks matter.
Installer *engines* (`vstenv/installers/`, InstallAware today) are shared by
whichever vendors use them. Built-in modules live under `vstenv/vendors/`; a
third-party package declares an entry point in the `vstenv.vendors` group whose
object is a `Vendor` instance.

### Native Instruments module

Native Access's NSIS installer does not run under Wine, so the app files are
extracted from it; the exe's stack reserve is raised to 64 MB; the NTK Daemon
helper service is installed from NA's own resources; NA's asar bundle is patched
so an installed Kontakt Player satisfies library dependencies (NA ≤ 3.25) and so
NA never self-updates (updates come from an installer you download); the
download location is pointed inside the prefix when the configured one is unset
or read-only; libraries the daemon forgot to register get their HKLM keys; the
`native-access://` sign-in callback is registered with the desktop. NI's
InstallAware product setups are run silently under an MSI trace and, when they
fail or stall (payload unpacked, then zero CPU forever), finished by hand from
the staged payload — including installs Native Access started and left half
done, which the app notices while NA is running.

### IK Multimedia module

The IK Product Manager installs as an ordinary Windows program. Two edits to its
Electron bundle make it work (accept Wine's `ver` output in its os-info module;
disable GPU acceleration in the app itself, since a plugin may start it), and it
launches with `--disable-gpu`. IK's JUCE plugin GUIs draw through Direct3D and
need DXVK to repaint; the app installs DXVK when the machine has a real Vulkan
driver and says so in Health when it does not.

### Steinberg module

The Download Assistant (JavaFX) gets its text back with the built-in Direct3D
for that program and `-Dprism.lcdtext=false`, and its `net-steinberg-sda://`
and `net-steinberg-activation-manager://` sign-in callbacks are registered with
the desktop. Its Install Assistant is .NET, so Wine Mono is installed on demand;
when the Install Assistant refuses a package (its signed PowerShell step cannot
be trusted under Wine), the app unpacks the package and runs its MSIs itself.
The Activation Manager keeps its sign-in only because the app's patched
advapi32.dll stores credential attributes, which Wine drops. HALion Sonic 7 and
that generation of Cubase and Dorico draw through DirectComposition, which Wine
does not implement: the app ships a patched dcomp.dll (wine-staging's work plus
what Steinberg's graphics library still needs) and installs it into its Wine
(`vstenv wine-fixes`), gives Steinberg programs and their bridged plugins Wine's
own Direct3D, and installs a "Segoe UI" font family (Microsoft's open-source
Selawik, renamed) so their dialogs have text. The patches are under
`patches/wine/` and are built by CI for the pinned Wine. The same treatment
is applied to any program or bridged plugin whose import table names
DirectComposition (JUCE 8 GUIs such as Spitfire Audio's), and a third patched
DLL, dxgi.dll, makes WaitForVBlank return so JUCE's vblank thread can exit.

## Command line

```
vstenv setup [--installer FILE]     vstenv vendors            vstenv manager <vendor> install|launch|repair|status
vstenv products [--vendor V]        vstenv install FILE       vstenv sync [DIR...]
vstenv programs | run NAME | uninstall NAME | menu [update|remove]
vstenv doctor | report | rescue-install | finish-installs | dxvk | wine-fixes | mono | webview2 | prefixes | url-handlers
```

## Development

```sh
python3 -m unittest discover -s tests -t .
flatpak/build.sh                     # build and install the Flatpak from the working tree
```

Pushing to `main` auto-tags the next patch release and publishes the bundle; the
push's last commit subject becomes the release note. `scripts/release.sh`
cuts a release with a chosen version and note.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE). vstenv is a companion to
[Performer](https://github.com/dguedry/linux-performer), which is under the same
licence.

The patches under [`patches/wine/`](patches/wine/) modify Wine and are therefore
covered by Wine's own licence (LGPL-2.1-or-later), not the GPL; see
[`patches/wine/README.md`](patches/wine/README.md). The DLLs built from them are
likewise LGPL. Everything else in this repository is GPL-3.0-or-later.
