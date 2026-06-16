"""
ui.ghost_text — GhostTextManager: inline suggestion state for AI completion.

Tracks the current set of inline completion candidates and exposes the active
GhostText decoration for the render pipeline.
"""

from __future__ import annotations

from collections.abc import Callable

from peovim.core.style import Style
from peovim.ui.decorations import GhostText

_GHOST_STYLE = Style(fg=(100, 100, 150))  # dim bluish-gray


class GhostTextManager:
    """Manages active inline completion suggestions (ghost text)."""

    def __init__(self) -> None:
        self._completions: list[dict] = []
        self._index: int = 0
        self._line: int = 0
        self._col: int = 0

    @property
    def active(self) -> bool:
        return bool(self._completions)

    def current_decorations(
        self, max_lines: int = 1, is_empty_line: Callable[[int], bool] | None = None
    ) -> list[GhostText]:
        """Return GhostText decorations for the current suggestion.

        max_lines controls how many suggestion lines to render.  Continuation
        lines (index > 0) are only emitted when is_empty_line(doc_line) is True,
        so they never obscure existing buffer content.
        """
        if not self._completions:
            return []
        display = self._completions[self._index].get("displayText", "")
        if not display:
            return []
        lines = display.split("\n")[:max_lines]
        result: list[GhostText] = []
        for i, ln in enumerate(lines):
            if not ln:
                continue
            doc_line = self._line + i
            col = self._col if i == 0 else 0
            if i > 0 and is_empty_line is not None and not is_empty_line(doc_line):
                break
            result.append(GhostText(line=doc_line, col=col, text=ln, style=_GHOST_STYLE))
        return result

    def set(self, line: int, col: int, completions: list[dict]) -> None:
        self._completions = [c for c in completions if c.get("displayText")]
        self._index = 0
        self._line = line
        self._col = col

    def clear(self) -> None:
        self._completions.clear()
        self._index = 0

    def accept(self) -> tuple[str, int, int] | None:
        """Return (text, range_start_line, range_start_col) for the current suggestion."""
        if not self._completions:
            return None
        comp = self._completions[self._index]
        text = comp.get("text", "")
        rng = comp.get("range", {})
        start = rng.get("start", {})
        start_line = start.get("line", self._line)
        start_col = start.get("character", self._col)
        return text, start_line, start_col

    def cycle_next(self) -> None:
        if self._completions:
            self._index = (self._index + 1) % len(self._completions)

    def cycle_prev(self) -> None:
        if self._completions:
            self._index = (self._index - 1) % len(self._completions)
