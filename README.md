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

It is built from OCI container images, rendered to composefs and packed into a
**signed** Unified Kernel Image (UKI) in CI, then delivered as OCI artifacts —
gated by `cosign` and fs-verity — and booted by a Secure Boot-signed Limine
under UEFI.

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
- **Verified end to end** — the composefs digest is pinned in the Secure
  Boot-signed UKI and enforced at mount by fs-verity; the bootable deployment
  artifact is `cosign`-signed, and every object is verified against the signed
  composefs digest before it is trusted.
- **Recoverable by design** — a clean factory copy of `/var` ships inside the
  image, so the machine can always be reset to defaults.

---

## Architecture

### Boot chain

```
UEFI firmware → Limine (ESP) → zerith.efi (UKI) → initramfs → composefs root → switch_root → dinit
```

1. **Limine (UEFI)** loads the selected deployment's Unified Kernel Image from
   the EFI System Partition. Limine itself is Secure Boot-signed with the same
   key as the UKI, so the firmware validates it before it runs.
2. The **UKI (`zerith.efi`)** bundles the kernel and a minimal busybox
   initramfs; it is assembled with `ukify` and signed for Secure Boot.
3. The **initramfs** loads the required modules, mounts the composefs root
   read-only — verifying it against the digest pinned in the UKI's signed
   command line, with per-object integrity enforced by fs-verity — layers the
   writable subvolumes on top, and `switch_root`s into the real system.
4. **dinit** takes over as PID 1 inside the immutable root.

Kernel command line: `deploy=<id> composefs.digest=<digest>` — baked into each
deployment's signed UKI at build time. `deploy=` names which deployment
directory to mount; `composefs.digest=` pins the exact root image the initramfs
is allowed to mount, so a tampered or swapped `root.cfs` is refused at boot. The
boot device is located by filesystem label (`LABEL=zerith`), so the command
line carries no device path.

### The read-only root (composefs)

The root filesystem is a **composefs** image, made of two parts:

- `root.cfs` — an EROFS metadata image describing the entire directory tree.
- a shared, content-addressed **object store** holding the actual file data,
  deduplicated by hash.

At boot the initramfs runs `mount.composefs`, which assembles these into a
single read-only root. Because file content is content-addressed, multiple
deployments share identical files instead of duplicating them. Integrity is
enforced rather than optional: the root is mounted against the digest pinned in
the signed UKI, and every backing object must carry the fs-verity digest
recorded in `root.cfs` or the kernel refuses to open it.

The OS uses a `/usr`-merged layout: `/bin`, `/lib`, `/lib64`, and `/sbin` are
symlinks into `/usr`, and everything the OS ships lives under `/usr`.

### Disk layout

Disk (`/dev/sdx`), GPT:

| Partition | Type        | Filesystem | Purpose                                          |
|-----------|-------------|------------|--------------------------------------------------|
| `sdx1`    | EFI System  | FAT32      | Limine (`/EFI/BOOT/BOOTX64.EFI`), `limine.conf`, and role-named UKIs (`/zerith/current.efi`, `/zerith/fallback.efi`) |
| `sdx2`    | Linux       | btrfs      | composefs images, object store, writable state   |

Limine is installed to the removable-media fallback path
(`/EFI/BOOT/BOOTX64.EFI`) so it boots in any firmware without relying on an
NVRAM entry — it survives firmware resets and board swaps, and boots unmodified
in a VM. A labelled `efibootmgr` entry pointing at the same file is added when
possible, as a best-effort bonus, but the system does not depend on it.

btrfs contents on `sdx2`:

```
@deploy                       # subvolume → /deploy
  current  ─▶ <id>            #   role symlinks (relative): the N (default-boot) image
  fallback ─▶ <id>            #   previous known-good image (N-1)
  staging  ─▶ <id>            #   transient, present only during an update
  <id>/root.cfs               #   per-deployment composefs index
  <id>/zerith.efi             #   per-deployment UKI (source for the ESP copy)
  <id>/BOOTX64.EFI            #   per-deployment signed Limine loader (source for the ESP copy)
  <id>/objects/               #   per-deployment hardlink holder (object GC refcount)
  <id>/deployment.json        #   self-describing metadata (id / version / digest / objects ref / shard digests)
  shared/objects/             #   shared content-addressed object store
  source.conf                 #   update channel (the signed artifact ref `update` pulls)
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

Zerith images are produced entirely by CI and delivered as OCI artifacts — the
target never renders an image or pulls a container, it only verifies and lands
prebuilt pieces.

One subtlety drives the whole shape of this: the composefs image's digest must
be known *before* the UKI is signed, because that digest is baked into the
UKI's signed kernel command line. So the composefs is rendered in CI, its
digest is pinned into the UKI, and the UKI is signed — all before anything is
published. (This is why the UKI is no longer built inside the `Containerfile`:
the digest doesn't exist until the final rootfs has been rendered.)

**In CI (`build.yml`):**

1. **Compose the OS** as an OCI image (Artix `base-dinit` + packages) via the
   `Containerfile`. The build also stashes the kernel and initramfs in the
   image for the render step.
2. **Post-process** — capture a clean `/var` skeleton to
   `/usr/share/factory/var`, relocate `/etc` to `/usr/etc` (the lower layer for
   the runtime overlay), and blank the mutable dirs back to empty mountpoints.
3. **Render composefs** — `mkcomposefs` produces `root.cfs` and the
   content-addressed object store, and the image's fs-verity digest is computed
   offline. (This runs as real root so file ownership and modes are recorded
   faithfully.)
4. **Build + sign the UKI and Limine** — `ukify` bundles the kernel + initramfs
   with `deploy=<id> composefs.digest=<digest>` on the command line, then
   `sbsign` signs both the UKI and the Limine loader for Secure Boot with the
   same key. The pinned digest is now part of the signed payload.
5. **Push via `oras`** — two artifacts go to the registry:
   - a **deployment artifact** (the signed UKI, the signed Limine loader,
     `root.cfs`, and `deployment.json`), unique per build;
   - an **objects artifact** — the content-addressed object store packed into
     deterministic tarballs, one per hash-prefix bucket. Identical buckets
     produce byte-identical blobs, so the registry deduplicates unchanged ones
     and a build re-uploads only the buckets that changed. `deployment.json`
     records each shard's digest so the host can fetch just the buckets that
     differ from what it already has.
6. **Sign with `cosign`** — the deployment artifact is signed (keyless, via the
   CI OIDC identity) so the host can verify provenance before trusting it. The
   objects don't need their own signature: their integrity is already anchored
   by the signed `root.cfs` digest and per-object fs-verity, so a tampered or
   swapped object can't satisfy the mount.

**On the host (`install` / `zerith-ctl`):**

7. **Pull + verify** — fetch the deployment artifact with `oras`, verify its
   `cosign` signature, then fetch object shards — skipping any whose digest
   matches the deployment already installed, so only the changed buckets come
   down the wire.
8. **Land + seal** — verify each object's fs-verity digest against its store
   path and enable fs-verity on it; place `root.cfs`, enable fs-verity, and
   confirm its measured digest matches the value signed into the UKI.
9. **Promote** — install the UKI and the signed Limine loader to the ESP, flip
   `current` (demoting the old current to `fallback`), and (on first install)
   seed `source.conf` with the update channel.

The split is now clean: **everything that produces or signs an image happens in
CI**, and the host does only verification and atomic placement. `install`
performs steps 7–9 for the first deployment; `zerith-ctl` does them for every
one after.

### Integrity chain

Each link is checked by the one before it:

```
Secure Boot → signed Limine → signed UKI → composefs.digest= (signed cmdline)
   → mount.composefs digest= (root image) → verity (every backing object)
```

and, for the update path, `cosign` over the deployment artifact gates what is
ever allowed to land on disk, while each object is verified against the signed
composefs digest as it's placed. Nothing is rendered on the target, so the
digest that boots is the same digest that was signed in CI.

---

## Host tooling

`zerith-ctl` operates on a running host (the `@deploy` subvolume at `/deploy`
and the ESP at `/efi`). Mutating commands take an exclusive lock on `@deploy`,
so a scheduled `update` and a hand-run `deploy` can't race each other.

| Command            | What it does                                                        |
|--------------------|---------------------------------------------------------------------|
| `status`           | show the update channel and the current / fallback / staging roles  |
| `deploy REF`       | set the update channel to `REF`, pull + verify it, and promote to current |
| `update`           | pull the configured channel, verify it, and promote it if it changed |
| `rollback` (`swap`)| swap current ⇄ fallback                                             |
| `gc`               | drop unreferenced deployments and sweep orphaned objects            |

An update fetches only the object shards whose digest differs from the current
deployment, so a small change pulls only the buckets it touched. Objects are
deduplicated across deployments in `shared/objects`, and each deployment keeps a
private hardlink "holder" of just the objects it references. GC reclaims an
object once its link count shows no holder still needs it, so removing an old
deployment frees only the files unique to it.

---

## Status

Zerith is an in-development, experimental distribution. Expect rough edges
around tooling and packaging. The core mechanics — composefs root, digest-pinned
UKI/Limine boot, signed `oras` / `cosign` delivery, role-based deployments with
an N-1 fallback, the `install` → `zerith-ctl` update lifecycle, writable
subvolumes, and factory reset — are the working foundation. It is not yet a
daily driver; treat it as a system to learn from and build on.
