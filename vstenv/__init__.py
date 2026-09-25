"""vstenv — a managed environment for Windows audio plugins on Linux.

It provisions its own Wine, installs each vendor's manager application and the
products it delivers, fixes what those need under Wine, and exposes the result
to Linux DAWs as ordinary VST2/VST3/CLAP plugins through yabridge. Everything
specific to one vendor lives in a vendor module (vstenv.vendors); the core knows
only Wine, the prefix, yabridge and generic Windows programs. The CLI
(vstenv.cli) and the GUI (vstenv.gui) are thin: every operation is a package
function that reports through a vstenv.progress.Reporter and returns plain data.
"""
__version__ = "0.1.2"
APP_NAME = "vstenv"
APP_ID = "io.github.dguedry.vstenv"
