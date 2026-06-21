# What makes Zerith strong

Zerith is an image-based, immutable Linux OS that delivers the whole system as
signed OCI artifacts and boots from a composefs read-only root. Below are the
design choices that make it distinctive — and the technical reasons each one
matters.

## Systemd-free by design

Zerith answers a question most immutable OSes don't: **what does a modern,
atomically-updated, image-based Linux look like without systemd?**

- **PID 1 is dinit** (~15 KLOC C++) on an Artix base — a small, single-threaded
  supervisor with per-service `run` files and no D-Bus dependency.
- **Custom initramfs `init`** built from busybox `sh` and applets, so the early
  boot path is short enough to read top to bottom.
- The only place systemd code appears is at *build* time (`ukify` and the EFI
  stub) — build tools that never ship in the image you actually boot.

If you want the immutable-OS benefits without inheriting the full systemd
surface, that's the whole point of Zerith.

## A minimal, fully-signed boot chain

Zerith skips both shim and GRUB. The loader is **Limine**, pinned and
sha256-verified in CI and Secure Boot-signed alongside the UKI:

- A roughly **50 KB signed loader** with a flat config and no scripting — a tiny
  attack surface compared with a full boot environment that ships scripting,
  modules, filesystem drivers, and a network stack.
- Your **own Secure Boot key** is enrolled directly in UEFI; there's no extra
  shim layer of indirection.
- The bootloader is part of the **same signed deployment artifact** as the OS,
  so it updates atomically with everything else — no separate boot-update step.

The result is an end-to-end verified path: **UEFI → Limine → UKI → composefs
digest → file content**.

## Always-on, per-object integrity

Integrity in Zerith isn't a configurable knob you have to remember to turn on —
it's unconditional:

- The composefs root is **always required**: boot fails outright if the digest
  doesn't match the value embedded in the UKI's signed kernel cmdline.
- **Every backing object is fs-verity-sealed.** The kernel checks the fs-verity
  Merkle tree at file-open time and refuses to open any file whose backing data
  doesn't match its recorded digest.

So tampering isn't just detected at mount — it's caught the moment a modified
file is read, on every file, every time.

## Updates that cost only what changed

Zerith stores objects as a **single slab blob plus an offset index** (two OCI blobs)
and updates over **HTTP Range fetches**:

- An update downloads **only the byte ranges covering changed objects**,
  coalesced into a handful of requests — so update bandwidth is proportional to
  the number of changed *bytes*, not the size of the layer that contains them.
- A single changed file pulls its exact bytes out of the slab, rather than
  re-downloading a whole compressed layer.
- Byte-identical slabs deduplicate automatically at the registry (same content →
  same blob digest).

On a rolling-release system, where updates scatter small changes across many
files, this keeps update size small and predictable.

## The immutable-OS fundamentals, done cleanly

Zerith keeps the proven parts of the image-based model:

- **Atomic whole-image swaps** instead of in-place package upgrades.
- **N-1 fallback** — any deployment can roll back to the previous known-good
  state.
- **Content-addressed dedup** between deployments via hardlinks into a shared
  store, so removing a deployment frees only the objects unique to it.
- A **true factory reset**.
- **cosign / sigstore** keyless signing over the deployment — the
  `deployment.json`, UKI, Limine, and root are all verified before they land.

## Small enough to actually understand

The entire system is a `Containerfile`, a short boot script (`init`), a
bootstrap `install`, and one tidy package behind `zerithctl`. You can read the
complete boot and update path in an afternoon — which makes Zerith both a
genuinely lean OS and an excellent way to learn how immutable boot, composefs,
and UKIs fit together.

---

For the details behind these strengths, see the boot chain in [boot.md](boot.md),
the trust chain in [integrity.md](integrity.md), and the object store in
[objects.md](objects.md).
