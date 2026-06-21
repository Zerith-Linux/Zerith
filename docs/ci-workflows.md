# CI workflows

The build runs in `.github/workflows/build.yml`. The workflow itself is kept to
orchestration — triggers, matrix, environment, action `uses:`, and `if:`
guards — while every non-trivial shell step delegates to a script under
`scripts/ci/`. This keeps the YAML readable and lets the scripts be linted
(`shellcheck`) and run locally.

## Step → script map

| Workflow step                      | Script | Runs |
|------------------------------------|--------|------|
| Post-process rootfs                | `scripts/ci/post-process-rootfs.sh` | under `buildah unshare` |
| Export rootfs                      | `scripts/ci/export-rootfs.sh` | on the runner |
| Render composefs + build/sign UKI  | `scripts/ci/render-uki.sh` (outer) → `build-uki-in-container.sh` (inner) | inner runs inside `archlinux:base` as root |
| Write deployment.json + pack slab  | `scripts/ci/pack-slab.sh` | on the runner |
| Verify slab integrity              | `scripts/ci/verify-slab.py` | on the runner (pre-push gate) |
| Push deployment + objects          | `scripts/ci/push-artifacts.sh` | on the runner |
| Cosign sign                        | `scripts/ci/cosign-sign.sh` | on the runner |

Trivial steps that only write `$GITHUB_ENV` / `$GITHUB_OUTPUT` (image name
casing, tag prefix, date/version, deploy id) stay inline — they are
orchestration, not logic.

## How the scripts are decoupled

Scripts take their inputs from plain environment variables (`IMAGE_NAME`,
`DEPLOY_ID`, `VERSION`, `SB_KEY`, …) rather than GitHub Actions expressions, so
they have no hard dependency on the CI platform. The workflow sets those vars in
each step's `env:`. `scripts/lib/common.sh` provides shared `log` / `die` /
`require_env` helpers, and each host-side script sources it; `require_env` fails
fast with a clear message when a needed variable is missing.

The one exception is `pack-slab.sh`, which appends the artifact refs it
computes to `$GITHUB_ENV` *only when that variable is set* — so it is a no-op
outside Actions and the script still runs cleanly when invoked by hand.

## The two-script render step

The render step is split because it spans two execution contexts. `render-uki.sh`
is the outer orchestrator that runs on the runner: it creates `out/`, invokes
`podman run` as root with the right mounts and `-e` passthroughs, and hands
ownership back afterwards. `build-uki-in-container.sh` is the inner script that
runs inside `archlinux:base`; it is intentionally self-contained (no repo
helpers) because the container has only what the outer script mounts in. See
[build-process.md](build-process.md) for what the render produces.
