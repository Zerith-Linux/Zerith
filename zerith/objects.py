"""The content-addressed object store: landing objects into the shared store,
maintaining per-deployment hardlink holders, and sweeping orphans for GC.

Objects live under ``shared/objects/<2 hex>/<rest>``, named by their fs-verity
sha256 digest. Two landing strategies feed the store, both funnelling through
:func:`place_object` (verify digest, move into place, seal with fs-verity):

* :func:`land_from_dir`  — copy from a local ``objects/`` tree (offline install).
* :func:`land_from_slab` — the single concatenated slab blob, fetched whole on a
  fresh install or by HTTP Range for incremental updates.

The fetch-strategy reasoning (coalescing, parallelism, holder refcounting) is in
docs/objects.md.
"""
from __future__ import annotations

import gzip
import os
import re
import shutil
import sys
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
    refs = referenced_objects(root_cfs)
    linked = 0
    prog = Progress(len(refs), label="linking")
    for rel in refs:
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
        prog.update()
    prog.finish(f"holder: linked {linked} object(s) for this deployment")


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
# The object slab: one concatenated blob (+ digest->offset index)
# --------------------------------------------------------------------------- #

def _fetch_index(repo: str, idx_meta: dict) -> dict[str, tuple[int, int]]:
    """Pull the small slab index blob whole and parse it into
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
    everything is missing, collapses into a single whole-slab stream). Each
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
                 auth: str | None, show_progress: bool = False) -> Path:
    """GET one byte range of the slab blob to ``out_path``. Safe in a worker
    thread (touches only its own file + shells out to curl). ``curl -L`` follows
    the registry's redirect to backing storage, carrying the Range.

    When ``show_progress`` is set, curl draws its own progress bar on stderr
    (used for the single-range case, e.g. a fresh install pulling the whole
    slab); otherwise it runs silently and its output is captured for error
    reporting.
    """
    url = f"https://{registry}/v2/{repo_path}/blobs/{blob_digest}"
    cmd = ["curl", "-fL", "--retry", "3", "--retry-delay", "1",
           "-H", f"Range: bytes={start}-{end}"]
    cmd += ["--progress-bar"] if show_progress else ["-sS"]
    if auth:
        cmd += ["-H", auth]
    cmd += ["-o", str(out_path), url]
    # Don't capture when showing the bar, so curl's meter reaches the terminal.
    run(cmd, capture=not show_progress)
    got, wanted = out_path.stat().st_size, end - start + 1
    if got != wanted:
        die(f"range fetch returned {got} bytes, expected {wanted} "
            f"(registry may not honor Range on {blob_digest})")
    return out_path


def _place_from_buffer(buf_path: Path, objs: list, shared: Path) -> int:
    """Slice each object out of a fetched buffer (a range, or the whole slab),
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


def land_from_slab(src, shared: Path) -> None:
    """Materialize this image's objects from the single slab blob.

    Fetches only the byte ranges covering objects we lack, coalescing nearby
    ones (:func:`_coalesce_ranges`). A fresh install — where every object is
    missing — naturally collapses into a single whole-slab range, so this one
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
    slab_digest = src.objects_slab["digest"]

    index = _fetch_index(repo, src.objects_index)
    want, inlined, already_present = _missing_objects(src, shared, index)
    if inlined:
        vlog(f"objects: {inlined} referenced object(s) inlined in image, "
             f"nothing to fetch for them")
    if not want:
        log(f"objects: all {already_present} present, nothing to fetch")
        return

    _land_slab_by_range(repo, slab_digest, want, shared, already_present)


def _land_slab_by_range(repo: str, slab_digest: str,
                        want: list[tuple[int, int, str]], shared: Path,
                        already_present: int) -> None:
    """Fetch only the byte ranges covering missing objects via HTTP Range."""
    require_tool("curl")
    ranges = _coalesce_ranges(want, config.COALESCE_GAP)
    span = sum(end - start + 1 for start, end, _ in ranges)

    if runtime.DRY_RUN:
        log(f"[dry-run] {len(want)} object(s) via {len(ranges)} range "
            f"request(s), ~{human_bytes(span)} from {slab_digest}")
        return

    auth = registry_auth_header(repo)
    registry, repo_path = repo.split("/", 1)
    staging = Path(tempfile.mkdtemp(prefix="zerith-slab-"))
    try:
        new = 0
        jobs = max(1, min(config.FETCH_JOBS, len(ranges)))
        # One range (e.g. a fresh whole-slab pull) has nothing for the per-range
        # bar to tick through, so let curl show its own byte-level meter instead.
        show_progress = len(ranges) == 1 and sys.stderr.isatty()
        prog = Progress(len(ranges), label="objects")
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {}
            for i, (start, end, objs) in enumerate(ranges):
                fut = pool.submit(_fetch_range, registry, repo_path,
                                  slab_digest, start, end, staging / f"r{i}",
                                  auth, show_progress)
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
