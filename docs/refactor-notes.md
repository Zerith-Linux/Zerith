# Refactor notes

This document records what changed in the maintainability refactor and the
issues that could not be safely resolved without hardware or product decisions.

## Major changes

### One tool, a real package

`install` and `zerithctl` previously duplicated a large amount of logic
(logging, command execution, fs-verity, OCI ref parsing, object landing, the
`Source` class, roles, metadata). They are now a single Python package,
`zerith/`, behind a thin `zerithctl` shim. All former `install` functionality is
a `zerithctl install` subcommand. The duplication is gone — both the first
install and an update share one `materialize` core and one object store.

The package is layered and imports in one direction
(`cli → installer/lifecycle → objects/layout/disk/bootloader/oci →
runtime/config`), so each piece is independently testable. Module map is in
`docs/project-structure.md`.

### `install` is now a bootstrap

`install` is a small bash script: curl-pipe-able, sudo re-exec, ensures the
required tools are present, locates or fetches `zerithctl`, runs an interactive
disk picker (or accepts `sysroot efi`), then execs `zerithctl install`. All
install *logic* lives in the package; the bootstrap only prepares the
environment and selects the target.

### Big functions split

Long routines were broken into named units: object landing now funnels three
strategies through one `place_object`; the pack path shares helpers between the
whole-pack (install) and HTTP-range (update) strategies; promote/rollback isolate
the crash-safe ESP ordering; `init` is organized into named boot stages.

### CI shell extracted

The inline heredocs in `.github/workflows/build.yml` moved to one script per
step under `scripts/ci/`, with shared helpers in `scripts/lib/common.sh`. The
workflow is now orchestration (triggers, matrix, `uses:`, one-line `run:`s).
Scripts take inputs from plain env vars, so they lint with `shellcheck` and run
outside Actions. Map in `docs/ci-workflows.md`.

### Containerfile

Now copies the `zerith/` package to `/usr/lib/zerith` and the `zerithctl` shim
to `/usr/local/bin` (the shim adds that dir to `sys.path`). No other build logic
changed.

### Docs and tests

Explanatory prose moved out of source comments into a cross-linked `docs/` tree;
the README is now a landing page that links into it. A `pytest` suite covers the
pure logic (digest/path math, range coalescing, index parsing, role symlinks, GC
sweep, `place_object` verification): 31 tests.

### Verification performed

`py_compile`, `ruff` (package + tests), `pytest` (31 passing), `shellcheck`
(all bash + `init` as POSIX sh), and `--dry-run` smoke tests of install
(disk/ref, mountpoints/local), deploy, status, rollback, and gc. The real
install/boot path (root, btrfs, composefs, UEFI, oras, cosign, a live registry)
cannot run in this environment, so behavior preservation there rests on
compile/lint/unit-test/dry-run plus static reasoning.

## Unresolved / needs-more-context issues

1. **Pack vs whole-pack fetch are kept as two strategies.** The HTTP-range path
   arguably subsumes the whole-pack stream (a fresh install coalesces into one
   big range). They were deliberately separate before, and unifying them fully
   would change install's wire behavior against real registries, so the refactor
   keeps both behind shared helpers selected by `allow_ranges`. Worth revisiting
   once there's a registry to test the range path on at install time.

2. **`zerithctl install` global-option placement.** Global flags (cosign, dry-run)
   precede the subcommand, to preserve existing `zerithctl` invocations. That
   makes the `install` bootstrap's hand-off command slightly awkward
   (`zerithctl --cosign-identity X install …`). Self-contained per-subcommand
   options would read better but would change the existing CLI shape; left as-is
   pending a decision on whether breaking the old arg order is acceptable.

3. **Limine config syntax is version-sensitive.** `limine.conf` keys
   (`/entry`, `protocol: efi_chainload`, `boot():` paths) have changed across
   Limine releases. The static config matches the assumed version but is not
   verified against the Limine actually shipped in the image; pin and confirm.

4. **`mkfs.btrfs` and fs-verity.** `disk.partition_disk` formats btrfs with
   defaults, relying on current btrfs-progs enabling verity on first use. On
   older btrfs-progs this may need an explicit feature flag; not detectable
   without the target's tool versions.

5. **Bootstrap tool fetch is unverified.** When piped from the web with no
   checkout, `install` clones/downloads the tool over TLS but does not verify a
   signature on the *tool itself* (the deployment artifact it then installs is
   cosign-verified). A signed release or pinned commit for the bootstrap would
   close that gap.

6. **No integration test of the object range math against a real pack.** The
   coalescing/index logic is unit-tested in isolation, but the end-to-end
   "index + pack produced by CI → ranges fetched → objects placed" loop is only
   reasoned about. A fixture pack built by `scripts/ci/pack-objects.sh` exercised
   by the landing code would be the natural next test.

7. **`efibootmgr` / NVRAM entry is best-effort.** Disk/partition resolution falls
   back to `/proc/mounts` parsing; on unusual topologies (md/dm, multipath) it
   may not resolve, in which case boot relies on the removable-media fallback
   path only. Behavior preserved from the original; flagged as a known limit.
