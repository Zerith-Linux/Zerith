# Integrity

Zerith's security model is a single chain where each link is checked by the one
before it, anchored in Secure Boot and ending at per-file kernel enforcement.
Nothing is rendered on the target, so the digest that boots is the same digest
that was signed in CI.

## The chain

```
Secure Boot → signed Limine → signed UKI → composefs.digest= (signed cmdline)
   → mount.composefs digest= (root image) → fs-verity (every backing object)
```

- **Secure Boot → Limine.** Firmware validates the Limine loader's signature
  before running it. Limine is pinned to v12.3.3 and signed with the same key as
  the UKI.
- **Limine → UKI.** Limine chainloads `zerith.efi` via `efi_chainload`. For EFI
  chainloading, Limine defers to the firmware's own Secure Boot verification of
  the chainloaded image, so a tampered UKI is rejected by the firmware — Limine
  itself does not need an enrolled config checksum for this link to hold.
- **UKI → composefs digest.** The kernel command line — including
  `composefs.digest=<hex>` — is part of the signed UKI payload, so the pinned
  root digest cannot be altered without breaking the signature.
- **digest → root image.** `mount.composefs` is given `digest=<pinned>` and
  refuses to assemble a `root.cfs` that does not match.
- **root image → objects.** `root.cfs` records an fs-verity digest for every
  file; the kernel refuses to open a backing object whose content does not
  measure to its recorded digest.

## The update path

For delivery, a second gate sits in front of the chain: the deployment artifact
(UKI + Limine loader + `root.cfs` + `deployment.json`) is **cosign-signed** with
the CI's keyless OIDC identity. `zerithctl` verifies that signature before
anything is allowed to land on disk. The trust identity ships in the image at
`/etc/environment` as `ZERITH_COSIGN_IDENTITY` (a regexp over the CI workflow's
OIDC identity), with the issuer defaulting to GitHub's OIDC provider.

Objects themselves are not separately signed, and do not need to be: their
integrity is already anchored by the signed `root.cfs` digest and per-object
fs-verity. As each object is placed, `zerithctl` re-measures it and checks the
result against its content-addressed store path, so a tampered or swapped object
cannot satisfy the mount.

## Why this shape

The composefs digest must be known *before* the UKI is signed, because the
digest is baked into the UKI's signed command line. That single constraint is
why the image is rendered and the UKI signed entirely in CI (see
[build-process.md](build-process.md)) and why the host only ever verifies and
places prebuilt, pinned pieces — it never renders an image of its own.
