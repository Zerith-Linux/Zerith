# Host tooling

`zerithctl` installs Zerith and manages the deployment lifecycle on a running
host. It is a thin shim over the `zerith` package (see
[project-structure.md](project-structure.md)); on an installed system the
package lives at `/usr/lib/zerith` and the shim on `PATH`.

## Commands

| Command              | What it does |
|----------------------|--------------|
| `status`             | show the update channel and the current / fallback / staging roles |
| `deploy REF`         | set the update channel to `REF`, pull + verify it, and promote to current |
| `update`             | pull the configured channel, verify it, and promote if it changed |
| `rollback` (`swap`)  | swap current ⇄ fallback |
| `gc`                 | drop unreferenced deployments and sweep orphaned objects |
| `install`            | partition (optional), initialize, and install Zerith |

Lifecycle commands operate on `@deploy` at `/deploy` and the ESP at `/efi`, and
take an exclusive lock on `@deploy` while mutating. The mechanics are in
[deployment.md](deployment.md).

## Options

Options follow their subcommand (`zerithctl status --deploy /deploy`,
`zerithctl install --disk … --dry-run`), so each command is self-contained.

Run-mode flags, accepted by every subcommand:

| Option | Meaning |
|--------|---------|
| `--dry-run` | print actions without changing anything |
| `-v`, `--verbose` | verbose logging |

Host-target options (`status`, `deploy`, `update`, `rollback`, `gc`):

| Option | Meaning |
|--------|---------|
| `--deploy PATH` | `@deploy` mountpoint (default `/deploy`) |
| `--esp PATH` | ESP mountpoint (default `/efi`) |
| `--config PATH` | update-channel config (default `<deploy>/source.conf`) |

Trust options (`deploy`, `update`, `install` — commands that pull a signed
artifact):

| Option | Meaning |
|--------|---------|
| `--cosign-identity RE` | cosign certificate-identity regexp (env `ZERITH_COSIGN_IDENTITY`) |
| `--cosign-issuer URL` | cosign OIDC issuer (env `ZERITH_COSIGN_ISSUER`) |
| `--insecure-skip-verify` | DEV ONLY: skip cosign verification |

## `zerithctl install`

```
zerithctl install --disk /dev/nvme0n1 --ref <oci-ref> [--yes] [--esp-size 1GiB] [--label zerith]
zerithctl install <sysroot> <efi>     --ref <oci-ref>
zerithctl install <sysroot> <efi>     --local <ci-output-dir>
```

Provide either `--disk` (auto-partition; destroys the disk) or `sysroot`+`efi`
mountpoints, and exactly one source: `--ref` (pull + verify a signed artifact)
or `--local` (offline CI output dir). `--no-limine` skips bootloader install;
`--label` must match the initramfs `LABEL=` (default `zerith`).

Most installs go through the `install` bootstrap rather than calling this
directly — see [deployment.md](deployment.md).

## Environment variables

`zerithctl`:

| Variable | Effect |
|----------|--------|
| `ZERITH_COSIGN_IDENTITY` | default `--cosign-identity` (shipped in `/etc/environment`) |
| `ZERITH_COSIGN_ISSUER` | default `--cosign-issuer` |
| `ZERITH_FETCH_JOBS` | parallel object fetches (default 8) |
| `ZERITH_COALESCE_GAP` | Range coalescing gap in bytes (default 1 MiB) |

The `install` bootstrap also reads: `ZERITH_REF` (artifact to install),
`ZERITH_REPO` / `ZERITH_BRANCH` (where to fetch the tool when piped from the
web), `ZERITH_DISK` (skip the interactive picker), `ZERITH_INSECURE` (skip
verification), and `ZERITH_YES` (skip the wipe confirmation).

## Examples

```sh
zerithctl status
zerithctl deploy ghcr.io/zerith-linux/zerith:latest   # set channel + deploy
zerithctl update                                       # pull configured channel
zerithctl rollback                                     # boot the previous image
zerithctl update --dry-run                             # preview an update
```
