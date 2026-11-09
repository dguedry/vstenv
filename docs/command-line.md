# vstenv on the command line

Everything the app's window does is also a command. In the Flatpak, run them as
`flatpak run --command=vstenv io.github.dguedry.vstenv <command>`; from a source
install, just `vstenv <command>`. `vstenv --help` and `vstenv <command> --help`
list every option.

```
vstenv setup [--installer FILE]     vstenv vendors            vstenv manager <vendor> install|launch|repair|status
vstenv products [--vendor V]        vstenv install FILE|DIR   vstenv sync [DIR...]
vstenv programs | run NAME | uninstall NAME | menu [update|remove] | remove-environment [--downloads]
vstenv doctor | report | rescue-install | finish-installs | dxvk | wine-fixes | mono | webview2 | prefixes | url-handlers
vstenv manager <vendor> apply-pending-update   install an update the manager downloaded for itself, with this app's fixes
vstenv wedge [status|clear]        no Wine GUI can open a window: show it, or restart (and boot) the Wine session
vstenv webview2 software-gl [on|off]   software rendering for WebView2 apps (the ☰ menu checkbox); no value shows it
vstenv recipe list | show program|plugin NAME | set program|plugin NAME rendering=wine [wm_away=on] [browser_software_gl=on] [hide_wine=on]
vstenv recipe reset program|plugin NAME | share program|plugin NAME [--symptom "..."] [--failed] | update
```

Common tasks:

```sh
vstenv install ~/Downloads/Setup.exe         # an installer, a .zip of plugins, or a .vst3/.clap/.dll
vstenv install ~/Downloads/Setup.exe --remedy silent=on   # run it again with one of the remedies a failure lists
vstenv uninstall "Kontakt 8"                 # a program's own uninstaller, or an NI product without one
vstenv remove-environment [--downloads]      # delete the prefix and everything setup made
vstenv menu remove                           # take away the menu and file manager entries
vstenv webview2 software-gl on               # software rendering for WebView2 apps
vstenv recipe set plugin NAME hide_wine=on   # a Troubleshoot setting, from a terminal
vstenv doctor                                # every Health check, with its fix
```

## Running from source

From source (Python 3.10+, GTK4/libadwaita for the GUI; 7-Zip is fetched if the host's is older than 24):

```sh
pip install -e .
vstenv setup                                          # prefix, fonts, C runtime, patched Wine DLLs, DXVK, yabridge, vendor prerequisites
vstenv manager ni install ~/Downloads/Native-Access-latest.exe
vstenv manager ni launch                              # sign in, install products; plugins are bridged as they appear
vstenv doctor                                         # every fix and prerequisite, with repair hints
```
