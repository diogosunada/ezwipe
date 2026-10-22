"""Command runner.

Every external command goes through a Runner so that:
- a missing tool or a timeout never raises; it comes back as a result,
- each command and its output can be kept as evidence,
- tests can swap in FakeRunner and check exactly what would be sent.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class CmdResult:
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def missing_tool(self) -> bool:
        return self.returncode == 127

    def record(self, max_out: int = 4000) -> dict:
        """Compact form for the audit log."""
        return {"cmd": " ".join(self.argv), "rc": self.returncode, "timed_out": self.timed_out,
                "duration_s": round(self.duration_s, 2),
                "stdout": self.stdout[-max_out:], "stderr": self.stderr[-max_out:]}


class Runner:
    def __init__(self):
        self.history: list[CmdResult] = []
        self._lock = threading.Lock()

    def _keep(self, r: CmdResult) -> CmdResult:
        with self._lock:
            self.history.append(r)
            del self.history[:-500]
        return r

    def run(self, argv: list[str], timeout: float = 60) -> CmdResult:
        t0 = time.monotonic()
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                               errors="replace")
            r = CmdResult(argv, p.returncode, p.stdout, p.stderr, time.monotonic() - t0)
        except FileNotFoundError:
            r = CmdResult(argv, 127, "", f"{argv[0]}: command not found", time.monotonic() - t0)
        except subprocess.TimeoutExpired as e:
            r = CmdResult(argv, -1, _text(e.stdout), _text(e.stderr), time.monotonic() - t0, True)
        return self._keep(r)

    def run_long(self, argv: list[str], timeout: float, tick: Callable[[float], None],
                 tick_every: float = 5.0) -> CmdResult:
        """Run a command that can take hours (ATA Secure Erase), calling tick(elapsed_s)
        while it runs. The command is never killed early except by the timeout."""
        t0 = time.monotonic()
        try:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 errors="replace")
        except FileNotFoundError:
            return self._keep(CmdResult(argv, 127, "", f"{argv[0]}: command not found"))
        while True:
            try:
                out, err = p.communicate(timeout=tick_every)
                return self._keep(CmdResult(argv, p.returncode, out, err, time.monotonic() - t0))
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - t0
                if elapsed > timeout:
                    p.kill()
                    out, err = p.communicate()
                    return self._keep(CmdResult(argv, -1, out, err, elapsed, True))
                tick(elapsed)


def _text(b) -> str:
    if b is None:
        return ""
    return b.decode(errors="replace") if isinstance(b, bytes) else b


Responder = Callable[[list[str]], CmdResult | tuple[int, str] | str]


@dataclass
class FakeRunner(Runner):
    """Test double. Rules map a command prefix to a canned answer.

    The first rule whose prefix matches the start of the command line wins.
    Unmatched commands return rc 127 so a test notices them.
    """

    rules: list[tuple[str, Responder | str | tuple[int, str]]] = field(default_factory=list)
    calls: list[list[str]] = field(default_factory=list)

    def __post_init__(self):
        Runner.__init__(self)

    def on(self, prefix: str, answer) -> "FakeRunner":
        self.rules.append((prefix, answer))
        return self

    def run(self, argv: list[str], timeout: float = 60) -> CmdResult:
        self.calls.append(list(argv))
        line = " ".join(argv)
        for prefix, answer in self.rules:
            if line.startswith(prefix):
                if callable(answer):
                    answer = answer(argv)
                if isinstance(answer, CmdResult):
                    return self._keep(answer)
                if isinstance(answer, str):
                    return self._keep(CmdResult(argv, 0, answer))
                rc, out = answer
                return self._keep(CmdResult(argv, rc, out))
        return self._keep(CmdResult(argv, 127, "", "no fake rule"))

    def run_long(self, argv, timeout, tick, tick_every=5.0) -> CmdResult:
        tick(1.0)
        return self.run(argv, timeout)

    def commands(self) -> list[str]:
        return [" ".join(c) for c in self.calls]
