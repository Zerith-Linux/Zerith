# Architecture

Zerith treats the OS as a versioned, read-only image rather than a mutable pile
of files. The whole system lives in an immutable composefs root; everything you
change at runtime is confined to a small set of writable areas. Updates are
atomic image swaps, and any deployment can roll back to the previous known-good
state.

## Design principles

- **Immutable root.** `/usr` and the rest of `/` are read-only and
  content-addressed; the running system cannot modify its own OS files.
- **Atomic, reversible updates.** Images are staged whole; switching the
  `current` role is the only "update" operation, and the previous image is
  always retained as `fallback`.
- **Clear separation of state.** OS in the image, machine config in `/etc`,
  variable data in `/var`, user data in `/home`. Nothing else persists.
- **Verified end to end.** The composefs digest is pinned in the Secure
  Boot-signed UKI and enforced at mount by fs-verity; the deployment artifact is
  cosign-signed. See [integrity.md](integrity.md).
- **Recoverable by design.** A clean factory copy of `/var` ships inside the
  image, so the machine can always be reset to defaults.

## The read-only root (composefs)

The root filesystem is a composefs image in two parts: `root.cfs`, an EROFS
metadata image describing the directory tree, and a shared, content-addressed
object store holding the actual file data, deduplicated by hash. At boot the
initramfs runs `mount.composefs`, which assembles these into one read-only root.

Because file content is content-addressed, multiple deployments share identical
files instead of duplicating them. Integrity is enforced, not optional: the root
is mounted against the digest pinned in the signed UKI, and every backing object
must carry the fs-verity digest recorded in `root.cfs` or the kernel refuses to
open it. The OS uses a `/usr`-merged layout — `/bin`, `/lib`, `/lib64`, `/sbin`
are symlinks into `/usr`.

## Disk layout

GPT with two partitions:

| Partition | Type       | Filesystem | Purpose |
|-----------|------------|------------|---------|
| 1         | EFI System | FAT32      | Limine (`/EFI/BOOT/BOOTX64.EFI`), `limine.conf`, role-named UKIs (`/zerith/current.efi`, `/zerith/fallback.efi`) |
| 2         | Linux      | btrfs      | composefs images, object store, writable state |

Limine is installed to the removable-media fallback path
(`/EFI/BOOT/BOOTX64.EFI`) so it boots in any firmware without an NVRAM entry; a
labelled `efibootmgr` entry is added best-effort but never depended on.

btrfs subvolumes on partition 2:

```
@deploy                     subvolume, mounted at /deploy
  current  ─▶ <id>          role symlinks (relative): the default-boot image (N)
  fallback ─▶ <id>          previous known-good image (N-1)
  staging  ─▶ <id>          transient, present only during an update
  <id>/root.cfs             per-deployment composefs index
  <id>/zerith.efi           per-deployment UKI (source for the ESP copy)
  <id>/BOOTX64.EFI          per-deployment signed Limine loader
  <id>/objects/             per-deployment hardlink holder (object GC refcount)
  <id>/deployment.json      self-describing metadata (id / version / digest / object refs)
  shared/objects/           shared content-addressed object store
  source.conf               update channel (the signed artifact ref `update` pulls)
@var                        subvolume, mounted at /var   (persistent)
@home                       subvolume, mounted at /home  (persistent)
```

## What is writable, and where

| Path                          | Backing              | Notes |
|-------------------------------|----------------------|-------|
| `/` and `/usr`                | composefs            | read-only, immutable |
| `/var`                        | `@var` btrfs subvol  | persistent variable state |
| `/home`                       | `@home` btrfs subvol | persistent user data |
| `/etc`                        | overlayfs            | lower `/usr/etc`, upper on `@var` (survives image updates) |
| `/tmp`, `/run`                | tmpfs                | volatile, recreated each boot |
| `/root`, `/srv`, `/usr/local` | symlinks → `/var/…`  | writable via `@var` |

The initramfs mounts only what must exist before `init` runs; everything
volatile is left to the init system. The boot stages are in [boot.md](boot.md).

## Deployments and the N-1 fallback

Each image has its own **deploy id** (baked into its UKI as `deploy=<id>`) and
its own directory under `@deploy`. Which image plays which part is tracked by
three relative symlinks — `current`, `fallback`, `staging` — by role, not by
directory name. A role change is a single atomic `rename()`, and each deployment
is self-describing via its own `deployment.json`, so there is no central index
to keep in sync.

The ESP carries two fixed, role-named UKIs Limine chainloads:
`/zerith/current.efi` and `/zerith/fallback.efi`. An update writes a new
deployment, copies its UKI into `current.efi`, and demotes the old current to
`fallback.efi`. Because each UKI already has its `deploy=<id>` baked in, the ESP
file names stay static while still mounting the right deployment.

```
   new image
       │  write + promote to "current"
       ▼
  ┌───────────────┐   demote    ┌────────────────┐
  │  current (N)  │ ──────────▶ │ fallback (N-1) │
  └───────────────┘             └────────────────┘
                                (old fallback discarded)
```

Because the object store is shared across deployments, promotion and demotion
move only the small `root.cfs` index and any new objects, never whole
filesystem copies. The lifecycle is detailed in [deployment.md](deployment.md).

## Writable state and factory reset

A clean, package-populated `/var` skeleton is captured at build time and stored
read-only inside the image at `/usr/share/factory/var`. On first boot — or any
boot where `@var` is empty — the initramfs seeds `@var` from it. A factory reset
is just discarding the writable state:

```sh
btrfs subvolume delete /mnt/@var
btrfs subvolume create /mnt/@var
# next boot re-seeds @var from /usr/share/factory/var
```

Because the factory copy lives in the immutable image, it cannot be corrupted by
the running system and always matches the deployed OS.
