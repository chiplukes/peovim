"""
OptionsAPI — option get, set, and registration
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.core.options import OptionsStore


class OptionsAPI:
    """Wraps OptionsStore for plugin option access."""

    def __init__(self, store: OptionsStore) -> None:
        self._store = store

    def get(self, name: str) -> Any:
        """Return the current global value for an option."""
        return self._store.get(name)

    def set(self, name: str, value: Any) -> None:
        """Set an option globally."""
        self._store.set_global(name, value)

    def define(
        self, name: str, type_: type, default: Any, scope: tuple[str, ...] = ("global",), validator=None, doc: str = ""
    ) -> None:
        """Register a plugin-defined option."""
        self._store.define(name, type_, default, scope, validator, doc)
