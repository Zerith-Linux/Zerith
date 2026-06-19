# Build process

Zerith images are produced entirely by CI and delivered as OCI artifacts. The
target never renders an image or pulls a container — it only verifies and lands
prebuilt pieces.

One subtlety drives the whole shape: the composefs image's digest must be known
*before* the UKI is signed, because that digest is baked into the UKI's signed
kernel command line. So the composefs is rendered in CI, its digest is pinned
into the UKI, and the UKI is signed — all before anything is published. This is
why the UKI is not built inside the `Containerfile`: the digest does not exist
until the final rootfs has been rendered.

## The Containerfile

A two-stage OCI build:

1. **`uki-builder` stage** (Arch base) builds the minimal busybox initramfs from
   `init`: it assembles the busybox applets, `modprobe`, and `mount.composefs`
   with their shared libraries, copies the required kernel modules, verifies the
   essential ones are present, and packs everything into `initramfs.img`. The
   kernel modules tree is exported alongside it.
2. **Runtime stage** (Artix `base-dinit`) installs the OS packages, fetches
   `oras` and `cosign`, builds the AUR desktop components, and copies in the
   initramfs, the kernel modules, the `zerith` package (to `/usr/lib/zerith`),
   the `zerithctl` shim (to `/usr/local/bin`), and `system_files/`. Runtime
   systemd is removed at the end — dinit is PID 1.

## The CI pipeline

The steps below run in `.github/workflows/build.yml`, each delegating to a
script in `scripts/ci/` (see [ci-workflows.md](ci-workflows.md)):

1. **Build the rootfs image** from the `Containerfile`.
2. **Post-process** — capture a clean `/var` skeleton to
   `/usr/share/factory/var`, relocate `/etc` to `/usr/etc` (the overlay's lower
   layer), and blank the mutable dirs back to empty mountpoints.
3. **Export** the post-processed rootfs to a plain directory.
4. **Render composefs + build/sign the UKI** — as real root: `mkcomposefs`
   produces `root.cfs` and the object store, the fs-verity digest is computed
   offline, `ukify` bakes `deploy=<id> composefs.digest=<digest>` into the UKI,
   and `sbsign` signs both the UKI and the Limine loader for Secure Boot.
5. **Pack objects + write metadata** — concatenate the objects into one pack
   blob with a digest→offset index, and write `deployment.json`
   ([objects.md](objects.md)).
6. **Push** the deployment and objects artifacts via `oras`.
7. **Cosign-sign** the deployment artifact (keyless, via the CI OIDC identity).

The split is clean: everything that produces or signs an image happens in CI,
and the host does only verification and atomic placement
([deployment.md](deployment.md)). The full trust chain is in
[integrity.md](integrity.md).
