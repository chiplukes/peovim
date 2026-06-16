"""
core.jumplist — Jump list: Ctrl-o / Ctrl-i navigation

Global jump list tracking cursor positions. Max depth: 100 entries.
Plugins push entries via editor.jumplist.push().
"""

from __future__ import annotations

_MAX_DEPTH = 100


class JumpList:  # cm:6e4b9a
    """
    Ordered list of (path, line, col, scroll_line) positions for Ctrl-o / Ctrl-i navigation.

    push() adds a position. back() moves backward (Ctrl-o). forward() moves
    forward (Ctrl-i). Consecutive duplicate positions are collapsed.
    path defaults to "" for same-file entries.
    scroll_line records the viewport so the exact view is restored on jump.
    """

    def __init__(self, max_depth: int = _MAX_DEPTH) -> None:
        self._entries: list[tuple[str, int, int, int]] = []
        self._index: int = -1  # current position in list (-1 = empty / at head)
        self._max_depth = max_depth

    def push(self, line: int, col: int, path: str = "", scroll_line: int = 0) -> None:
        """Add a new position. Truncates any forward entries after current."""
        pos = (path, line, col, scroll_line)
        # Deduplicate: same path/line/col regardless of scroll
        if self._index >= 0:
            e = self._entries[self._index]
            if e[0] == path and e[1] == line and e[2] == col:
                return
        # Truncate forward history
        if self._index < len(self._entries) - 1:
            self._entries = self._entries[: self._index + 1]
        self._entries.append(pos)
        # Enforce max depth by dropping oldest entries
        if len(self._entries) > self._max_depth:
            excess = len(self._entries) - self._max_depth
            self._entries = self._entries[excess:]
        self._index = len(self._entries) - 1

    def back(self) -> tuple[str, int, int, int] | None:
        """Move backward (Ctrl-o). Returns (path, line, col, scroll_line) or None."""
        if self._index <= 0:
            return None
        self._index -= 1
        return self._entries[self._index]

    def forward(self) -> tuple[str, int, int, int] | None:
        """Move forward (Ctrl-i). Returns (path, line, col, scroll_line) or None."""
        if self._index >= len(self._entries) - 1:
            return None
        self._index += 1
        return self._entries[self._index]

    def current(self) -> tuple[str, int, int, int] | None:
        """Return current position without moving."""
        if self._index < 0 or not self._entries:
            return None
        return self._entries[self._index]

    def __len__(self) -> int:
        return len(self._entries)

    def can_go_back(self) -> bool:
        return self._index > 0

    def can_go_forward(self) -> bool:
        return self._index < len(self._entries) - 1
