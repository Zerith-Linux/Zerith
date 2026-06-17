# Zerith

An immutable, image-based Linux distribution built on Artix, with a
composefs read-only root, role-based deployments with an N-1 fallback,
and a true factory-reset model.

**No Bootc here ;P**

But Zerith is definitely inspired from bootc

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

## Why Zerith?

Zerith exists to answer one question: **what does a modern, image-based,
atomically-updated Linux look like without systemd?**

The immutable-OS idea — a read-only, versioned root you swap whole and roll
back on failure — has been well proven by projects like Fedora Silverblue,
bootc, and the Universal Blue family. But nearly all of them are built *on*
systemd: systemd as PID 1, plus its surrounding stack (systemd-boot, journald,
logind, systemd-sysupdate, ostree wired into systemd units). If you'd rather
not run systemd, the immutable-OS design space is almost empty. Zerith is an
experiment to fill that gap.

- **No systemd in the running OS.** Init is **dinit**, on an Artix base — no
  PID 1 systemd, no journald, no logind. (The one place systemd code appears is
  the *build*: the UKI is assembled with `ukify` and systemd's EFI stub. Those
  are build-time tools and never ship in the image you boot.)
- **Keeps the good parts of immutable OSes.** Read-only composefs root, atomic
  whole-image updates, an N-1 fallback to roll back to, content-addressed
  dedup between deployments, and a true factory reset.
- **Small and legible.** The whole system is a `Containerfile` plus three short
  scripts — `init`, `install`, and `zerith-ctl`. You can read the entire boot
  and update path in an afternoon; there's no large init system or update
  daemon to reverse-engineer.
- **Built like a container, not assembled on the box.** The OS is an OCI image
  you can rebuild, inspect, and diff. Updates are image swaps, not in-place
  package upgrades.

It's also, frankly, a **learn-by-building project** — a way to understand how
immutable boot, composefs, and UKIs actually fit together by wiring them up
from scratch instead of inheriting a turnkey stack. If you want a battle-tested
daily driver today, the systemd-based options above are far more mature. If you
want a systemd-free take on the same ideas — or just to see how little code the
core really needs — that's the point of Zerith.

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

Disk (`/dev/sdx`), GPT:

| Partition | Type        | Filesystem | Purpose                                          |
|-----------|-------------|------------|--------------------------------------------------|
| `sdx1`    | EFI System  | FAT32      | Limine + role-named UKIs (`/zerith/current.efi`, `/zerith/fallback.efi`) |
| `sdx2`    | Linux       | btrfs      | composefs images, object store, writable state   |

btrfs contents on `sdx2`:

```
@deploy                       # subvolume → /deploy
  current  ─▶ <id>            #   role symlinks (relative): the N (default-boot) image
  fallback ─▶ <id>            #   previous known-good image (N-1)
  staging  ─▶ <id>            #   transient, present only during an update
  <id>/root.cfs               #   per-deployment composefs index
  <id>/zerith.efi             #   per-deployment UKI (source for the ESP copy)
  <id>/objects/               #   per-deployment hardlink holder (object GC refcount)
  <id>/deployment.json        #   self-describing metadata (id / version / image)
  shared/objects/             #   shared content-addressed object store
  source.conf                 #   update channel (the image `update` pulls)
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
which part is tracked by three **relative symlinks** inside `@deploy` —
`current`, `fallback`, and `staging` — by **role**, not by directory name. A
role change is a single atomic `rename()`, and each deployment is fully
self-describing via its own `deployment.json`, so there's no central index file
to keep in sync:

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

> The `install` script performs the **initial install** (one deployment as
> `current`, seeding `source.conf` with the update channel). From then on,
> `zerith-ctl` drives the lifecycle on the running host — `deploy`, `update`,
> `rollback`/`swap`, and `gc` — handling staging, promote/demote, and object
> reclamation. See **Host tooling** and **Status**.

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

Steps 1–4 and 6 are the **image build** (the `Containerfile`, run in CI). Steps
5 and 7 happen on the host at **install/update time** — `install` does them for
the first deployment, and `zerith-ctl` for every one after.

---

## Host tooling

`zerith-ctl` operates on a running host (the `@deploy` subvolume at `/deploy`
and the ESP at `/efi`). Mutating commands take an exclusive lock on `@deploy`,
so a scheduled `update` and a hand-run `deploy` can't race each other.

| Command            | What it does                                                        |
|--------------------|---------------------------------------------------------------------|
| `status`           | show the update channel and the current / fallback / staging roles  |
| `deploy IMAGE`     | set the update channel to `IMAGE`, pull it, and promote to current  |
| `update`           | pull the configured channel image and promote it if it changed      |
| `rollback` (`swap`)| swap current ⇄ fallback                                             |
| `gc`               | drop unreferenced deployments and sweep orphaned objects            |

Objects are deduplicated across deployments in `shared/objects`, and each
deployment keeps a private hardlink "holder" of just the objects it references.
GC reclaims an object once its link count shows no holder still needs it, so
removing an old deployment frees only the files unique to it.

---

## Status

Zerith is an in-development, experimental distribution. Expect rough edges
around tooling and packaging. The core mechanics — composefs root, UKI/Limine
boot, role-based deployments with an N-1 fallback, the `install` → `zerith-ctl`
update lifecycle, writable subvolumes, and factory reset — are the working
foundation. It is not yet a daily driver; treat it as a system to learn from
and build on.
