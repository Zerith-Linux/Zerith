"""Fresh-install orchestration: create the btrfs subvolume layout, land the
first deployment as ``current``, seed the update channel, and install Limine.

This is the ``install`` subcommand's core. Everything that mutates an
already-installed host lives in :mod:`zerith.lifecycle`; this module only handles
the one-time initial install. See docs/deployment.md.
"""
from __future__ import annotations

from pathlib import Path

from . import bootloader, config, layout, lifecycle, runtime
from .oci import Source
from .runtime import die, log, vlog


def init_layout(sysroot: Path, efi: Path) -> None:
    """Create the persistent btrfs subvolumes and the directories the store and
    ESP need. Idempotent: existing subvolumes are left in place."""
    for sub in ("@var", "@home", "@deploy"):
        path = sysroot / sub
        if not runtime.DRY_RUN and path.exists():
            vlog(f"subvolume {sub} already present")
            continue
        runtime.run(["btrfs", "subvolume", "create", str(path)])

    for d in (sysroot / "@deploy" / "shared" / "objects", efi / "zerith"):
        if runtime.DRY_RUN:
            log(f"[dry-run] mkdir -p {d}")
        else:
            d.mkdir(parents=True, exist_ok=True)
            vlog(f"ensured dir {d}")


def install_deploy(sysroot: Path, efi: Path, src: Source) -> None:
    """Land the first deployment and make it ``current``: materialize it, copy
    its UKI onto the ESP, set the role, seed ``source.conf``, and write the
    static ``limine.conf``."""
    deploy = sysroot / "@deploy"
    ddir = layout.deploy_dir(deploy, src.deploy_id)
    if not runtime.DRY_RUN and ddir.exists():
        die(f"deployment {src.deploy_id} already installed at {ddir}")

    # First install: every object is missing, so the slab range fetch coalesces
    # the whole want-list into a single whole-slab range.
    lifecycle.materialize(deploy, src)

    esp_uki = layout.esp_uki(efi, "current")
    if runtime.DRY_RUN:
        log(f"[dry-run] cp {src.uki} -> {esp_uki}")
    else:
        esp_uki.parent.mkdir(parents=True, exist_ok=True)
        layout.atomic_copy(ddir / config.UKI_NAME, esp_uki)

    layout.set_role(deploy, "current", src.deploy_id)
    if src.ref:                                   # seed the update channel
        layout.write_source(deploy / config.SOURCE_CONF_NAME, src.ref)
    bootloader.write_limine_conf(efi)

    log(f"installed deployment {src.deploy_id} "
        f"(version {src.version}) as current")
