"""
core.document — Document: buffer + file metadata + undo stack

Wraps PieceTable with path, encoding, line_ending, dirty flag, and UndoStack.
Exposes str-level API (get_line, insert at char col) converting to/from bytes.
Emits buffer_changed events. Handles file I/O (load, save, save_as, reload).

See notes/plan_piece_table.md §CRLF Policy and §Encoding Policy.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from charset_normalizer import from_bytes

from peovim.core.buffer import PieceTable
from peovim.core.history import UndoStack
from peovim.core.persistence import atomic_write_bytes
from peovim.core.snapshot import BufferSnapshot

_EOL_TO_FILEFORMAT = {"\n": "unix", "\r\n": "dos", "\r": "mac"}
_FILEFORMAT_TO_EOL = {value: key for key, value in _EOL_TO_FILEFORMAT.items()}


def _stat_fingerprint(path: Path | None) -> tuple[int, int] | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _scan_line_endings(raw: bytes) -> tuple[dict[str, int], list[str]]:
    counts = {"\n": 0, "\r\n": 0, "\r": 0}
    order: list[str] = []
    idx = 0
    while idx < len(raw):
        byte = raw[idx]
        if byte == 13:
            if idx + 1 < len(raw) and raw[idx + 1] == 10:
                counts["\r\n"] += 1
                if "\r\n" not in order:
                    order.append("\r\n")
                idx += 2
                continue
            counts["\r"] += 1
            if "\r" not in order:
                order.append("\r")
        elif byte == 10:
            counts["\n"] += 1
            if "\n" not in order:
                order.append("\n")
        idx += 1
    return counts, order


def _preferred_line_ending(counts: dict[str, int], order: list[str]) -> str:
    candidates = [(style, count) for style, count in counts.items() if count > 0]
    if not candidates:
        return "\n"
    ranked = sorted(
        candidates,
        key=lambda item: (-item[1], order.index(item[0]) if item[0] in order else len(order)),
    )
    return ranked[0][0]


def _normalize_text_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


class Document:  # cm:d5f3b8
    """
    A text document. Owns a PieceTable and UndoStack.

    All mutation methods (insert, delete, replace) operate in character/line
    coordinates and convert to byte offsets internally.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path: Path | None = path
        self.name: str | None = None  # display name for scratch buffers (no path)
        self.encoding: str = "utf-8"
        self.line_ending: str = "\n"
        self.had_mixed_line_endings: bool = False
        self.filetype: str = ""
        self._loaded_file_state: tuple[int, int] | None = _stat_fingerprint(path)

        self._table = PieceTable()
        self._table.clear()
        self._undo = UndoStack()
        self._changed_handlers: list[Callable[[Document], None]] = []
        self._change_counter: int = 0  # +1 on edit, -1 on undo, +1 on redo
        self._clean_counter: int = 0  # _change_counter value at last save/load

    @property
    def dirty(self) -> bool:
        return self._change_counter != self._clean_counter

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, path: Path) -> None:
        """Load from file. Detects encoding, line ending, and filetype."""
        self.path = path
        raw = path.read_bytes()
        self.load_bytes(raw)
        # Filetype from extension (shebang already set in load_bytes)
        if not self.filetype:
            from peovim.core.filetype import detect_filetype

            self.filetype = detect_filetype(str(path))
        self._loaded_file_state = _stat_fingerprint(path)

    def load_bytes(self, raw: bytes) -> None:
        """Load from raw bytes. Detects encoding and CRLF."""
        # Detect encoding
        if raw.startswith(b"\xef\xbb\xbf"):
            self.encoding = "utf-8-sig"
            raw = raw[3:]
        else:
            result = from_bytes(raw).best()
            self.encoding = result.encoding if result else "utf-8"

        # Detect and normalize line ending
        counts, order = _scan_line_endings(raw)
        self.had_mixed_line_endings = sum(1 for count in counts.values() if count > 0) > 1
        self.line_ending = _preferred_line_ending(counts, order)
        content = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

        # Ensure UTF-8 for internal storage
        try:
            text = content.decode(self.encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = content.decode("utf-8", errors="replace")
            self.encoding = "utf-8"

        utf8_content = text.encode("utf-8")
        self._table.load(utf8_content)
        self._undo = UndoStack()
        self._change_counter = 0
        self._clean_counter = 0
        # Detect filetype from shebang (extension detection done in load())
        from peovim.core.filetype import detect_filetype

        first_line = text.split("\n", 1)[0]
        self.filetype = detect_filetype(str(self.path) if self.path else None, first_line)
        self._loaded_file_state = None

    def load_string(self, text: str) -> None:
        """Load from a Python string (no encoding/CRLF detection)."""
        normalized = _normalize_text_line_endings(text)
        counts, order = _scan_line_endings(text.encode("utf-8"))
        self.had_mixed_line_endings = sum(1 for count in counts.values() if count > 0) > 1
        self.line_ending = _preferred_line_ending(counts, order)
        self._table.load(normalized.encode("utf-8"))
        self._undo = UndoStack()
        self._change_counter = 0
        self._clean_counter = 0
        from peovim.core.filetype import detect_filetype

        first_line = normalized.split("\n", 1)[0]
        self.filetype = detect_filetype(str(self.path) if self.path else None, first_line)
        self._loaded_file_state = _stat_fingerprint(self.path)

    def reload(self) -> None:
        """Reload from disk, discarding all changes."""
        if self.path is None:
            raise ValueError("Document has no path to reload from")
        self.load(self.path)
        self._notify_changed()

    def has_external_changes(self) -> bool:
        """Return True when the file-backed document differs from the last loaded/saved disk state."""
        if self.path is None or self._loaded_file_state is None:
            return False
        return _stat_fingerprint(self.path) != self._loaded_file_state

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def save(self, path: Path | None = None) -> str | None:
        """Save to disk. Uses self.path if path is None.

        Returns a warning string if the file could not be re-encoded in its
        original encoding and was saved as UTF-8 instead, otherwise None.
        """
        target = path or self.path
        if target is None:
            raise ValueError("No path to save to")
        data = self._table.get_bytes(0, self._table.total_bytes())
        if self.line_ending != "\n":
            data = data.replace(b"\n", self.line_ending.encode())
        warning: str | None = None
        if self.encoding not in ("utf-8", "utf-8-sig"):
            try:
                data = data.decode("utf-8").encode(self.encoding)
            except (LookupError, UnicodeEncodeError) as exc:
                warning = f"Warning: could not re-encode as {self.encoding} ({exc}); saved as UTF-8"
        # save policy: single-writer (the owning editor buffer is the only writer)
        atomic_write_bytes(target, data)
        self.path = target
        self._loaded_file_state = _stat_fingerprint(target)
        self._clean_counter = self._change_counter
        return warning

    def save_as(self, path: Path) -> None:
        self.path = path
        self.save()

    def mark_clean(self) -> None:
        """Mark the document as unmodified (dirty=False) without saving."""
        self._clean_counter = self._change_counter

    @property
    def fileformat(self) -> str:
        return _EOL_TO_FILEFORMAT.get(self.line_ending, "unix")

    def set_fileformat(self, fileformat: str) -> None:
        self.line_ending = _FILEFORMAT_TO_EOL.get(fileformat, "\n")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def version(self) -> int:
        return self._table.version

    def line_count(self) -> int:
        return self._table.line_count()

    # ------------------------------------------------------------------
    # String-level read access
    # ------------------------------------------------------------------

    def get_line(self, n: int) -> str:
        raw = self._table.get_line_bytes(n)
        return raw.decode("utf-8", errors="replace")

    def get_text(self) -> str:
        raw = self._table.get_bytes(0, self._table.total_bytes())
        return raw.decode("utf-8", errors="replace")

    def snapshot(self) -> BufferSnapshot:
        snap = self._table.snapshot()
        if self.filetype:
            # Return a new frozen copy with filetype stamped in
            from dataclasses import replace

            return replace(snap, filetype=self.filetype)
        return snap

    # ------------------------------------------------------------------
    # Column conversion
    # ------------------------------------------------------------------

    def char_to_byte(self, line: int, char_col: int) -> int:
        """Convert character column to byte offset within line."""
        line_bytes = self._table.get_line_bytes(line)
        line_str = line_bytes.decode("utf-8", errors="replace")
        prefix = line_str[:char_col]
        return len(prefix.encode("utf-8"))

    def byte_to_char(self, line: int, byte_col: int) -> int:
        """Convert byte offset within line to character column."""
        line_bytes = self._table.get_line_bytes(line)
        return len(line_bytes[:byte_col].decode("utf-8", errors="replace"))

    def _line_byte_offset(self, line: int, char_col: int) -> int:
        """Absolute byte offset in the PieceTable for (line, char_col)."""
        byte_col = self.char_to_byte(line, char_col)
        return self._table.byte_offset_of(line, byte_col)

    # ------------------------------------------------------------------
    # Mutations (str-level, record to undo stack)
    # ------------------------------------------------------------------

    def insert(self, line: int, col: int, text: str) -> None:
        """Insert text at (line, col) where col is a character offset."""
        text = _normalize_text_line_endings(text)
        pos = self._line_byte_offset(line, col)
        edit = self._table.insert(pos, text.encode("utf-8"))
        self._undo.push(edit)
        self._change_counter += 1
        self._notify_changed()

    def delete(self, start_line: int, start_col: int, end_line: int, end_col: int) -> None:
        """Delete text from (start_line, start_col) to (end_line, end_col) exclusive."""
        start_pos = self._line_byte_offset(start_line, start_col)
        end_pos = self._line_byte_offset(end_line, end_col)
        if end_pos <= start_pos:
            return
        edit = self._table.delete(start_pos, end_pos - start_pos)
        self._undo.push(edit)
        self._change_counter += 1
        self._notify_changed()

    def replace(self, start_line: int, start_col: int, end_line: int, end_col: int, text: str) -> None:
        """Replace range with new text as a compound edit."""
        text = _normalize_text_line_endings(text)
        with self._undo.compound():
            start_pos = self._line_byte_offset(start_line, start_col)
            end_pos = self._line_byte_offset(end_line, end_col)
            if end_pos > start_pos:
                edit = self._table.delete(start_pos, end_pos - start_pos)
                self._undo.push(edit)
            if text:
                edit = self._table.insert(start_pos, text.encode("utf-8"))
                self._undo.push(edit)
        self._change_counter += 1
        self._notify_changed()

    # ------------------------------------------------------------------
    # Undo / redo
    # ------------------------------------------------------------------

    def undo(self) -> tuple[int, int] | None:
        """Undo the last step.

        Returns (char_line, char_col) of the change site so the caller can
        move the cursor there, or None if there was nothing to undo.
        """
        result = self._undo.undo(self._table)
        if result is not None:
            self._change_counter -= 1
            self._notify_changed()
            return self._change_site(result[0].pos)
        return None

    def redo(self) -> tuple[int, int] | None:
        """Redo the last undone step.

        Returns (char_line, char_col) of the change site, or None if nothing
        to redo.
        """
        result = self._undo.redo(self._table)
        if result is not None:
            self._change_counter += 1
            self._notify_changed()
            return self._change_site(result[0].pos)
        return None

    def _change_site(self, byte_pos: int) -> tuple[int, int]:
        """Convert a byte offset to (char_line, char_col), clamped to current content."""
        pos = min(byte_pos, self._table.total_bytes())
        line, byte_col = self._table.line_col_of(pos)
        return (line, self.byte_to_char(line, byte_col))

    @contextmanager
    def compound_edit(self):
        """Context manager that groups all mutations into one undo step."""
        self._undo.begin_compound()
        try:
            yield
        finally:
            self._undo.end_compound()

    def begin_compound(self) -> None:
        """Open a compound edit group (reentrant). Pair with end_compound()."""
        self._undo.begin_compound()

    def end_compound(self) -> None:
        """Close a compound edit group opened with begin_compound()."""
        self._undo.end_compound()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def on_changed(self, handler: Callable[[Document], None]) -> None:
        self._changed_handlers.append(handler)

    def off_changed(self, handler: Callable[[Document], None]) -> None:
        self._changed_handlers.remove(handler)

    def _notify_changed(self) -> None:
        for handler in self._changed_handlers:
            handler(self)
