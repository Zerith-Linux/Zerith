"""Zerith host tooling.

A small package behind the ``zerithctl`` command: installs Zerith and manages
the deployment lifecycle (status, deploy, update, rollback, gc) on a running
host. The layered design is documented in docs/project-structure.md.
"""
from __future__ import annotations

from .cli import main

__all__ = ["main"]
__version__ = "1.0.0"
