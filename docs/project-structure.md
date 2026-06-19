# Project structure

This is the map of the repository and the reading order for the rest of the
docs. Zerith is deliberately small: a container build plus a short boot script,
a bootstrap installer, and one Python package behind `zerithctl`.

## Repository layout

```
Containerfile              OCI build: initramfs builder stage + Artix runtime image
init                       initramfs PID 1 — assembles the composefs root, switch_root
install                    bash bootstrap — prepares the env, then runs `zerithctl install`
zerithctl                  thin entry point that loads the zerith package
zerith/                    the host-tooling package (install + lifecycle logic)
scripts/
  ci/                      CI step scripts (bash per step; verify-pack.py is Python)
  lib/                     shared shell helpers for the CI scripts
.github/workflows/build.yml  CI orchestration (calls scripts/ci/*)
system_files/              files copied verbatim into the image
docs/                      this documentation
tests/                     pytest suite for the pure logic in zerith/
sb.crt                     Secure Boot certificate (public)
```

## The `zerith` package

`zerithctl` is a shim; the work lives in focused modules. From leaves to
orchestration:

| Module           | Responsibility |
|------------------|----------------|
| `config.py`      | Constants and environment-driven tunables (names, schemas, defaults). |
| `runtime.py`     | Run flags (`DRY_RUN`/`VERBOSE`), logging, command execution, precondition checks. |
| `verity.py`      | fs-verity measure / enable. |
| `progress.py`    | Human-readable sizes and the single-writer download bar. |
| `oci.py`         | OCI ref parsing, cosign verification, registry auth, the `Source` wrapper. |
| `objects.py`     | The content-addressed object store: landing (dir / shards / pack+range), holders, GC sweep. |
| `layout.py`      | On-disk layout primitives: roles, metadata, `source.conf`, ESP paths, atomic copy. |
| `lifecycle.py`   | `materialize`, stage, promote, rollback, gc, status — operations on an installed host. |
| `disk.py`        | Whole-disk partition / format / mount for fresh installs. |
| `bootloader.py`  | Limine config and loader / efibootmgr installation. |
| `installer.py`   | Fresh-install orchestration: layout init + first deployment. |
| `cli.py`         | Argument parsing and dispatch for every subcommand. |

The dependency direction is one-way: `cli` → `installer`/`lifecycle` →
`objects`/`layout`/`disk`/`bootloader`/`oci` → `runtime`/`config`. Nothing lower
imports anything higher, which keeps the pieces independently testable.

## Where to read next

- [architecture.md](architecture.md) — the system design and on-disk layout.
- [boot.md](boot.md) — the boot chain and what `init` does.
- [integrity.md](integrity.md) — the end-to-end trust chain.
- [objects.md](objects.md) — the content-addressed store and object transfer.
- [build-process.md](build-process.md) — how an image is produced in CI.
- [ci-workflows.md](ci-workflows.md) — the workflow and its extracted scripts.
- [deployment.md](deployment.md) — installing and the update lifecycle.
- [host-tooling.md](host-tooling.md) — `zerithctl` and `install` usage.
- [development.md](development.md) — working on the code and running the tests.
