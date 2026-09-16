"""
core.window — Window (viewport): cursor + scroll offset + Document reference

A Window is a view into a Document. Multiple windows may reference the same
Document. Holds cursor position, scroll offset, and window-local options.
Has no rendering logic — pure state.

See notes/architecture.md for the Buffer/Window/Tab Model.
"""

from __future__ import annotations

from typing import TypedDict

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
        # How far *into* the virtual-line block anchored right after scroll_line
        # to start painting, instead of from scroll_line's own content.
        # scroll_line is always a real buffer line (there's no addressable
        # "line" inside a virtual-only gap to point scroll_line itself at);
        # this is the extra sub-line offset that makes exact mid-gap alignment
        # possible for the diff view. 0 means disabled (render scroll_line's
        # own content normally, the default for every ordinary window); N >= 1
        # means "start at the block's row N-1" (skip scroll_line's own content
        # row entirely, plus N-1 rows of the block) — offset by one so 0 can
        # mean "disabled" and still let the block's very first row (N=1) be
        # addressable. Only compare.py's cross-pane alignment ever sets this
        # nonzero. See notes/architecture.md.
        self.scroll_virtual_skip: int = 0
        self.follow_cursor: bool = True
        self.width: int = width
        self.height: int = height
        self.options: dict = {}  # window-local options
        self.folds: FoldStore = FoldStore()

    # ------------------------------------------------------------------
    # Scroll helpers
    # ------------------------------------------------------------------

    def scroll_to_cursor(
        self,
        text_width: int | None = None,
        *,
        center: bool = False,
        scrolloff: int | None = None,
        sidescrolloff: int | None = None,
        tabstop: int | None = None,
    ) -> None:
        """Adjust scroll_line and scroll_col so cursor is visible, respecting scrolloff/sidescrolloff.

        text_width: visible text columns (window width minus gutter). When None,
        self.width is used as a conservative fallback — callers with accurate
        gutter info should pass the real value.

        center: when True and the cursor is outside the current viewport, center the
        cursor in the window instead of just ensuring it is visible.

        scrolloff/sidescrolloff/tabstop: effective (global-aware) option values
        to use instead of `self.options` (a window-local *override* dict — see
        its declaration in __init__ — that's never populated from the global
        OptionsStore). Window has no OptionsStore reference by design, so it
        can't resolve the global value itself; callers that have one (the
        dispatcher, WindowAPI, plugins) should pass the effective value here.
        None (the default) falls back to `self.options.get(..., 0)`, i.e.
        today's behavior — only a window-local override applies, matching
        every caller that predates this parameter and doesn't pass it.
        """
        self.follow_cursor = True

        # --- Vertical ---
        so = int(scrolloff if scrolloff is not None else self.options.get("scrolloff", 0))
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
        sso = int(sidescrolloff if sidescrolloff is not None else self.options.get("sidescrolloff", 0))
        tabstop = int(tabstop if tabstop is not None else (self.options.get("tabstop", 4) or 4))
        tw = max(1, text_width if text_width is not None and text_width > 0 else self.width)
        try:
            line_text = self.document.get_line(self.cursor.line)
        except Exception:
            return
        if self.scroll_col > len(line_text):
            self.scroll_col = 0
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
            scroll_virtual_skip=self.scroll_virtual_skip,
        )

    def __repr__(self) -> str:
        return (
            f"Window(cursor={self.cursor}, "
            f"scroll=({self.scroll_line},{self.scroll_col}), "
            f"size={self.width}x{self.height})"
        )


class ScrollOptions(TypedDict, total=False):
    """Return shape of `effective_scroll_options()` — a TypedDict (not a plain
    `dict[str, int]`) so `**effective_scroll_options(...)` type-checks cleanly
    against `scroll_to_cursor()`'s mixed int/bool keyword parameters (a plain
    homogeneous dict makes mypy conservatively check every keyword parameter
    against the dict's value type, including unrelated ones like `center: bool`).
    """

    scrolloff: int
    sidescrolloff: int
    tabstop: int


def effective_scroll_options(editor_state: object | None) -> ScrollOptions:
    """scrolloff/sidescrolloff/tabstop kwargs for `Window.scroll_to_cursor()`,
    read from the real OptionsStore on `editor_state`.

    `Window.options` is a window-local *override* dict that's never populated
    from global settings (`options.set("scrolloff", N)` in init.py) — reading
    it directly, as `scroll_to_cursor()` does when these aren't passed, silently
    behaves as if scrolloff/sidescrolloff/tabstop were always 0 or default,
    regardless of what's globally configured (see notes/architecture.md). No
    code sets a *window-scoped* override for any of these today, so the plain
    global lookup (no win_id) used here is the correct effective value in
    every real case. Callers with an EditorState (the dispatcher, WindowAPI,
    plugins) should pass `**effective_scroll_options(editor_state)` into
    `scroll_to_cursor()` instead of letting it fall back to `Window.options`.
    """
    store = getattr(editor_state, "options", None)
    if store is None:
        return {}
    result: ScrollOptions = {}
    for name in ("scrolloff", "sidescrolloff", "tabstop"):
        value = store.get(name)
        if value is not None:
            result[name] = int(value)  # type: ignore[literal-required]
    return result
