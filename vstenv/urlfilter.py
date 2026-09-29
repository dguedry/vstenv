"""Drop a vendor's storefront URLs before they reach the browser.

Some IK Multimedia plugins open a web page for every item in a list as a
"check for new content" scan: SampleTank walks its sound packs and calls
ShellExecute on https://www.ikmultimedia.com/appsupd/st4sounds.php?a=<pack>
(and the matching products store page) for each one, so simply using the plugin
sprays dozens of browser tabs. Wine sends every ShellExecute of an http(s) URL
through winebrowser.exe (the prefix's http/https shell/open/command), so one
filter there covers every plugin and the manager at once, without patching each
binary.

This installs a small cmd wrapper as that handler: it forwards a normal link to
the real winebrowser.exe, and silently drops URLs matching the vendor store
patterns below. Sign-in callbacks and genuine links still open; only the
storefront/content-scan URLs are suppressed.
"""
from __future__ import annotations

from pathlib import Path

from .progress import null_reporter
from .wine import Prefix

# The wrapper lives here in the prefix and is what the http/https handlers call.
WRAPPER = r"C:\windows\vstenv-url-filter.cmd"
REAL = r"C:\windows\system32\winebrowser.exe"
HANDLER = f'cmd /c "{WRAPPER}" "%1"'
MARK = "vstenv-url-filter"

# URL substrings to drop (case-insensitive). Kept deliberately narrow: a vendor's
# storefront and content-scan endpoints, not its whole domain (sign-in and real
# links must still open).
DROP = (
    "/appsupd/",                 # IK's per-pack "check for updates" scan (st3sounds/st4sounds/...)
    "st3sounds-", "st4sounds-",  # the sound-pack query values, wherever they appear
    "ikmultimedia.com/products/index.php",   # the store page opened alongside each pack
)

# The registry keys whose shell/open/command Wine uses to open a URL.
_URL_KEYS = (r"HKCR\http\shell\open\command", r"HKCR\https\shell\open\command")

def _cmd_body() -> str:
    # cmd batch: %1 is the URL. Lowercase it, drop on any DROP substring, else run
    # winebrowser. echo is suppressed; unknown/edge inputs fall through to open.
    conds = "\r\n".join(
        f'echo %URL% | findstr /I /C:"{s}" >nul && goto drop' for s in DROP
    )
    return (
        "@echo off\r\n"
        f"rem {MARK}: drop a vendor's storefront/content-scan URLs, open the rest\r\n"
        "set URL=%~1\r\n"
        f"{conds}\r\n"
        f'start "" "{REAL}" "%~1"\r\n'
        "goto end\r\n"
        ":drop\r\n"
        f'rem {MARK}: suppressed %URL%\r\n'
        ":end\r\n"
    )

def _wrapper_path(p: Prefix) -> Path:
    return p.drive_c / "windows/vstenv-url-filter.cmd"

def installed(p: Prefix) -> bool:
    try: return MARK in _wrapper_path(p).read_text(errors="replace")
    except OSError: return False

def install(p: Prefix, reporter=None) -> bool:
    """Write the filter wrapper and point the prefix's http/https handlers at it.
    Idempotent. Returns True when the handler is in place afterwards."""
    r = null_reporter(reporter)
    r.step("URL filter (drop vendor storefront tab-spam)")
    dst = _wrapper_path(p)
    try:
        dst.write_text(_cmd_body(), newline="")
    except OSError as e:
        r.fail(f"could not write the filter: {str(e)[:80]}"); return False
    # Point http/https at the wrapper (keeping winebrowser as the real opener the
    # wrapper calls). Only rewrite when not already ours, so a user override is
    # not clobbered on every setup.
    changed = 0
    for key in _URL_KEYS:
        cur = p.reg_query(key).get("(Default)", "")
        if MARK in cur or cur == HANDLER: continue
        if "winebrowser" not in cur.lower() and cur:      # someone else owns it: leave it
            r.log(f"{key} is not the winebrowser default; left as is"); continue
        try:
            p.reg_set_default(key, HANDLER); changed += 1
        except Exception as e:
            r.fail(f"{key}: {str(e)[:60]}"); return False
    (r.ok if installed(p) else r.fail)(
        "http/https routed through the filter" + (f" ({changed} updated)" if changed else " (already)"))
    return installed(p)

def remove(p: Prefix, reporter=None):
    """Restore the plain winebrowser handler and delete the wrapper."""
    r = null_reporter(reporter); r.step("Removing the URL filter")
    for key in _URL_KEYS:
        if MARK in p.reg_query(key).get("(Default)", ""):
            try: p.reg_set_default(key, f'"{REAL}" "%1"')
            except Exception: pass
    dst = _wrapper_path(p)
    try: dst.unlink()
    except OSError: pass
    r.ok()

def status(p: Prefix) -> dict:
    return {"installed": installed(p),
            "handler": p.reg_query(_URL_KEYS[0]).get("(Default)", "")}
