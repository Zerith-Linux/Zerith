# Development

## Layout and dependency direction

The host tooling is one package, `zerith/`, behind the `zerithctl` shim. Modules
are layered and import in one direction only:

```
cli → installer / lifecycle → objects / layout / disk / bootloader / oci → runtime / config
```

Nothing lower imports anything higher. New logic should land in the lowest layer
that fits, and orchestration (sequencing those pieces) belongs in `installer`,
`lifecycle`, or `cli`. The module-by-module responsibilities are in
[project-structure.md](project-structure.md).

## Run flags

`runtime.DRY_RUN` and `runtime.VERBOSE` are module-level flags set once by the
CLI. Always read them as attributes (`runtime.DRY_RUN`), never import the value
(`from runtime import DRY_RUN`), or you will capture a stale `False`. All
shell-outs go through `runtime.run`, which logs and skips mutating commands under
`--dry-run` while still letting read-only captures execute, so a dry run can
reason about real state.

## Working without real hardware

The full install/boot path needs root, btrfs, composefs, UEFI, and `oras` /
`cosign`, so most local work relies on:

- `--dry-run`, which exercises argument parsing and the full dispatch path while
  only printing the mutating actions; and
- the pytest suite, which covers the pure logic (digest/path math, range
  coalescing, index parsing, role symlinks, GC sweep) on temp dirs.

```sh
python3 zerithctl --dry-run install --disk /dev/sdX --ref ghcr.io/zerith-linux/zerith:latest --yes
python3 zerithctl --dry-run --deploy /tmp/d --esp /tmp/e update
```

## Tests, lint, and syntax

```sh
python3 -m pytest -q                 # unit tests for pure logic
python3 -m ruff check zerith/ zerithctl
python3 -m py_compile zerith/*.py zerithctl
shellcheck install scripts/lib/*.sh scripts/ci/*.sh
shellcheck -s sh init
```

Tests target functions with no I/O or filesystem-only behavior, constructing
small fixtures in `tmp_path`. Network, signing, and partitioning paths are out
of scope for unit tests — keep new pure logic factored into testable functions
rather than burying it inside an orchestration step.

## Style

- Keep comments minimal and about *why*; the prose explanations live in `docs/`.
  When a module references a concept, link the relevant doc in its docstring.
- Prefer small, named functions over long inline blocks — this is true for the
  CI shell (`scripts/ci/`) as much as the Python.
- Match the existing formatting; `ruff` is the arbiter for the Python.
- The initramfs `init` is POSIX `sh` for busybox — no bashisms; verify with
  `shellcheck -s sh`.

## Adding a CI step

Put the shell in a new `scripts/ci/<step>.sh`, source `scripts/lib/common.sh`,
declare its inputs with `require_env`, and call it from a one-line `run:` in the
workflow. See [ci-workflows.md](ci-workflows.md).
