"""Who is still holding a finished command's output pipe.

A Wine command can exit while something it started keeps running and keeps the
command's stdout open: a vendor daemon is the usual culprit. Reading to end of
file then waits for that process instead of the command, which looks like a
hang with no explanation -- vstenv's own runs would sit out their whole
timeout (ten minutes for an install step, fifteen for wineboot) and report
nothing useful.

Cabinet (github.com/Mark12870/cabinet) solves this by finding the processes
that inherited the pipe and naming them; its Native Instruments notes describe
exactly this hang, where every direct Wine step started the NTK daemon and the
daemon inherited that step's pipe. This module does the same lookup for
vstenv: given the pipe we are reading, say which processes are holding it open.

Nothing here kills anything. It turns a silent wait into a sentence a person
can act on, and the caller decides what to do.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import host

# Processes that legitimately hold pipes open and are not the problem: our own
# interpreter, the shell wrappers the command ran through, and the portal
# helper a sandboxed vstenv spawns commands with.
_IGNORE = {"python3", "python", "sh", "bash", "flatpak-spawn", "flatpak"}


def pipe_of(fileobj) -> str | None:
    """The 'pipe:[N]' name of a file object we are reading, or None.

    None means there is nothing to look up -- the stream is a file, already
    closed, or not something /proc describes as a pipe."""
    try:
        return os.readlink(f"/proc/self/fd/{fileobj.fileno()}")
    except (OSError, ValueError, AttributeError):
        return None


# Walks the host's /proc, because a sandboxed vstenv sees only its own in its
# namespace while every Wine process of ours runs on the host (host.py).
_SCAN = r"""for p in /proc/[0-9]*; do
  pid=${p#/proc/}
  [ "$pid" = "$$" ] && continue
  for fd in "$p"/fd/*; do
    [ -L "$fd" ] || continue
    if [ "$(readlink "$fd" 2>/dev/null)" = "$VSTENV_PIPE" ]; then
      printf '%s\t%s\n' "$pid" "$(cat "$p/comm" 2>/dev/null)"
      break
    fi
  done
done"""


def holders(pipe: str, exclude: set[int] | None = None) -> list[tuple[int, str]]:
    """(pid, name) of processes holding `pipe` open, worth telling someone about.

    Our own process and the shells a command ran through are left out, as is
    any pid the caller excludes (the command's own process, typically)."""
    if not pipe:
        return []
    out = []
    skip = (exclude or set()) | {os.getpid()}
    for line in host.sh(_SCAN, env={"VSTENV_PIPE": pipe}).splitlines():
        pid, _, name = line.partition("\t")
        if not pid.isdigit():
            continue
        pid, name = int(pid), name.strip()
        if pid in skip or name in _IGNORE:
            continue
        out.append((pid, name))
    return out


def describe(found: list[tuple[int, str]]) -> str:
    """One sentence naming what is holding the pipe, for a log or a reporter."""
    if not found:
        return ""
    names = sorted({name for _pid, name in found if name})
    which = ", ".join(names) if names else f"{len(found)} process(es)"
    return (f"{which} kept this command's output open after it finished, so waiting for "
            f"its output waits for them")


def end(found: list[tuple[int, str]], signal: str = "TERM") -> list[int]:
    """Signal the holders. Returns the pids signalled.

    Only ever called with a list the caller got from holders(), and only when
    something decided those processes should go -- this module does not make
    that decision on its own."""
    pids = [pid for pid, _name in found]
    if pids:
        host.sh("kill -%s %s 2>/dev/null || true" % (signal, " ".join(str(p) for p in pids)))
    return pids


def run_watching(cmd, *, env=None, cwd=None, timeout=600, grace=3.0, on_linger=None,
                 text=True, **kw):
    """Run a command, and if its output stays open after it exits, say who by.

    subprocess.run() reads to end of file, so a daemon that inherited the
    command's stdout keeps the call waiting long after the command is gone.
    This waits for the command, gives the output `grace` seconds to close on
    its own, and if something is still holding it calls
    on_linger(found, note) -- then stops waiting for that output rather than
    blocking on it. The caller decides what to do about the processes named.

    Returns a CompletedProcess like host.run does; stdout is whatever had been
    written by then.
    """
    import subprocess, threading, time
    from . import host
    proc = host.popen(cmd, env=env, cwd=cwd, stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, text=text, **kw)
    pipe = pipe_of(proc.stdout)

    # Read in a thread: the whole point is not to block the caller on a pipe a
    # third process is holding open.
    chunks: list[str] = []
    reader = threading.Thread(target=lambda: _drain_into(proc.stdout, chunks), daemon=True)
    reader.start()

    try: proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.wait(timeout=10)

    # The command itself is done. If the reader is still going, something else
    # holds the pipe -- find out what, once, after a short grace period.
    reader.join(timeout=grace)
    found = []
    if reader.is_alive() and pipe:
        found = holders(pipe, exclude={proc.pid})
        if found and on_linger is not None:
            on_linger(found, describe(found))

    return subprocess.CompletedProcess(cmd, proc.returncode, "".join(chunks), "")


def _drain_into(stream, chunks: list) -> None:
    """Collect output line by line, so whatever was written is kept even when a
    lingering process stops the stream ever reaching end of file."""
    try:
        for line in iter(stream.readline, ""):
            chunks.append(line)
    except Exception:
        pass
    finally:
        try: stream.close()
        except Exception: pass
