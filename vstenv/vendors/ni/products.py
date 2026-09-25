"""What Native Access installed, and the registry Kontakt needs to see it.

- Content libraries install through Native Access. The daemon sometimes skips
  the HKLM key Kontakt scans at startup; register_library() rebuilds it.
- NI applications deployed by this app have no Uninstall record; ni_programs()
  lists them from HKLM\\Software\\Native Instruments\\<Name>\\InstallDir.
"""
import json, re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from ...progress import null_reporter
from ...wine import Prefix

NI_KEY = r"HKLM\Software\Native Instruments"
NI_REG_PREFIX = "Software\\Native Instruments\\"

def hints_path(p: Prefix) -> Path:
    return p.drive_c / "Program Files/Common Files/Native Instruments/Service Center/NativeAccess.xml"
def installed_products_dir(p: Prefix) -> Path:
    return p.public_docs / "Native Instruments/installed_products"
def ras3_dir(p: Prefix) -> Path:
    return p.public_docs / "Native Instruments/Native Access/ras3"

@dataclass
class NIProduct:
    name: str
    upid: str = ""
    type: str = ""          # App | Content | Bundle | Utility
    regkey: str = ""
    hu: str = ""
    jdx: str = ""
    content_dir: str = ""   # from installed_products json
    install_dir: str = ""
    version: str = ""
    registered: bool = False    # HKLM key present
    licensed: bool = False      # ras3 jwt present and non-empty
    deps: list = field(default_factory=list)

def hints(p: Prefix) -> dict[str, NIProduct]:
    """Product catalogue NA writes (name -> product)."""
    f = hints_path(p)
    if not f.exists(): return {}
    out = {}
    for pr in ET.parse(f).getroot().findall("Product"):
        n = pr.findtext("Name") or ""
        out[n] = NIProduct(name=n, upid=pr.findtext("UPID") or "", type=pr.findtext("Type") or "",
                           regkey=pr.findtext("RegKey") or n, hu=pr.findtext("ProductSpecific/HU") or "",
                           jdx=pr.findtext("ProductSpecific/JDX") or "",
                           deps=[(d.text, d.get("minVersion")) for d in pr.findall("Dependencies/AppDependency")])
    return out

def hive_keys(p: Prefix) -> dict[str, dict[str, str]]:
    """HKLM\\Software\\Native Instruments\\* from the on-disk hive (fast, may lag a
    running wineserver by up to a minute). Values are strings."""
    reg = p.path / "system.reg"; out = {}
    if not reg.exists(): return out
    cur = None
    for line in reg.read_text(errors="ignore").splitlines():
        m = re.match(r"^\[Software\\\\Native Instruments\\\\([^\]]+)\]", line)
        if m: cur = m.group(1).replace("\\\\", "\\"); out[cur] = {}; continue
        if line.startswith("["): cur = None; continue
        if cur and line.startswith('"'):
            mv = re.match(r'^"([^"]+)"=(?:"(.*)"|dword:([0-9a-f]+)|str\(\d+\):"(.*)")', line)
            if mv:
                v = mv.group(2) if mv.group(2) is not None else (mv.group(4) if mv.group(4) is not None else str(int(mv.group(3), 16)))
                out[cur][mv.group(1)] = v.replace("\\\\", "\\") if isinstance(v, str) else v
    return out

def installed(p: Prefix) -> list[NIProduct]:
    """Installed products = daemon's installed_products records, enriched with
    catalogue, registry and license state."""
    cat = hints(p); keys = hive_keys(p); lic = {f.stem: f.stat().st_size > 0 for f in ras3_dir(p).glob("*.jwt")} if ras3_dir(p).exists() else {}
    out = []
    d = installed_products_dir(p)
    if d.exists():
        for f in sorted(d.glob("*.json")):
            try: j = json.loads(f.read_text(errors="replace"))
            except Exception: j = {}
            pr = cat.get(f.stem) or NIProduct(name=f.stem, regkey=f.stem)
            pr.content_dir = j.get("ContentDir", ""); pr.install_dir = j.get("InstallDir", "")
            pr.version = j.get("ContentVersion", "")
            k = keys.get(pr.regkey, {})
            pr.registered = bool(k.get("ContentDir") or k.get("InstallDir"))
            pr.licensed = lic.get(pr.upid, False)
            out.append(pr)
    return out

# --- libraries ------------------------------------------------------------------------
def register_library(p: Prefix, name: str, reporter=None) -> bool:
    """Write HKLM\\Software\\Native Instruments\\<RegKey> for an installed content
    library (ContentDir/ContentVersion from installed_products, HU/JDX from the
    catalogue). Returns True if it wrote anything."""
    r = null_reporter(reporter)
    r.step(f"Registering library: {name}")
    cat = hints(p); pr = cat.get(name)
    rec = installed_products_dir(p) / f"{name}.json"
    if not rec.exists(): r.fail("not installed (no installed_products record)"); return False
    j = json.loads(rec.read_text(errors="replace"))
    key = f"{NI_KEY}\\{pr.regkey if pr else name}"
    if p.reg_query(key).get("ContentDir"): r.skip("already registered"); return False
    vals = {"ContentDir": j["ContentDir"], "ContentVersion": j.get("ContentVersion", "1.0.0")}
    if pr:
        if pr.hu: vals["HU"] = pr.hu
        if pr.jdx: vals["JDX"] = pr.jdx
    for k, v in vals.items(): p.reg_add(key, k, v)
    p.reg_add(key, "Visibility", "3", "REG_DWORD")
    r.ok("restart Kontakt to see it"); return True

def register_all_libraries(p: Prefix, reporter=None) -> list[str]:
    done = []
    for pr in installed(p):
        if pr.type == "Content" and not pr.registered:
            if register_library(p, pr.name, reporter): done.append(pr.name)
    return done

# --- programs -----------------------------------------------------------------------------
def ni_programs(p: Prefix) -> list:
    """NI applications from HKLM Software/Native Instruments/<Name>/InstallDir: those
    this app deployed itself have no Uninstall record. Libraries (no exe) are skipped;
    Native Access manages their removal."""
    from ...programs import Program, reg_sections, find_exe
    out = []
    try: sections = reg_sections((p.path / "system.reg").read_text(encoding="utf-8", errors="replace"))
    except OSError: return out
    for key, vals in sections.items():
        if not key.startswith(NI_REG_PREFIX) or "\\" in key[len(NI_REG_PREFIX):]: continue
        name = key[len(NI_REG_PREFIX):]; d = vals.get("InstallDir", "")
        if not d or name in ("Native Access", "Service Center", "NTKDaemon"): continue
        exe = find_exe(p, d)
        if exe: out.append(Program(name=name, version=vals.get("ContentVersion", ""), publisher="Native Instruments", exe=exe, install_dir=d, sources=["ni"]))
    return out

# --- what Native Access downloaded ---------------------------------------------------------
def find_download(p: Prefix, name: str) -> Path | None:
    """NA keeps the downloaded installer (zip or exe) in its download location."""
    from . import native_access as na
    loc, _ = na.download_location_status(p)
    dirs = [p.to_host(loc)] if loc else []
    dirs.append(p.drive_c / "users/Public/Downloads")
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    for d in dirs:
        if not d.is_dir(): continue
        for f in sorted(d.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
            if f.suffix.lower() in (".zip", ".exe") and key in re.sub(r"[^a-z0-9]", "", f.name.lower()):
                return f
    return None
