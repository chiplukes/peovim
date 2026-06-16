"""
core.history — UndoStack: Edit record groups

Manages undo/redo for a single Document. Each stack entry is a list[Edit]
representing one user-visible undo step. Supports compound edits (multiple
operations grouped into one step via a context manager).

See notes/architecture.md for the component design overview.
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from typing import TYPE_CHECKING

from peovim.core.buffer import Edit

if TYPE_CHECKING:
    from peovim.core.buffer import PieceTable


class UndoStack:  # cm:3a6e9c
    """
    Undo/redo for one Document.

    Each entry is a list[Edit] representing one user-visible undo step.
    Undo reverses the edits in reverse order. Redo re-applies them forward.
    """

    def __init__(self, max_depth: int = 10_000) -> None:
        self._stack: deque[list[Edit]] = deque(maxlen=max_depth)
        self._redo: deque[list[Edit]] = deque(maxlen=max_depth)
        self._open: list[Edit] | None = None  # open compound group
        self._compound_depth: int = 0  # nesting level for reentrant compound()

    # ------------------------------------------------------------------
    # Push
    # ------------------------------------------------------------------

    def push(self, edit: Edit) -> None:
        """Push a single edit. If compound is open, append to it."""
        self._redo.clear()
        if self._open is not None:
            self._open.append(edit)
        else:
            self._stack.append([edit])

    # ------------------------------------------------------------------
    # Compound edits
    # ------------------------------------------------------------------

    def begin_compound(self) -> None:
        """Open a compound group. Reentrant: inner calls extend the outer group."""
        self._compound_depth += 1
        if self._compound_depth == 1:
            self._open = []

    def end_compound(self) -> None:
        """Close a compound group. Only commits to the undo stack at depth 0."""
        if self._compound_depth <= 0:
            return
        self._compound_depth -= 1
        if self._compound_depth == 0:
            if self._open:
                self._stack.append(self._open)
            self._open = None

    @contextmanager
    def compound(self):
        """Context manager for a compound edit group. Reentrant."""
        self.begin_compound()
        try:
            yield
        finally:
            self.end_compound()

    # ------------------------------------------------------------------
    # Undo / redo
    # ------------------------------------------------------------------

    def undo(self, table: PieceTable) -> list[Edit] | None:
        """Undo the last step. Returns the reversed group, or None if empty."""
        if not self._stack:
            return None
        group = self._stack.pop()
        for edit in reversed(group):
            self._apply(table, self._invert(edit))
        self._redo.append(group)
        return group

    def redo(self, table: PieceTable) -> list[Edit] | None:
        """Redo the last undone step."""
        if not self._redo:
            return None
        group = self._redo.pop()
        for edit in group:
            self._apply(table, edit)
        self._stack.append(group)
        return group

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _invert(edit: Edit) -> Edit:
        return Edit(
            kind="delete" if edit.kind == "insert" else "insert",
            pos=edit.pos,
            text=edit.text,
        )

    @staticmethod
    def _apply(table: PieceTable, edit: Edit) -> None:
        """Apply an edit directly to the PieceTable (bypasses undo recording)."""
        if edit.kind == "insert":
            table.insert(edit.pos, edit.text)
        else:
            table.delete(edit.pos, len(edit.text))
