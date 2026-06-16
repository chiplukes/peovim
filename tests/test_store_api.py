from __future__ import annotations

import json

from peovim.core.store_api import PluginStore


def test_plugin_store_set_persists_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("peovim.core.store_api._get_data_dir", lambda: tmp_path)

    store = PluginStore("example")
    store.set("enabled", True)

    path = tmp_path / "stores" / "example.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"enabled": True}


def test_plugin_store_save_failure_preserves_existing_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("peovim.core.store_api._get_data_dir", lambda: tmp_path)

    store = PluginStore("example")
    store.set("enabled", True)
    path = tmp_path / "stores" / "example.json"
    original = path.read_text(encoding="utf-8")

    def _boom(path, text, *, encoding="utf-8") -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("peovim.core.store_api.atomic_write_text", _boom)

    store.set("mode", "slow")

    assert path.read_text(encoding="utf-8") == original
    assert list(path.parent.glob("*.tmp")) == []
    persisted = PluginStore("example")
    assert persisted.get("enabled") is True
    assert persisted.get("mode") is None
