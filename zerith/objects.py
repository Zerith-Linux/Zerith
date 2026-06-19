"""The content-addressed object store: landing objects into the shared store,
maintaining per-deployment hardlink holders, and sweeping orphans for GC.

Objects live under ``shared/objects/<2 hex>/<rest>``, named by their fs-verity
sha256 digest. Three landing strategies feed the store, all funnelling through
:func:`place_object` (verify digest, move into place, seal with fs-verity):

* :func:`land_from_dir`  — copy from a local ``objects/`` tree (offline install).
* :func:`land_from_ref`  — legacy per-prefix shard tarballs, diffed by digest.
* :func:`land_from_pack` — schema>=2 single pack blob, fetched whole on a fresh
  install or by HTTP Range for incremental updates.

The fetch-strategy reasoning (coalescing, parallelism, holder refcounting) is in
docs/objects.md.
"""
from __future__ import annotations

import gzip
import os
import re
import shutil
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config, runtime
from .oci import ref_repo, registry_auth_header
from .progress import Progress, human_bytes
from .runtime import die, log, require_tool, run, vlog
from .verity import enable_verity, measure_file

# Every 64-hex sha256 in a composefs dump is a referenced object.
_DIGEST_RE = re.compile(r"\b[0-9a-f]{64}\b")

# Object store paths are "<2 hex>/<hex…>"; rejecting anything else stops a
# crafted tar entry from escaping the shared store via path traversal.
_OBJ_PATH_RE = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{2,}$")


def _rel_for_digest(digest: str) -> str:
    """Map a bare sha256 hex digest to its ``ab/cdef…`` store path."""
    return f"{digest[:2]}/{digest[2:]}"


def place_object(tmp: Path, shared: Path, rel: str) -> bool:
    """Verify a fetched blob's fs-verity digest matches its store path, move it
    into the shared store, and seal it. Returns ``False`` if already present.
    """
    dest = shared / rel
    if dest.exists():
        return False
    want = rel.replace("/", "")
    got = measure_file(tmp)
    if got != want:
        die(f"object digest mismatch: {rel} (measured {got})")
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, dest)
    enable_verity(dest)
    return True


def referenced_objects(root_cfs: Path) -> set[str]:
    """Store paths (``ab/cdef…``) referenced by a single composefs image."""
    dump = run(["composefs-info", "dump", str(root_cfs)], capture=True)
    return {_rel_for_digest(d) for d in _DIGEST_RE.findall(dump)}


def link_holder(shared: Path, holder: Path, root_cfs: Path) -> None:
    """Hardlink exactly this deployment's objects from the shared store into its
    private holder dir, bumping each object's link count. GC keys off that count
    to know when an object has no remaining holders.
    """
    require_tool("composefs-info")
    holder.mkdir(parents=True, exist_ok=True)
    linked = 0
    for rel in referenced_objects(root_cfs):
        src = shared / rel
        if not src.is_file():
            continue
        dst = holder / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dst)
            linked += 1
        except FileExistsError:
            pass
    vlog(f"holder: linked {linked} object(s) for this deployment")


# --------------------------------------------------------------------------- #
# Strategy 1: local objects/ tree (offline install)
# --------------------------------------------------------------------------- #

def land_from_dir(src_objects: Path, shared: Path) -> None:
    """Merge a local ``objects/`` tree into the shared store."""
    count = 0
    for root, _, files in os.walk(src_objects):
        for name in files:
            fp = Path(root) / name
            rel = str(fp.relative_to(src_objects))
            dest = shared / rel
            if dest.exists():
                continue
            if runtime.DRY_RUN:
                log(f"[dry-run] place object {rel}")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.parent / f".{name}.tmp"
            shutil.copy2(fp, tmp)
            if place_object(tmp, shared, rel):
                count += 1
    vlog(f"landed {count} new object(s) from {src_objects}")


# --------------------------------------------------------------------------- #
# Strategy 2: legacy per-prefix shard tarballs
# --------------------------------------------------------------------------- #

def _extract_shard(tar_path: Path, shared: Path) -> tuple[int, int]:
    """Extract one shard tar into the shared store, skipping objects already
    present and verifying + sealing each new one. Returns ``(new, present)``."""
    new = present = 0
    with tarfile.open(tar_path) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            rel = (member.name[len("objects/"):]
                   if member.name.startswith("objects/") else member.name)
            if not _OBJ_PATH_RE.match(rel):
                continue
            dest = shared / rel
            if dest.exists():
                present += 1
                continue
            if runtime.DRY_RUN:
                log(f"[dry-run] extract object {rel}")
                continue
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.parent / f".{rel.replace('/', '_')}.tmp"
            with open(tmp, "wb") as out:
                shutil.copyfileobj(extracted, out)
            if place_object(tmp, shared, rel):
                new += 1
            else:
                present += 1
    return new, present


def _land_full_shards(objects_ref: str, shared: Path) -> None:
    """Pull the whole objects artifact and extract every shard. Used when no
    shard digest map is available to diff against."""
    staging = Path(tempfile.mkdtemp(prefix="zerith-obj-"))
    try:
        run(["oras", "pull", objects_ref, "-o", str(staging)])
        new = present = 0
        for tar_path in sorted(staging.rglob("*.tar")):
            a, b = _extract_shard(tar_path, shared)
            new += a
            present += b
        log(f"objects: extracted {new} new, {present} already present "
            f"(full pull)")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def land_from_ref(objects_ref: str, shared: Path,
                  new_shards: dict | None = None,
                  old_shards: dict | None = None) -> None:
    """Materialize objects from per-prefix shard tarballs, fetching only the
    shards whose digest changed vs the current deployment. With no diff map
    (first install or older artifact), pull the whole artifact.
    """
    require_tool("oras")
    new_shards = new_shards or {}
    old_shards = old_shards or {}
    if not (new_shards and old_shards):
        _land_full_shards(objects_ref, shared)
        return

    repo = ref_repo(objects_ref)
    todo = [(pfx, digest) for pfx, digest in sorted(new_shards.items())
            if old_shards.get(pfx) != digest]   # unchanged buckets are on disk

    if runtime.DRY_RUN:
        for pfx, digest in todo:
            log(f"[dry-run] oras blob fetch {repo}@{digest} ({pfx})")
        log(f"objects: {len(todo)} changed shard(s) of {len(new_shards)}")
        return

    staging = Path(tempfile.mkdtemp(prefix="zerith-obj-"))
    try:
        new = present = 0
        jobs = max(1, min(config.FETCH_JOBS, len(todo)))
        prog = Progress(len(todo), label="objects")
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(_fetch_shard, repo, pfx, digest, staging): pfx
                       for pfx, digest in todo}
            for fut in as_completed(futures):
                tar_path = fut.result()         # re-raises any fetch failure
                nbytes = tar_path.stat().st_size
                a, b = _extract_shard(tar_path, shared)
                new += a
                present += b
                tar_path.unlink(missing_ok=True)
                prog.update(nbytes)
        prog.finish(
            f"objects: {len(todo)} changed shard(s) of {len(new_shards)} "
            f"({jobs}-way parallel); {new} new, {present} present; "
            f"{human_bytes(prog.nbytes)} in {prog.elapsed:.1f}s "
            f"({human_bytes(prog.nbytes / prog.elapsed)}/s avg)")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _fetch_shard(repo: str, prefix: str, digest: str, staging: Path) -> Path:
    """Fetch one shard blob to ``staging/<prefix>.tar``. Safe in a worker thread:
    it touches only its own file and shells out to oras (which releases the GIL
    while waiting on the network). ``capture=True`` keeps oras off the TTY so the
    single main-thread progress bar owns the terminal.
    """
    tar_path = staging / f"{prefix}.tar"
    run(["oras", "blob", "fetch", f"{repo}@{digest}",
         "--output", str(tar_path)], capture=True)
    return tar_path


# --------------------------------------------------------------------------- #
# Strategy 3: schema>=2 single pack blob (+ digest->offset index)
# --------------------------------------------------------------------------- #

def _fetch_index(repo: str, idx_meta: dict) -> dict[str, tuple[int, int]]:
    """Pull the small pack index blob whole and parse it into
    ``{digest_hex: (offset, length)}``."""
    digest = idx_meta["digest"]
    staging = Path(tempfile.mkdtemp(prefix="zerith-idx-"))
    try:
        out = staging / "index"
        run(["oras", "blob", "fetch", f"{repo}@{digest}",
             "--output", str(out)], capture=True)
        raw = out.read_bytes()
        if idx_meta.get("encoding") == "gzip":
            raw = gzip.decompress(raw)
        return _parse_index(raw.decode())
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _parse_index(text: str) -> dict[str, tuple[int, int]]:
    """Parse ``<digest> <offset> <length>`` lines into a lookup dict."""
    index: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        if not line:
            continue
        digest, offset, length = line.split()
        index[digest] = (int(offset), int(length))
    return index


def _coalesce_ranges(want: list[tuple[int, int, str]],
                     gap: int) -> list[tuple[int, int, list]]:
    """Merge ``(offset, length, digest)`` entries (sorted by offset) whose gap to
    the running range is ``<= gap`` bytes into one HTTP range, so scattered
    objects collapse into a handful of requests (and a fresh install, where
    everything is missing, collapses into a single whole-pack stream). Each
    returned range is ``(start, end, [(offset_within_range, length, digest)…])``.
    """
    ranges: list[tuple[int, int, list]] = []
    cur: tuple[int, int, list] | None = None
    for off, length, digest in want:
        if cur is None:
            cur = (off, off + length - 1, [(0, length, digest)])
            continue
        start, end, objs = cur
        if off - (end + 1) <= gap:
            objs.append((off - start, length, digest))
            cur = (start, max(end, off + length - 1), objs)
        else:
            ranges.append(cur)
            cur = (off, off + length - 1, [(0, length, digest)])
    if cur is not None:
        ranges.append(cur)
    return ranges


def _fetch_range(registry: str, repo_path: str, blob_digest: str,
                 start: int, end: int, out_path: Path,
                 auth: str | None) -> Path:
    """GET one byte range of the pack blob to ``out_path``. Safe in a worker
    thread (touches only its own file + shells out to curl). ``curl -L`` follows
    the registry's redirect to backing storage, carrying the Range.
    """
    url = f"https://{registry}/v2/{repo_path}/blobs/{blob_digest}"
    cmd = ["curl", "-fsSL", "--retry", "3", "--retry-delay", "1",
           "-H", f"Range: bytes={start}-{end}"]
    if auth:
        cmd += ["-H", auth]
    cmd += ["-o", str(out_path), url]
    run(cmd, capture=True)
    got, wanted = out_path.stat().st_size, end - start + 1
    if got != wanted:
        die(f"range fetch returned {got} bytes, expected {wanted} "
            f"(registry may not honor Range on {blob_digest})")
    return out_path


def _place_from_buffer(buf_path: Path, objs: list, shared: Path) -> int:
    """Slice each object out of a fetched buffer (a range, or the whole pack),
    verify + seal it, and place it in the shared store. Seeks per object so a big
    buffer never sits in memory all at once. Offsets are relative to the buffer.
    Returns how many objects were newly placed.
    """
    new = 0
    with open(buf_path, "rb") as buf:
        for rel_off, length, digest in objs:
            rel = _rel_for_digest(digest)
            dest = shared / rel
            if dest.exists():
                continue
            buf.seek(rel_off)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.parent / f".{digest}.tmp"
            with open(tmp, "wb") as out:
                out.write(buf.read(length))
            if place_object(tmp, shared, rel):
                new += 1
    return new


def _missing_objects(src, shared: Path,
                     index: dict[str, tuple[int, int]]
                     ) -> tuple[list[tuple[int, int, str]], int, int]:
    """From this image's referenced objects, return ``(want, inlined, present)``
    where ``want`` is the ``(offset, length, digest)`` list (sorted by offset)
    for objects we lack, ``inlined`` counts referenced objects absent from the
    index because composefs inlined their payload into the image itself, and
    ``present`` counts objects already in the shared store.
    """
    want: list[tuple[int, int, str]] = []
    inlined = 0
    present = 0
    for rel in referenced_objects(src.root_cfs):
        if (shared / rel).exists():
            present += 1
            continue
        digest = rel.replace("/", "")
        entry = index.get(digest)
        if entry is None:
            inlined += 1
            continue
        want.append((entry[0], entry[1], digest))
    want.sort()
    return want, inlined, present


def land_from_pack(src, shared: Path) -> None:
    """Materialize this image's objects from the single pack blob.

    Fetches only the byte ranges covering objects we lack, coalescing nearby
    ones (:func:`_coalesce_ranges`). A fresh install — where every object is
    missing — naturally collapses into a single whole-pack range, so this one
    path serves both first install and incremental update (verified against
    GHCR; see docs/objects.md). :func:`place_object` re-checks every fs-verity
    digest before the object is trusted, and :func:`_fetch_range` rejects any
    response that doesn't honor the requested Range.
    """
    require_tool("oras")
    require_tool("composefs-info")
    require_tool("curl")
    objects_ref = src.objects_ref
    if not objects_ref:
        die("deployment metadata has no objects_ref")
    repo = ref_repo(objects_ref)
    pack_digest = src.objects_pack["digest"]

    index = _fetch_index(repo, src.objects_index)
    want, inlined, already_present = _missing_objects(src, shared, index)
    if inlined:
        vlog(f"objects: {inlined} referenced object(s) inlined in image, "
             f"nothing to fetch for them")
    if not want:
        log(f"objects: all {already_present} present, nothing to fetch")
        return

    _land_pack_by_range(repo, pack_digest, want, shared, already_present)


def _land_pack_by_range(repo: str, pack_digest: str,
                        want: list[tuple[int, int, str]], shared: Path,
                        already_present: int) -> None:
    """Fetch only the byte ranges covering missing objects via HTTP Range."""
    require_tool("curl")
    ranges = _coalesce_ranges(want, config.COALESCE_GAP)
    span = sum(end - start + 1 for start, end, _ in ranges)

    if runtime.DRY_RUN:
        log(f"[dry-run] {len(want)} object(s) via {len(ranges)} range "
            f"request(s), ~{human_bytes(span)} from {pack_digest}")
        return

    auth = registry_auth_header(repo)
    registry, repo_path = repo.split("/", 1)
    staging = Path(tempfile.mkdtemp(prefix="zerith-pack-"))
    try:
        new = 0
        jobs = max(1, min(config.FETCH_JOBS, len(ranges)))
        prog = Progress(len(ranges), label="objects")
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {}
            for i, (start, end, objs) in enumerate(ranges):
                fut = pool.submit(_fetch_range, registry, repo_path,
                                  pack_digest, start, end, staging / f"r{i}",
                                  auth)
                futures[fut] = (start, end, objs)
            for fut in as_completed(futures):
                buf_path = fut.result()         # re-raises fetch failure
                start, end, objs = futures[fut]
                new += _place_from_buffer(buf_path, objs, shared)
                buf_path.unlink(missing_ok=True)
                prog.update(end - start + 1)
        total = already_present + new
        prog.finish(
            f"objects: {already_present} already present, "
            f"{new} fetched ({total} needed via {len(ranges)} range "
            f"request(s), {jobs}-way); {human_bytes(prog.nbytes)} "
            f"in {prog.elapsed:.1f}s "
            f"({human_bytes(prog.nbytes / prog.elapsed)}/s)")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Garbage collection of orphaned objects
# --------------------------------------------------------------------------- #

def _try(fn) -> None:
    """Run a filesystem op, ignoring races / permission noise."""
    try:
        fn()
    except OSError:
        pass


def sweep_orphans(shared: Path) -> int:
    """Unlink objects with no remaining holder (link count == 1), then prune the
    now-empty fanout dirs. Returns how many objects were removed."""
    if not shared.is_dir():
        return 0
    swept = 0
    for root, _, files in os.walk(shared):
        for name in files:
            fp = Path(root) / name
            try:
                orphan = fp.stat().st_nlink == 1
            except OSError:
                continue
            if orphan:
                _try(fp.unlink)
                swept += 1
    for root, dirs, _ in os.walk(shared, topdown=False):
        for d in dirs:
            _try((Path(root) / d).rmdir)
    return swept
