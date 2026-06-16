# Zerith OS

Zerith is a next-generation, immutable Linux distribution designed for robustness, security, and atomic updates. It is a **curated and opinionated experience**, providing a fully set up, modern, UEFI-only environment out of the box.

Zerith leverages modern storage technologies like **composefs** and **EROFS** to provide a strictly read-only root filesystem with efficient deduplication and cryptographic verification.

## Core Architecture

- **Immutable Core:** The root filesystem is mounted as a read-only `composefs` volume. This ensures the system remains in a known-good state and is protected against accidental or malicious modification.
- **Linear Cascade Deployment:** Unlike traditional A/B models that toggle between two slots, Zerith employs a Linear Cascade model with an N-1 fallback state. New system images are always staged to slot `a`. Before a new image is staged, the previous content of slot `a` is cascaded to slot `b`, ensuring that slot `b` always contains a reliable N-1 fallback (the previous known-good state). Updates are delivered as signed filesystem images that are applied atomically.
- **Deduplication with composefs:** By using `composefs`, multiple versions of the OS (or multiple containers) can share the same underlying data blocks in a shared object store, significantly reducing disk usage.
- **Systemd-free (Artix-based):** Zerith is built on the Artix Linux base using **dinit** as the service manager, providing a fast and lightweight init system without the complexity of systemd.
- **State Management:**
    - `/usr`: Strictly read-only, containing the OS core.
    - `/etc`: Managed via a persistent overlay (upper layer on `/var`), allowing configuration changes to survive updates while maintaining a factory-reset path.
    - `/var` & `/home`: Persistent storage on Btrfs subvolumes, providing data durability and snapshot capabilities.
    - **Factory Reset:** Seeding `/var` from `/usr/share/factory` ensures a clean state can be restored at any time.

## Key Technologies

- **Limine:** A modern, advanced, and portable bootloader used to boot the system.
- **UKIs (Unified Kernel Images):** Combined kernel, initramfs, and stub in a single EFI executable for simplified and secure booting.
- **EROFS:** An ultra-efficient read-only filesystem used for the underlying storage images.
- **Btrfs:** Used for the underlying persistent storage to provide subvolumes and snapshot support.
- **Podman:** Included by default for OCI container management, emphasizing a container-centric workflow for user applications.

## Getting Started

Zerith is currently in active development. We are working on providing official build scripts and installation media. In the meantime, you can explore the system architecture through the `Containerfile` and `init` script.

## Development

The project is currently in early development. The build process is containerized via the `Containerfile`, which generates the Unified Kernel Image (UKI) and prepares the initial filesystem structure.

### Build Requirements
- `podman` or `docker`
- `binutils` (for UKI manipulation)
- `composefs` tools

---

**Zerith OS** — *Immutable. Atomic. Simple.*
