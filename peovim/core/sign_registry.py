"""
core.sign_registry — SignRegistry: global sign type registration

Sign types are registered by plugins (e.g. 'gitsigns.add', 'lsp.error').
The sign column in window_renderer.py looks up sign chars here.
"""

from __future__ import annotations

from dataclasses import dataclass

from peovim.core.style import Style


@dataclass
class SignType:
    """A registered sign type."""

    name: str
    char: str  # 1-2 chars displayed in the sign column
    style: Style


class SignRegistry:
    """Global registry of sign types."""

    def __init__(self) -> None:
        self._types: dict[str, SignType] = {}

    def register(self, name: str, char: str, style: Style) -> None:
        """Register a sign type by name."""
        self._types[name] = SignType(name=name, char=char, style=style)

    def get(self, name: str) -> SignType | None:
        """Look up a sign type by name. Returns None if not found."""
        return self._types.get(name)

    def list_types(self) -> list[str]:
        """Return sorted list of registered type names."""
        return sorted(self._types.keys())
