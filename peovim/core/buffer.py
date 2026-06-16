"""
core.buffer — PieceTable text storage

Stores buffer content as a sequence of spans (pieces) over two byte buffers
(original and add). Operates entirely in bytes; the Document layer handles
str/encoding. CRLF is never present here — Document normalises on load.

See notes/plan_piece_table.md for the complete algorithm specification.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Literal

from peovim.core.snapshot import BufferSnapshot, PendingEdit

# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------


@dataclass
class Piece:
    buf: Literal["original", "add"]
    start: int  # byte offset into that buffer
    length: int  # byte count
    newlines: int  # cached \n count (used for incremental line index, Phase 9)


@dataclass
class Edit:
    """Single atomic mutation — sufficient to undo or redo."""

    kind: Literal["insert", "delete"]
    pos: int  # byte offset in logical text at time of edit
    text: bytes  # bytes inserted or deleted


# ---------------------------------------------------------------------------
# PieceTable
# ---------------------------------------------------------------------------


class PieceTable:  # cm:1c7a4e
    """
    Piece-table text buffer. All content is stored as bytes (UTF-8).
    CRLF is never stored here — Document normalizes on load.
    """

    def __init__(self) -> None:
        self._original: bytes = b""
        self._add: bytearray = bytearray()
        self._pieces: list[Piece] = []
        self._piece_offsets: list[int] = []  # _piece_offsets[i] = logical byte start of _pieces[i]
        self._line_offsets: list[int] = [0]
        self._version: int = 0
        self._pending_edits: list[PendingEdit] = []

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def load(self, content: bytes) -> None:
        """Initialize from file content (UTF-8 bytes, CRLF already normalized)."""
        self._original = content
        self._add = bytearray()
        if content:
            self._pieces = [Piece("original", 0, len(content), content.count(b"\n"))]
            self._piece_offsets = [0]
        else:
            self._pieces = []
            self._piece_offsets = []
        self._rebuild_line_index()
        self._version = 0
        self._pending_edits = []

    def clear(self) -> None:
        """Reset to empty buffer."""
        self._original = b""
        self._add = bytearray()
        self._pieces = []
        self._piece_offsets = []
        self._line_offsets = [0]
        self._version = 0
        self._pending_edits = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def version(self) -> int:
        return self._version

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------

    def total_bytes(self) -> int:
        return sum(p.length for p in self._pieces)

    def line_count(self) -> int:
        return len(self._line_offsets)

    def get_bytes(self, start: int, end: int) -> bytes:
        assert 0 <= start <= end <= self.total_bytes()
        result = bytearray()
        if start == end or not self._pieces:
            return bytes(result)
        first = max(0, bisect.bisect_right(self._piece_offsets, start) - 1)
        for i in range(first, len(self._pieces)):
            piece_pos = self._piece_offsets[i]
            if piece_pos >= end:
                break
            piece = self._pieces[i]
            buf = self._original if piece.buf == "original" else self._add
            cut_start = piece.start + max(0, start - piece_pos)
            cut_end = piece.start + min(piece.length, end - piece_pos)
            result.extend(buf[cut_start:cut_end])
        return bytes(result)

    def get_line_bytes(self, n: int) -> bytes:
        assert 0 <= n < self.line_count()
        start = self._line_offsets[n]
        end = self._line_offsets[n + 1] if n + 1 < len(self._line_offsets) else self.total_bytes()
        data = self.get_bytes(start, end)
        return data.rstrip(b"\n")

    def byte_offset_of(self, line: int, col_bytes: int) -> int:
        assert 0 <= line < self.line_count()
        return self._line_offsets[line] + col_bytes

    def line_col_of(self, pos: int) -> tuple[int, int]:
        assert 0 <= pos <= self.total_bytes()
        line = bisect.bisect_right(self._line_offsets, pos) - 1
        col = pos - self._line_offsets[line]
        return (line, col)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def insert(self, pos: int, text: bytes) -> Edit:
        assert 0 <= pos <= self.total_bytes()
        assert len(text) > 0

        # Record edit geometry before the line index changes.
        start_point = self._point_of(pos)
        newlines = text.count(b"\n")
        if newlines == 0:
            new_end_point = (start_point[0], start_point[1] + len(text))
        else:
            new_end_point = (start_point[0] + newlines, len(text) - text.rfind(b"\n") - 1)
        self._pending_edits.append(
            PendingEdit(
                pos,
                pos,
                pos + len(text),
                start_point[0],
                start_point[1],
                start_point[0],
                start_point[1],
                new_end_point[0],
                new_end_point[1],
            )
        )

        piece_idx, offset = self._find_piece(pos)
        if piece_idx < len(self._pieces):
            self._split_piece(piece_idx, offset)
            insert_idx = piece_idx if offset == 0 else piece_idx + 1
        else:
            insert_idx = len(self._pieces)

        add_start = len(self._add)
        self._add.extend(text)

        # Coalesce with the previous piece when it's a contiguous add-buffer run.
        # Keeps the piece list O(1) for sequential typing instead of O(keystrokes).
        if (
            insert_idx > 0
            and self._pieces[insert_idx - 1].buf == "add"
            and self._pieces[insert_idx - 1].start + self._pieces[insert_idx - 1].length == add_start
        ):
            prev = self._pieces[insert_idx - 1]
            self._pieces[insert_idx - 1] = Piece(
                "add",
                prev.start,
                prev.length + len(text),
                prev.newlines + text.count(b"\n"),
            )
            # All pieces at insert_idx and beyond shift right by len(text).
            for i in range(insert_idx, len(self._piece_offsets)):
                self._piece_offsets[i] += len(text)
        else:
            new_piece = Piece("add", add_start, len(text), text.count(b"\n"))
            self._pieces.insert(insert_idx, new_piece)
            # New piece starts at pos; all subsequent pieces shift right by len(text).
            self._piece_offsets.insert(insert_idx, pos)
            for i in range(insert_idx + 1, len(self._piece_offsets)):
                self._piece_offsets[i] += len(text)

        self._update_line_index_insert(pos, text)
        self._version += 1
        return Edit("insert", pos, text)

    def delete(self, pos: int, length: int) -> Edit:
        assert pos >= 0
        assert length > 0
        assert pos + length <= self.total_bytes()

        # Record edit geometry before the line index changes.
        start_point = self._point_of(pos)
        old_end_point = self._point_of(pos + length)
        self._pending_edits.append(
            PendingEdit(
                pos,
                pos + length,
                pos,
                start_point[0],
                start_point[1],
                old_end_point[0],
                old_end_point[1],
                start_point[0],
                start_point[1],
            )
        )

        deleted = self.get_bytes(pos, pos + length)

        # Split at start of deletion
        start_idx, start_offset = self._find_piece(pos)
        if start_idx < len(self._pieces):
            self._split_piece(start_idx, start_offset)
            del_start = start_idx if start_offset == 0 else start_idx + 1
        else:
            del_start = len(self._pieces)

        # Split at end of deletion (piece indices may have shifted by split above)
        end_idx, end_offset = self._find_piece(pos + length)
        if end_idx < len(self._pieces):
            self._split_piece(end_idx, end_offset)
            del_end = end_idx if end_offset == 0 else end_idx + 1
        else:
            del_end = len(self._pieces)

        del self._pieces[del_start:del_end]
        del self._piece_offsets[del_start:del_end]
        for i in range(del_start, len(self._piece_offsets)):
            self._piece_offsets[i] -= length

        self._update_line_index_delete(pos, length)
        self._version += 1
        return Edit("delete", pos, deleted)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> BufferSnapshot:
        """Produce an immutable snapshot safe for background threads."""
        edits = tuple(self._pending_edits)
        self._pending_edits.clear()
        return BufferSnapshot(
            pieces=tuple(self._pieces),
            original=self._original,
            add=bytes(self._add),
            version=self._version,
            line_offsets=tuple(self._line_offsets),
            pending_edits=edits,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_piece(self, pos: int) -> tuple[int, int]:
        """Return (piece_index, offset_within_piece) for byte offset pos. O(log pieces)."""
        if not self._piece_offsets:
            return (0, 0)
        i = bisect.bisect_right(self._piece_offsets, pos) - 1
        if i >= len(self._pieces):
            return (len(self._pieces), 0)
        return (i, pos - self._piece_offsets[i])

    def _split_piece(self, idx: int, offset: int) -> None:
        """Split _pieces[idx] at offset. No-op if offset is 0 or piece.length."""
        piece = self._pieces[idx]
        if offset == 0 or offset == piece.length:
            return
        buf = self._original if piece.buf == "original" else self._add
        left_data = buf[piece.start : piece.start + offset]
        right_data = buf[piece.start + offset : piece.start + piece.length]
        left = Piece(piece.buf, piece.start, offset, left_data.count(b"\n"))
        right = Piece(piece.buf, piece.start + offset, piece.length - offset, right_data.count(b"\n"))
        self._pieces[idx : idx + 1] = [left, right]
        self._piece_offsets.insert(idx + 1, self._piece_offsets[idx] + offset)

    def _point_of(self, byte_pos: int) -> tuple[int, int]:
        """Return (row, col) for a byte offset using the current _line_offsets."""
        row = bisect.bisect_right(self._line_offsets, byte_pos) - 1
        return (row, byte_pos - self._line_offsets[row])

    def _update_line_index_insert(self, pos: int, text: bytes) -> None:
        """Incrementally update _line_offsets after inserting text at byte pos."""
        n = len(text)
        shift_from = bisect.bisect_right(self._line_offsets, pos)
        for i in range(shift_from, len(self._line_offsets)):
            self._line_offsets[i] += n
        new_offsets = [pos + i + 1 for i, byte in enumerate(text) if byte == 0x0A]
        if new_offsets:
            self._line_offsets[shift_from:shift_from] = new_offsets

    def _update_line_index_delete(self, pos: int, length: int) -> None:
        """Incrementally update _line_offsets after deleting length bytes at byte pos."""
        remove_start = bisect.bisect_right(self._line_offsets, pos)
        remove_end = bisect.bisect_right(self._line_offsets, pos + length)
        del self._line_offsets[remove_start:remove_end]
        for i in range(remove_start, len(self._line_offsets)):
            self._line_offsets[i] -= length

    def _rebuild_line_index(self) -> None:
        """Rebuild _line_offsets. O(total_bytes). Called after every mutation."""
        offsets = [0]
        pos = 0
        for piece in self._pieces:
            buf = self._original if piece.buf == "original" else self._add
            data = buf[piece.start : piece.start + piece.length]
            for i, byte in enumerate(data):
                if byte == 0x0A:  # b'\n'
                    offsets.append(pos + i + 1)
            pos += piece.length
        self._line_offsets = offsets
