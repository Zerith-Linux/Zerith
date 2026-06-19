# The object store

File data lives in a shared, content-addressed store, separate from the
`root.cfs` metadata image that describes the directory tree. Objects are named
by their fs-verity sha256 digest and stored at `shared/objects/<ab>/<cdef…>`.
Because deployments reference the same objects by hash, identical files are
stored once and shared across every deployment.

> Analogy: think of the object store as a library's stacks and `root.cfs` as a
> single book's index of call numbers. Two books (deployments) can both cite the
> same volume; the library keeps one copy on the shelf, and each book's index
> just points at it.

## Landing strategies

`zerith/objects.py` funnels three sources through one `place_object` step
(verify the fs-verity digest matches the store path, move into place, seal with
fs-verity):

- **Local directory** (`land_from_dir`) — copy from a CI output `objects/` tree,
  for offline `--local` installs.
- **Pack blob** (`land_from_pack`) — the schema-2 default (see below).
- **Legacy shards** (`land_from_ref`) — per-prefix tarballs, diffed by digest
  against the current deployment so only changed buckets are fetched. Kept for
  older artifacts.

## The pack blob and index (schema 2)

CI concatenates every object (sorted by digest) into one **pack blob** and
writes an **index** mapping each object's digest to its `[offset, length]` in
the pack. Both are pushed as ordinary OCI blobs. Sorting by store path equals
sorting by digest, so an unchanged set of objects produces a byte-identical pack
— identical blob digest — and the registry deduplicates the whole push.

The host fetches the small index once, computes which objects it lacks (from
this image's `root.cfs`), and then transfers only what it needs:

- **Fresh install** (`allow_ranges=False`) — almost everything is missing, so
  the whole pack is streamed once with `oras` and sliced locally.
- **Incremental update** (`allow_ranges=True`) — only the byte ranges covering
  missing objects are fetched with HTTP Range requests, coalescing objects that
  sit within `ZERITH_COALESCE_GAP` bytes of each other into a single request.

Either way, one big blob lives on the registry and downloads are surgical. Some
referenced objects are absent from the index because composefs inlines small
file payloads directly into `root.cfs`; those need no fetch and are skipped.

## Parallelism and progress

Shard and range fetches run in a thread pool (`ZERITH_FETCH_JOBS`, default 8).
Each worker shells out to `oras`/`curl`, which releases the GIL while waiting on
the network, and touches only its own temp file. The terminal progress bar is
written by the main thread alone, so nothing races on the TTY; on a non-TTY
(CI logs, journald) only the final summary line is printed.

## Garbage collection

Each deployment keeps a private **holder** directory (`<id>/objects/`) of
hardlinks to exactly the objects it references. So a shared object referenced by
K live deployments has link count `1 + K` (the store entry plus K holders).
`gc` removes any deployment not pointed at by a role symlink, then sweeps the
store: an object whose link count is `1` has no holder left and is unlinked.
Removing an old deployment therefore frees only the files unique to it.

## Tunables

| Variable               | Default | Effect |
|------------------------|---------|--------|
| `ZERITH_FETCH_JOBS`    | 8       | Parallel object/range fetches. |
| `ZERITH_COALESCE_GAP`  | 1048576 | Max already-present bytes folded into one Range request (0 disables). |

See [deployment.md](deployment.md) for how landing fits into staging and
promotion, and [integrity.md](integrity.md) for why objects need no separate
signature.
