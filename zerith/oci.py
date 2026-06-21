"""OCI artifact handling: ref parsing, cosign verification, registry auth, and
the :class:`Source` that wraps a pulled (or local) deployment.

Every deployment originates from a signed OCI artifact pulled with ``oras`` and
verified with ``cosign`` before anything touches disk. The trust chain is
described in docs/integrity.md.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from . import config, runtime
from .runtime import die, require_tool, run, vlog


def ref_repo(ref: str) -> str:
    """Strip the tag/digest from an OCI ref, leaving ``registry/repo`` (for
    ``oras blob fetch repo@digest``).

    A colon separates a tag only in the final path segment, so a registry port
    (``host:5000/repo``) is handled correctly.
    """
    at = ref.split("@", 1)[0]
    slash = at.rfind("/")
    last = at[slash + 1:]
    if ":" in last:
        return at[:slash + 1] + last.split(":", 1)[0]
    return at


def registry_auth_header(repo: str) -> str | None:
    """Return an ``Authorization: Bearer ...`` header for anonymous pulls from
    ``repo``, or ``None`` if the registry serves blobs without auth.

    ``oras`` does this internally, but raw HTTP Range requests need the token, so
    we replicate just the pull-scope grab: a bare ``GET /v2/`` answers 401 with a
    ``WWW-Authenticate`` realm we then exchange for a token.
    """
    registry = repo.split("/", 1)[0]
    hdrs = run(["curl", "-sS", "-o", os.devnull, "-D", "-",
                f"https://{registry}/v2/"], capture=True)
    m = re.search(r"(?im)^www-authenticate:\s*bearer\s+(.+)$", hdrs)
    if not m:
        return None
    params = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
    realm = params.get("realm")
    if not realm:
        return None
    query = (f"service={params.get('service', registry)}"
             f"&scope=repository:{repo.split('/', 1)[1]}:pull")
    try:
        token = json.loads(run(["curl", "-fsSL", f"{realm}?{query}"],
                               capture=True))
    except ValueError:
        return None
    bearer = token.get("token") or token.get("access_token")
    return f"Authorization: Bearer {bearer}" if bearer else None


def cosign_verify(ref: str, identity_re: str | None, issuer: str,
                  skip: bool) -> None:
    """Verify ``ref`` was signed by the expected CI OIDC identity, or abort."""
    if skip:
        runtime.log(f"WARNING: skipping signature verification for {ref}")
        return
    if not identity_re:
        die("cosign identity not set (pass --cosign-identity or "
            "ZERITH_COSIGN_IDENTITY, or --insecure-skip-verify for dev)")
    require_tool("cosign")
    run(["cosign", "verify",
         "--certificate-identity-regexp", identity_re,
         "--certificate-oidc-issuer", issuer,
         ref], capture=True)
    vlog(f"cosign verified {ref}")


class Source:
    """A resolved deployment: a directory holding ``zerith.efi``, ``root.cfs``,
    ``deployment.json`` (and, for ``--local`` installs, an ``objects/`` tree),
    plus the parsed metadata. Accessors read straight from the metadata so the
    schema lives in one place.
    """

    def __init__(self, staging: Path, meta: dict, *, ref: str | None,
                 local_objects: Path | None = None, cleanup=None) -> None:
        self.staging = staging
        self.meta = meta
        self.ref = ref
        self.local_objects = local_objects
        self._cleanup = cleanup

    @property
    def uki(self) -> Path:
        return self.staging / config.UKI_NAME

    @property
    def root_cfs(self) -> Path:
        return self.staging / config.CFS_NAME

    @property
    def bootloader(self) -> Path:
        return self.staging / config.BOOTLOADER_NAME

    @property
    def deploy_id(self) -> str:
        return self.meta["deploy_id"]

    @property
    def version(self) -> str:
        return self.meta.get("version", "unknown")

    @property
    def digest(self) -> str:
        return self.meta["composefs_digest"]

    @property
    def objects_ref(self) -> str | None:
        return self.meta.get("objects_ref")

    @property
    def objects_slab(self) -> dict:
        """``{digest, size}`` of the single concatenated object slab blob."""
        return self.meta.get("objects_slab") or {}

    @property
    def objects_index(self) -> dict:
        """``{digest, size, encoding}`` of the slab's digest->offset index."""
        return self.meta.get("objects_index") or {}

    def cleanup(self) -> None:
        if self._cleanup:
            self._cleanup()


def _read_meta(staging: Path) -> dict:
    if runtime.DRY_RUN and not (staging / config.META_NAME).is_file():
        return {"deploy_id": "dryrunid00000000", "version": "unknown",
                "composefs_digest": "0" * 64, "objects_ref": None}
    try:
        return json.loads((staging / config.META_NAME).read_text())
    except (OSError, ValueError) as e:
        die(f"bad or missing {config.META_NAME} in source: {e}")


def source_from_ref(ref: str, cos_id: str | None, cos_issuer: str,
                    skip_verify: bool) -> Source:
    """Pull and verify a signed deployment artifact into a temp dir."""
    require_tool("oras")
    cosign_verify(ref, cos_id, cos_issuer, skip_verify)
    staging = Path(tempfile.mkdtemp(prefix="zerith-pull-"))
    run(["oras", "pull", ref, "-o", str(staging)])
    meta = _read_meta(staging)
    if not meta.get("deploy_id") and not runtime.DRY_RUN:
        shutil.rmtree(staging, ignore_errors=True)
        die(f"artifact {ref} has no deploy_id in {config.META_NAME}")
    return Source(staging, meta, ref=ref,
                  cleanup=lambda: shutil.rmtree(staging, ignore_errors=True))


def source_from_local(local: Path) -> Source:
    """Wrap a local CI output dir (``zerith.efi``, ``root.cfs``,
    ``deployment.json``, ``objects/``) for offline installs."""
    if not runtime.DRY_RUN and not local.is_dir():
        die(f"--local {local} is not a directory")
    meta = _read_meta(local)
    objects = local / "objects"
    if not runtime.DRY_RUN and not objects.is_dir():
        die(f"--local {local} has no objects/ directory")
    return Source(local, meta, ref=None, local_objects=objects)
