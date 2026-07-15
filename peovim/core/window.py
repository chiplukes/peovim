"""
core.window — Window (viewport): cursor + scroll offset + Document reference

A Window is a view into a Document. Multiple windows may reference the same
Document. Holds cursor position, scroll offset, and window-local options.
Has no rendering logic — pure state.

See notes/architecture.md for the Buffer/Window/Tab Model.
"""

from __future__ import annotations

from peovim.core.cursor import Cursor
from peovim.core.document import Document
from peovim.core.fold import FoldStore
from peovim.core.snapshot import WindowSnapshot
from peovim.ui.text_layout import display_col_to_logical_col, logical_col_to_display_col


class Window:  # cm:8f2d5b
    """
    A viewport into a Document.

    Owns a Cursor and scroll offsets. Multiple Window objects may share the
    same Document (split views). The Window does not render — that is done
    by WindowRenderer in peovim/ui/window_renderer.py.
    """

    def __init__(self, document: Document, width: int = 80, height: int = 24) -> None:
        self.document: Document = document
        self.cursor: Cursor = Cursor()
        self.scroll_line: int = 0  # first visible line
        self.scroll_col: int = 0  # first visible column (byte offset)
        self.follow_cursor: bool = True
        self.width: int = width
        self.height: int = height
        self.options: dict = {}  # window-local options
        self.folds: FoldStore = FoldStore()

    # ------------------------------------------------------------------
    # Scroll helpers
    # ------------------------------------------------------------------

    def scroll_to_cursor(self, text_width: int | None = None, *, center: bool = False) -> None:
        """Adjust scroll_line and scroll_col so cursor is visible, respecting scrolloff/sidescrolloff.

        text_width: visible text columns (window width minus gutter). When None,
        self.width is used as a conservative fallback — callers with accurate
        gutter info should pass the real value.

        center: when True and the cursor is outside the current viewport, center the
        cursor in the window instead of just ensuring it is visible.
        """
        self.follow_cursor = True

        # --- Vertical ---
        so = int(self.options.get("scrolloff", 0))
        target = self.cursor.line
        if center and (target < self.scroll_line or target >= self.scroll_line + self.height):
            self.scroll_line = max(0, target - self.height // 2)
        elif target - so < self.scroll_line:
            self.scroll_line = max(0, target - so)
        elif target + so >= self.scroll_line + self.height:
            self.scroll_line = target + so - self.height + 1
        self.scroll_line = max(0, self.scroll_line)

        # --- Horizontal ---
        # Use the provided text_width (excl. gutter), falling back to self.width.
        sso = int(self.options.get("sidescrolloff", 0))
        tabstop = int(self.options.get("tabstop", 4) or 4)
        tw = max(1, text_width if text_width is not None and text_width > 0 else self.width)
        try:
            line_text = self.document.get_line(self.cursor.line)
        except Exception:
            return
        cursor_dcol = logical_col_to_display_col(line_text, self.cursor.col, tabstop)
        scroll_dcol = logical_col_to_display_col(line_text, self.scroll_col, tabstop)
        if cursor_dcol < scroll_dcol + sso:
            new_scroll_dcol = max(0, cursor_dcol - sso)
            self.scroll_col = display_col_to_logical_col(line_text, new_scroll_dcol, tabstop)
        elif cursor_dcol >= scroll_dcol + tw - sso:
            new_scroll_dcol = max(0, cursor_dcol - tw + 1 + sso)
            self.scroll_col = display_col_to_logical_col(line_text, new_scroll_dcol, tabstop)
        self.scroll_col = max(0, self.scroll_col)

    def center_on_cursor(self) -> None:
        """Set scroll so the cursor appears in the middle of the window."""
        self.follow_cursor = True
        self.scroll_line = max(0, self.cursor.line - self.height // 2)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self, global_options: dict | None = None) -> WindowSnapshot:
        """Produce an immutable snapshot for background threads.

        global_options, if provided, is merged as a base layer so that global
        OptionsStore values are visible to the renderer.  Window-local options
        (set via :set or per-buffer API) take precedence over global defaults.
        """
        opts = dict(global_options) if global_options else {}
        opts.update(self.options)  # local overrides global
        return WindowSnapshot(
            buffer_snapshot=self.document.snapshot(),
            cursor_line=self.cursor.line,
            cursor_col=self.cursor.col,
            scroll_line=self.scroll_line,
            scroll_col=self.scroll_col,
            width=self.width,
            height=self.height,
            options=opts,
            closed_folds=tuple(self.folds.closed_folds()),
        )

    def __repr__(self) -> str:
        return (
            f"Window(cursor={self.cursor}, "
            f"scroll=({self.scroll_line},{self.scroll_col}), "
            f"size={self.width}x{self.height})"
        )
