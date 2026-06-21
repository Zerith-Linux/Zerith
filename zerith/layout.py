"""On-disk layout primitives for the ``@deploy`` subvolume and the ESP.

These are the low-level building blocks the lifecycle and installer compose:
deployment directories, the three role symlinks, per-deployment
``deployment.json`` metadata, the ``source.conf`` update channel, and atomic
copies onto the ESP. The layout itself is documented in docs/architecture.md.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import config, runtime
from .runtime import now, vlog


# --------------------------------------------------------------------------- #
# Deployment directories
# --------------------------------------------------------------------------- #

def deploy_dir(deploy: Path, deploy_id: str) -> Path:
    return deploy / deploy_id


def shared_objects(deploy: Path) -> Path:
    return deploy / "shared" / "objects"


# --------------------------------------------------------------------------- #
# Roles (relative symlinks: current / fallback / staging)
# --------------------------------------------------------------------------- #

def read_role(deploy: Path, role: str) -> str | None:
    """The deploy id a role points at, or ``None`` if the role is unset."""
    link = deploy / role
    if not link.is_symlink():
        return None
    return os.path.basename(os.readlink(link))


def set_role(deploy: Path, role: str, deploy_id: str) -> None:
    """Point ``role`` at ``deploy_id`` via an atomic symlink replace."""
    link = deploy / role
    if runtime.DRY_RUN:
        runtime.log(f"[dry-run] ln -sfn {deploy_id} {link}")
        return
    tmp = deploy / f".{role}.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(deploy_id)
    os.replace(tmp, link)
    vlog(f"role {role} -> {deploy_id}")


def clear_role(deploy: Path, role: str) -> None:
    link = deploy / role
    if runtime.DRY_RUN:
        runtime.log(f"[dry-run] rm -f {link}")
        return
    if link.is_symlink() or link.exists():
        link.unlink()
        vlog(f"role {role} cleared")


# --------------------------------------------------------------------------- #
# Per-deployment metadata (deployment.json)
# --------------------------------------------------------------------------- #

def write_meta(ddir: Path, src) -> None:
    """Write a deployment's self-describing ``deployment.json`` from a Source."""
    meta = {
        "schema": config.META_SCHEMA,
        "deploy_id": src.deploy_id,
        "version": src.version,
        "composefs_digest": src.digest,
        "objects_ref": src.objects_ref,
        "objects_slab": src.objects_slab,
        "objects_index": src.objects_index,
        "ref": src.ref,
        "deployed_at": now(),
    }
    path = ddir / config.META_NAME
    if runtime.DRY_RUN:
        runtime.log(f"[dry-run] write {path}")
        vlog(json.dumps(meta, indent=2))
        return
    path.write_text(json.dumps(meta, indent=2) + "\n")
    vlog(f"wrote {path}")


def load_meta(deploy: Path, deploy_id: str | None) -> dict:
    """Read a deployment's ``deployment.json``; empty dict if absent/unreadable."""
    if not deploy_id:
        return {}
    try:
        return json.loads(
            (deploy_dir(deploy, deploy_id) / config.META_NAME).read_text())
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------- #
# Update channel (source.conf)
# --------------------------------------------------------------------------- #

def read_source(config_path: Path) -> str | None:
    try:
        return json.loads(config_path.read_text()).get("ref")
    except (OSError, ValueError):
        return None


def write_source(config_path: Path, ref: str) -> None:
    data = {"schema": config.CONFIG_SCHEMA, "ref": ref, "updated_at": now()}
    if runtime.DRY_RUN:
        runtime.log(f"[dry-run] write {config_path} (ref={ref})")
        return
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_name(config_path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, config_path)
    vlog(f"update channel -> {ref}")


# --------------------------------------------------------------------------- #
# ESP paths + atomic copy
# --------------------------------------------------------------------------- #

def esp_uki(esp: Path, role: str) -> Path:
    """The role-named UKI Limine chainloads (``current.efi`` / ``fallback.efi``)."""
    return esp / "zerith" / f"{role}.efi"


def esp_limine(esp: Path) -> Path:
    """The Limine loader on the ESP, at the removable-media fallback path so it
    boots without an NVRAM entry."""
    return esp / "EFI" / "BOOT" / config.BOOTLOADER_NAME


def atomic_copy(src: Path, dst: Path) -> None:
    """Copy ``src`` onto ``dst`` via a temp file + rename, so readers (the
    firmware) never see a half-written file."""
    if runtime.DRY_RUN:
        runtime.log(f"[dry-run] cp {src} -> {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)
    vlog(f"copied {src} -> {dst}")
