#!/usr/bin/env bash
#
# Push the two OCI artifacts via oras:
#   * deployment = UKI + root.cfs + BOOTX64.EFI + deployment.json (cosign-signed)
#   * objects    = the pack blob + the gzip index (two blobs total)
# An unchanged pack dedups at the registry by digest. See docs/build-process.md.
#
# Requires: DEPLOY_REF_ID, OBJ_REF, TAG_PREFIX, DEFAULT_TAG (set by pack-objects).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$DIR/../lib/common.sh"
require_env DEPLOY_REF_ID OBJ_REF DEFAULT_TAG
TAG_PREFIX="${TAG_PREFIX:-}"

( cd out && oras push "$DEPLOY_REF_ID" \
    --artifact-type application/vnd.zerith.deployment+json \
    deployment.json:application/vnd.zerith.deployment.config+json \
    zerith.efi:application/vnd.zerith.uki \
    BOOTX64.EFI:application/vnd.zerith.bootloader \
    root.cfs:application/vnd.zerith.composefs.image+erofs )

# Move the channel tag to this build.
oras tag "$DEPLOY_REF_ID" "${TAG_PREFIX}${DEFAULT_TAG}"

( cd out && oras push "$OBJ_REF" \
    --artifact-type application/vnd.zerith.objects \
    objects.pack:application/vnd.zerith.objects.pack \
    objects.index.gz:application/vnd.zerith.objects.index+gzip )
log "pushed deployment + objects artifacts"
