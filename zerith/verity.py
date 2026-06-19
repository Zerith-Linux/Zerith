"""fs-verity helpers.

composefs names every object after its fs-verity (sha256 Merkle) digest and
records the same per-file digests inside ``root.cfs``. We verify a landed blob's
digest matches its name before trusting it, then enable kernel fs-verity so the
runtime ``verity`` mount flag is enforceable. The integrity rationale lives in
docs/integrity.md.
"""
from __future__ import annotations

from pathlib import Path

from . import runtime
from .runtime import run


def measure_file(path: Path) -> str:
    """Offline fs-verity sha256 digest (bare hex) of a file's contents.

    ``fsverity digest`` prints ``sha256:<hex>  <path>``; we keep the bare hex.
    """
    out = run(["fsverity", "digest", str(path)], capture=True)
    return out.split()[0].split(":", 1)[-1]


def enable_verity(path: Path) -> None:
    """Seal ``path`` with kernel fs-verity (no-op under dry-run)."""
    if runtime.DRY_RUN:
        runtime.log(f"[dry-run] fsverity enable {path}")
        return
    run(["fsverity", "enable", str(path)])
