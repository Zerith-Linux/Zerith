# Deployment

Two phases: a one-time **install** that lays Zerith onto a disk, and the ongoing
**lifecycle** (`deploy` / `update` / `rollback` / `gc`) that `zerithctl` runs on
the booted host. Both share the same core — landing objects, verifying
`root.cfs`, writing metadata — so the only real difference is partitioning and
role bookkeeping. Command reference is in [host-tooling.md](host-tooling.md).

## Installing

The `install` script is a thin bash bootstrap, curl-pipe-able and run under
sudo. It does only environment setup and target selection, then hands the real
work to `zerithctl install`:

```sh
curl -fsSL https://raw.githubusercontent.com/Zerith-Linux/Zerith/main/install | sudo bash
```

It (1) re-execs under sudo if needed, (2) ensures the tools the installer needs
are on `PATH` (installing them with pacman where available), (3) locates
`zerithctl` — using the checkout it lives in, or fetching one — (4) picks a
target disk interactively when none is given (reading the choice from the
terminal even under `curl | bash`), and (5) execs `zerithctl install`.

Two targeting modes:

- **Whole disk** — `zerithctl install --disk /dev/nvme0n1` partitions (GPT: ESP
  + btrfs), formats, and mounts before installing. Destroys the disk; guarded by
  a confirmation unless `--yes`.
- **Existing partitions** — `zerithctl install <sysroot> <efi>` installs onto
  already-mounted targets, doing no partitioning.

Source of the first deployment is either `--ref <oci-ref>` (pull + cosign-verify
a signed artifact) or `--local <dir>` (an offline CI output directory).

What the install does, in order: create the `@var` / `@home` / `@deploy`
subvolumes and the store/ESP directories; materialize the first deployment (land
objects, place and verify `root.cfs`, link its holder, copy the UKI); copy the
UKI to the ESP as `current.efi`; set the `current` role; seed `source.conf` with
the update channel; write the static `limine.conf`; and install the Limine
loader.

## The lifecycle

On a running host, `zerithctl` operates on `@deploy` (`/deploy`) and the ESP
(`/efi`). Mutating commands take an exclusive lock on `@deploy`, so a scheduled
`update` and a hand-run `deploy` cannot race.

**Stage** (`materialize` + mark `staging`). Pull and verify the artifact, then
build the deployment on disk: land only the objects this image needs
([objects.md](objects.md)), copy and seal `root.cfs` and confirm its measured
digest matches the value signed into the UKI, hardlink the GC holder, copy the
UKI and signed loader, and write `deployment.json`. Staging an image already on
disk is a no-op reuse.

**Promote.** Make the staged image `current` and demote the old current to
`fallback`. The ESP is updated first, in crash-safe order — a valid fallback is
in place before `current.efi` swings to the new image — because boot reads the
ESP, not the role symlinks. The signed Limine loader on the ESP is refreshed too
so a bootloader/key update propagates. Then the role symlinks flip and `gc`
runs.

**Rollback** (`rollback` / `swap`). Swap `current` ⇄ `fallback` on both the ESP
UKIs and the role symlinks — the recovery path when a new image misbehaves.

**GC.** Remove any deployment no role points at, then sweep objects whose
hardlink count shows no remaining holder. Promotion runs this automatically; you
can also run it on demand.

## Updates over the wire

`update` pulls the channel recorded in `source.conf`; if its deploy id already
matches `current`, it is a no-op. Otherwise it stages and promotes. It transfers
only the byte ranges of the pack covering objects the host lacks, so a small OS
change pulls only the bytes it touched.
