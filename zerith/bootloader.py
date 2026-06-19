"""Limine bootloader installation and its static config.

Limine is installed to the removable-media fallback path
(``/EFI/BOOT/BOOTX64.EFI``) so it boots in any firmware without an NVRAM entry;
a labelled efibootmgr entry pointing at the same file is added best-effort. The
boot chain is described in docs/boot.md.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import config, runtime
from .runtime import log, run, vlog

# Static two-entry config, written once at install and never touched by updates.
# Syntax confirmed against Limine v12.3.3 (the pinned version): `protocol:
# efi_chainload` is a documented alias of the `efi` protocol, and `path:` takes
# a `boot():/…` location. With a signed UKI + Secure Boot, the firmware's own
# image verification gates the chainloaded UKI, so the pinned composefs.digest
# in the UKI's signed cmdline can't be stripped at boot. See docs/boot.md.
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


def write_limine_conf(efi: Path) -> None:
    """Write the static ``limine.conf`` once; leave an existing one alone."""
    path = efi / "limine.conf"
    if runtime.DRY_RUN:
        log(f"[dry-run] write {path} (static current/fallback)")
        vlog(LIMINE_CONF)
        return
    if path.exists():
        vlog(f"{path} already present, leaving it")
        return
    path.write_text(LIMINE_CONF)
    vlog(f"wrote {path}")


def _esp_block_device(efi: Path) -> str | None:
    """The /dev node backing the ESP mountpoint, from /proc/mounts."""
    def unescape(p: str) -> str:
        return (p.replace(r"\040", " ").replace(r"\011", "\t")
                 .replace(r"\012", "\n").replace(r"\134", "\\"))

    target = os.path.realpath(str(efi))
    found: str | None = None
    for line in Path("/proc/mounts").read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("/dev/"):
            if os.path.realpath(unescape(parts[1])) == target:
                found = parts[0]
    return found


def _disk_and_partnum(part_dev: str) -> tuple[str, str] | None:
    """Resolve a partition node to its parent disk and partition number."""
    name = os.path.basename(part_dev)
    sysblk = Path("/sys/class/block") / name
    pfile = sysblk / "partition"
    if not pfile.is_file():
        return None
    part_num = pfile.read_text().strip()
    parent = os.path.basename(os.path.dirname(os.path.realpath(str(sysblk))))
    return (f"/dev/{parent}", part_num) if parent else None


def install_limine(efi: Path, disk: Path | None = None,
                   esp_part: str | None = None,
                   loader_src: Path | None = None) -> None:
    """Copy the Limine loader onto the ESP fallback path and, best-effort, add a
    labelled NVRAM boot entry. The fallback path alone is enough to boot; the
    NVRAM entry just ranks Zerith above other fallbacks where it can be created.
    """
    if runtime.DRY_RUN:
        log("[dry-run] install Limine (copy loader + efibootmgr entry)")
        return

    loader = _resolve_loader(loader_src)
    if loader is None:
        return
    dest = efi / "EFI" / "BOOT" / config.BOOTLOADER_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(loader, dest)
    vlog(f"copied Limine loader to {dest}")

    disk, part_num = _resolve_disk_partition(efi, disk, esp_part)
    if disk is None or part_num is None:
        log("note: could not resolve ESP disk/partition; booting via the "
            "fallback path only (no labelled NVRAM entry)")
        return
    _create_nvram_entry(disk, part_num)


def _resolve_loader(loader_src: Path | None) -> str | None:
    """Return the Secure Boot-signed Limine loader from the deployment artifact,
    or ``None`` to skip. The OS image ships no loader of its own — it is fetched
    and signed in CI and travels in the artifact — so there is no local fallback.
    """
    if loader_src is not None and loader_src.is_file():
        return str(loader_src)
    log("warning: deployment artifact has no Limine loader; skipping bootloader "
        "install (boot relies on an already-present loader)")
    return None


def _resolve_disk_partition(efi: Path, disk: Path | None,
                            esp_part: str | None
                            ) -> tuple[Path | None, str | None]:
    """Work out the ESP's disk and partition number from explicit args or, as a
    fallback, from the live mount table."""
    part_num: str | None = None
    if esp_part is not None:
        m = re.search(r"p?(\d+)$", esp_part)
        part_num = m.group(1) if m else None
    if disk is None or part_num is None:
        dev = _esp_block_device(efi)
        resolved = _disk_and_partnum(dev) if dev else None
        if resolved:
            disk = disk or Path(resolved[0])
            part_num = part_num or resolved[1]
    return disk, part_num


def _create_nvram_entry(disk: Path, part_num: str) -> None:
    """Add a labelled efibootmgr entry pointing at the fallback loader path."""
    if not shutil.which("efibootmgr"):
        log("note: efibootmgr not found; booting via the fallback path only")
        return
    if not os.path.exists("/sys/firmware/efi/efivars"):
        log("note: efivars not mounted; booting via the fallback path only")
        return
    if config.EFI_LABEL in run(["efibootmgr"], capture=True):
        vlog(f"UEFI boot entry {
             config.EFI_LABEL!r} already present, leaving it")
        return
    run(["efibootmgr", "--create",
         "--disk", str(disk), "--part", part_num,
         "--label", config.EFI_LABEL,
         "--loader", r"\EFI\BOOT\BOOTX64.EFI",
         "--unicode"])
    log("Limine boot entry created")
