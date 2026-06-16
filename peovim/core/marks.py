"""
core.marks — Vim mark store

Buffer-local marks (a-z), global marks (A-Z), and special marks:
  `  last jump position    .  last change position
  [  start of last yank    ]  end of last yank
  <  visual selection start  >  visual selection end
"""

from __future__ import annotations

# Position is (line, col)
Position = tuple[int, int]

# Valid mark names
_LOCAL_MARKS = frozenset("abcdefghijklmnopqrstuvwxyz")
_GLOBAL_MARKS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_SPECIAL_MARKS = frozenset("`'.<>[]{}()")


class MarkStore:
    """
    Stores and retrieves mark positions.

    Local marks (a-z): per-buffer, cleared on buffer close.
    Global marks (A-Z): persistent across buffers (stored with buffer path).
    Special marks: set automatically by the editor (last jump, last change, etc.).
    """

    def __init__(self) -> None:
        # Local marks: name -> (line, col)
        self._local: dict[str, Position] = {}
        # Special marks: name -> (line, col)
        self._special: dict[str, Position] = {}
        # Global marks: name -> (path, line, col) — path can be empty string
        self._global: dict[str, tuple[str, int, int]] = {}

    def set(self, name: str, line: int, col: int) -> None:
        """Set a mark at (line, col). name is a single character."""
        if name in _LOCAL_MARKS:
            self._local[name] = (line, col)
        elif name in _GLOBAL_MARKS:
            self._global[name] = ("", line, col)
        elif name in _SPECIAL_MARKS:
            self._special[name] = (line, col)
        # Unknown mark names silently ignored

    def set_global(self, name: str, path: str, line: int, col: int) -> None:
        """Set a global mark (A-Z) with an associated file path."""
        if name in _GLOBAL_MARKS:
            self._global[name] = (path, line, col)

    def get(self, name: str) -> Position | None:
        """Return (line, col) for a local or special mark, or None if not set."""
        if name in _LOCAL_MARKS:
            return self._local.get(name)
        if name in _SPECIAL_MARKS:
            return self._special.get(name)
        if name in _GLOBAL_MARKS:
            entry = self._global.get(name)
            if entry is not None:
                _, line, col = entry
                return (line, col)
        return None

    def get_global(self, name: str) -> tuple[str, int, int] | None:
        """Return (path, line, col) for a global mark, or None if not set."""
        return self._global.get(name)

    def delete(self, name: str) -> None:
        """Delete a mark."""
        self._local.pop(name, None)
        self._special.pop(name, None)
        self._global.pop(name, None)

    def clear_local(self) -> None:
        """Clear all local marks (e.g., on buffer close)."""
        self._local.clear()

    def list_marks(self) -> dict[str, Position]:
        """Return all currently set local + special marks."""
        result: dict[str, Position] = {}
        result.update(self._local)
        result.update(self._special)
        return result
