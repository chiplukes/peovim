"""Frame composition helpers extracted from `EventLoop`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from peovim.ui.cell_grid import CellGrid
from peovim.ui.layout import Rect, compute_layout
from peovim.ui.status_bar import render_status_bar

if TYPE_CHECKING:
    from peovim.ui.event_loop import EventLoop


class FrameController:
    """Owns frame layout, theme resolution, and body composition for `EventLoop`."""

    def __init__(self, host: EventLoop) -> None:
        self._host = host
        self._cached_theme_name: str | None = None
        self._cached_theme: object | None = None
        self._layout_cache_key: tuple | None = None
        self._layout_cache_value: tuple[dict, list[Rect], int, Rect | None, Rect | None] | None = None

    def render_body(self, grid: CellGrid, cols: int, rows: int, *, clear_grid: bool = True) -> None:
        host = self._host
        if clear_grid:
            grid.clear()
        tab = host._workspace.active_tab
        layout, separators, win_rows, sidebar_rect, bottom_panel_rect = self.compute_frame_layout(tab, cols, rows)
        theme = self.resolve_frame_theme()

        host._render_window_content(grid, tab, layout, theme)
        self.render_separators(grid, separators)
        host._render_sidebar(grid, sidebar_rect, theme)
        host._render_bottom_panel(grid, bottom_panel_rect, theme)
        host._render_which_key_panel(grid, cols, rows)

        self.render_status_row(grid, cols, rows)
        self.render_cmdline_row(grid, cols, rows)
        host._render_tree_views(grid, win_rows)
        host._render_overlay_widgets(grid, tab, layout)

    def compute_frame_layout(
        self, tab: object, cols: int, rows: int
    ) -> tuple[dict, list[Rect], int, Rect | None, Rect | None]:
        host = self._host

        # Bottom panel height (takes priority — reserved before which-key)
        bp = host._bottom_panel
        bp_height = bp.reserved_height(rows) if (bp is not None and getattr(bp, "visible", False)) else 0
        bottom_panel_rect: Rect | None = None

        # Which-key height: only shown in its own slot when bottom panel is hidden
        wk_panel = host._which_key_panel
        bp_visible = bp_height > 0
        wk_height = (
            0
            if bp_visible
            else (wk_panel.panel_height(cols) if (wk_panel is not None and getattr(wk_panel, "is_open", False)) else 0)
        )

        win_rows = max(1, rows - 2 - wk_height - bp_height)
        sidebar_rect: Rect | None = None
        separators: list[Rect] = []
        sidebar_width = 0
        if host._sidebar is not None and getattr(host._sidebar, "visible", False):
            sidebar_width = host._sidebar.reserved_width(cols)

        cache_key = (
            self._layout_signature(getattr(tab, "root", None)),
            cols,
            rows,
            win_rows,
            sidebar_width,
            bp_height,
        )
        if cache_key == self._layout_cache_key and self._layout_cache_value is not None:
            layout, separators, cached_win_rows, sidebar_rect, bottom_panel_rect = self._layout_cache_value
            host._current_layout = layout
            host._current_sidebar_rect = sidebar_rect
            host._current_bottom_panel_rect = bottom_panel_rect
            return layout, separators, cached_win_rows, sidebar_rect, bottom_panel_rect

        if sidebar_width > 0 and cols - sidebar_width > 1:
            sidebar_rect = Rect(0, 0, sidebar_width, win_rows)
            separators.append(Rect(sidebar_width, 0, 1, win_rows))
            layout_rect = Rect(sidebar_width + 1, 0, max(1, cols - sidebar_width - 1), win_rows)
        else:
            layout_rect = Rect(0, 0, cols, win_rows)
        layout, separators = compute_layout(tab.root, layout_rect)
        if sidebar_rect is not None:
            separators.insert(0, Rect(sidebar_rect.width, 0, 1, win_rows))

        if bp_height > 0:
            bp_y = rows - 2 - bp_height
            bottom_panel_rect = Rect(0, bp_y, cols, bp_height)

        host._current_layout = layout
        host._current_sidebar_rect = sidebar_rect
        host._current_bottom_panel_rect = bottom_panel_rect
        cached: tuple[dict, list[Rect], int, Rect | None, Rect | None] = (
            layout,
            separators,
            win_rows,
            sidebar_rect,
            bottom_panel_rect,
        )
        self._layout_cache_key = cache_key
        self._layout_cache_value = cached
        return cached

    def resolve_frame_theme(self):
        from peovim.syntax.themes import get_theme

        host = self._host
        theme_name = host._editor_state.active_theme if host._editor_state is not None else "catppuccin"
        if theme_name == self._cached_theme_name and self._cached_theme is not None:
            return self._cached_theme

        theme = get_theme(theme_name) or get_theme("catppuccin")
        self._cached_theme_name = theme_name
        self._cached_theme = theme
        return theme

    @staticmethod
    def render_separators(grid: CellGrid, separators: list[Rect]) -> None:
        for sep in separators:
            if sep.width == 1:
                for row in range(sep.height):
                    grid.write(sep.y + row, sep.x, "│", fg=(80, 80, 100))
            else:
                grid.write_str(sep.y, sep.x, "─" * sep.width, fg=(80, 80, 100))

    def render_status_row(self, grid: CellGrid, cols: int, rows: int) -> None:
        host = self._host
        status_rect = Rect(0, rows - 2, cols, 1)
        render_status_bar(
            host._workspace.active_tab.active_window,
            host._engine.mode,
            status_rect,
            grid,
            workspace=host._workspace,
            editor_state=host._editor_state,
        )

    def render_cmdline_row(self, grid: CellGrid, cols: int, rows: int) -> None:
        host = self._host
        cmdline_rect = Rect(0, rows - 1, cols, 1)
        if host._cmdline.active:
            host._cmdline.render_completion(cmdline_rect, grid, max_rows=max(0, rows - 1))
        host._cmdline.render(cmdline_rect, grid)
        if not host._cmdline.active and host._editor_state is not None and host._editor_state.message:
            from peovim.ui.event_loop import _render_message

            _render_message(host._editor_state.message, cmdline_rect, grid)

    def _layout_signature(self, node: object) -> tuple:
        if node is None:
            return ("none",)
        if getattr(node, "is_leaf", False):
            window = getattr(node, "window", None)
            return ("leaf", id(window))
        left = getattr(node, "left", None)
        right = getattr(node, "right", None)
        if left is not None or right is not None:
            return (
                "vsplit",
                round(float(getattr(node, "ratio", 0.5)), 6),
                self._layout_signature(left),
                self._layout_signature(right),
            )
        top = getattr(node, "top", None)
        bottom = getattr(node, "bottom", None)
        return (
            "hsplit",
            round(float(getattr(node, "ratio", 0.5)), 6),
            self._layout_signature(top),
            self._layout_signature(bottom),
        )
