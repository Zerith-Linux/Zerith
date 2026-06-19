"""Pure-logic tests: OCI ref parsing, env tunables, partition naming."""
import importlib

import pytest

from zerith import config, disk
from zerith.oci import ref_repo


@pytest.mark.parametrize("ref,expected", [
    ("ghcr.io/org/zerith:latest", "ghcr.io/org/zerith"),
    ("ghcr.io/org/zerith@sha256:" + "a" * 64, "ghcr.io/org/zerith"),
    ("ghcr.io/org/zerith", "ghcr.io/org/zerith"),
    ("registry:5000/org/zerith:tag", "registry:5000/org/zerith"),
    ("registry:5000/org/zerith", "registry:5000/org/zerith"),
])
def test_ref_repo_strips_tag_keeps_port(ref, expected):
    assert ref_repo(ref) == expected


@pytest.mark.parametrize("disk_node,esp,btrfs", [
    ("/dev/sda", "/dev/sda1", "/dev/sda2"),
    ("/dev/nvme0n1", "/dev/nvme0n1p1", "/dev/nvme0n1p2"),
    ("/dev/mmcblk0", "/dev/mmcblk0p1", "/dev/mmcblk0p2"),
    ("/dev/vdb", "/dev/vdb1", "/dev/vdb2"),
])
def test_partition_names(disk_node, esp, btrfs):
    from pathlib import Path
    assert disk.partition_names(Path(disk_node)) == (esp, btrfs)


def test_env_int_default_and_clamp(monkeypatch):
    monkeypatch.delenv("ZERITH_FETCH_JOBS", raising=False)
    importlib.reload(config)
    assert config.FETCH_JOBS == 8

    monkeypatch.setenv("ZERITH_FETCH_JOBS", "0")     # below minimum -> clamp
    importlib.reload(config)
    assert config.FETCH_JOBS == 1

    monkeypatch.setenv("ZERITH_FETCH_JOBS", "nonsense")  # unparseable -> default
    importlib.reload(config)
    assert config.FETCH_JOBS == 8

    monkeypatch.delenv("ZERITH_FETCH_JOBS", raising=False)
    importlib.reload(config)
