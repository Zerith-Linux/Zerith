# Zerith

An immutable, image-based Linux distribution built on Artix, with a
composefs read-only root, role-based deployments with an N-1 fallback,
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
- **Atomic, reversible updates** — images are staged whole; switching deployments is
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

1. **Limine (UEFI)** loads the selected deployment's Unified Kernel Image from the
   EFI System Partition.
2. The **UKI (`zerith.efi`)** bundles the kernel and a minimal busybox
   initramfs; it is assembled with `ukify`.
3. The **initramfs** loads the required modules, mounts the composefs root
   read-only, layers the writable subvolumes on top, and `switch_root`s into
   the real system.
4. **dinit** takes over as PID 1 inside the immutable root.

Kernel command line: `deploy=<id>` — baked into each deployment's UKI at build
time, it names which deployment directory to mount. The boot device is located
by filesystem label (`LABEL=zerith`), so the command line carries no device path.

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
| `vda1`    | EFI System  | FAT32      | Limine + role-named UKIs (`/zerith/current.efi`, `/zerith/fallback.efi`) |
| `vda2`    | Linux       | btrfs      | composefs images, object store, writable state   |

btrfs contents on `vda2`:

```
@deploy                       # subvolume → /deploy
  <id>/root.cfs               #   per-deployment composefs index (current = N)
  <id>/zerith.efi             #   per-deployment UKI (source for the ESP copy)
  shared/objects/             #   shared content-addressed object store
  state.json                  #   deployment roles: current / fallback / staging
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
root, `@var`, `@home`, `@deploy`, and the `/etc` overlay). Everything volatile
is left to the init system.

### Deployments and the N-1 fallback

Each system image is identified by its own **deploy id** (baked into its UKI as
`deploy=<id>`) and lives in its own directory under `@deploy`. Which image plays
which part is tracked in `state.json` by **role**, not by directory name:

- **`current`** — the image booted by default (the `N` state).
- **`fallback`** — the previous known-good image, kept for recovery (the `N-1`
  state).
- **`staging`** — a freshly written image not yet promoted to `current`.

The ESP carries two fixed, role-named UKIs that Limine chainloads:
`/zerith/current.efi` and `/zerith/fallback.efi`. An update writes a new
deployment, copies its UKI into `current.efi`, and demotes the old current to
`fallback.efi`; because each UKI already has its `deploy=<id>` baked in, the ESP
file names can stay static while still mounting the right deployment.

```
   new image
       │  write + promote to "current"
       ▼
  ┌──────────────────┐   demote    ┌──────────────────┐
  │  current  (N)    │ ──────────▶ │  fallback (N-1)  │
  └──────────────────┘             └──────────────────┘
                                   (old fallback discarded)
```

If `current` fails to boot, selecting the previous Limine entry boots
`fallback` — the last known-good image. Because the object store is shared
across deployments, promotion and demotion move only the small `root.cfs` index
and any new objects, never whole filesystem copies.

> The installer currently performs the **initial install** (one deployment as
> `current`, with `state.json` seeded for the role model above). The full
> promote/demote update flow is still in progress — see **Status**.

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
7. **Deploy** — write the new image as a deploy-id directory, sync objects into
   the shared store, promote it to `current` (demoting the old current to
   `fallback`), and copy its UKI to `/zerith/current.efi` on the ESP.

---

## Status

Zerith is an in-development, experimental distribution. Expect rough edges
around tooling and update orchestration. Core mechanics — composefs root,
UKI/Limine boot, role-based deployments, writable subvolumes, and factory reset — are
the working foundation.
