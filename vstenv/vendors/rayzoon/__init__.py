"""Rayzoon's Jamstix 4: what its plugin's host needs to show the editor.

Jamstix installs through its Manager like any program, so this module has no
manager of its own. Its plugin is a thin jamstix4.dll: the interface lives in a
FireMonkey core, in a process of its own (jamstix4.exe) or loaded into the host
(js4sl64.rtxc), depending on the mode Jamstix picks. In every case the editor
needs three things under Wine, all of which the core inherits from the host:

- A DPI-aware host. The core checks, and against an unaware one it opens "Your DAW
  plugin interface is not High-DPI aware" in a form nobody can see and waits for
  OK: the editor stays on "Jamstix is loading...".
- rtscom disabled. Wine's stub RealTimeStylus answers "not implemented", which
  FireMonkey shows as an error dialog ("Cannot communicate with core GUI ID=2").
- No Direct3D 11, so FireMonkey takes its GDI+ canvas (see fmx.py). On the Direct3D
  path the controls do not draw. With rtscom disabled it also made the host
  allocate 35 GB and fill it, once it was in the mode where the core is loaded
  into the host, until the machine ran out of memory.
"""
from __future__ import annotations

import re

from .. import Vendor

# The plugin's path as the host is given it. The Manager puts it in Program Files/
# Steinberg/VstPlugins, which Steinberg's module sets Wine's Direct3D for; a
# WINEDLLOVERRIDES here replaces that, since the launcher reads this list after it.
PLUGIN = "/jamstix4."
DLL_OVERRIDES = "rtscom=d;d3d11,dxgi=d"
DPI_AWARE = "2"     # per monitor, as WINE_DPI_AWARENESS takes it


class Rayzoon(Vendor):
    id = "rayzoon"
    name = "Rayzoon"
    publisher = re.compile(r"^rayzoon", re.I)

    def plugin_host_env(self, p):
        return [(PLUGIN, f"WINE_DPI_AWARENESS={DPI_AWARE}"), (PLUGIN, f"WINEDLLOVERRIDES={DLL_OVERRIDES}")]


VENDOR = Rayzoon()
