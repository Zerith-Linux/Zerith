# Zerith

An immutable, image-based Linux distribution built on Artix, with a composefs
read-only root, role-based deployments with an N-1 fallback, and a true
factory-reset model.

**No Bootc here ;P** — but Zerith is definitely inspired by bootc.

---

## What is Zerith?

Zerith treats the operating system as a **versioned, read-only image** rather
than a mutable pile of files. The entire OS lives in an immutable composefs
root; everything you change at runtime is confined to a small, well-defined set
of writable areas. Updates are **atomic image swaps**, not in-place package
upgrades, and any deployment can be rolled back to the previous known-good
state.

It is built from OCI container images, rendered to composefs and packed into a
**signed** Unified Kernel Image (UKI) in CI, then delivered as OCI artifacts —
gated by `cosign` and fs-verity — and booted by a Secure Boot-signed Limine
under UEFI.

## Why Zerith?

Zerith exists to answer one question: **what does a modern, image-based,
atomically-updated Linux look like without systemd?** The immutable-OS idea is
well proven by Fedora Silverblue, bootc, and the Universal Blue family — but
nearly all of them are built *on* systemd. Zerith fills that gap:

- **No systemd in the running OS.** Init is **dinit**, on an Artix base. (The
  one place systemd code appears is the *build*: the UKI is assembled with
  `ukify` and systemd's EFI stub, build-time tools that never ship in the image
  you boot.)
- **Keeps the good parts of immutable OSes.** Read-only composefs root, atomic
  whole-image updates, an N-1 fallback, content-addressed dedup between
  deployments, and a true factory reset.
- **Small and legible.** The whole system is a `Containerfile` plus a short boot
  script (`init`), a bootstrap installer (`install`), and one tidy package
  behind `zerithctl`. You can read the entire boot and update path in an
  afternoon.

It's a **learn-by-building project**: a way to understand how immutable boot,
composefs, and UKIs fit together by wiring them up from scratch. If you want a
battle-tested daily driver today, the systemd-based options above are far more
mature. If you want a systemd-free take on the same ideas, that's the point of
Zerith.

## Quick start

Install onto a disk (this **erases** it), straight from the web:

```sh
curl -fsSL https://raw.githubusercontent.com/Zerith-Linux/Zerith/main/install | sudo bash
```

Then manage the system with `zerithctl` — `status`, `deploy REF`, `update`,
`rollback`, `gc`. See [docs/host-tooling.md](docs/host-tooling.md).

## Documentation

The deep dives live in [`docs/`](docs/):

- [Project structure](docs/project-structure.md) — repo layout and the module map.
- [Architecture](docs/architecture.md) — the design and on-disk layout.
- [Boot](docs/boot.md) — the boot chain and the initramfs `init`.
- [Integrity](docs/integrity.md) — the end-to-end trust chain.
- [Objects](docs/objects.md) — the content-addressed store and object transfer.
- [Build process](docs/build-process.md) — how an image is produced in CI.
- [CI workflows](docs/ci-workflows.md) — the workflow and its extracted scripts.
- [Deployment](docs/deployment.md) — installing and the update lifecycle.
- [Host tooling](docs/host-tooling.md) — `zerithctl` and `install` reference.
- [Development](docs/development.md) — working on the code and running the tests.

## Status

Zerith is an in-development, experimental distribution; expect rough edges. The
core mechanics — composefs root, digest-pinned UKI/Limine boot, signed `oras` /
`cosign` delivery, role-based deployments with an N-1 fallback, the install →
`zerithctl` lifecycle, writable subvolumes, and factory reset — are the working
foundation. It is not yet a daily driver; treat it as a system to learn from and
build on.
