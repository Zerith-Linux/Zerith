"""Object-store logic: digest/path math, index parsing, range coalescing,
missing-set computation, GC sweep, and place_object verification."""
from types import SimpleNamespace

import pytest

from zerith import objects


def test_rel_for_digest():
    d = "ab" + "c" * 62
    assert objects._rel_for_digest(d) == "ab/" + "c" * 62


def test_digest_regex_finds_all():
    a, b = "a" * 64, "b" * 64
    text = f"file foo {a} mode 0644\nfile bar {b} mode 0755\n"
    assert objects._DIGEST_RE.findall(text) == [a, b]


@pytest.mark.parametrize("rel,ok", [
    ("ab/" + "c" * 62, True),
    ("ab/cd", True),
    ("a/" + "b" * 62, False),       # one-char prefix
    ("../etc/passwd", False),       # traversal
    ("abc/" + "d" * 60, False),     # three-char prefix
])
def test_obj_path_regex(rel, ok):
    assert bool(objects._OBJ_PATH_RE.match(rel)) is ok


def test_parse_index():
    text = "aaaa 0 10\nbbbb 10 25\ncccc 35 5\n"
    assert objects._parse_index(text) == {
        "aaaa": (0, 10), "bbbb": (10, 25), "cccc": (35, 5)}


def test_coalesce_merges_within_gap():
    want = [(0, 10, "a"), (12, 4, "b"), (1000, 8, "c")]
    ranges = objects._coalesce_ranges(want, gap=8)   # 0-9 and 12-15 merge
    assert len(ranges) == 2
    start, end, objs = ranges[0]
    assert (start, end) == (0, 15)
    assert objs == [(0, 10, "a"), (12, 4, "b")]      # offsets relative to range
    assert ranges[1][2] == [(0, 8, "c")]


def test_coalesce_zero_gap_keeps_separate():
    want = [(0, 10, "a"), (12, 4, "b")]
    ranges = objects._coalesce_ranges(want, gap=0)
    assert len(ranges) == 2


def test_coalesce_fresh_install_collapses_to_one():
    # Contiguous objects (a packed blob with nothing present) -> single range.
    want = [(0, 5, "a"), (5, 5, "b"), (10, 5, "c")]
    ranges = objects._coalesce_ranges(want, gap=0)
    assert len(ranges) == 1
    assert ranges[0][0] == 0 and ranges[0][1] == 14


def test_missing_objects_splits_present_inlined_missing(tmp_path, monkeypatch):
    present = "aa" + "0" * 62
    missing = "bb" + "1" * 62
    inlined = "cc" + "2" * 62
    monkeypatch.setattr(objects, "referenced_objects",
                        lambda _cfs: {objects._rel_for_digest(present),
                                      objects._rel_for_digest(missing),
                                      objects._rel_for_digest(inlined)})
    shared = tmp_path / "shared"
    (shared / present[:2]).mkdir(parents=True)
    (shared / objects._rel_for_digest(present)).write_bytes(b"x")
    index = {missing: (100, 40)}              # inlined absent from index
    src = SimpleNamespace(root_cfs=tmp_path / "root.cfs")

    want, n_inlined, n_present = objects._missing_objects(src, shared, index)
    assert want == [(100, 40, missing)]
    assert n_inlined == 1
    assert n_present == 1


def test_sweep_orphans_by_link_count(tmp_path):
    shared = tmp_path / "shared"
    (shared / "ab").mkdir(parents=True)
    orphan = shared / "ab" / ("c" * 62)
    held = shared / "ab" / ("d" * 62)
    orphan.write_bytes(b"orphan")             # link count 1 -> swept
    held.write_bytes(b"held")
    holder_dir = tmp_path / "holder"
    holder_dir.mkdir()
    (holder_dir / "h").hardlink_to(held)      # link count 2 -> kept

    swept = objects.sweep_orphans(shared)
    assert swept == 1
    assert not orphan.exists()
    assert held.exists()


def test_place_object_verifies_digest(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    rel = "ab/" + "c" * 62
    blob = tmp_path / "incoming"
    blob.write_bytes(b"data")

    monkeypatch.setattr(objects, "enable_verity", lambda _p: None)
    # Matching digest -> placed.
    monkeypatch.setattr(objects, "measure_file", lambda _p: rel.replace("/", ""))
    assert objects.place_object(blob, shared, rel) is True
    assert (shared / rel).is_file()

    # Already present -> False, no re-measure needed.
    blob2 = tmp_path / "incoming2"
    blob2.write_bytes(b"data")
    assert objects.place_object(blob2, shared, rel) is False


def test_place_object_rejects_mismatch(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    rel = "ab/" + "c" * 62
    blob = tmp_path / "incoming"
    blob.write_bytes(b"tampered")
    monkeypatch.setattr(objects, "enable_verity", lambda _p: None)
    monkeypatch.setattr(objects, "measure_file", lambda _p: "deadbeef")

    with pytest.raises(SystemExit):
        objects.place_object(blob, shared, rel)
