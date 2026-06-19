#!/usr/bin/env bash
#
# Render the composefs image + object store and build/sign the UKI.
#
# This runs the heavy lifting inside an archlinux:base container as REAL root
# (sudo -E): mkcomposefs must read root-only files (shadow, credstore) and record
# faithful uid/gid/mode into root.cfs. fs-verity is NOT enabled here — the digest
# is computed offline and the target re-enables verity on landing.
#
# Requires: DEPLOY_ID. Optional: SB_KEY, SB_CERT (PEM) for Secure Boot signing.
# See docs/build-process.md and docs/integrity.md.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$DIR/../lib/common.sh"
require_env DEPLOY_ID

mkdir -p out
# Mount the inner script in and run it; -E preserves DEPLOY_ID/SB_KEY/SB_CERT so
# the -e passthroughs carry their values into the container.
sudo -E podman run --rm \
    -v "$PWD/rootfs:/rootfs:ro" \
    -v "$PWD/out:/out" \
    -v "$DIR:/ci:ro" \
    -e DEPLOY_ID -e SB_KEY -e SB_CERT -e LIMINE_VERSION -e LIMINE_ZIP_SHA256 \
    docker.io/archlinux:base bash /ci/build-uki-in-container.sh

# /out is root-owned (rendered as root); hand it back to the runner so the later
# unprivileged push/sign steps can read it.
sudo chown -R "$(id -u):$(id -g)" out
log "composefs + UKI ready in out/"
