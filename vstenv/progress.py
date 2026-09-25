"""Progress reporting shared by the CLI and the GUI.

A Reporter receives step events and log lines. The CLI prints them; the GUI
maps them onto a step list and a log pane. Functions in this package accept
`reporter=None` and fall back to a silent reporter, so they are usable from
tests and scripts too.
"""
import sys, time
from dataclasses import dataclass, field

OK, FAIL, SKIP, RUN = "ok", "fail", "skip", "run"

@dataclass
class Step:
    name: str
    status: str = RUN
    detail: str = ""
    started: float = field(default_factory=time.time)
    ended: float | None = None

class Reporter:
    """Base reporter: records steps, ignores output. Subclass to display."""
    def __init__(self):
        self.steps: list[Step] = []
        self._current: Step | None = None

    # -- steps ---------------------------------------------------------------
    def step(self, name: str) -> Step:
        s = Step(name); self.steps.append(s); self._current = s
        self.on_step(s); return s
    def ok(self, detail=""):   self._end(OK, detail)
    def skip(self, detail=""): self._end(SKIP, detail)
    def fail(self, detail=""): self._end(FAIL, detail)
    def _end(self, status, detail):
        if self._current is None: return
        self._current.status, self._current.detail, self._current.ended = status, detail, time.time()
        self.on_step(self._current); self._current = None

    # -- free-form output ----------------------------------------------------
    def log(self, line: str): self.on_log(line)
    def progress(self, done: int, total: int | None, label: str = ""): self.on_progress(done, total, label)

    # -- hooks (override) ----------------------------------------------------
    def on_step(self, step: Step): pass
    def on_log(self, line: str): pass
    def on_progress(self, done, total, label): pass

    @property
    def failed(self): return [s for s in self.steps if s.status == FAIL]

class ConsoleReporter(Reporter):
    MARK = {OK: "✓", FAIL: "✗", SKIP: "–", RUN: "…"}
    def __init__(self, out=sys.stdout):
        super().__init__(); self.out = out; self._last_pct = -1
    def on_step(self, s):
        if s.status == RUN:
            print(f"  … {s.name}", end="\r", file=self.out, flush=True)
        else:
            d = f"  ({s.detail})" if s.detail else ""
            print(f"  {self.MARK[s.status]} {s.name}{d}".ljust(78), file=self.out, flush=True)
    def on_log(self, line): print(f"      {line}", file=self.out, flush=True)
    def on_progress(self, done, total, label):
        if total:
            pct = done * 100 // total
            step = 1 if self.out.isatty() else 25
            if pct // step != self._last_pct // step or pct == 100:
                self._last_pct = pct
                print(f"      {label} {pct:3d}%  ({done/1e6:.0f}/{total/1e6:.0f} MB)", end="\r", file=self.out, flush=True)
                if pct == 100: print(file=self.out)

def null_reporter(r):
    return r if r is not None else Reporter()
