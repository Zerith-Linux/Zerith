"""Deployment lifecycle on an installed host: materialize a deployment on disk,
stage and promote it, roll back, garbage-collect, and report status.

:func:`materialize` is the shared core — landing objects, placing and verifying
``root.cfs``, hardlinking the GC holder, and writing metadata — used by both an
update (:func:`stage`) and the first install. The flip of the ``current`` /
``fallback`` roles and the crash-safe ESP ordering live in :func:`promote` and
:func:`rollback`; see docs/deployment.md.
"""
from __future__ import annotations

import fcntl
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from . import config, layout, objects, runtime
from .oci import Source
from .runtime import die, log, vlog
from .verity import enable_verity, measure_file


@contextmanager
def deploy_lock(deploy: Path) -> Generator[None, None, None]:
    """Hold an exclusive lock on ``@deploy`` so a scheduled ``update`` and a
    hand-run ``deploy`` can't race. No-op under dry-run."""
    if runtime.DRY_RUN:
        yield
        return
    fd = os.open(deploy / config.LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("another zerithctl holds the lock; waiting…")
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def materialize(deploy: Path, src: Source, *, allow_ranges: bool,
                old_shards: dict) -> None:
    """Build deployment ``src`` on disk under ``@deploy``: land its objects,
    place + verify ``root.cfs``, link the GC holder, copy the UKI and loader,
    and write ``deployment.json``. Callers decide whether an existing deployment
    dir is reused (update) or refused (install) before calling.
    """
    shared = layout.shared_objects(deploy)
    ddir = layout.deploy_dir(deploy, src.deploy_id)
    holder = ddir / "objects"
    root_cfs = ddir / config.CFS_NAME
    deploy_uki = ddir / config.UKI_NAME

    if not runtime.DRY_RUN and not src.uki.is_file():
        die(f"deployment artifact has no {config.UKI_NAME}")

    if runtime.DRY_RUN:
        log(f"[dry-run] mkdir -p {ddir}")
    else:
        ddir.mkdir(parents=True, exist_ok=True)

    _land_objects(deploy, src, shared, allow_ranges=allow_ranges,
                  old_shards=old_shards)
    _place_root_cfs(src, root_cfs)

    if runtime.DRY_RUN:
        log(f"[dry-run] hardlink referenced objects into {holder}")
    else:
        objects.link_holder(shared, holder, root_cfs)

    if runtime.DRY_RUN:
        log(f"[dry-run] cp {src.uki} -> {deploy_uki}")
        log(f"[dry-run] cp {src.bootloader} -> {ddir / config.BOOTLOADER_NAME}")
    else:
        shutil.copy2(src.uki, deploy_uki)
        if src.bootloader.is_file():
            shutil.copy2(src.bootloader, ddir / config.BOOTLOADER_NAME)

    layout.write_meta(ddir, src)


def _land_objects(deploy: Path, src: Source, shared: Path, *,
                  allow_ranges: bool, old_shards: dict) -> None:
    """Dispatch to the right object-landing strategy for this source."""
    if src.local_objects is not None:
        objects.land_from_dir(src.local_objects, shared)
    elif src.objects_pack:
        objects.land_from_pack(src, shared, allow_ranges=allow_ranges)
    elif src.objects_ref:
        objects.land_from_ref(src.objects_ref, shared, src.object_shards,
                              old_shards)
    else:
        die("no object source (neither local objects/ nor objects_ref)")


def _place_root_cfs(src: Source, root_cfs: Path) -> None:
    """Copy ``root.cfs`` into place, seal it, and confirm its measured digest
    matches the value signed into the UKI."""
    if runtime.DRY_RUN:
        log(f"[dry-run] cp {src.root_cfs} -> {root_cfs}; verify {src.digest}")
        return
    shutil.copy2(src.root_cfs, root_cfs)
    enable_verity(root_cfs)
    got = measure_file(root_cfs)
    if got != src.digest:
        die(f"root.cfs digest mismatch: expected {src.digest}, got {got}")
    vlog("root.cfs digest verified against signed metadata")


def stage(deploy: Path, src: Source) -> None:
    """Materialize a deployment (idempotent) and mark it ``staging``.

    For schema>=2 packs this fetches only the byte ranges for missing objects;
    for legacy shards it diffs against the current deployment's shard map.
    """
    root_cfs = layout.deploy_dir(deploy, src.deploy_id) / config.CFS_NAME
    if not runtime.DRY_RUN and root_cfs.exists():
        log(f"deployment {src.deploy_id} already on disk, reusing")
    else:
        cur_id = layout.read_role(deploy, "current")
        old_shards = layout.load_meta(deploy, cur_id).get("object_shards", {})
        materialize(deploy, src, allow_ranges=True, old_shards=old_shards)
    layout.set_role(deploy, "staging", src.deploy_id)


def promote(deploy: Path, esp: Path, new_id: str) -> None:
    """Make ``new_id`` the current deployment; demote the old current to
    fallback. The ESP is updated first, in crash-safe order: a valid fallback is
    in place before current swings to the new image (boot reads the ESP, not the
    role symlinks).
    """
    old_current = layout.read_role(deploy, "current")
    if new_id == old_current:
        log(f"{new_id} is already current; nothing to promote")
        layout.clear_role(deploy, "staging")
        return

    new_uki = layout.deploy_dir(deploy, new_id) / config.UKI_NAME
    if not runtime.DRY_RUN and not new_uki.is_file():
        die(f"staged UKI missing: {new_uki}")

    cur_efi = layout.esp_uki(esp, "current")
    if old_current is not None and (runtime.DRY_RUN or cur_efi.is_file()):
        layout.atomic_copy(cur_efi, layout.esp_uki(esp, "fallback"))
    layout.atomic_copy(new_uki, cur_efi)

    # Refresh the signed Limine loader so a bootloader/key update propagates.
    new_loader = layout.deploy_dir(deploy, new_id) / config.BOOTLOADER_NAME
    if runtime.DRY_RUN or new_loader.is_file():
        layout.atomic_copy(new_loader, layout.esp_limine(esp))

    if old_current is not None:
        layout.set_role(deploy, "fallback", old_current)
    layout.set_role(deploy, "current", new_id)
    layout.clear_role(deploy, "staging")
    gc(deploy)
    log(f"promoted {new_id}" + (f" (previous current {old_current} -> fallback)"
                                if old_current else ""))


def rollback(deploy: Path, esp: Path) -> None:
    """Swap current <-> fallback on both the ESP and the role symlinks."""
    cur = layout.read_role(deploy, "current")
    fb = layout.read_role(deploy, "fallback")
    if fb is None:
        die("no fallback deployment to roll back to")

    cur_efi = layout.esp_uki(esp, "current")
    fb_efi = layout.esp_uki(esp, "fallback")
    if not runtime.DRY_RUN and not fb_efi.is_file():
        die(f"fallback UKI missing on ESP: {fb_efi}")

    if runtime.DRY_RUN:
        log(f"[dry-run] swap {cur_efi} <-> {fb_efi}")
    else:
        tmp = cur_efi.with_name(".swap.efi.tmp")
        shutil.copy2(cur_efi, tmp)
        os.replace(fb_efi, cur_efi)
        os.replace(tmp, fb_efi)
        vlog("swapped ESP UKIs")

    layout.set_role(deploy, "current", fb)
    if cur is not None:
        layout.set_role(deploy, "fallback", cur)
    log(f"rolled back: current is now {fb} (was {cur})")


def _collect_stale(deploy: Path) -> list[Path]:
    """Deployment dirs eligible for removal: real dirs, not ``shared``, not a
    dotfile, and not currently targeted by a role symlink."""
    protected = {layout.read_role(deploy, r) for r in config.ROLES}
    protected.discard(None)
    stale = []
    for p in sorted(deploy.iterdir()):
        if p.is_symlink() or not p.is_dir():
            continue
        if p.name == "shared" or p.name.startswith(".") or p.name in protected:
            continue
        stale.append(p)
    return stale


def gc(deploy: Path) -> None:
    """Remove any deployment not held by a role, then sweep orphaned objects.

    Object GC is by hardlink count: a shared object referenced by K live
    deployments has link count ``1 + K``, so ``count == 1`` means no holder
    references it any more.
    """
    shared = layout.shared_objects(deploy)
    stale = _collect_stale(deploy)

    for p in stale:
        if runtime.DRY_RUN:
            log(f"[dry-run] rm -rf {p}")
        else:
            shutil.rmtree(p)
            vlog(f"removed deployment {p.name}")

    if runtime.DRY_RUN:
        log(f"[dry-run] sweep orphaned objects in {shared} (link count == 1)")
        return

    swept = objects.sweep_orphans(shared)
    if stale:
        extra = f"; {swept} orphan object(s)" if swept else ""
        log(f"gc: removed {len(stale)} deployment(s): "
            f"{', '.join(p.name for p in stale)}{extra}")
    else:
        vlog("gc: nothing to remove")


def status(deploy: Path, config_path: Path) -> None:
    """Print the update channel and the current / fallback / staging roles."""
    channel = layout.read_source(config_path) or "(unset — run deploy REF)"
    print(f"channel   {channel}")
    for role in config.ROLES:
        rid = layout.read_role(deploy, role)
        if rid is None:
            print(f"{role:8}  -")
            continue
        meta = layout.load_meta(deploy, rid)
        ver = meta.get("version", "?")
        ref = meta.get("ref") or "-"
        print(f"{role:8}  {rid}  version={ver}  ref={ref}")


def check_layout(deploy: Path, esp: Path) -> None:
    """Sanity-check that ``deploy`` and ``esp`` point at a real Zerith host."""
    if runtime.DRY_RUN:
        return
    if not layout.shared_objects(deploy).is_dir():
        die(f"{deploy} doesn't look like a Zerith @deploy mount "
            f"(no shared/objects) — point --deploy at it")
    if not (esp / "zerith").is_dir():
        die(f"{esp} doesn't look like the Zerith ESP (no zerith/) — "
            f"point --esp at it")


def do_deploy(deploy: Path, esp: Path, ref: str, cos_id: str | None,
              cos_issuer: str, skip_verify: bool) -> None:
    """Pull + verify ``ref``, then stage and promote it (unless already current)."""
    from .oci import source_from_ref
    src = source_from_ref(ref, cos_id, cos_issuer, skip_verify)
    try:
        if src.deploy_id == layout.read_role(deploy, "current"):
            log(f"already on {src.deploy_id} (version {src.version}); "
                f"up to date")
            return
        stage(deploy, src)
        promote(deploy, esp, src.deploy_id)
        log(f"now current: {src.deploy_id} (version {src.version})")
    finally:
        src.cleanup()
