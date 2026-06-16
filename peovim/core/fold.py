"""
core.fold — FoldRange and FoldStore: manual fold management

FoldStore lives on Window (window-local). Folds are identified by their
start line. A fold is open or closed; only closed folds affect rendering
and cursor navigation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FoldRange:
    start_line: int
    end_line: int
    open: bool = False  # False = closed (renders as one indicator line)


class FoldStore:
    """Manages manual folds for a single window."""

    def __init__(self) -> None:
        self._folds: list[FoldRange] = []

    # ------------------------------------------------------------------
    # Create / delete
    # ------------------------------------------------------------------

    def create(self, start: int, end: int) -> None:
        """Create a closed fold covering [start, end].

        Any existing fold wholly contained in [start, end] is removed first
        so we don't end up with nested manual folds.
        """
        if end < start:
            start, end = end, start
        self._folds = [f for f in self._folds if not (f.start_line >= start and f.end_line <= end)]
        self._folds.append(FoldRange(start, end, open=False))
        self._folds.sort(key=lambda f: f.start_line)

    def delete(self, line: int) -> None:
        """Delete the fold that contains *line* (if any)."""
        fold = self.fold_at(line)
        if fold is not None:
            self._folds.remove(fold)

    # ------------------------------------------------------------------
    # Open / close / toggle
    # ------------------------------------------------------------------

    def open(self, line: int) -> None:
        fold = self.fold_at(line)
        if fold is not None:
            fold.open = True

    def close(self, line: int) -> None:
        fold = self.fold_at(line)
        if fold is not None:
            fold.open = False

    def toggle(self, line: int) -> None:
        fold = self.fold_at(line)
        if fold is not None:
            fold.open = not fold.open

    def open_all(self) -> None:
        for f in self._folds:
            f.open = True

    def close_all(self) -> None:
        for f in self._folds:
            f.open = False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def fold_at(self, line: int) -> FoldRange | None:
        """Return the fold whose range contains *line*, or None."""
        for f in self._folds:
            if f.start_line <= line <= f.end_line:
                return f
        return None

    def is_folded(self, line: int) -> bool:
        """Return True if *line* is inside a closed fold's body (not the header)."""
        return any(not f.open and f.start_line < line <= f.end_line for f in self._folds)

    def fold_header(self, line: int) -> FoldRange | None:
        """Return the fold if *line* is the header (start_line) of a closed fold."""
        for f in self._folds:
            if not f.open and f.start_line == line:
                return f
        return None

    def closed_folds(self) -> list[tuple[int, int]]:
        """Return sorted list of (start_line, end_line) for all closed folds."""
        return [(f.start_line, f.end_line) for f in self._folds if not f.open]

    def __len__(self) -> int:
        return len(self._folds)
