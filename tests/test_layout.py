"""Layout primitives: role symlinks, metadata round-trip, source.conf, ESP
paths. These touch only a temp directory."""
from types import SimpleNamespace

from zerith import config, layout


def _fake_source(deploy_id="dep123", **over):
    base = dict(deploy_id=deploy_id, version="20260101", digest="d" * 64,
                objects_ref="ghcr.io/o/zerith-objects:dep123",
                objects_slab={"digest": "sha256:s", "size": 1},
                objects_index={"digest": "sha256:i", "size": 1,
                               "encoding": "gzip"},
                ref="ghcr.io/o/zerith:latest")
    base.update(over)
    return SimpleNamespace(**base)


def test_role_set_read_clear(tmp_path):
    assert layout.read_role(tmp_path, "current") is None
    layout.set_role(tmp_path, "current", "dep123")
    assert layout.read_role(tmp_path, "current") == "dep123"
    # Re-point is atomic and overwrites.
    layout.set_role(tmp_path, "current", "dep456")
    assert layout.read_role(tmp_path, "current") == "dep456"
    layout.clear_role(tmp_path, "current")
    assert layout.read_role(tmp_path, "current") is None


def test_meta_round_trip(tmp_path):
    src = _fake_source()
    ddir = layout.deploy_dir(tmp_path, src.deploy_id)
    ddir.mkdir(parents=True)
    layout.write_meta(ddir, src)
    meta = layout.load_meta(tmp_path, src.deploy_id)
    assert meta["deploy_id"] == src.deploy_id
    assert meta["composefs_digest"] == src.digest
    assert meta["schema"] == config.META_SCHEMA
    assert meta["objects_slab"] == src.objects_slab


def test_load_meta_missing_returns_empty(tmp_path):
    assert layout.load_meta(tmp_path, "nope") == {}
    assert layout.load_meta(tmp_path, None) == {}


def test_source_conf_round_trip(tmp_path):
    cfg = tmp_path / config.SOURCE_CONF_NAME
    assert layout.read_source(cfg) is None
    layout.write_source(cfg, "ghcr.io/o/zerith:latest")
    assert layout.read_source(cfg) == "ghcr.io/o/zerith:latest"


def test_esp_paths(tmp_path):
    assert layout.esp_uki(tmp_path, "current") == tmp_path / "zerith" / "current.efi"
    assert layout.esp_uki(tmp_path, "fallback") == tmp_path / "zerith" / "fallback.efi"
    assert layout.esp_limine(tmp_path) == \
        tmp_path / "EFI" / "BOOT" / config.BOOTLOADER_NAME


def test_atomic_copy(tmp_path):
    src = tmp_path / "src"
    src.write_bytes(b"payload")
    dst = tmp_path / "sub" / "dst"
    layout.atomic_copy(src, dst)
    assert dst.read_bytes() == b"payload"
