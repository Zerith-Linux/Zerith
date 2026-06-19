"""Whole-disk partitioning, formatting, and mounting for the ``install --disk``
path. Only used on a fresh install; once Zerith owns the disk, lifecycle
operations never touch partitions. See docs/deployment.md.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable

from . import runtime
from .runtime import die, run, vlog


def partition_names(disk: Path) -> tuple[str, str]:
    """ESP and btrfs partition node names: ``nvme0n1`` -> ``p1``/``p2``,
    ``sda`` -> ``1``/``2`` (NVMe-style disks end in a digit and take a ``p``)."""
    d = str(disk)
    sep = "p" if d[-1].isdigit() else ""
    return f"{d}{sep}1", f"{d}{sep}2"


def assert_disk_free(disk: Path) -> None:
    """Refuse to partition a disk that has a mounted partition."""
    if runtime.DRY_RUN:
        return
    d = str(disk)
    for line in Path("/proc/mounts").read_text().splitlines():
        dev = line.split()[0]
        if dev == d or dev.startswith(d):
            die(f"{dev} is mounted — refusing to partition {disk}")


def confirm_wipe(disk: Path, yes: bool) -> None:
    """Interactive 'this erases everything' confirmation, unless ``--yes``."""
    if yes or runtime.DRY_RUN:
        return
    print(f"WARNING: this will ERASE ALL DATA on {disk}.")
    try:
        resp = input("Type 'yes' to continue: ")
    except EOFError:
        die("no terminal to confirm on; pass --yes to proceed")
    if resp.strip().lower() != "yes":
        die("aborted")


def partition_disk(disk: Path, esp_size: str, label: str) -> tuple[str, str]:
    """Wipe ``disk``, lay down GPT (ESP + btrfs), and format both. Returns the
    ESP and btrfs partition nodes."""
    run(["sgdisk", "--zap-all", str(disk)])
    run(["sgdisk", f"-n1:0:+{esp_size}", "-t1:EF00", "-c1:ESP", str(disk)])
    run(["sgdisk", "-n2:0:0", "-t2:8300", f"-c2:{label}", str(disk)])
    run(["partprobe", str(disk)])
    esp_part, btrfs_part = partition_names(disk)
    run(["mkfs.fat", "-F32", "-n", f"efi-{label}", esp_part])
    # btrfs fs-verity needs no mkfs flag (unlike ext4's -O verity): it's a
    # compat_ro feature the kernel sets automatically the first time a file has
    # verity enabled. The only requirement is kernel >= 5.15, which the shipped
    # linux-zen satisfies; the first `fsverity enable` fails loudly otherwise.
    run(["mkfs.btrfs", "-f", "-L", label, btrfs_part])
    return esp_part, btrfs_part


def mount_targets(esp_part: str, btrfs_part: str
                  ) -> tuple[Path, Path, Callable[[], None]]:
    """Mount both partitions to temp dirs. Returns ``(sysroot, efi, cleanup)``."""
    if runtime.DRY_RUN:
        base = Path("/run/zerith-install")
        runtime.log(f"[dry-run] mount {btrfs_part} {base / 'sysroot'}")
        runtime.log(f"[dry-run] mount {esp_part} {base / 'efi'}")
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

    vlog(f"mounted sysroot={sysroot} efi={efi}")
    return sysroot, efi, cleanup
