# Boot

## The chain

```
UEFI firmware → Limine (ESP) → zerith.efi (UKI) → initramfs → composefs root → switch_root → dinit
```

1. **Limine (UEFI)** loads the selected deployment's UKI from the ESP. Limine is
   pinned to **v12.3.3** (the official prebuilt `BOOTX64.EFI` from the binary
   release, fetched and sha256-verified in CI, then Secure Boot-signed with the
   same key as the UKI). It is never installed as a system package and ships only
   in the signed deployment artifact, so the firmware validates it before it runs.
   Limine chainloads the
   UKI via `protocol: efi_chainload`; for EFI chainloading the firmware's own
   Secure Boot verification gates the UKI, so Limine needs no enrolled config
   checksum of its own — the trust comes from the firmware validating each
   signed image in turn (see [integrity.md](integrity.md)).
2. The **UKI (`zerith.efi`)** bundles the kernel and a minimal busybox
   initramfs, assembled with `ukify` and signed for Secure Boot.
3. The **initramfs** loads modules, mounts the composefs root read-only —
   verifying it against the digest pinned in the UKI's signed command line, with
   per-object integrity enforced by fs-verity — layers the writable subvolumes
   on top, and `switch_root`s into the real system.
4. **dinit** takes over as PID 1 inside the immutable root.

## Kernel command line

```
deploy=<id> composefs.digest=<digest>
```

Baked into each deployment's signed UKI at build time. `deploy=` names which
deployment directory to mount; `composefs.digest=` pins the exact root image the
initramfs may mount, so a tampered or swapped `root.cfs` is refused at boot. The
boot device is found by filesystem label (`LABEL=zerith`), so the command line
carries no device path. This pinning is the anchor of the trust chain in
[integrity.md](integrity.md).

## What `init` does

The initramfs `init` is POSIX `sh` (busybox) and is organized as a sequence of
named stages; any failure drops to a rescue shell rather than panicking.

1. **`mount_api_filesystems`** — `/proc`, `/sys`, `/dev`, and wire up the
   console.
2. **`load_drivers`** — `modprobe` the storage and filesystem modules, then
   confirm the ones boot cannot proceed without (`erofs`, `overlay`, loop). The
   VMD module loads before NVMe so an SSD behind Intel VMD/RST is found.
3. **`parse_cmdline`** — read `deploy=` and `composefs.digest=` from
   `/proc/cmdline`; refuse to continue if either is missing.
4. **`mount_boot_device`** — resolve `LABEL=zerith`, mount the btrfs top level,
   and locate the deployment at `@deploy/<id>`.
5. **`mount_composefs_root`** — prime a loop device (no udev here), then
   `mount.composefs` the root with `digest=<pinned>` and `verity`. Mount the ESP
   read-only under the new root.
6. **`mount_writable_state`** — mount `@var`, `@home`, `@deploy`; seed `/var`
   from `/usr/share/factory/var` on first boot; assemble the `/etc` overlay
   (lower `/usr/etc`, upper on `@var`).
7. **`handoff`** — bind `/dev`, mount `/run` tmpfs, and `switch_root` into
   `/sbin/init`.

Only what must exist before `init` runs is mounted here; volatile mounts are
left to dinit. The writable-state model is described in
[architecture.md](architecture.md).
