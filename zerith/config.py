"""Constants and environment-driven tunables shared across the toolset.

Grouping every magic value here keeps the rest of the package free of literals
and gives a single place to audit names that must stay in step with the
initramfs (``init``) and the CI workflow.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- On-disk locations -------------------------------------------------------
DEFAULT_DEPLOY = Path("/deploy")          # @deploy subvolume mountpoint
DEFAULT_ESP = Path("/efi")                # EFI System Partition mountpoint

SOURCE_CONF_NAME = "source.conf"          # update-channel config in @deploy
LOCK_NAME = ".zerithctl.lock"             # exclusive-lock file in @deploy

# --- Schema versions written into JSON ---------------------------------------
CONFIG_SCHEMA = 1                         # source.conf
META_SCHEMA = 2                           # per-deployment deployment.json

# --- Deployment roles (relative symlinks inside @deploy) ---------------------
ROLES = ("current", "fallback", "staging")

# --- File names inside a deployment / artifact (set by CI via oras titles) ---
UKI_NAME = "zerith.efi"                   # Unified Kernel Image
CFS_NAME = "root.cfs"                     # composefs metadata image
META_NAME = "deployment.json"             # self-describing deployment metadata
BOOTLOADER_NAME = "BOOTX64.EFI"           # Secure Boot-signed Limine loader

# --- Disk defaults (fresh-install / --disk mode) -----------------------------
DEFAULT_ESP_SIZE = "1GiB"
DEFAULT_LABEL = "zerith"                  # MUST match the init's LABEL=zerith
EFI_LABEL = "Zerith Boot Manager"         # efibootmgr NVRAM entry label

# --- cosign trust policy -----------------------------------------------------
# Identity is the CI workflow's OIDC identity; issuer is GitHub's OIDC provider.
DEFAULT_COSIGN_ISSUER = "https://token.actions.githubusercontent.com"


def _env_int(name: str, default: int, *, minimum: int) -> int:
    """Read a non-negative int from the environment, clamped to ``minimum``.

    Falls back to ``default`` on anything unparseable so a stray value can never
    crash the tool.
    """
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


# Parallel slab range fetches. Each range is an independent network round trip,
# so a small pool keeps the link busy between requests. See docs/objects.md.
FETCH_JOBS = _env_int("ZERITH_FETCH_JOBS", 8, minimum=1)

# Max gap (bytes of already-present data) to fold into a single HTTP Range
# request when fetching from the slab blob. 0 disables coalescing.
COALESCE_GAP = _env_int("ZERITH_COALESCE_GAP", 1 << 20, minimum=0)
