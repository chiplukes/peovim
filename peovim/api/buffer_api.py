"""
BufferAPI — buffer content, decorations, and lifecycle
"""

from __future__ import annotations

import pathlib
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.core.decorations_store import DecorationsStore
    from peovim.core.document import Document
    from peovim.core.sign_registry import SignRegistry


class BufferAPI:  # cm:d8c7a3
    """
    Public API for a single buffer (Document).

    Wraps Document content access and DecorationsStore mutations.
    buf_id = id(document) for Phase 6.
    """

    def __init__(
        self,
        document: Document,
        decorations: DecorationsStore,
        sign_registry: SignRegistry,
        dispatcher: Any = None,
    ) -> None:
        self._doc = document
        self._decorations = decorations
        self._sign_registry = sign_registry
        self._dispatcher = dispatcher
        self._batch_actions: list | None = None  # None = not in a batch

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def buf_id(self) -> int:
        return id(self._doc)

    @property
    def path(self) -> pathlib.Path | None:
        return self._doc.path

    @property
    def name(self) -> str:
        if self._doc.path is not None:
            return self._doc.path.name
        return getattr(self._doc, "name", "") or "[Scratch]"

    @property
    def filetype(self) -> str:
        return getattr(self._doc, "filetype", "") or ""

    @property
    def encoding(self) -> str:
        return getattr(self._doc, "encoding", "utf-8")

    @property
    def line_ending(self) -> str:
        return getattr(self._doc, "line_ending", "\n")

    @property
    def is_readonly(self) -> bool:
        return bool(getattr(self._doc, "readonly", False))

    @property
    def is_listed(self) -> bool:
        return bool(getattr(self._doc, "listed", True))

    def set_listed(self, listed: bool) -> None:
        self._doc.listed = listed  # type: ignore[attr-defined]

    @property
    def version(self) -> int:
        return self._doc.version

    def is_valid(self) -> bool:
        return True

    def is_modified(self) -> bool:
        return self._doc.dirty

    # ------------------------------------------------------------------
    # Content access
    # ------------------------------------------------------------------

    def line_count(self) -> int:
        return self._doc.line_count()

    def get_line(self, n: int) -> str:
        """Return line n (0-based) without trailing newline."""
        return self._doc.get_line(n)

    def get_lines(self, start: int = 0, end: int | None = None) -> list[str]:
        """Return lines [start, end) as a list of strings."""
        count = self._doc.line_count()
        if end is None:
            end = count
        end = min(end, count)
        return [self._doc.get_line(i) for i in range(start, end)]

    def get_text(self) -> str:
        """Return the full buffer content as a string."""
        lines = self.get_lines()
        return "\n".join(lines)

    def set_text(self, text: str) -> None:
        """Replace the full buffer content without routing through edit actions."""
        self._doc.load_string(text)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Save buffer to disk. Raises ValueError if no path is set."""
        self._doc.save()
        self._emit("buffer_saved")

    def save_as(self, path: str | pathlib.Path) -> None:
        """Save buffer to a new path and update self.path."""
        self._doc.save_as(pathlib.Path(path))
        self._emit("buffer_saved")

    def reload(self) -> None:
        """Reload from disk, discarding unsaved changes."""
        self._doc.reload()

    def set_filetype(self, ft: str) -> None:
        """Override the detected filetype."""
        self._doc.filetype = ft

    def get_option(self, name: str) -> Any:
        """Return a buffer-local option. Buffer-local options are stored on the document."""
        return getattr(self._doc, f"_opt_{name}", None)

    def set_option(self, name: str, value: Any) -> None:
        """Set a buffer-local option."""
        setattr(self._doc, f"_opt_{name}", value)

    def apply_edits(self, edits: list) -> None:
        """Apply a list of TextEdit tuples atomically as one undo step.

        Each edit is (start_line, start_col, end_line, end_col, new_text).
        Applied in reverse line order (LSP convention) so earlier edits
        don't shift the positions of later ones.
        """
        sorted_edits = sorted(edits, key=lambda e: (e[0], e[1]), reverse=True)
        with self.batch():
            for edit in sorted_edits:
                sl, sc, el, ec, text = edit
                self.replace(sl, sc, el, ec, text)

    def set_lines(self, start: int, end: int, lines: list[str]) -> None:
        """Replace lines [start, end) with the given list of strings."""
        new_text = "\n".join(lines)
        end_clamped = min(end, self._doc.line_count())
        if end_clamped <= start:
            self.insert(start, 0, new_text + ("\n" if lines else ""))
        else:
            last_line = self._doc.get_line(end_clamped - 1)
            self.replace(start, 0, end_clamped - 1, len(last_line), new_text)

    def _emit(self, event: str) -> None:
        """Fire a buffer event on the editor event bus if available."""
        if self._dispatcher is None:
            return
        es = getattr(self._dispatcher, "_editor_state", None)
        bus = getattr(es, "event_bus", None) if es is not None else None
        if bus is not None:
            bus.emit(event, buf_id=self.buf_id)

    # ------------------------------------------------------------------
    # Decorations
    # ------------------------------------------------------------------

    def add_highlight(
        self, ns: str, start_line: int, start_col: int, end_line: int, end_col: int, style: Any, priority: int = 0
    ) -> int:
        """Add a highlight region. Returns dec_id."""
        from peovim.ui.decorations import HighlightRegion

        if not hasattr(style, "fg"):
            from peovim.core.style import Style

            style = Style(fg=style) if style else Style()
        dec = HighlightRegion(start_line, start_col, end_line, end_col, style, priority)
        return self._decorations.add(self.buf_id, ns, dec)

    def remove_highlight(self, ns: str, dec_id: int) -> None:
        """Remove a highlight by dec_id."""
        self._decorations.remove(self.buf_id, ns, dec_id)

    def add_sign(self, ns: str, line: int, sign_type_name: str, priority: int = 0) -> int:
        """Add a sign from a registered sign type. Returns dec_id."""
        from peovim.ui.decorations import Sign

        stype = self._sign_registry.get(sign_type_name)
        if stype is None:
            return -1
        dec = Sign(line=line, char=stype.char, style=stype.style, priority=priority)
        return self._decorations.add(self.buf_id, ns, dec)

    def add_sign_raw(self, ns: str, line: int, char: str, style: Any, priority: int = 0) -> int:
        """Add a sign with explicit char and style. Returns dec_id."""
        from peovim.ui.decorations import Sign

        if not hasattr(style, "fg"):
            from peovim.core.style import Style

            style = Style(fg=style) if style else Style()
        dec = Sign(line=line, char=char, style=style, priority=priority)
        return self._decorations.add(self.buf_id, ns, dec)

    def remove_sign(self, ns: str, dec_id: int) -> None:
        """Remove a sign by dec_id."""
        self._decorations.remove(self.buf_id, ns, dec_id)

    def add_virtual_text(self, ns: str, line: int, text: str, style: Any, priority: int = 0) -> int:
        """Add virtual text after a line. Returns dec_id."""
        from peovim.ui.decorations import VirtualText

        if not hasattr(style, "fg"):
            from peovim.core.style import Style

            style = Style(fg=style) if style else Style()
        dec = VirtualText(line=line, text=text, style=style, priority=priority)
        return self._decorations.add(self.buf_id, ns, dec)

    def remove_virtual_text(self, ns: str, dec_id: int) -> None:
        self._decorations.remove(self.buf_id, ns, dec_id)

    def add_virtual_line(self, ns: str, after_line: int, style: Any, count: int = 1) -> int:
        """Add `count` blank virtual lines after `after_line` (-1 = before line 0). Returns dec_id."""
        from peovim.ui.decorations import VirtualLine

        if not hasattr(style, "fg"):
            from peovim.core.style import Style

            style = Style(bg=style) if style else Style()
        dec = VirtualLine(after_line=after_line, style=style, count=count)
        return self._decorations.add(self.buf_id, ns, dec)

    def remove_virtual_line(self, ns: str, dec_id: int) -> None:
        self._decorations.remove(self.buf_id, ns, dec_id)

    def set_ghost_text(self, ns: str, line: int, col: int, text: str, style: Any = None) -> int:
        """Set faded inline ghost text at (line, col). Returns dec_id."""
        from peovim.ui.decorations import GhostText

        if style is None or not hasattr(style, "fg"):
            from peovim.core.style import Style

            style = Style(fg=(100, 100, 100)) if style is None else Style(fg=style)
        dec = GhostText(line=line, col=col, text=text, style=style)
        return self._decorations.add(self.buf_id, ns, dec)

    def clear_ghost_text(self, ns: str) -> None:
        """Remove all ghost text in namespace ns for this buffer."""
        self._decorations.clear_namespace(self.buf_id, ns)

    def clear_namespace(self, ns: str) -> None:
        """Remove all decorations in namespace ns for this buffer."""
        self._decorations.clear_namespace(self.buf_id, ns)

    def add_decoration(self, ns: str, dec: object) -> int:
        """Add a pre-built decoration object to namespace ns. Returns dec_id."""
        return self._decorations.add(self.buf_id, ns, dec)

    # ------------------------------------------------------------------
    # Text mutations (needed by text-editing plugins)
    # ------------------------------------------------------------------

    def insert(self, line: int, col: int, text: str) -> None:
        """Insert text at (line, col). Goes through dispatcher for full undo/dot-repeat tracking."""
        from peovim.modal.actions import InsertText

        self._dispatch_mutation([InsertText(line, col, text)])

    def delete(self, start_line: int, start_col: int, end_line: int, end_col: int) -> None:
        """Delete text from (start_line, start_col) to (end_line, end_col)."""
        from peovim.modal.actions import DeleteRange

        self._dispatch_mutation([DeleteRange(start_line, start_col, end_line, end_col)])

    def replace(self, start_line: int, start_col: int, end_line: int, end_col: int, text: str) -> None:
        """Replace the given range with text."""
        from peovim.modal.actions import ReplaceRange

        self._dispatch_mutation([ReplaceRange(start_line, start_col, end_line, end_col, text)])

    @contextmanager
    def batch(self):  # type: ignore[return]
        """
        Wrap mutations in a single undo/dot-repeat step.

        All insert/delete/replace calls inside the block are grouped into one
        CompoundAction, which means a single `u` undoes everything and `.` replays
        the whole batch.
        """
        if self._batch_actions is not None:
            # Already inside a batch — just accumulate into the outer one
            yield
            return
        self._batch_actions = []
        try:
            yield
        finally:
            collected = self._batch_actions
            self._batch_actions = None
            if collected and self._dispatcher is not None:
                from peovim.modal.actions import CompoundAction

                self._dispatcher.ensure_public_mutation_allowed("CompoundAction")
                saved = self._dispatcher._dot_repeat
                compound = CompoundAction(tuple(collected))
                if getattr(self._dispatcher, "_in_dispatch", False):
                    self._dispatcher._apply(compound)
                else:
                    self._dispatcher.dispatch([compound])
                self._dispatcher._dot_repeat = saved

    def _dispatch_mutation(self, actions: list) -> None:
        """Route mutations through batch accumulator or dispatcher."""
        if self._batch_actions is not None:
            self._batch_actions.extend(actions)
        elif self._dispatcher is not None:
            operation = type(actions[0]).__name__ if actions else "mutation"
            self._dispatcher.ensure_public_mutation_allowed(operation)
            self._dispatcher.dispatch(actions)
        else:
            # No dispatcher available (e.g. tests that construct BufferAPI directly)
            from peovim.modal.actions import DeleteRange, InsertText, ReplaceRange

            for a in actions:
                if isinstance(a, InsertText):
                    self._doc.insert(a.line, a.col, a.text)
                elif isinstance(a, DeleteRange):
                    self._doc.delete(a.start_line, a.start_col, a.end_line, a.end_col)
                elif isinstance(a, ReplaceRange):
                    self._doc.replace(a.start_line, a.start_col, a.end_line, a.end_col, a.new_text)
