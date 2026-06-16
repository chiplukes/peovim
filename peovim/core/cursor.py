"""
core.cursor — Cursor position with virtual column tracking

Tracks (line, col) and a virtual_col used by j/k to preserve column across
shorter lines, matching Vim's cursor movement semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.core.buffer import PieceTable


class Cursor:
    """
    Editor cursor. Holds (line, col) in character coordinates.

    virtual_col: the "target" column used when moving vertically with j/k.
    It is updated on any explicit horizontal move and preserved across
    vertical moves that clamp the column.
    """

    def __init__(self) -> None:
        self.line: int = 0
        self.col: int = 0
        self.virtual_col: int = 0

    # ------------------------------------------------------------------
    # Primary move operation
    # ------------------------------------------------------------------

    def move_to(self, line: int, col: int) -> None:
        """Move cursor to (line, col) and update virtual_col."""
        self.line = line
        self.col = col
        self.virtual_col = col

    # ------------------------------------------------------------------
    # Clamping
    # ------------------------------------------------------------------

    def clamp(self, buf: PieceTable, normal_mode: bool = True) -> None:
        """Clamp (line, col) to valid positions within buf.

        In normal mode, the last valid col on a non-empty line is len-1.
        In insert mode, col may equal line length (cursor after last char).
        """
        max_line = buf.line_count() - 1
        self.line = max(0, min(self.line, max_line))

        line_bytes = buf.get_line_bytes(self.line)
        line_len = len(line_bytes.decode("utf-8", errors="replace"))

        max_col = (max(0, line_len - 1) if line_len > 0 else 0) if normal_mode else line_len

        self.col = max(0, min(self.col, max_col))

    # ------------------------------------------------------------------
    # Directional moves
    # ------------------------------------------------------------------

    def move_right(self, buf: PieceTable, normal_mode: bool = True) -> None:
        line_bytes = buf.get_line_bytes(self.line)
        line_len = len(line_bytes.decode("utf-8", errors="replace"))
        max_col = max(0, line_len - 1) if normal_mode else line_len
        new_col = min(self.col + 1, max_col)
        self.move_to(self.line, new_col)

    def move_left(self, buf: PieceTable) -> None:
        new_col = max(0, self.col - 1)
        self.move_to(self.line, new_col)

    def move_up(self, buf: PieceTable, preserve_virtual: bool = False) -> None:
        if self.line == 0:
            return
        target_col = self.virtual_col if preserve_virtual else self.col
        self.line -= 1
        self._apply_virtual_col(buf, target_col)

    def move_down(self, buf: PieceTable, preserve_virtual: bool = False) -> None:
        if self.line >= buf.line_count() - 1:
            return
        target_col = self.virtual_col if preserve_virtual else self.col
        self.line += 1
        self._apply_virtual_col(buf, target_col)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_virtual_col(self, buf: PieceTable, target_col: int) -> None:
        """Set col to min(target_col, line_len-1) while preserving virtual_col."""
        line_bytes = buf.get_line_bytes(self.line)
        line_len = len(line_bytes.decode("utf-8", errors="replace"))
        max_col = max(0, line_len - 1)
        self.col = min(target_col, max_col)
        self.virtual_col = target_col  # preserve even if clamped

    def __repr__(self) -> str:
        return f"Cursor(line={self.line}, col={self.col}, virtual_col={self.virtual_col})"
