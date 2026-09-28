from __future__ import annotations

import os

import pytest

from peovim.core.persistence import atomic_write_bytes, atomic_write_text


def test_atomic_write_text_replaces_existing_file(tmp_path) -> None:
    target = tmp_path / "state.json"
    target.write_text('{"old": true}', encoding="utf-8")

    atomic_write_text(target, '{"new": true}', encoding="utf-8")

    assert target.read_text(encoding="utf-8") == '{"new": true}'


def test_atomic_write_bytes_falls_back_to_in_place_write_when_temp_file_cannot_be_created(
    tmp_path, monkeypatch
) -> None:
    # Simulates a directory that allows modifying an existing file but not
    # creating new entries in it (restrictive directory ACLs, some network
    # mounts, etc.) — mkstemp() fails, but the target file itself is writable.
    target = tmp_path / "state.bin"
    target.write_bytes(b"original")

    def _boom(*args, **kwargs):
        raise PermissionError("cannot create temp file in this directory")

    monkeypatch.setattr("peovim.core.persistence.tempfile.mkstemp", _boom)

    atomic_write_bytes(target, b"updated")

    assert target.read_bytes() == b"updated"
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_bytes_retries_on_replace_file_exists(tmp_path, monkeypatch) -> None:
    # GVFS FUSE mounts (AFP/SMB) are known to raise EEXIST from os.replace()
    # instead of atomically replacing the destination like a real POSIX
    # filesystem does. Verify we retry via unlink-then-replace rather than
    # failing the save outright.
    target = tmp_path / "state.bin"
    target.write_bytes(b"original")

    real_replace = os.replace
    calls = {"count": 0}

    def _flaky_replace(src, dst) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise FileExistsError(17, "File exists")
        real_replace(src, dst)

    monkeypatch.setattr("peovim.core.persistence.os.replace", _flaky_replace)

    atomic_write_bytes(target, b"updated")

    assert target.read_bytes() == b"updated"
    assert calls["count"] == 2
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_bytes_cleans_temp_and_preserves_original_on_replace_failure(tmp_path, monkeypatch) -> None:
    target = tmp_path / "state.bin"
    target.write_bytes(b"original")

    def _boom(src, dst) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("peovim.core.persistence.os.replace", _boom)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_bytes(target, b"updated")

    assert target.read_bytes() == b"original"
    assert list(tmp_path.glob("*.tmp")) == []
