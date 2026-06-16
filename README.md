# Zerith

An immutable, image-based Linux distribution built on Artix, with a
composefs read-only root, Linear Cascade Deployment with N-1 fallback,
and a true factory-reset model.

---

## What is Zerith?

Zerith is a curated, opinionated experience that treats the operating system as
a **versioned, read-only image** rather than a mutable pile of files. The
entire OS lives in an immutable composefs root; everything you change at
runtime is confined to a small, well-defined set of writable areas. Updates are
**atomic image swaps**, not in-place package upgrades, and any deployment can
be rolled back to the previous known-good state.

It is built from OCI container images, converted to composefs, packed into a
Unified Kernel Image (UKI), and booted by Limine under UEFI.

## Design principles

- **Immutable root** — `/usr` and the rest of `/` are read-only and
  content-addressed. The running system cannot modify its own OS files.
- **Atomic, reversible updates** — images are staged whole; switching slots is
  the only "update" operation, and the previous image is always retained.
- **Clear separation of state** — OS in the image, machine config in `/etc`,
  variable data in `/var`, user data in `/home`. Nothing else persists.
- **Reproducible builds** — the OS is produced by a container build pipeline,
  so an image is a deterministic artifact you can rebuild and inspect.
- **Recoverable by design** — a clean factory copy of `/var` ships inside the
  image, so the machine can always be reset to defaults.

---

## Architecture

### Boot chain

```
UEFI firmware → Limine (ESP) → zerith.efi (UKI) → initramfs → composefs root → switch_root → dinit
```

1. **Limine (UEFI)** loads the selected slot's Unified Kernel Image from the
   EFI System Partition.
2. The **UKI (`zerith.efi`)** bundles the kernel and a minimal busybox
   initramfs; it is assembled with `ukify`.
3. The **initramfs** loads the required modules, mounts the composefs root
   read-only, layers the writable subvolumes on top, and `switch_root`s into
   the real system.
4. **dinit** takes over as PID 1 inside the immutable root.

Kernel command line: `slot=a boot=/dev/vda2` — `slot` selects the deployment,
`boot` names the device holding the composefs images and writable state.

### The read-only root (composefs)

The root filesystem is a **composefs** image, made of two parts:

- `root.cfs` — an EROFS metadata image describing the entire directory tree.
- a shared, content-addressed **object store** holding the actual file data,
  deduplicated by hash.

At boot the initramfs runs `mount.composefs`, which assembles these into a
single read-only root. Because file content is content-addressed, multiple
deployments share identical files instead of duplicating them, and the image
can optionally be integrity-verified with fs-verity.

The OS uses a `/usr`-merged layout: `/bin`, `/lib`, `/lib64`, and `/sbin` are
symlinks into `/usr`, and everything the OS ships lives under `/usr`.

### Disk layout

Disk (`/dev/vda`), GPT:

| Partition | Type        | Filesystem | Purpose                                          |
|-----------|-------------|------------|--------------------------------------------------|
| `vda1`    | EFI System  | FAT32      | Limine + per-slot UKIs (`/deploy/<slot>/zerith.efi`) |
| `vda2`    | Linux       | btrfs      | composefs images, object store, writable state   |

btrfs contents on `vda2`:

```
/deploy/a/root.cfs            # slot A image index (N)
/deploy/b/root.cfs            # slot B image index (N-1 fallback)
/deploy/shared/objects/       # shared content-addressed object store
@var                          # subvolume → /var   (persistent)
@home                         # subvolume → /home  (persistent)
```

### What is writable, and where it lives

| Path                          | Backing               | Notes                                            |
|-------------------------------|-----------------------|--------------------------------------------------|
| `/` and `/usr`                | composefs             | read-only, immutable                             |
| `/var`                        | `@var` btrfs subvol   | persistent variable state                        |
| `/home`                       | `@home` btrfs subvol  | persistent user data                             |
| `/etc`                        | overlayfs             | lower = `/usr/etc`, upper on `@var` (survives image cascades) |
| `/tmp`, `/run`                | tmpfs                 | volatile, recreated each boot                    |
| `/root`, `/srv`, `/usr/local` | symlinks → `/var/...` | writable via `@var`                              |

The initramfs mounts only what must exist before `init` runs (the composefs
root, `@var`, `@home`, and the `/etc` overlay). Everything volatile is left to
the init system.

### Linear Cascade Deployment

Zerith uses a **Linear Cascade** model with an **N-1 fallback** state:

- New system images are always staged to slot **`a`**.
- Before a new image is staged, the current contents of slot `a` are
  **cascaded down to slot `b`**.
- Slot `b` therefore always holds a reliable **N-1 fallback** of the previous
  known-good state.

```
   new image
       │  stage
       ▼
  ┌───────────┐   cascade   ┌───────────┐
  │  slot a   │ ──────────▶ │  slot b   │
  │   (N)     │             │  (N-1)    │
  └───────────┘             └───────────┘
                            (old N-1 discarded)
```

If slot `a` fails to boot or proves bad, the system falls back to slot `b`,
the last known-good image. Because the object store is shared between slots,
cascading and staging move only the small `root.cfs` index and any new
objects — never whole filesystem copies.

### Writable state and factory reset

A clean, package-populated `/var` skeleton is captured at build time and stored
read-only inside the image at `/usr/share/factory/var`.

- On first boot — or any boot where `@var` is empty — the initramfs seeds
  `@var` from this factory copy.
- A **factory reset** is just discarding the writable state:

  ```sh
  btrfs subvolume delete /mnt/@var
  btrfs subvolume create /mnt/@var
  # next boot re-seeds @var from /usr/share/factory/var
  ```

Because the factory copy lives in the immutable image, it cannot be corrupted
by the running system and always matches the deployed OS.

---

## Build pipeline

Zerith images are produced from container images, not assembled on the target:

1. **Compose the OS** as an OCI image (Artix `base-dinit` + packages).
2. **Capture factory `/var`** — clean regenerable caches/logs, then copy the
   skeleton to `/usr/share/factory/var`.
3. **Move `/etc` to `/usr/etc`** — this becomes the lower layer for the runtime
   `/etc` overlay.
4. **Post-process** — strip mutable/volatile content (`/var`, `/etc`, `/home`,
   …) while keeping empty mountpoints.
5. **Convert to composefs** — `mkcomposefs` produces `root.cfs` and populates
   the shared object store.
6. **Build the UKI** — `ukify` bundles the kernel + initramfs into
   `zerith.efi`.
7. **Deploy** — cascade slot `a` → slot `b`, stage the new image to slot `a`,
   sync objects, and place the UKI on the ESP.

---

## Status

Zerith is an in-development, experimental distribution. Expect rough edges
around tooling and update orchestration. Core mechanics — composefs root,
UKI/Limine boot, cascade slots, writable subvolumes, and factory reset — are
the working foundation.
