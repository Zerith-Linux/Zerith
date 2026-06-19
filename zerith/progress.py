"""Single-writer download progress bar for object fetches.

Only the main thread calls :meth:`Progress.update` / :meth:`Progress.finish`, so
nothing races on the terminal. On a non-TTY (CI logs, journald) the bar stays
silent and only the final ``finish`` summary is emitted, keeping captured output
free of carriage-return control codes.
"""
from __future__ import annotations

import sys
import time

from .runtime import log

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def human_bytes(n: float) -> str:
    """Human-readable byte size / rate, e.g. ``6.1 MiB`` or ``512 B``."""
    i = 0
    while n >= 1024.0 and i < len(_UNITS) - 1:
        n /= 1024.0
        i += 1
    return f"{n:.0f} {_UNITS[i]}" if i == 0 else f"{n:.1f} {_UNITS[i]}"


class Progress:
    """Track completed work items and bytes transferred, drawing a bar on a TTY."""

    _BAR_WIDTH = 22

    def __init__(self, total: int, label: str = "objects") -> None:
        self.total = max(1, total)
        self.label = label
        self.done = 0
        self.nbytes = 0
        self.start = time.monotonic()
        self.tty = sys.stderr.isatty()

    def update(self, nbytes: int) -> None:
        self.done += 1
        self.nbytes += nbytes
        if not self.tty:
            return
        filled = int(self._BAR_WIDTH * self.done / self.total)
        bar = "█" * filled + "░" * (self._BAR_WIDTH - filled)
        rate = self.nbytes / self.elapsed
        sys.stderr.write(
            f"\r\033[Kzerithctl: {self.label} [{bar}] "
            f"{self.done}/{self.total}  {human_bytes(self.nbytes)}  "
            f"{human_bytes(rate)}/s")
        sys.stderr.flush()

    @property
    def elapsed(self) -> float:
        return max(time.monotonic() - self.start, 1e-6)

    def finish(self, msg: str) -> None:
        if self.tty:
            sys.stderr.write("\r\033[K")        # wipe the bar line first
            sys.stderr.flush()
        log(msg)
