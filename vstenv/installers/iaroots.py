"""Where an InstallAware payload goes, when the installer never said.

NI's setups are InstallAware: the inner MSI's Directory table has a root per
destination, named P<hash>_1 with DefaultDir ".", and the real path of each
root is a property the setup's compiled script sets at run time. Our MSI
trace catches those assignments (parse_trace), which is how the deploy
fallback learns its roots for Kontakt. Absynth 5's setup spins on a Wine stub
before Windows Installer runs at all, so its trace holds nothing, and the
rescue stopped at "trace has no destination roots" (2026-10-13 .. 10-16).

The paths are not in the MSI, nor hashed from any literal in the setup files
(tried CRC32 of every path template there). But what each root CONTAINS says
where it goes, and NI's layout is the same for every product: the root with
"Service Center" and other products in it is Common Files\\Native Instruments;
a root of bare plugin .dlls is a VSTPlugins folder, 64 or 32 bit by the PE
header; a .vst3 goes to Common Files\\VST3; the product's .exe marks its
application folder, again by bitness; documentation with no binaries is the
application folder too. AAX bundles are left out: nothing here can host them.

The registry values NI products need (InstallDir, InstallVST64Dir,
ContentDir, ContentVersion ...) ARE in the MSI's Registry table, with the path
ones referring to run-time properties; their NAMES say which root each one
means, so they are filled from the same inference.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

PF64 = "C:\\Program Files"
PF32 = "C:\\Program Files (x86)"
SKIP_EXT = {".aaxplugin"}

def pe_bits(path: Path) -> int | None:
    """64 or 32 for a PE file, None for anything else."""
    try:
        with open(path, "rb") as f:
            head = f.read(0x40)
            if len(head) < 0x40 or head[:2] != b"MZ": return None
            off = struct.unpack_from("<I", head, 0x3c)[0]
            f.seek(off); sig = f.read(6)
        if sig[:4] != b"PE\0\0": return None
        machine = struct.unpack_from("<H", sig, 4)[0]
        return {0x8664: 64, 0xaa64: 64, 0x14c: 32}.get(machine)
    except (OSError, struct.error): return None

def _long(part: str) -> str: return part.split("|", 1)[-1]

def _root_tree(msi):
    """root id -> list of (subpath, filename, source subpath) for every file."""
    dirs = {d["Directory"]: (d["Directory_Parent"], d["DefaultDir"]) for d in msi.rows("Directory")}
    comp_dir = {c["Component"]: c["Directory_"] for c in msi.rows("Component")}
    def chain(d):
        tparts, sparts = [], []
        while d in dirs:
            parent, dd = dirs[d]
            if parent == "TARGETDIR" or not parent: return d, "/".join(reversed(tparts)), "/".join(reversed(sparts))
            tpart, spart = dd.split(":", 1) if ":" in dd else (dd, dd)
            t, s = _long(tpart), _long(spart)
            if t != ".": tparts.append(t)
            if s != ".": sparts.append(s)
            d = parent
        return None, "", ""
    tree: dict[str, list[tuple[str, str, str]]] = {}
    for f in msi.rows("File"):
        d = comp_dir.get(f["Component_"])
        if not d: continue
        root, tsub, ssub = chain(d)
        if root: tree.setdefault(root, []).append((tsub, _long(f["FileName"]), ssub))
    return tree

def _bits_of(bag: Path, files, root: str) -> int | None:
    """Bitness of the first PE file of a root found in the bag."""
    for tsub, name, ssub in files:
        if os.path.splitext(name)[1].lower() not in (".exe", ".dll", ".vst3"): continue
        cand = bag / ssub / name if ssub else bag / name
        if not cand.exists():
            hashdir = bag / "OFFLINE" / root[1:].split("_", 1)[0]
            found = next(hashdir.rglob(name), None) if hashdir.is_dir() else None
            if found is None: found = next(bag.rglob(name), None)
            cand = found
        if cand is not None and (b := pe_bits(cand)) is not None: return b
    return None

def infer_roots(msi, bag: Path, product: str) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """(roots, regkeys, notes). roots: Directory id -> Windows path, like
    parse_trace's. regkeys: 'SOFTWARE\\...\\Value' -> data, like parse_trace's."""
    bag = Path(bag); notes: list[str] = []; roots: dict[str, str] = {}
    places: dict[str, str] = {}      # role -> path, for the registry values
    for root, files in _root_tree(msi).items():
        exts = {os.path.splitext(n)[1].lower() for _, n, _ in files}
        tops = {t.split("/")[0] for t, _, _ in files if t}
        root_names = {n.lower() for t, n, _ in files if not t}
        bits = _bits_of(bag, files, root)
        if exts & SKIP_EXT:
            notes.append(f"{root}: AAX bundle, not deployed (nothing here hosts AAX)"); continue
        if ".vst3" in exts:
            dest = PF64 + "\\Common Files\\VST3"; places["vst3"] = dest; why = "VST3 bundle"
        elif "service center" in {t.lower() for t in tops} or len(tops) >= 2 and product in tops and not root_names:
            dest = PF64 + "\\Common Files\\Native Instruments"; places["common"] = dest; why = "Service Center and product folders"
            if product in tops: places["content"] = dest + "\\" + product
        elif root_names and all(n.endswith(".dll") for n in root_names) and not tops:
            dest = (PF64 + "\\Native Instruments\\VSTPlugins 64 bit") if bits != 32 else (PF32 + "\\Native Instruments\\VSTPlugins 32 bit")
            places["vst64" if bits != 32 else "vst32"] = dest; why = f"plugin .dlls, {bits or 64}-bit"
        elif f"{product.lower()}.exe" in root_names or any(n.endswith(".exe") for n in root_names):
            dest = (PF64 if bits != 32 else PF32) + f"\\Native Instruments\\{product}"
            places["app64" if bits != 32 else "app32"] = dest; why = f"the program, {bits or 64}-bit"
        else:
            dest = PF64 + f"\\Native Instruments\\{product}"; places.setdefault("app64", dest); why = "documentation and data, no binaries"
        roots[root] = dest; notes.append(f"{root}: {len(files)} files -> {dest} ({why})")
    regkeys: dict[str, str] = {}
    role_of = {"InstallDir": ("app64", "app32"), "InstallVST64Dir": ("vst64",), "InstallVSTDir": ("vst32",),
               "ContentDir": ("content", "app64", "app32"), "InstallVST3Dir": ("vst3",)}
    prefix = f"SOFTWARE\\Native Instruments\\{product}".lower()
    for row in msi.rows("Registry"):
        if str(row.get("Root")) != "2" or not (row.get("Key") or "").lower().startswith(prefix): continue
        name, value = row.get("Name") or "", str(row.get("Value") or "")
        if not name: continue
        if value.startswith("[") and value.endswith("]"):
            roles = role_of.get(name)
            if not roles: continue
            path = next((places[x] for x in roles if x in places), None)
            if path is None: continue
            value = path + "\\"
        regkeys[f"{row['Key']}\\{name}"] = value
    return roots, regkeys, notes
