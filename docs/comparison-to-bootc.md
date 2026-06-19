# Comparison to bootc

Both Zerith and [bootc](https://github.com/containers/bootc) are image-based,
immutable Linux distributions that deliver the OS as OCI artifacts and use
composefs for the read-only root. The core idea is the same; the tradeoffs
differ at every layer of the stack.

## Bootloader

| | Zerith | bootc |
|---|---|---|
| Loader | **Limine** — pinned, sha256-verified in CI, Secure Boot-signed alongside the UKI | **GRUB+shim** (default) or **systemd-boot** (composefs backend, sealed-image mode) |
| Sourcing | Fetched from upstream release as part of the CI pipeline; ships only in the signed deployment artifact | Installed from the OS image's package manager; updated as part of the image |
| Attack surface | Minimal two-stage bootloader (~50 KB signed binary, flat config with no scripting) | GRUB is a full boot environment with scripting, modules, filesystem drivers, network stack |
| Shim chain | None — your signing key is enrolled directly in UEFI | Microsoft-signed shim validates GRUB, which validates the UKI or kernel; works on locked-down firmware without key enrollment |

Zerith skips shim and GRUB entirely: Limine is small enough that the whole
loader can be pinned, hash-verified, and signed in one CI step. bootc's shim
chain adds indirection but boots on hardware where you cannot enroll a custom
UEFI key.

## Init system

| | Zerith | bootc |
|---|---|---|
| PID 1 | **dinit** (~15 KLOC C++) | **systemd** (~1.4 MLOC) |
| Initramfs | Custom `init` (busybox sh + applets) | systemd in the initramfs |
| Service supervision | Per-service `run` files, single-threaded, no D-Bus | Full service manager, journal, udev, logind, tmpfiles, socket activation |
| Update mechanism | Standalone `zerithctl` binary | systemd-sysupdate (part of systemd) |

systemd is the practical choice for broad hardware support (udev, logind,
initramfs features). dinit is smaller and simpler but requires manual
configuration for hardware bringup that systemd handles out of the box.

## Root filesystem integrity

Both use composefs for the read-only root, but the enforcement model differs:

| | Zerith | bootc (composefs + sealed) |
|---|---|---|
| composefs usage | Always required — boot fails if the digest doesn't match | Optional (`enabled = true` / `verity` / `false`) |
| Digest anchor | Embedded in the UKI's signed kernel cmdline (`composefs.digest=`) | Same in sealed-image mode |
| Per-object fs-verity | **Every backing object** is fs-verity-sealed; the kernel refuses to open a file whose backing data doesn't match its recorded digest | Applied to the composefs image file itself (the `.img`); per-object fs-verity via `trusted.overlay.metacopy` xattrs when `enabled = verity` |
| Kernel enforcement | At file-open time (the kernel checks the fs-verity Merkle tree on read) | On composefs mount (the digest must match), or per-file via xattrs in verity mode |

In practice both produce a signed chain from UEFI → bootloader → UKI →
composefs digest → file content. Zerith requires per-object fs-verity
unconditionally; bootc makes composefs and verity configurable.

## Update transport and storage

| | Zerith | bootc |
|---|---|---|
| Object format | Pack blob + offset index (two OCI blobs) | OCI layers unpacked into an ostree content-addressed repo |
| Delta mechanism | **HTTP Range fetches** — only the byte ranges covering changed objects are downloaded, coalesced into a handful of requests | Whole OCI layer download. `rpm-ostree build-chunked-oci` can split the image into smaller layers so a single-file change touches only one smaller layer |
| Granularity | Single byte range (one changed file → its exact bytes in the pack) | Compressed OCI layer tarball (a changed file redownloads the entire layer it belongs to) |
| Fresh install | Range coalesces into a single whole-pack Range request | All OCI layers are pulled |
| Registry deduplication | Byte-identical pack → same blob digest → automatic | Layer-level (depends on compressed layer content) |
| Bootloader updates | Part of the same deployment artifact, updated atomically with the OS | Managed separately via `bootupctl update` / systemd-boot |

Zerith's pack-blob model means the bandwidth cost of an update is proportional
to the number of changed bytes, not the size of the layer that contains them.
On a rolling-release distro where package updates touch small files spread
across the filesystem, this difference matters.

## Object deduplication and GC

| | Zerith | bootc |
|---|---|---|
| Store | `shared/objects/<ab>/<cdef...>` — flat content-addressed by fs-verity sha256 | `/sysroot/ostree/repo` — ostree content-addressed store (SHA-256, similar to git) |
| Per-deployment refcounting | Hardlink holder dir (`<id>/objects/`). GC sweeps objects with link count 1 (no remaining holders) | ostree built-in refcounting. GC via `ostree prune` |
| Shared objects | Multiple deployments hardlink the same store entry | Multiple deployments hardlink the same ostree checkout objects |

Functionally equivalent. Both use hardlinks so removing a deployment frees
only the objects unique to it.

## Supply chain and signing

| | Zerith | bootc |
|---|---|---|
| Artifact signing | **cosign** (sigstore, keyless OIDC) — the deployment.json + UKI + Limine + root.cfs is verified before landing | cosign/sigstore at the registry level, inherited from the container runtime (podman), not built into bootc itself |
| Object signing | None needed — integrity anchored by the signed UKI digest and per-file fs-verity | Same — ostree content-addressing + composefs digest replaces per-object signatures |
| Signing key | Your own UEFI Secure Boot key enrolled in firmware | Microsoft-signed shim + your MOK or distro key |

Same trust model — just different key enrollment paths.

---

## Summary

Zerith makes different tradeoffs in the boot chain (no shim, no GRUB, pinned
Limine), the init system (dinit instead of systemd), and update transport
(byte-level HTTP Range instead of OCI layers). bootc builds on established
infrastructure (shim, GRUB, systemd, ostree) with correspondingly broader
hardware support and operational maturity.

The table below shows where each choice lands:

| Concern | Zerith | bootc |
|---|---|---|
| Bootloader size/complexity | Lower | Higher (GRUB) |
| Shim requirement | No (enroll your own key) | Yes (works on locked firmware) |
| Update bandwidth | Proportional to changed bytes | Proportional to changed layers |
| Init surface | Smaller (dinit) | Larger (systemd) |
| composefs enforcement | Always, per-object fs-verity | Configurable, variable depth |
| Hardware support | Rolling your own | Broad, distribution-tested |
| Operational maturity | Experimental | Production (Fedora IoT/CoreOS) |

If you want a closer look, the boot chain is documented in [boot.md](boot.md),
integrity in [integrity.md](integrity.md), and the object store in
[objects.md](objects.md).
