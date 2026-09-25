"""DXVK: Direct3D on Vulkan, for plugin GUIs that Wine's own renderer draws wrong.

Recent JUCE-based plugins (IK Multimedia's are the ones we hit) draw through
Direct3D and do not repaint correctly under WineD3D: switching a tab leaves the
previous screen behind until the window is resized or hovered over, which also
makes clicks look like they were ignored. DXVK translates Direct3D to Vulkan and
draws them correctly.

It is only worth installing when the machine has a *real* Vulkan driver. Any
GPU with a Mesa or NVIDIA driver qualifies, integrated graphics included; a
plugin editor is a trivial workload. What does not qualify is software
rendering (lavapipe/llvmpipe, what you get in a VM or with no driver
installed): it would draw the GUI on the CPU and steal cycles from the audio
thread, which is worse than the redraw quirk it fixes. On such a machine we
leave WineD3D alone and Health says what to install.
"""
import json, os, re, shutil, subprocess, tarfile
from pathlib import Path

from . import paths
from .download import fetch
from .progress import null_reporter
from .wine import Prefix

# Pinned so a build is reproducible; the tarball's hash is checked on download.
DXVK = {
    "version": "3.1",
    "url": "https://github.com/doitsujin/dxvk/releases/download/v3.1/dxvk-3.1.tar.gz",
    "sha256": "30f9cc326874be344285582275446968cfa4c069db31ce56df312d6644179154",
}
# The DLLs DXVK replaces. d3d8 is left out: nothing here needs it.
DLLS = ("d3d9", "d3d10core", "d3d11", "dxgi")
MARKER = "vstenv-dxvk.json"          # in the prefix, records what we installed
BACKUP_SUFFIX = ".wine-builtin"       # Wine's own DLL, kept next to the DXVK one

# --- is Vulkan usable here? ---------------------------------------------------------------------
SOFTWARE_RENDERERS = ("llvmpipe", "lavapipe", "swiftshader", "softpipe")

def vulkan_devices() -> list[dict]:
    """Vulkan devices this machine reports: [{'name', 'type', 'software'}]. Empty
    when Vulkan is unavailable. Uses vulkaninfo when present, otherwise reads the
    ICD manifests, which at least tells us a driver is installed."""
    out: list[dict] = []
    if shutil.which("vulkaninfo"):
        try:
            cp = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=30)
            # One block per device ("GPU0:", "GPU1:", ...); deviceType comes before
            # deviceName, so collect per block rather than pairing lines as they come.
            cur: dict = {}
            def flush():
                if cur.get("name"):
                    kind = cur.get("type", "")
                    out.append({"name": cur["name"], "type": kind,
                                "software": kind.endswith("CPU")
                                            or any(s in cur["name"].lower() for s in SOFTWARE_RENDERERS)
                                            or any(s in cur.get("driver", "").lower() for s in SOFTWARE_RENDERERS)})
                cur.clear()
            for line in cp.stdout.splitlines():
                if re.match(r"\s*GPU\d+:", line): flush()
                elif (m := re.search(r"deviceName\s*=\s*(.+)", line)): cur["name"] = m.group(1).strip()
                elif (m := re.search(r"deviceType\s*=\s*(\S+)", line)): cur["type"] = m.group(1).strip()
                elif (m := re.search(r"driverName\s*=\s*(.+)", line)): cur["driver"] = m.group(1).strip()
            flush()
            if out: return out
        except (OSError, subprocess.SubprocessError): pass
    # No vulkaninfo (it lives in vulkan-tools and is often absent). Ask the Vulkan
    # loader itself through ctypes: enumerate physical devices and read their type.
    # Note what does NOT work here: counting ICD manifests in /usr/share/vulkan.
    # Mesa ships a manifest for every GPU family it supports (asahi, panfrost,
    # radeon, ...) on every machine, so their presence says nothing about the
    # hardware present -- a VM with no GPU lists a dozen of them.
    out.extend(_devices_via_loader())
    # The same physical device can be enumerated more than once when several ICD
    # manifests point at it, which is normal inside a Flatpak sandbox.
    seen, unique = set(), []
    for d in out:
        key = (d["name"], d["software"])
        if key in seen: continue
        seen.add(key); unique.append(d)
    return unique

def _devices_via_loader() -> list[dict]:
    """Enumerate Vulkan devices through libvulkan directly."""
    import ctypes
    try: lib = ctypes.CDLL("libvulkan.so.1")
    except OSError: return []

    class AppInfo(ctypes.Structure):
        _fields_ = [("sType", ctypes.c_int), ("pNext", ctypes.c_void_p), ("pApplicationName", ctypes.c_char_p),
                    ("applicationVersion", ctypes.c_uint32), ("pEngineName", ctypes.c_char_p),
                    ("engineVersion", ctypes.c_uint32), ("apiVersion", ctypes.c_uint32)]
    class InstInfo(ctypes.Structure):
        _fields_ = [("sType", ctypes.c_int), ("pNext", ctypes.c_void_p), ("flags", ctypes.c_uint32),
                    ("pApplicationInfo", ctypes.POINTER(AppInfo)), ("enabledLayerCount", ctypes.c_uint32),
                    ("ppEnabledLayerNames", ctypes.c_void_p), ("enabledExtensionCount", ctypes.c_uint32),
                    ("ppEnabledExtensionNames", ctypes.c_void_p)]
    # VkPhysicalDeviceProperties: the fields we need are at the front, then a 256-byte name
    class Props(ctypes.Structure):
        _fields_ = [("apiVersion", ctypes.c_uint32), ("driverVersion", ctypes.c_uint32),
                    ("vendorID", ctypes.c_uint32), ("deviceID", ctypes.c_uint32),
                    ("deviceType", ctypes.c_uint32), ("deviceName", ctypes.c_char * 256),
                    ("pipelineCacheUUID", ctypes.c_uint8 * 16), ("limits", ctypes.c_uint8 * 504),
                    ("sparseProperties", ctypes.c_uint8 * 20)]

    app = AppInfo(0, None, b"vstenv", 1, b"vstenv", 1, 1 << 22)      # VK_API_VERSION_1_0
    info = InstInfo(1, None, 0, ctypes.pointer(app), 0, None, 0, None)  # INSTANCE_CREATE_INFO
    inst = ctypes.c_void_p()
    if lib.vkCreateInstance(ctypes.byref(info), None, ctypes.byref(inst)) != 0: return []
    try:
        n = ctypes.c_uint32(0)
        if lib.vkEnumeratePhysicalDevices(inst, ctypes.byref(n), None) != 0 or n.value == 0: return []
        devs = (ctypes.c_void_p * n.value)()
        if lib.vkEnumeratePhysicalDevices(inst, ctypes.byref(n), devs) != 0: return []
        found = []
        for d in devs[:n.value]:
            pr = Props()
            lib.vkGetPhysicalDeviceProperties(ctypes.c_void_p(d), ctypes.byref(pr))
            name = pr.deviceName.decode(errors="replace").strip("\x00")
            # VK_PHYSICAL_DEVICE_TYPE_CPU == 4
            found.append({"name": name, "type": f"type {pr.deviceType}",
                          "software": pr.deviceType == 4 or any(sw in name.lower() for sw in SOFTWARE_RENDERERS)})
        return found
    except Exception:
        return []
    finally:
        try: lib.vkDestroyInstance(inst, None)
        except Exception: pass

def vulkan_ok() -> tuple[bool, str]:
    """(usable, detail). Usable means at least one hardware Vulkan device."""
    devs = vulkan_devices()
    if not devs:
        return False, "no Vulkan driver found (install mesa-vulkan-drivers for AMD/Intel, or your GPU vendor's driver)"
    hw = [d for d in devs if not d["software"]]
    if not hw:
        names = ", ".join(d["name"] for d in devs)
        return False, f"only software rendering available ({names}): DXVK would draw plugin GUIs on the CPU"
    return True, ", ".join(d["name"] for d in hw)

# --- install ------------------------------------------------------------------------------------
def marker(p: Prefix) -> dict | None:
    f = p.drive_c / MARKER
    try: return json.loads(f.read_text()) if f.exists() else None
    except (OSError, ValueError): return None

def installed_version(p: Prefix) -> str | None:
    m = marker(p)
    return m.get("version") if m else None

def _extract(reporter) -> Path:
    r = null_reporter(reporter)
    tgz = fetch(DXVK["url"], paths.DOWNLOADS / Path(DXVK["url"]).name, sha256=DXVK.get("sha256"), reporter=r, label="DXVK")
    d = paths.DOWNLOADS / f"dxvk-{DXVK['version']}-x"
    shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    with tarfile.open(tgz) as t: t.extractall(d, filter="tar")
    for cand in d.rglob("x64"):
        if (cand / "d3d11.dll").exists(): return cand
    raise FileNotFoundError("no x64/d3d11.dll in the DXVK tarball")

def install(p: Prefix, reporter=None, force=False) -> dict:
    """Put DXVK's DLLs in the prefix and tell Wine to use them, when the machine
    has a hardware Vulkan driver. Idempotent; returns what was done."""
    r = null_reporter(reporter)
    ok, detail = vulkan_ok()
    if not ok:
        r.step("Installing DXVK (Direct3D on Vulkan)"); r.skip(detail)
        return {"installed": False, "reason": detail}
    if installed_version(p) == DXVK["version"] and not force:
        return {"installed": True, "version": DXVK["version"], "reason": "already installed"}

    r.step(f"Installing DXVK {DXVK['version']} for plugin GUIs")
    src = _extract(r)
    s32 = p.drive_c / "windows/system32"
    s32.mkdir(parents=True, exist_ok=True)
    copied = []
    for dll in DLLS:
        f = src / f"{dll}.dll"
        if not f.exists(): continue
        dst = s32 / f"{dll}.dll"
        backup = s32 / f"{dll}.dll{BACKUP_SUFFIX}"
        if dst.exists() and not backup.exists(): shutil.copy2(dst, backup)   # keep Wine's own
        tmp = s32 / f".{dll}.dll.new"                                        # never write in place:
        shutil.copy(f, tmp); tmp.replace(dst)                                # a running plugin has it mapped
        copied.append(dll)
    # Prefix-wide: any plugin, now or later, gets the working renderer. A program
    # that misbehaves under DXVK can be excepted with an AppDefaults override.
    p.reg_import_values({r"HKCU\Software\Wine\DllOverrides": {d: ("REG_SZ", "native") for d in copied}},
                        "vstenv-dxvk.reg")
    (p.drive_c / MARKER).write_text(json.dumps({"version": DXVK["version"], "dlls": copied, "vulkan": detail}))
    r.ok(f"{len(copied)} DLLs, Vulkan: {detail}")
    return {"installed": True, "version": DXVK["version"], "dlls": copied, "vulkan": detail}

def uninstall(p: Prefix, reporter=None) -> bool:
    """Put Wine's own Direct3D DLLs back and drop the overrides."""
    r = null_reporter(reporter)
    m = marker(p)
    if not m: return False
    r.step("Removing DXVK (back to Wine's renderer)")
    s32 = p.drive_c / "windows/system32"
    for dll in m.get("dlls", DLLS):
        backup = s32 / f"{dll}.dll{BACKUP_SUFFIX}"
        if backup.exists():
            tmp = s32 / f".{dll}.dll.new"; shutil.copy(backup, tmp); tmp.replace(s32 / f"{dll}.dll")
    p.reg_import_values({r"HKCU\Software\Wine\DllOverrides": {d: ("REG_SZ", "builtin") for d in m.get("dlls", DLLS)}},
                        "vstenv-dxvk-off.reg")
    (p.drive_c / MARKER).unlink(missing_ok=True)
    r.ok("Wine's builtin Direct3D restored")
    return True

def status(p: Prefix) -> dict:
    ok, detail = vulkan_ok()
    m = marker(p)
    return {"vulkan_ok": ok, "vulkan": detail,
            "installed": m is not None, "version": (m or {}).get("version"),
            "wanted": DXVK["version"]}
