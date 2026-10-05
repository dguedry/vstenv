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
  [ -d "$p/fd" ] || continue
  for fd in "$p"/fd/*; do
    [ -L "$fd" ] || continue
    t=$(readlink "$fd" 2>/dev/null)
    case " $VSTENV_PIPES " in
      *" $t "*) printf '%s\t%s\n' "$pid" "$(cat "$p/comm" 2>/dev/null)"; break ;;
    esac
  done
done"""


def holders(pipes, exclude: set[int] | None = None) -> list[tuple[int, str]]:
    """(pid, name) of processes holding any of `pipes` open, worth reporting.

    `pipes` is one 'pipe:[N]' name or several. One /proc walk covers them all:
    the scan is the expensive part (seconds on a busy machine), so stdout and
    stderr are looked up together rather than once each.

    Our own process and the shells a command ran through are left out, as is
    any pid the caller excludes (the command's own, typically)."""
    if isinstance(pipes, str): pipes = [pipes]
    wanted = [p for p in pipes if p]
    if not wanted:
        return []
    out, seen = [], set()
    skip = (exclude or set()) | {os.getpid()}
    for line in host.sh(_SCAN, env={"VSTENV_PIPES": " ".join(wanted)}).splitlines():
        pid, _, name = line.partition("\t")
        if not pid.isdigit():
            continue
        pid, name = int(pid), name.strip()
        if pid in skip or pid in seen or name in _IGNORE:
            continue
        seen.add(pid)
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
                 capture=True, text=True, stdout=None, stderr=None, **kw):
    """Run a command; if its output stays open after it exits, say who by.

    subprocess.run() reads to end of file, so a process that inherited the
    command's stdout -- a vendor daemon, typically -- keeps the call waiting
    long after the command itself is gone. This waits for the command, gives
    the output `grace` seconds to close on its own, and if something still
    holds it calls on_linger(found, note) and stops waiting on that output
    instead of blocking. Whatever had been written is kept.

    A drop-in for host.run(): same CompletedProcess, stdout and stderr kept
    apart, and TimeoutExpired raised when the command itself overruns (with
    .vstenv_lingering set when something was found holding the output).
    """
    import subprocess, threading
    from . import host

    if not capture:
        # Nothing to read, so nothing can linger on our pipes.
        return host.run(cmd, env=env, cwd=cwd, timeout=timeout,
                        stdout=stdout, stderr=stderr, **kw)

    proc = host.popen(cmd, env=env, cwd=cwd, stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, text=text, **kw)
    pipes = [p for p in (pipe_of(proc.stdout), pipe_of(proc.stderr)) if p]
    outs: list[str] = []
    errs: list[str] = []
    readers = [threading.Thread(target=_drain_into, args=(proc.stdout, outs), daemon=True),
               threading.Thread(target=_drain_into, args=(proc.stderr, errs), daemon=True)]
    for t in readers: t.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired: pass

    # The command is done. A reader still running means something else holds
    # that pipe: find out what, once, after a short grace period.
    for t in readers: t.join(timeout=grace)
    found: list[tuple[int, str]] = []
    if any(t.is_alive() for t in readers):
        found = holders(pipes, exclude={proc.pid})
        if found and on_linger is not None:
            on_linger(found, describe(found))

    out, err = "".join(outs), "".join(errs)
    if timed_out:
        e = subprocess.TimeoutExpired(cmd, timeout, out, err)
        if found: e.vstenv_lingering = describe(found)
        raise e
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _drain_into(stream, chunks: list) -> None:
    """Collect output line by line, so whatever was written is kept even when a
    lingering process stops the stream ever reaching end of file."""
    if stream is None: return
    try:
        for line in iter(stream.readline, ""):
            chunks.append(line)
    except Exception:
        pass
    finally:
        try: stream.close()
        except Exception: pass
