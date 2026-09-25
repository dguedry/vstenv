# vstenv — VST Environment for Linux

A managed environment for Windows audio plugins on Linux. It provisions its own
Wine, installs each vendor's manager application and the products it delivers,
applies every fix those need under Wine, and exposes the result to Linux DAWs as
ordinary VST2 / VST3 / CLAP plugins through [yabridge](https://github.com/robbert-vdh/yabridge)
— without you ever dealing with Wine. Installed programs show up in your
desktop's application menu like any other app.

Vendor support lives in **modules**. Native Instruments (Native Access, Kontakt,
the NTK daemon, library registration) and IK Multimedia (IK Product Manager) are
built in; a separate package can add another vendor without touching the core.
vstenv is the vendor-modular successor to [nilinux](https://github.com/dguedry/nilinux).

Not affiliated with or endorsed by Native Instruments GmbH or IK Multimedia Production srl.

## Install

Download `vstenv.flatpak` from the [latest release](https://github.com/dguedry/vstenv/releases/latest), then:

```sh
flatpak install --user vstenv.flatpak     # needs the flathub remote for the GNOME 50 runtime
flatpak run io.github.dguedry.vstenv
```

Vendor managers are not bundled. On first run the app prepares the environment
(a portable Wine build is downloaded), then the Install tab links to each
vendor's download page; pick the downloaded installer and the rest is automatic.

From source (Python 3.10+, `7z`, `cabextract`, GTK4/libadwaita for the GUI):

```sh
pip install -e .
vstenv setup                                          # prefix, fonts, C runtime, DXVK, yabridge, vendor prerequisites
vstenv manager ni install ~/Downloads/Native-Access-latest.exe
vstenv manager ni launch                              # sign in, install products; plugins are bridged as they appear
vstenv doctor                                         # every fix and prerequisite, with repair hints
```

## What you get

- **Plugins** — what each vendor's manager installed, and what is bridged for DAWs.
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

## Command line

```
vstenv setup [--installer FILE]     vstenv vendors            vstenv manager <vendor> install|launch|repair|status
vstenv products [--vendor V]        vstenv install FILE       vstenv sync [DIR...]
vstenv programs | run NAME | uninstall NAME | menu [update|remove]
vstenv doctor | report | rescue-install | finish-installs | dxvk | prefixes | url-handlers
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
