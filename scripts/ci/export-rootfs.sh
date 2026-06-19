#!/usr/bin/env bash
#
# Export the "post-processed" image's filesystem to ./rootfs/ for offline
# composefs rendering. See docs/build-process.md.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$DIR/../lib/common.sh"

mkdir -p rootfs
cid="$(podman create localhost/post-processed)"
trap 'podman rm "$cid" >/dev/null 2>&1 || true' EXIT
podman export "$cid" | sudo tar -x -C rootfs
log "exported rootfs/"
