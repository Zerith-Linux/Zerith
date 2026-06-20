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


def _human_duration(secs: float) -> str:
    """Compact duration, e.g. ``9s``, ``1m05s``, ``2h03m``."""
    s = int(secs + 0.5)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


class Progress:
    """Track completed work items and bytes transferred, drawing a bar on a TTY.

    Redraws are throttled to at most one paint per ``_MIN_REDRAW_INTERVAL``
    (the final item always paints), so a burst of fast completions costs one
    flush, not thousands. The displayed transfer rate is an exponential moving
    average folded once per paint, so it tracks current speed instead of being
    dragged down by a slow start; the cumulative ``nbytes`` / ``elapsed`` used
    by :meth:`finish` summaries stay true overall averages.
    """

    _BAR_WIDTH = 22
    _MIN_REDRAW_INTERVAL = 0.1   # seconds between TTY paints
    _RATE_ALPHA = 0.3            # EMA weight on the newest sample

    def __init__(self, total: int, label: str = "objects") -> None:
        self.total = max(1, total)
        self.label = label
        self.done = 0
        self.nbytes = 0
        self.start = time.monotonic()
        self.tty = sys.stderr.isatty()
        self._last_draw = self.start    # also the anchor for the rate window
        self._pending_bytes = 0         # bytes seen since the last paint
        self._rate = None               # EMA bytes/sec, None until first sample

    def update(self, nbytes: int = 0) -> None:
        self.done += 1
        self.nbytes += nbytes
        if not self.tty:
            return
        self._pending_bytes += nbytes
        now = time.monotonic()
        # Throttle: skip the paint unless the interval elapsed or we just
        # finished the last item (which must always render).
        if self.done < self.total and now - self._last_draw < self._MIN_REDRAW_INTERVAL:
            return
        self._draw(now)

    def _draw(self, now: float) -> None:
        # Fold the bytes accumulated since the last paint into the EMA, using
        # the actual wall-clock window so bursty completions don't spike it.
        window = now - self._last_draw
        if window > 0 and self._pending_bytes:
            inst = self._pending_bytes / window
            self._rate = inst if self._rate is None \
                else self._RATE_ALPHA * inst + (1 - self._RATE_ALPHA) * self._rate
        self._pending_bytes = 0
        self._last_draw = now

        filled = int(self._BAR_WIDTH * self.done / self.total)
        bar = "█" * filled + "░" * (self._BAR_WIDTH - filled)
        pct = 100 * self.done / self.total
        line = (f"\r\033[Kzerithctl: {self.label} [{bar}] "
                f"{pct:3.0f}% {self.done}/{self.total}")
        if self.nbytes:
            line += f"  {human_bytes(self.nbytes)}"
            if self._rate is not None:
                line += f"  {human_bytes(self._rate)}/s"
        eta = self._eta()
        if eta is not None:
            line += f"  eta {_human_duration(eta)}"
        sys.stderr.write(line)
        sys.stderr.flush()

    def _eta(self) -> float | None:
        """Seconds remaining from the cumulative item rate, or ``None`` once
        every item is done. Cumulative (not EMA) keeps the estimate steady."""
        if self.done >= self.total:
            return None
        return self.elapsed * (self.total - self.done) / self.done

    @property
    def elapsed(self) -> float:
        return max(time.monotonic() - self.start, 1e-6)

    def finish(self, msg: str) -> None:
        if self.tty:
            sys.stderr.write("\r\033[K")        # wipe the bar line first
            sys.stderr.flush()
        log(msg)


class StatusLine:
    """A single-line status indicator that rewrites itself on a TTY while a
    non-quantified phase (e.g.  verifying, linking) runs.  On a non-TTY every
    call to :meth:`show` is a silent no-op so only the final :meth:`done`
    summary pollutes CI logs."""

    def __init__(self) -> None:
        self.tty = sys.stderr.isatty()

    def show(self, msg: str) -> None:
        """Show a status message, overwriting the previous line."""
        if self.tty:
            sys.stderr.write(f"\r\033[Kzerithctl: {msg}")
            sys.stderr.flush()

    def done(self, msg: str) -> None:
        """Clear the status line and write a permanent log message."""
        if self.tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        log(msg)
