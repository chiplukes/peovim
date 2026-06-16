"""
core.snapshot — BufferSnapshot and WindowSnapshot frozen dataclasses

Immutable point-in-time views of editor state, safe to pass to background
threads. Created on the main thread; background workers (syntax, renderer)
receive only these, never live Buffer/Window objects.

Thread ownership rules: see notes/architecture.md §Concurrency Model.
Field definitions: see notes/plan_piece_table.md §BufferSnapshot.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingEdit:
    """Geometry for one tree-sitter tree.edit() call."""

    start_byte: int
    old_end_byte: int
    new_end_byte: int
    start_row: int
    start_col: int
    old_end_row: int
    old_end_col: int
    new_end_row: int
    new_end_col: int


@dataclass(frozen=True)
class BufferSnapshot:
    """Immutable point-in-time view of a PieceTable. Safe for any thread."""

    pieces: tuple  # tuple[Piece, ...] — avoiding circular import
    original: bytes
    add: bytes
    version: int
    line_offsets: tuple  # tuple[int, ...]
    filetype: str = ""  # e.g. 'python', 'rust', '' = unknown
    pending_edits: tuple = ()  # tuple[PendingEdit, ...] — edits since last snapshot


@dataclass(frozen=True)
class WindowSnapshot:
    """Immutable view of a window's rendering state. Safe for any thread."""

    buffer_snapshot: BufferSnapshot
    cursor_line: int
    cursor_col: int  # byte offset within line
    scroll_line: int  # first visible line
    scroll_col: int  # first visible column
    width: int  # window width in cells
    height: int  # window height in cells
    options: dict  # treat as read-only; frozen at creation time
    closed_folds: tuple = ()  # tuple[(start_line, end_line), ...] sorted by start
