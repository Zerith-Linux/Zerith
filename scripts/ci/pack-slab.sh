#!/usr/bin/env bash
#
# Pack every composefs object into ONE slab blob with a digest->[offset,length]
# index, and write the signed-artifact metadata (deployment.json). Clients fetch
# the small index, work out which objects they lack, and pull only those byte
# ranges off the slab. See docs/objects.md.
#
# Requires: IMAGE_REGISTRY, IMAGE_NAME, DEPLOY_ID, VERSION, DEPLOYED_AT,
# DEFAULT_TAG. Optional: TAG_PREFIX. Appends refs to $GITHUB_ENV when set.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$DIR/../lib/common.sh"
require_env IMAGE_REGISTRY IMAGE_NAME DEPLOY_ID VERSION DEPLOYED_AT DEFAULT_TAG
TAG_PREFIX="${TAG_PREFIX:-}"

OBJ_REF="${IMAGE_REGISTRY}/${IMAGE_NAME}-objects:${DEPLOY_ID}"
DIGEST="$(cat out/composefs.digest)"

# Sorting by relpath ("ab/cdef") equals sorting by digest (the '/' sits at a
# fixed position), so pack order is stable: an unchanged object set yields a
# byte-identical slab -> identical blob digest -> the registry dedups the whole
# push. Build the slab with one xargs|cat (no per-object process spawn) and
# compute offsets in one awk pass over the same sorted list, so index and slab
# agree by construction.
( cd out/objects
  find . -type f -printf '%P\t%s\n' | LC_ALL=C sort > /tmp/objs
  cut -f1 /tmp/objs | tr '\n' '\0' | xargs -0 cat > ../objects.slab
  awk -F'\t' 'BEGIN{off=0} { d=$1; sub("/","",d); print d, off, $2; off += $2 }' \
    /tmp/objs > ../objects.index )
gzip -9 -f out/objects.index                      # -> out/objects.index.gz

SLAB_DIGEST="sha256:$(sha256sum out/objects.slab | awk '{print $1}')"
SLAB_SIZE="$(stat -c %s out/objects.slab)"
IDX_DIGEST="sha256:$(sha256sum out/objects.index.gz | awk '{print $1}')"
IDX_SIZE="$(stat -c %s out/objects.index.gz)"
log "slab: $(wc -l < /tmp/objs) objects, ${SLAB_SIZE} bytes (${SLAB_DIGEST})"

# deployment.json records the slab + index blob digests so the client can
# address them directly. The artifact is cosign-signed (digests anchored) and
# objects self-verify (fs-verity name == content), so a tampered slab can't land.
jq -n \
    --arg deploy_id "$DEPLOY_ID" \
    --arg version "$VERSION" \
    --arg digest "$DIGEST" \
    --arg objects_ref "$OBJ_REF" \
    --arg deployed_at "$DEPLOYED_AT" \
    --arg slab_digest "$SLAB_DIGEST" --argjson slab_size "$SLAB_SIZE" \
    --arg idx_digest "$IDX_DIGEST" --argjson idx_size "$IDX_SIZE" \
    '{schema:2, deploy_id:$deploy_id, version:$version,
      composefs_digest:$digest, objects_ref:$objects_ref,
      objects_slab:{digest:$slab_digest, size:$slab_size},
      objects_index:{digest:$idx_digest, size:$idx_size, encoding:"gzip"},
      deployed_at:$deployed_at}' \
    > out/deployment.json

# Export refs for the push/sign steps (only meaningful inside GitHub Actions).
if [ -n "${GITHUB_ENV:-}" ]; then
    {
        echo "OBJ_REF=${OBJ_REF}"
        echo "DEPLOY_REF_ID=${IMAGE_REGISTRY}/${IMAGE_NAME}:${TAG_PREFIX}${DEPLOY_ID}"
        echo "DEPLOY_REF_CHANNEL=${IMAGE_REGISTRY}/${IMAGE_NAME}:${TAG_PREFIX}${DEFAULT_TAG}"
    } >> "$GITHUB_ENV"
fi
log "wrote out/deployment.json"
