#!/usr/bin/env python3
"""Pre-push integrity gate.

Confirm out/objects.pack + out/objects.index.gz reconstruct every object in
out/objects/ byte-for-byte. A pack/index bug (wrong offset, truncated blob,
mis-sorted index) fails here, on the runner, instead of reaching the registry.
See docs/objects.md and docs/ci-workflows.md.

Reads out/objects.pack, out/objects.index.gz and out/objects/. No special tools.
"""
from __future__ import annotations

import gzip
import os
import sys

PACK = "out/objects.pack"
INDEX = "out/objects.index.gz"
STORE = "out/objects"


def _die(msg: str) -> None:
    print(f"verify-pack: {msg}", file=sys.stderr)
    sys.exit(1)


def _load_index() -> list[tuple[str, int, int]]:
    index = []
    with gzip.open(INDEX, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            digest, off, length = line.split()
            index.append((digest, int(off), int(length)))
    return index


def verify() -> int:
    index = _load_index()
    pack_size = os.path.getsize(PACK)
    store_count = sum(len(files) for _, _, files in os.walk(STORE))

    errors = 0
    expected_off = 0
    with open(PACK, "rb") as pack:
        for digest, off, length in index:
            if off != expected_off:
                print(f"  GAP/OVERLAP at {digest}: "
                      f"index off={off} expected={expected_off}")
                errors += 1
            expected_off = off + length
            obj_path = os.path.join(STORE, digest[:2], digest[2:])
            if not os.path.isfile(obj_path):
                print(f"  MISSING object file for index entry {digest}")
                errors += 1
                continue
            pack.seek(off)
            with open(obj_path, "rb") as obj:
                if pack.read(length) != obj.read():
                    print(f"  BYTE MISMATCH for {
                          digest} (off={off} len={length})")
                    errors += 1

    if expected_off != pack_size:
        print(f"  PACK SIZE MISMATCH: index ends at {expected_off}, "
              f"pack is {pack_size}")
        errors += 1
    if len(index) != store_count:
        print(f"  COUNT MISMATCH: index has {
              len(index)}, store has {store_count}")
        errors += 1

    if errors:
        print(
            f">> verify-pack: FAILED with {errors} error(s)", file=sys.stderr)
        return 1
    print(f">> verify-pack: OK — {len(index)} objects, {pack_size} bytes, "
          f"contiguous and byte-identical to the object store")
    return 0


def main() -> int:
    if not os.path.isfile(PACK):
        _die(f"{PACK} missing (run pack-objects first)")
    if not os.path.isfile(INDEX):
        _die(f"{INDEX} missing")
    if not os.path.isdir(STORE):
        _die(f"{STORE} missing")
    return verify()


if __name__ == "__main__":
    sys.exit(main())
