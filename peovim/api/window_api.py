"""
WindowAPI — cursor, scroll, and options for a single window
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.buffer_api import BufferAPI
    from peovim.core.window import Window
    from peovim.core.workspace import Workspace
    from peovim.modal.dispatcher import ActionDispatcher
    from peovim.modal.engine import ModalEngine


class WindowAPI:
    """Public API for a single editor window."""

    def __init__(
        self,
        window: Window,
        buf_api: BufferAPI,
        engine: ModalEngine | None = None,
        dispatcher: ActionDispatcher | None = None,
        workspace: Workspace | None = None,
    ) -> None:
        self._window = window
        self._buf = buf_api
        self._engine = engine
        self._dispatcher = dispatcher
        self._workspace = workspace

    def buffer(self) -> BufferAPI:
        return self._buf

    @property
    def win_id(self) -> int:
        """Return a stable identity for this window during its lifetime."""
        return id(self._window)

    @property
    def cursor(self) -> tuple[int, int]:
        """Return (line, col) cursor position (0-based)."""
        return self._window.cursor.line, self._window.cursor.col

    def set_cursor(self, line: int, col: int) -> None:
        """Move cursor to (line, col)."""
        from peovim.modal.engine import Mode

        self._window.cursor.move_to(line, col)
        self._window.follow_cursor = True
        normal_mode = True
        if self._engine is not None:
            normal_mode = self._engine.mode not in {Mode.INSERT, Mode.REPLACE}
        self._window.cursor.clamp(self._window.document._table, normal_mode=normal_mode)
        if self._engine is not None and getattr(self._engine, "_document", None) is self._window.document:
            self._engine.set_cursor(self._window.cursor.line, self._window.cursor.col)

    def scroll_to_cursor(self) -> None:
        """Adjust scroll so the cursor is visible."""
        self._window.scroll_to_cursor()
        if self._engine is not None and getattr(self._engine, "_document", None) is self._window.document:
            self._engine.set_scroll(self._window.scroll_line)

    def set_scroll_line(self, line: int) -> None:
        """Set the first visible line for the window."""
        self._window.scroll_line = max(0, line)
        self._window.follow_cursor = False
        if self._engine is not None and getattr(self._engine, "_document", None) is self._window.document:
            self._engine.set_scroll(self._window.scroll_line)

    @property
    def scroll_offset(self) -> tuple[int, int]:
        """Return (scroll_line, scroll_col) — the top-left of the visible area."""
        return self._window.scroll_line, self._window.scroll_col

    def visible_range(self) -> tuple[int, int]:
        """Return (first_visible_line, last_visible_line)."""
        start = self._window.scroll_line
        end = start + self._window.height - 1
        return start, min(end, self._window.document.line_count() - 1)

    def get_width(self) -> int:
        return self._window.width

    def get_height(self) -> int:
        return self._window.height

    def is_valid(self) -> bool:
        return True

    def is_focused(self) -> bool:
        """Return True if this window is the active window in its tab."""
        if self._workspace is None:
            return False
        return self._workspace.active_window is self._window

    def get_option(self, name: str) -> Any:
        return self._window.options.get(name)

    def set_option(self, name: str, value: Any) -> None:
        self._window.options[name] = value
        if name == "fileformat":
            self._window.document.set_fileformat(value)

    # ------------------------------------------------------------------
    # Visual selection
    # ------------------------------------------------------------------

    def get_visual_selection(self) -> tuple[str, tuple[int, int], tuple[int, int]] | None:
        """Return the current or last visual selection.

        Returns (mode_name, (start_line, start_col), (end_line, end_col)) where
        mode_name is 'char', 'line', or 'block'.  Returns None if no selection.
        The returned range is always in (anchor <= cursor) order.
        """
        if self._engine is None:
            return None

        from peovim.modal.engine import Mode

        mode = self._engine.mode
        active_visual = mode in {Mode.VISUAL_CHAR, Mode.VISUAL_LINE, Mode.VISUAL_BLOCK}

        if active_visual:
            anchor: tuple[int, int] | None = getattr(self._engine, "_visual_anchor", None)
            cursor: tuple[int, int] | None = getattr(self._engine, "_cursor", None)
            if anchor is not None and cursor is not None:
                aline, acol = anchor
                cline, ccol = cursor
                mode_map = {
                    Mode.VISUAL_CHAR: "char",
                    Mode.VISUAL_LINE: "line",
                    Mode.VISUAL_BLOCK: "block",
                }
                mode_name = mode_map.get(mode, "char")
                if (aline, acol) <= (cline, ccol):
                    return mode_name, (aline, acol), (cline, ccol)
                return mode_name, (cline, ccol), (aline, acol)

        last = getattr(self._engine, "_last_visual_selection", None)
        if last is not None:
            lmode, start, end = last
            mode_map = {
                Mode.VISUAL_CHAR: "char",
                Mode.VISUAL_LINE: "line",
                Mode.VISUAL_BLOCK: "block",
            }
            return mode_map.get(lmode, "char"), start, end

        return None

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def split(self, direction: str = "v", buffer: Any = None) -> WindowAPI:
        """Split this window. direction='v' (vertical/side-by-side) or 'h' (horizontal/stacked).

        Optionally opens `buffer` (a BufferAPI) in the new pane.
        Returns a WindowAPI for the newly created window.
        """
        if self._workspace is None or self._dispatcher is None:
            raise RuntimeError("WindowAPI has no workspace/dispatcher — cannot split")

        tab = self._workspace.active_tab
        if tab.active_window is not self._window:
            tab.focus_window(self._window)

        new_win = tab.split_horizontal() if direction == "h" else tab.split_vertical()

        if buffer is not None:
            doc = getattr(buffer, "_doc", None)
            if doc is not None:
                new_win.document = doc

        self._dispatcher.window = new_win

        from peovim.api.buffer_api import BufferAPI as _BufAPI
        from peovim.core.decorations_store import DecorationsStore
        from peovim.core.sign_registry import SignRegistry

        # Reuse existing decorations/sign_registry from dispatcher if available
        es = getattr(self._dispatcher, "_editor_state", None)
        decs = getattr(es, "decorations", DecorationsStore()) if es else DecorationsStore()
        signs = getattr(es, "sign_registry", SignRegistry()) if es else SignRegistry()
        buf = _BufAPI(new_win.document, decs, signs, self._dispatcher)
        return WindowAPI(new_win, buf, self._engine, self._dispatcher, self._workspace)

    def close(self) -> None:
        """Close this window. Raises ValueError if it is the last window in the tab."""
        if self._workspace is None:
            raise RuntimeError("WindowAPI has no workspace — cannot close")

        tab = self._workspace.active_tab
        if tab.active_window is not self._window:
            tab.focus_window(self._window)
        tab.close_active()

        active = self._workspace.active_window
        if self._dispatcher is not None:
            self._dispatcher.window = active

    def focus(self) -> None:
        """Make this window the active window."""
        if self._workspace is None:
            raise RuntimeError("WindowAPI has no workspace — cannot focus")

        for tab in self._workspace.tabs:
            wins = tab.all_windows()
            if self._window in wins:
                self._workspace.active_tab_index = self._workspace.tabs.index(tab)
                tab.focus_window(self._window)
                if self._dispatcher is not None:
                    self._dispatcher.window = self._window
                return
        raise ValueError("Window not found in workspace")
