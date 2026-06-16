"""
ui.mouse_dispatcher — MouseDispatcher: terminal mouse events → editor actions

Translates raw (row, col) mouse coordinates into editor actions:
  - Left click: focus window + move cursor
  - Scroll up/down: scroll the hovered window
  - Scrollbar click/drag: scroll without moving the text cursor
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from peovim.ui.scrollbar import scrollbar_scroll_line_for_thumb_top, scrollbar_thumb_range, scrollbar_width
from peovim.ui.text_layout import display_col_to_logical_col, logical_col_to_display_col

if TYPE_CHECKING:
    from peovim.core.workspace import Workspace
    from peovim.modal.dispatcher import ActionDispatcher
    from peovim.modal.engine import ModalEngine
    from peovim.ui.backend import MouseEvent
    from peovim.ui.layout import Rect
    from peovim.ui.sidebar import SidebarHost

SCROLL_AMOUNT = 3  # lines scrolled per scroll event


class MouseDispatcher:
    """Translate MouseEvent objects into editor actions."""

    def __init__(
        self,
        workspace: Workspace,
        engine: ModalEngine,
        dispatcher: ActionDispatcher,
        get_layout_fn: Callable[[], dict] | None = None,
        get_sidebar_rect_fn: Callable[[], Rect | None] | None = None,
        get_sidebar_fn: Callable[[], SidebarHost | None] | None = None,
        get_bottom_panel_rect_fn: Callable[[], Rect | None] | None = None,
        get_bottom_panel_fn: Callable[[], Any] | None = None,
    ) -> None:
        self._workspace = workspace
        self._engine = engine
        self._dispatcher = dispatcher
        self._get_layout: Callable[[], dict] = get_layout_fn or (lambda: {})
        self._get_sidebar_rect: Callable[[], Rect | None] = get_sidebar_rect_fn or (lambda: None)
        self._get_sidebar: Callable[[], SidebarHost | None] = get_sidebar_fn or (lambda: None)
        self._get_bottom_panel_rect: Callable[[], Rect | None] = get_bottom_panel_rect_fn or (lambda: None)
        self._get_bottom_panel: Callable[[], Any] = get_bottom_panel_fn or (lambda: None)
        self._drag_active: bool = False  # True while left button is held and dragging
        self._drag_leaf: Any = None  # WindowLeaf the drag started in
        self._drag_rect: Any = None  # screen Rect of that leaf
        self._scrollbar_drag_active: bool = False
        self._scrollbar_drag_leaf: Any = None
        self._scrollbar_drag_rect: Any = None
        self._scrollbar_drag_offset: int = 0

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def handle(self, event: MouseEvent) -> None:
        """Dispatch a mouse event to the appropriate handler."""
        button = event.button
        if button == 3:  # scroll up
            self._scroll(-SCROLL_AMOUNT, event)
        elif button == 4:  # scroll down
            self._scroll(SCROLL_AMOUNT, event)
        elif event.dragging and button == 0:  # left-button drag
            if self._scrollbar_drag_active:
                self._drag_scrollbar(event)
            else:
                self._drag(event)
        elif button == 0 and event.pressed:  # left click (press)
            self._drag_active = False
            self._drag_leaf = None
            self._drag_rect = None
            self._click(event)
        elif button == 0 and not event.pressed:  # left release
            self._drag_active = False
            self._drag_leaf = None
            self._drag_rect = None
            self._scrollbar_drag_active = False
            self._scrollbar_drag_leaf = None
            self._scrollbar_drag_rect = None
            self._scrollbar_drag_offset = 0

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    def _click(self, event: MouseEvent) -> None:
        """Focus window under cursor and move buffer cursor there."""
        from peovim.modal.actions import MoveCursor

        self._scrollbar_drag_active = False
        self._scrollbar_drag_leaf = None
        self._scrollbar_drag_rect = None
        self._scrollbar_drag_offset = 0

        if self._click_sidebar(event):
            return

        if self._click_bottom_panel(event):
            return

        layout = self._get_layout()
        hit = self._window_at(event.col, event.row, layout)
        if hit is None:
            return

        leaf, rect = hit
        self._focus_window(leaf)
        if self._press_scrollbar(leaf, rect, event):
            return

        line, col = self._buffer_position_for_point(leaf.window, rect, event.row, event.col)

        self._dispatcher.dispatch([MoveCursor(line, col)])

    def _click_bottom_panel(self, event: MouseEvent) -> bool:
        bp = self._get_bottom_panel()
        bp_rect = self._get_bottom_panel_rect()
        if bp is None or bp_rect is None or not getattr(bp, "visible", False):
            return False
        if not (bp_rect.x <= event.col < bp_rect.x + bp_rect.width):
            return False
        if not (bp_rect.y <= event.row < bp_rect.y + bp_rect.height):
            return False
        local_row = event.row - bp_rect.y
        local_col = event.col - bp_rect.x
        click = getattr(bp, "click", None)
        if callable(click):
            return bool(click(local_col, local_row))
        focus = getattr(bp, "focus", None)
        if callable(focus):
            focus()
        return True

    def _click_sidebar(self, event: MouseEvent) -> bool:
        sidebar = self._get_sidebar()
        sidebar_rect = self._get_sidebar_rect()
        if sidebar is None or sidebar_rect is None or not getattr(sidebar, "visible", False):
            return False
        if not (sidebar_rect.x <= event.col < sidebar_rect.x + sidebar_rect.width):
            return False
        if not (sidebar_rect.y <= event.row < sidebar_rect.y + sidebar_rect.height):
            return False
        local_row = event.row - sidebar_rect.y
        local_col = event.col - sidebar_rect.x
        click = getattr(sidebar, "click", None)
        if callable(click):
            return bool(click(local_row, local_col))
        focus = getattr(sidebar, "focus", None)
        if callable(focus):
            focus()
        return True

    def _drag(self, event: MouseEvent) -> None:
        """Handle left-button drag: enter visual mode on first drag, extend selection,
        and auto-scroll when the pointer reaches the top or bottom of the window."""
        from peovim.modal.actions import EnterVisualMode, MoveCursor
        from peovim.modal.engine import Mode

        if not self._drag_active:
            self._drag_active = True
            # Enter visual char mode if not already in a visual mode
            mode = self._engine.mode
            visual_modes = (Mode.VISUAL_CHAR, Mode.VISUAL_LINE, Mode.VISUAL_BLOCK)
            if mode not in visual_modes:
                self._dispatcher.dispatch([EnterVisualMode("char")])
            # Record which window the drag started in so we can still reference
            # it if the mouse wanders outside any window rect.
            layout = self._get_layout()
            hit = self._window_at(event.col, event.row, layout)
            if hit is not None:
                self._drag_leaf, self._drag_rect = hit

        # Prefer the window actually under the pointer; fall back to the drag origin.
        layout = self._get_layout()
        hit = self._window_at(event.col, event.row, layout)
        if hit is not None:
            leaf, rect = hit
            self._drag_leaf, self._drag_rect = leaf, rect
        elif self._drag_leaf is not None:
            leaf, rect = self._drag_leaf, self._drag_rect
        else:
            return

        window = leaf.window
        doc = window.document
        line_count = doc.line_count()

        # Auto-scroll when the pointer is at or past the top/bottom edge.
        # Speed is proportional to how far outside the window the pointer is.
        top_edge = rect.y
        bottom_edge = rect.y + rect.height - 1
        if event.row < top_edge:
            scroll_delta = -(top_edge - event.row)
        elif event.row > bottom_edge:
            scroll_delta = event.row - bottom_edge
        elif event.row == top_edge:
            scroll_delta = -1
        elif event.row == bottom_edge:
            scroll_delta = 1
        else:
            scroll_delta = 0

        if scroll_delta and line_count > 0:
            max_scroll = max(0, line_count - window.height)
            window.scroll_line = max(0, min(window.scroll_line + scroll_delta, max_scroll))

        if line_count > 0:
            line, col = self._buffer_position_for_point(window, rect, event.row, event.col)
        else:
            line = col = 0
        self._dispatcher.dispatch([MoveCursor(line, col)])

    def _drag_scrollbar(self, event: MouseEvent) -> None:
        leaf = self._scrollbar_drag_leaf
        rect = self._scrollbar_drag_rect
        if leaf is None or rect is None:
            return

        window = leaf.window
        local_row = max(0, min(event.row - rect.y, rect.height - 1))
        thumb_top = local_row - self._scrollbar_drag_offset
        self._set_window_scroll(
            window,
            rect.height,
            scrollbar_scroll_line_for_thumb_top(
                window.document.line_count(),
                rect.height,
                thumb_top,
            ),
        )

    def _scroll(self, lines: int, event: MouseEvent | None = None) -> None:
        """Scroll the active window by `lines` lines."""
        from peovim.modal.actions import ScrollView

        if event is not None:
            layout = self._get_layout()
            hit = self._window_at(event.col, event.row, layout)
            if hit is not None:
                leaf, _rect = hit
                self._focus_window(leaf)
        self._dispatcher.dispatch([ScrollView(lines)])

    def _window_at(self, x: int, y: int, layout: dict) -> tuple[Any, Any] | None:
        """Find the (WindowLeaf, Rect) whose screen rect contains (x, y)."""
        for leaf, rect in layout.items():
            if rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height:
                return leaf, rect
        return None

    def _buffer_position_for_point(self, window, rect: Rect, row: int, col: int) -> tuple[int, int]:
        gutter_w = self._window_gutter_width(window)
        line = row - rect.y + window.scroll_line
        text_display_col = max(0, col - rect.x - gutter_w)

        doc = window.document
        line_count = doc.line_count()
        if line_count <= 0:
            return 0, 0

        line = max(0, min(line, line_count - 1))
        line_text = doc.get_line(line)
        tabstop = int(self._effective_option(window, "tabstop", 4) or 4)
        scroll_display_col = logical_col_to_display_col(line_text, window.scroll_col, tabstop)
        logical_col = display_col_to_logical_col(line_text, scroll_display_col + text_display_col, tabstop)
        line_len = len(line_text)
        return line, max(0, min(logical_col, max(0, line_len - 1)))

    def _press_scrollbar(self, leaf, rect: Rect, event: MouseEvent) -> bool:
        if not self._is_scrollbar_hit(leaf.window, rect, event):
            return False

        self._scrollbar_drag_active = True
        self._scrollbar_drag_leaf = leaf
        self._scrollbar_drag_rect = rect

        local_row = event.row - rect.y
        thumb_top, thumb_height = scrollbar_thumb_range(
            leaf.window.document.line_count(), rect.height, leaf.window.scroll_line
        )
        if thumb_top <= local_row < thumb_top + thumb_height:
            self._scrollbar_drag_offset = local_row - thumb_top
        else:
            self._scrollbar_drag_offset = thumb_height // 2
            self._set_window_scroll(
                leaf.window,
                rect.height,
                scrollbar_scroll_line_for_thumb_top(
                    leaf.window.document.line_count(),
                    rect.height,
                    local_row - self._scrollbar_drag_offset,
                ),
            )
        return True

    def _is_scrollbar_hit(self, window, rect: Rect, event: MouseEvent) -> bool:
        if scrollbar_width({"scrollbar": self._effective_option(window, "scrollbar", False)}) == 0:
            return False
        if rect.width <= 0:
            return False
        return event.col == rect.x + rect.width - 1

    def _window_gutter_width(self, window) -> int:
        line_count = window.document.line_count()
        number_w = 0
        if self._effective_option(window, "number", False) or self._effective_option(window, "relativenumber", False):
            number_w = max(len(str(line_count)), 3) + 1

        signcolumn = self._effective_option(window, "signcolumn", "auto")
        if signcolumn == "yes":
            sign_w = 2
        elif signcolumn == "auto":
            sign_w = 2 if self._has_signs(window) else 0
        else:
            sign_w = 0
        return number_w + sign_w

    def _has_signs(self, window) -> bool:
        editor_state = getattr(self._dispatcher, "_editor_state", None)
        if editor_state is None:
            return False
        return editor_state.decorations.has_signs(id(window.document))

    def _effective_option(self, window, name: str, default: Any) -> Any:
        if name in window.options:
            return window.options[name]
        editor_state = getattr(self._dispatcher, "_editor_state", None)
        if editor_state is None:
            return default
        global_options = editor_state.options.global_as_dict()
        return global_options.get(name, default)

    def _focus_window(self, leaf) -> None:
        tab = self._workspace.active_tab
        if leaf.window is not tab.active_window:
            with contextlib.suppress(ValueError):
                tab.focus_window(leaf.window)
        self._dispatcher.window = leaf.window

    def _set_window_scroll(self, window, viewport_height: int, scroll_line: int) -> None:
        max_scroll = max(0, window.document.line_count() - viewport_height)
        window.scroll_line = max(0, min(scroll_line, max_scroll))
        window.follow_cursor = False
