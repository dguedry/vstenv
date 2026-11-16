# yabridge patches vstenv ships

`scripts/build-yabridge.sh` applies every `*.patch` here to yabridge master
before building it. Bump `BUILD_REV` in that script and in `vstenv/yabridge.py`
whenever a patch is added or changed, so installed builds get replaced.

- `plugin-wine-version-from-prefix.patch`: yabridge's log line `wine version:`
  ran plain `wine --version` unless `WINELOADER` was set, so it named the
  distribution's Wine even though vstenv's `yabridge-host.exe` launcher starts
  the host with the Wine recorded in `<prefix>/wineloader`. The patch reads that
  file too, so the log names the Wine the plugin really runs on.

These patches modify yabridge and are under its licence, GPL-3.0-or-later.
