"""Process execution, logging, and global run flags.

Every other module routes shell-outs through :func:`run` and user-facing
messages through :func:`log` / :func:`vlog` / :func:`die`, so dry-run and
verbose handling live in exactly one place. ``--dry-run`` and ``--verbose`` set
the two module-level flags here once, at startup, from the CLI.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import NoReturn

# Set once by the CLI before any work begins.
DRY_RUN = False
VERBOSE = False

_PREFIX = "zerithctl"


def log(msg: str) -> None:
    print(f"{_PREFIX}: {msg}")


def vlog(msg: str) -> None:
    if VERBOSE:
        log(msg)


def die(msg: str) -> NoReturn:
    print(f"{_PREFIX}: error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], *, capture: bool = False, check: bool = True) -> str:
    """Run ``cmd``. Mutating commands (``capture=False``) are skipped and logged
    under ``--dry-run``; read-only captures still run so the dry run can reason
    about real state. Returns captured stdout (stripped) or ``""``.
    """
    printable = " ".join(cmd)
    if DRY_RUN and not capture:
        log(f"[dry-run] {printable}")
        return ""
    vlog(f"exec: {printable}")
    res = subprocess.run(cmd, text=True, capture_output=capture)
    if res.returncode != 0 and check:
        if capture and res.stderr:
            sys.stderr.write(res.stderr)
        die(f"command failed ({res.returncode}): {printable}")
    return (res.stdout or "").strip() if capture else ""


def now() -> str:
    """Current UTC time as an ISO-8601 string, second precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_root() -> None:
    if not DRY_RUN and os.geteuid() != 0:
        die("must run as root")


def require_tool(name: str) -> None:
    if not DRY_RUN and not shutil.which(name):
        die(f"required tool '{name}' not found on PATH")
