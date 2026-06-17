#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import re
from typing import NoReturn
from datetime import datetime, timezone
from pathlib import Path

# matches the Containerfile COPY target
DEFAULT_UKI_IN_IMAGE = "usr/lib/uki/zerith.efi"

DEFAULT_ESP_SIZE = "1GiB"

# MUST match the init's LABEL=zerith
DEFAULT_LABEL = "zerith"

DRY_RUN = False
VERBOSE = False


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #

def log(msg: str) -> None:
    print(f"install-zerith: {msg}")


def vlog(msg: str) -> None:
    if VERBOSE:
        log(msg)


def die(msg: str) -> "NoReturn":
    print(f"install-zerith: error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], *, capture: bool = False) -> str:
    printable = " ".join(cmd)
    if DRY_RUN:
        log(f"[dry-run] {printable}")
        return ""
    vlog(f"exec: {printable}")
    res = subprocess.run(cmd, text=True, capture_output=capture)
    if res.returncode != 0:
        if capture and res.stderr:
            sys.stderr.write(res.stderr)
        die(f"command failed ({res.returncode}): {printable}")
    return (res.stdout or "").strip() if capture else ""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_root() -> None:
    if not DRY_RUN and os.geteuid() != 0:
        die("must run as root")


# --------------------------------------------------------------------------- #
# Disk partitioning (--disk mode)
# --------------------------------------------------------------------------- #

def partition_names(disk: Path) -> tuple[str, str]:
    """ESP and btrfs partition node names: nvme0n1 -> p1/p2, sda -> 1/2."""
    d = str(disk)
    sep = "p" if d[-1].isdigit() else ""
    return f"{d}{sep}1", f"{d}{sep}2"


def assert_disk_free(disk: Path) -> None:
    if DRY_RUN:
        return
    d = str(disk)
    for line in Path("/proc/mounts").read_text().splitlines():
        dev = line.split()[0]
        # disk itself or any of its partitions
        if dev == d or dev.startswith(d):
            die(f"{dev} is mounted — refusing to partition {disk}")


def confirm_wipe(disk: Path, yes: bool) -> None:
    if yes or DRY_RUN:
        return
    print(f"WARNING: this will ERASE ALL DATA on {disk}.")
    try:
        resp = input("Type 'yes' to continue: ")
    except EOFError:
        die("no terminal to confirm on; pass --yes to proceed")
    if resp.strip().lower() != "yes":
        die("aborted")


def partition_disk(disk: Path, esp_size: str, label: str) -> tuple[str, str]:
    """Wipe `disk`, lay down GPT (ESP + btrfs), format both. Returns part nodes."""
    run(["sgdisk", "--zap-all", str(disk)])
    run(["sgdisk", f"-n1:0:+{esp_size}", "-t1:EF00", "-c1:ESP", str(disk)])
    run(["sgdisk", "-n2:0:0", "-t2:8300", f"-c2:{label}", str(disk)])
    run(["partprobe", str(disk)])
    esp_part, btrfs_part = partition_names(disk)
    run(["mkfs.fat", "-F32", "-n", f"efi-{label}", esp_part])
    run(["mkfs.btrfs", "-f", "-L", label, btrfs_part])
    return esp_part, btrfs_part


def mount_targets(esp_part: str, btrfs_part: str):
    """Mount the two partitions to temp dirs. Returns (sysroot, efi, cleanup)."""
    if DRY_RUN:
        base = Path("/run/zerith-install")
        log(f"[dry-run] mount {btrfs_part} {base/'sysroot'}")
        log(f"[dry-run] mount {esp_part} {base/'efi'}")
        return base / "sysroot", base / "efi", lambda: None

    base = Path(tempfile.mkdtemp(prefix="zerith-install-"))
    sysroot, efi = base / "sysroot", base / "efi"
    sysroot.mkdir()
    efi.mkdir()
    run(["mount", btrfs_part, str(sysroot)])
    run(["mount", esp_part, str(efi)])

    def cleanup() -> None:
        run(["umount", str(efi)])
        run(["umount", str(sysroot)])
        shutil.rmtree(base, ignore_errors=True)

    return sysroot, efi, cleanup


# --------------------------------------------------------------------------- #
# Layout initialization (idempotent)
# --------------------------------------------------------------------------- #

def init_layout(sysroot: Path, efi: Path) -> None:
    for sub in ("@var", "@home", "@deploy"):
        path = sysroot / sub
        if (not DRY_RUN) and path.exists():
            vlog(f"subvolume {sub} already present")
            continue
        run(["btrfs", "subvolume", "create", str(path)])

    deploy = sysroot / "deploy"
    for d in (deploy / "shared" / "objects",   # canonical content-addressed store
              deploy / "zenith",               # state.json lives here
              # bootable UKIs (current.efi/fallback.efi)
              efi / "zerith"):
        if DRY_RUN:
            log(f"[dry-run] mkdir -p {d}")
        else:
            d.mkdir(parents=True, exist_ok=True)
            vlog(f"ensured dir {d}")


# --------------------------------------------------------------------------- #
# Source resolution
# --------------------------------------------------------------------------- #

class Source:
    def __init__(self, rootfs: Path, uki: Path, deploy_id: str, version: str,
                 cleanup=None):
        self.rootfs = rootfs
        self.uki = uki
        self.deploy_id = deploy_id
        self.version = version
        self._cleanup = cleanup

    def cleanup(self) -> None:
        if self._cleanup:
            self._cleanup()


def source_from_image(image: str, uki_in_image: str) -> Source:
    run(["podman", "pull", image])
    fmt = '{{ index .Labels "%s" }}'
    deploy_id = run(["podman", "image", "inspect", "--format",
                   fmt % "org.zerith.deploy-id", image], capture=True)
    version = run(["podman", "image", "inspect", "--format",
                   fmt % "org.opencontainers.image.version", image], capture=True)
    if not deploy_id and not DRY_RUN:
        die(f"image {image} has no org.zerith.deploy-id label")
    mountpoint = run(["podman", "image", "mount", image], capture=True)
    rootfs = Path(mountpoint or "/dry-run-rootfs")
    return Source(
        rootfs=rootfs,
        uki=rootfs / uki_in_image,
        deploy_id=deploy_id or "dryrunid00000000",
        version=version or "unknown",
        cleanup=lambda: run(["podman", "image", "unmount", image]),
    )


def source_from_rootfs(rootfs: Path, uki: Path, deploy_id: str,
                       version: str) -> Source:
    if not DRY_RUN:
        if not rootfs.is_dir():
            die(f"--rootfs {rootfs} is not a directory")
        if not uki.is_file():
            die(f"--uki {uki} not found")
    return Source(rootfs=rootfs, uki=uki, deploy_id=deploy_id,
                  version=version or "unknown")


# --------------------------------------------------------------------------- #
# State — current/fallback/staging roles (3-deployment pool)
# --------------------------------------------------------------------------- #

def state_path(sysroot: Path) -> Path:
    return sysroot / "deploy" / "zenith" / "state.json"


def init_state(src: Source) -> dict:
    return {
        "current": src.deploy_id,
        "fallback": None,
        "staging": None,
        "next_seq": 2,
        "deployments": {
            src.deploy_id: {
                "version": src.version,
                "seq": 1,
                "deployed_at": now(),
            },
        },
    }


def save_state(sysroot: Path, state: dict) -> None:
    path = state_path(sysroot)
    if DRY_RUN:
        log(f"[dry-run] write state -> {path}")
        vlog(json.dumps(state, indent=2))
        return
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)
    vlog(f"wrote state -> {path}")


# --------------------------------------------------------------------------- #
# Bootloader config — STATIC, written once
# --------------------------------------------------------------------------- #

LIMINE_CONF = """\
timeout: 3
default_entry: 1

/Zerith
    protocol: efi_chainload
    path: boot():/zerith/current.efi

/Zerith (previous)
    protocol: efi_chainload
    path: boot():/zerith/fallback.efi
"""


def write_limine(efi: Path) -> None:
    """Write the static two-entry limine.conf, once. Updates never touch it.

    NOTE: verify keys/paths against your Limine version — syntax has changed
    across releases. `fallback.efi` won't exist until the first update;
    selecting it before then just fails, which is harmless.
    """
    path = efi / "limine.conf"
    if DRY_RUN:
        log(f"[dry-run] write {path} (static current/fallback)")
        vlog(LIMINE_CONF)
        return
    if path.exists():
        vlog(f"{path} already present, leaving it")
        return
    path.write_text(LIMINE_CONF)
    vlog(f"wrote {path}")


# --------------------------------------------------------------------------- #
# Limine installation
# --------------------------------------------------------------------------- #

def install_limine(efi: Path, disk: Path, esp_part: str) -> None:
    """Copy Limine EFI loader to ESP and create UEFI boot entry."""
    if DRY_RUN:
        log("[dry-run] install Limine")
        return

    limine_src = "/usr/share/limine/BOOTX64.EFI"
    if not os.path.isfile(limine_src):
        log("warning: Limine EFI loader not found at %s, skipping" % limine_src)
        return

    # Destination: /EFI/zerith-limine/BOOTX64.EFI
    dest_dir = efi / "EFI" / "zerith-limine"
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(limine_src, dest_dir / "BOOTX64.EFI")
    vlog(f"copied Limine loader to {dest_dir / 'BOOTX64.EFI'}")

    # Prepare efibootmgr command
    m = re.search(r'p?(\d+)$', esp_part)
    if not m:
        log("warning: could not parse partition number from %s, skipping efibootmgr" % esp_part)
        return
    part_num = m.group(1)

    if not shutil.which("efibootmgr"):
        log("warning: efibootmgr not found, skipping boot entry creation")
        return
    if not os.path.exists("/sys/firmware/efi/efivars"):
        log("warning: efivars not mounted, skipping boot entry creation")
        return

    cmd = [
        "efibootmgr",
        "--create",
        "--disk", str(disk),
        "--part", part_num,
        "--label", "Zerith Boot Manager",
        "--loader", r"\EFI\zerith-limine\BOOTX64.EFI",
        "--unicode"
    ]
    run(cmd)
    log("Limine boot entry created")


# --------------------------------------------------------------------------- #
# Deploy
# --------------------------------------------------------------------------- #

def deploy(sysroot: Path, efi: Path, src: Source) -> None:
    shared = sysroot / "deploy" / "shared" / "objects"
    deploy_dir = sysroot / "deploy" / src.deploy_id
    holder = deploy_dir / "objects"
    root_cfs = deploy_dir / "root.cfs"
    btrfs_uki = deploy_dir / "zerith.efi"          # per-deployment source of truth
    esp_uki = efi / "zerith" / "current.efi"       # fixed role-named bootable copy

    if (not DRY_RUN) and deploy_dir.exists():
        die(f"deployment {src.deploy_id} already installed at {deploy_dir}")

    if DRY_RUN:
        log(f"[dry-run] mkdir -p {deploy_dir}")
    else:
        deploy_dir.mkdir(parents=True, exist_ok=True)

    # Render the read-only composefs image into the SHARED object store.
    run(["mkcomposefs", f"--digest-store={shared}",
         str(src.rootfs), str(root_cfs)])

    # Per-deployment hardlink holder. Fresh install => the shared store holds
    # exactly this deployment's objects, so a hardlink mirror is correct. Every
    # object now has link count 2 (shared + holder); GC keys off that.
    if DRY_RUN:
        log(f"[dry-run] mkdir -p {holder}")
        log(f"[dry-run] cp -a -l {shared}/. {holder}/")
    else:
        holder.mkdir(parents=True, exist_ok=True)
        run(["cp", "-a", "-l", f"{shared}/.", f"{holder}/"])

    # Keep the deployment's UKI with it (the source `promote` pulls from later),
    # and copy it to the ESP's fixed current.efi. The baked deploy=<id> mounts
    # the right dir regardless of the file name, so the ESP names stay static.
    if DRY_RUN:
        log(f"[dry-run] cp {src.uki} -> {btrfs_uki}")
        log(f"[dry-run] cp {src.uki} -> {esp_uki}")
    else:
        shutil.copy2(src.uki, btrfs_uki)
        shutil.copy2(src.uki, esp_uki)
        vlog(f"installed UKI -> {esp_uki}")

    state = init_state(src)
    save_state(sysroot, state)
    write_limine(efi)

    log(f"installed deployment {
        src.deploy_id} (version {src.version}) as current")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    global DRY_RUN, VERBOSE

    p = argparse.ArgumentParser(
        prog="install-zerith",
        description="Partition (optional), initialize, and install Zerith.",
    )
    p.add_argument("sysroot", nargs="?", type=Path,
                   help="mountpoint of the target btrfs (omit when using --disk)")
    p.add_argument("efi", nargs="?", type=Path,
                   help="mountpoint of the ESP (omit when using --disk)")

    p.add_argument("--disk", type=Path,
                   help="whole disk to auto-partition, e.g. /dev/nvme0n1 (DESTROYS it)")
    p.add_argument("--yes", action="store_true",
                   help="skip the wipe confirmation")
    p.add_argument("--esp-size", default=DEFAULT_ESP_SIZE,
                   help="ESP size (default 1GiB)")
    p.add_argument("--label", default=DEFAULT_LABEL,
                   help="btrfs label; MUST match the init's LABEL= (default zerith)")

    p.add_argument("--image", help="OCI image ref to pull and install")
    p.add_argument("--rootfs", type=Path,
                   help="alternative: an extracted/mounted rootfs")
    p.add_argument("--uki", type=Path, help="UKI path (with --rootfs)")
    p.add_argument("--deploy-id", help="deployment id (with --rootfs)")
    p.add_argument("--version", default="",
                   help="version label (with --rootfs)")
    p.add_argument("--uki-in-image", default=DEFAULT_UKI_IN_IMAGE,
                   help="UKI path inside the image (with --image)")

    p.add_argument("--no-limine", action="store_true",
                   help="skip installing the Limine bootloader (efibootmgr)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")

    args = p.parse_args(argv)
    DRY_RUN = args.dry_run
    VERBOSE = args.verbose

    # target: either --disk (we partition+mount) or sysroot+efi (already mounted)
    if args.disk and (args.sysroot or args.efi):
        die("give either --disk OR sysroot+efi, not both")
    if not args.disk and not (args.sysroot and args.efi):
        die("provide --disk DEV, or both sysroot and efi mountpoints")

    # source: exactly one of --image / --rootfs
    if bool(args.image) == bool(args.rootfs):
        die("provide exactly one source: --image REF  or  --rootfs DIR")
    if args.rootfs and not (args.uki and args.deploy_id):
        die("--rootfs also requires --uki and --deploy-id")

    require_root()

    if args.disk:
        assert_disk_free(args.disk)
        confirm_wipe(args.disk, args.yes)
        esp_part, btrfs_part = partition_disk(
            args.disk, args.esp_size, args.label)
        sysroot, efi, unmount = mount_targets(esp_part, btrfs_part)
        # Install Limine after mounting
        if not args.no_limine:
            install_limine(efi, args.disk, esp_part)
    else:
        sysroot, efi, unmount = args.sysroot, args.efi, (lambda: None)

    try:
        init_layout(sysroot, efi)
        if args.image:
            src = source_from_image(args.image, args.uki_in_image)
        else:
            src = source_from_rootfs(
                args.rootfs, args.uki, args.deploy_id, args.version)
        try:
            deploy(sysroot, efi, src)
        finally:
            src.cleanup()
    finally:
        unmount()

    log("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
