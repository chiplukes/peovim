"""
core.store_api — PluginStore: persistent JSON key-value storage per plugin

Stored at: platformdirs.user_data_dir('peovim') / 'stores' / '<name>.json'
Each plugin gets its own store via api.store.get_store(name).
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from peovim.core.persistence import atomic_write_text


def _get_data_dir() -> Path:
    """Return the user data directory for 'peovim', falling back to a temp dir."""
    try:
        import platformdirs

        base = platformdirs.user_data_dir("peovim", ensure_exists=True)
        return Path(base)
    except Exception:
        import tempfile

        return Path(tempfile.gettempdir()) / "peovim"


class PluginStore:
    """Persistent JSON key-value store for a single plugin."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._path: Path = _get_data_dir() / "stores" / f"{name}.json"
        self._data: dict[str, Any] | None = None  # None = not yet loaded

    def _load(self) -> None:
        """Lazily load from JSON file."""
        if self._data is not None:
            return
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        """Write current data to JSON file, creating dirs if needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(Exception):
            # save policy: single-writer (each plugin gets a unique path via its plugin name)
            atomic_write_text(self._path, json.dumps(self._data, indent=2), encoding="utf-8")

    def get(self, key: str, default: Any = None) -> Any:
        self._load()
        assert self._data is not None
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._load()
        assert self._data is not None
        self._data[key] = value
        self._save()

    def delete(self, key: str) -> None:
        self._load()
        assert self._data is not None
        self._data.pop(key, None)
        self._save()

    def clear(self) -> None:
        self._load()
        self._data = {}
        self._save()

    def keys(self) -> list[str]:
        self._load()
        assert self._data is not None
        return list(self._data.keys())
