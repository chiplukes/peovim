from __future__ import annotations

import pytest

from peovim.core.persistence import atomic_write_bytes, atomic_write_text


def test_atomic_write_text_replaces_existing_file(tmp_path) -> None:
    target = tmp_path / "state.json"
    target.write_text('{"old": true}', encoding="utf-8")

    atomic_write_text(target, '{"new": true}', encoding="utf-8")

    assert target.read_text(encoding="utf-8") == '{"new": true}'


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
