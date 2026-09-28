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

## Tested

Verified end to end on this setup: Ubuntu 24.04 and Fedora 43 hosts, the pinned
Wine 11.17 build, plugins loaded in a Linux DAW through yabridge.

| Vendor | Manager | Verified |
|---|---|---|
| Native Instruments | Native Access 3.26 | Sign-in through the browser callback, product installs, library registration; Kontakt 8 standalone and VST3, with its libraries in a DAW |
| IK Multimedia | IK Product Manager 1.1.15 | Sign-in, product installs; IK plugin GUIs draw through DXVK |
| Steinberg | Download Assistant 1.40, Activation Manager 1.9, Library Manager 3.2 | Sign-in that survives restarts, downloads, installs through the Install Assistant, VST Sound library registration; HALion Sonic 7 standalone and VST3 in a DAW, playing and loading libraries, quitting cleanly |
| Arturia (no module: plain Windows installer) | Arturia Software Center 2.12 | Analog Lab V VST3 and VST2 bridged and playing |

Health lists every check behind these; when one fails it names the fix.

## What needed patching, and why

The app is honest about this in its Plugins tab, and so is this README. None of
it is Wine's fault.

- **Native Access.** A company that sells thousands of dollars of instruments
  ships a storefront that is a web page in a browser it cannot keep running:
  its own installer cannot install it, its GPU process crash-loops, its sandbox
  cannot start its own children, its updater would re-break it, and its helper
  daemon claims fixed localhost ports as if it owned the machine. Seven repairs
  before it opens: the app unpacks the installer itself, raises the stack
  reserve, patches the bundle, starts it with `--disable-gpu --no-sandbox`,
  installs the daemon from NI's own files and wires the browser sign-in link.
- **Kontakt.** The flagship sampler arrives in an InstallAware wrapper that
  unpacks its payload, then sits at half a core doing nothing, forever. The app
  kills it and copies the files the installer already had. Libraries the daemon
  cannot be bothered to register, the app registers.
- **IK Product Manager.** IK copied NI's Electron storefront and its mistakes,
  then added one of its own: it refuses to run because the output of `ver` does
  not look like a Windows it has met. Two edits to its bundle and
  `--disable-gpu`, and it behaves.
- **Steinberg.** The installer chain is a museum of Microsoft technology: a
  Java 8 / JavaFX downloader starts a .NET Install Assistant that runs
  PowerShell scripts signed for a Windows trust check, and when that check
  fails it refuses to install Steinberg's own packages. Five runtimes to copy a
  file. The Activation Manager is a Qt 6 front end to a licence engine that
  talks nanomsg over named pipes to store one token, and stores it with a
  credential attribute Wine used to throw away, so it forgot your sign-in on
  every restart and then opened itself in your face each time a plugin asked
  for a licence. And the current products rewrote their GUI library on
  DirectComposition, Direct2D and DirectWrite together, the one Windows
  graphics stack nobody outside Redmond implements, and made it abort rather
  than fall back: an instrument that cannot draw a button without a desktop
  compositor. Hence the Steinberg module's list of fixes, two patched Wine
  DLLs, and a Segoe UI font family so its dialogs are not blank.

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

## What you get

- **Plugins** — what each vendor's manager installed, and what is bridged for DAWs.
  Anything that only works because the app patched something carries a pill
  (*patched*, *limited*, *cannot run*) and expands to one sentence saying what
  the vendor did and what the app does about it; "What needed patching, and
  why" at the top gathers them.
- **Programs** — every Windows program in the prefix, run with the environment's
  own Wine (vendor launch fixes and per-program quirks applied); each also gets a
  desktop menu entry with its own icon (`vstenv menu`).
- **Install** — per vendor: open the manager, get it from the vendor, install or
  update it from a downloaded installer, and (NI) install a product from its own
  installer when the manager's install fails. Plus any third-party plugin
  installer, extra plugin folders, and finishing interrupted installs.
- **Health** — every check with a fix, sign-in handler registration, and a
  scrubbed diagnostic report to attach to a bug.

Bridged plugins land in `~/.vst/yabridge`, `~/.vst3/yabridge` and
`~/.clap/yabridge`; point your DAW there if it does not scan them already. They
are ordinary bundles: nothing about how a DAW is started matters.

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
`patches/wine/` and are built by CI for the pinned Wine.

## Command line

```
vstenv setup [--installer FILE]     vstenv vendors            vstenv manager <vendor> install|launch|repair|status
vstenv products [--vendor V]        vstenv install FILE       vstenv sync [DIR...]
vstenv programs | run NAME | uninstall NAME | menu [update|remove]
vstenv doctor | report | rescue-install | finish-installs | dxvk | wine-fixes | mono | prefixes | url-handlers
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

MIT.
