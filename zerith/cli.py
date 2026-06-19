"""Command-line interface for ``zerithctl``.

One tool drives the whole deployment lifecycle on a running host
(``status`` / ``deploy`` / ``update`` / ``rollback`` / ``gc``) and the initial
install (``install``). Options follow their subcommand
(``zerithctl status --deploy /deploy``, ``zerithctl install --disk … --dry-run``)
so each command is self-contained. See docs/host-tooling.md.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import config, disk, installer, layout, lifecycle, runtime
from .bootloader import install_limine
from .oci import source_from_local, source_from_ref
from .runtime import die


def _common_opts() -> argparse.ArgumentParser:
    """Run-mode flags shared by every subcommand."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--dry-run", action="store_true",
                   help="print actions without changing anything")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _trust_opts() -> argparse.ArgumentParser:
    """cosign verification options (commands that pull a signed artifact)."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--cosign-identity",
                   default=os.environ.get("ZERITH_COSIGN_IDENTITY"),
                   help="cosign --certificate-identity-regexp "
                        "(env ZERITH_COSIGN_IDENTITY)")
    p.add_argument("--cosign-issuer",
                   default=os.environ.get("ZERITH_COSIGN_ISSUER",
                                          config.DEFAULT_COSIGN_ISSUER),
                   help="cosign --certificate-oidc-issuer")
    p.add_argument("--insecure-skip-verify", action="store_true",
                   help="DEV ONLY: skip cosign verification")
    return p


def _host_opts() -> argparse.ArgumentParser:
    """Target paths for commands operating on an installed host."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--deploy", type=Path, default=config.DEFAULT_DEPLOY,
                   help="@deploy subvolume mountpoint (default /deploy)")
    p.add_argument("--esp", type=Path, default=config.DEFAULT_ESP,
                   help="ESP mountpoint (default /efi)")
    p.add_argument("--config", type=Path, default=None,
                   help="update-channel config (default <deploy>/source.conf)")
    return p


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zerithctl",
        description="Install and manage Zerith deployments.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    common, trust, host = _common_opts(), _trust_opts(), _host_opts()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", parents=[common, host],
                   help="show current / fallback / staging")

    deploy = sub.add_parser(
        "deploy", parents=[common, host, trust],
        help="set the update channel to REF, pull+verify it, promote")
    deploy.add_argument(
        "ref", help="signed artifact ref, e.g. ghcr.io/zerith-linux/zerith:latest")

    sub.add_parser("update", parents=[common, host, trust],
                   help="pull the configured channel and promote if it changed")
    sub.add_parser("rollback", aliases=["swap"], parents=[common, host],
                   help="swap current <-> fallback")
    sub.add_parser("gc", parents=[common, host],
                   help="remove unreferenced deployments and orphaned objects")

    _add_install_parser(sub, common, trust)
    return p


def _add_install_parser(sub, common, trust) -> None:
    inst = sub.add_parser(
        "install", parents=[common, trust],
        help="partition (optional), initialize, and install Zerith",
        description="Install Zerith from a signed OCI deployment artifact onto "
                    "a whole disk (--disk) or existing mountpoints.",
    )
    inst.add_argument("sysroot", nargs="?", type=Path,
                      help="target btrfs mountpoint (omit when using --disk)")
    inst.add_argument("efi", nargs="?", type=Path,
                      help="ESP mountpoint (omit when using --disk)")
    inst.add_argument("--disk", type=Path,
                      help="whole disk to auto-partition, e.g. /dev/nvme0n1 "
                           "(DESTROYS it)")
    inst.add_argument("--yes", action="store_true",
                      help="skip the wipe confirmation")
    inst.add_argument("--esp-size", default=config.DEFAULT_ESP_SIZE,
                      help="ESP size (default 1GiB)")
    inst.add_argument("--label", default=config.DEFAULT_LABEL,
                      help="btrfs label; MUST match the init's LABEL= "
                           "(default zerith)")
    inst.add_argument(
        "--ref", help="signed OCI deployment artifact ref to pull")
    inst.add_argument("--local", type=Path,
                      help="local CI output dir (zerith.efi, root.cfs, "
                           "deployment.json, objects/) for offline installs")
    inst.add_argument("--no-limine", action="store_true",
                      help="skip installing the Limine bootloader")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    runtime.DRY_RUN = args.dry_run
    runtime.VERBOSE = args.verbose

    if args.cmd == "install":
        return _cmd_install(args)
    return _cmd_host(args)


def _cmd_host(args: argparse.Namespace) -> int:
    """Lifecycle commands that operate on an already-installed host."""
    deploy, esp = args.deploy, args.esp
    config_path = args.config or (deploy / config.SOURCE_CONF_NAME)

    if args.cmd == "status":
        lifecycle.status(deploy, config_path)
        return 0

    runtime.require_root()
    lifecycle.check_layout(deploy, esp)

    with lifecycle.deploy_lock(deploy):
        if args.cmd == "gc":
            lifecycle.gc(deploy)
        elif args.cmd in ("rollback", "swap"):
            lifecycle.rollback(deploy, esp)
        elif args.cmd == "deploy":
            lifecycle.do_deploy(deploy, esp, args.ref, args.cosign_identity,
                                args.cosign_issuer, args.insecure_skip_verify)
            layout.write_source(config_path, args.ref)
        elif args.cmd == "update":
            ref = layout.read_source(config_path)
            if not ref:
                die(f"no update channel configured in {config_path}; "
                    f"run 'deploy REF' first")
            runtime.log(f"update: pulling configured channel {ref}")
            lifecycle.do_deploy(deploy, esp, ref, args.cosign_identity,
                                args.cosign_issuer, args.insecure_skip_verify)
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    """The initial install: partition (optional), init layout, land + promote
    the first deployment, install Limine."""
    if args.disk and (args.sysroot or args.efi):
        die("give either --disk OR sysroot+efi, not both")
    if not args.disk and not (args.sysroot and args.efi):
        die("provide --disk DEV, or both sysroot and efi mountpoints")
    if bool(args.ref) == bool(args.local):
        die("provide exactly one source: --ref REF  or  --local DIR")

    runtime.require_root()

    if args.disk:
        disk.assert_disk_free(args.disk)
        disk.confirm_wipe(args.disk, args.yes)
        esp_part, btrfs_part = disk.partition_disk(
            args.disk, args.esp_size, args.label)
        sysroot, efi, unmount = disk.mount_targets(esp_part, btrfs_part)
        limine_disk, limine_part = args.disk, esp_part
    else:
        sysroot, efi, unmount = args.sysroot, args.efi, (lambda: None)
        limine_disk, limine_part = None, None

    try:
        installer.init_layout(sysroot, efi)
        if args.ref:
            src = source_from_ref(args.ref, args.cosign_identity,
                                  args.cosign_issuer, args.insecure_skip_verify)
        else:
            src = source_from_local(args.local)
        try:
            installer.install_deploy(sysroot, efi, src)
            if not args.no_limine:
                install_limine(efi, limine_disk, limine_part, src.bootloader)
        finally:
            src.cleanup()
    finally:
        unmount()

    runtime.log("done.")
    return 0
