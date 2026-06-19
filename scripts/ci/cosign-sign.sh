#!/usr/bin/env bash
#
# Keyless (OIDC) cosign signature over the deployment artifact. Only this
# artifact is signed: object integrity is already anchored by it (signed UKI ->
# composefs.digest -> verified root.cfs -> per-object fs-verity), so a swapped
# object can't satisfy the mount. See docs/integrity.md.
#
# Requires: DEPLOY_REF_ID. Expects COSIGN_YES=true in the environment.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$DIR/../lib/common.sh"
require_env DEPLOY_REF_ID

cosign sign "$DEPLOY_REF_ID"
log "signed $DEPLOY_REF_ID"
