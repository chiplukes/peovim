"""
ModalAPI — read/write access to the modal engine's mode and visual state.

Plugins that need to inspect or change the current mode (e.g. flash jump,
visual-mode resumption after a jump) use this instead of reaching into the
private engine object.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.modal.engine import ModalEngine, Mode


class ModalAPI:
    """Public access to the modal engine's mode and visual-selection state."""

    def __init__(self, engine: ModalEngine) -> None:
        self._engine = engine

    def mode(self) -> Mode:
        """Return the current editor mode."""
        return self._engine.mode

    def visual_anchor(self) -> tuple[int, int]:
        """Return the visual selection anchor as (line, col).

        The anchor is preserved even after leaving visual mode so it can be
        restored by plugins (e.g. flash jump) that exit and re-enter visual mode.
        """
        return self._engine._visual_anchor

    def set_mode(self, mode: Any) -> None:
        """Set the engine mode (use Mode enum values)."""
        self._engine.set_mode(mode)

    def set_visual_anchor(self, line: int, col: int) -> None:
        """Set the visual selection anchor."""
        self._engine.set_visual_anchor(line, col)
