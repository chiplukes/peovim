"""UI presentation helpers extracted from `EventLoop`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from peovim.ui.cell_grid import CellGrid
from peovim.ui.layout import Rect
from peovim.ui.text_layout import logical_col_to_display_col
from peovim.ui.window_renderer import _gutter_width

if TYPE_CHECKING:
    from peovim.ui.event_loop import EventLoop


class OverlayPresentationController:
    """Owns overlay key routing and widget presentation for `EventLoop`."""

    def __init__(self, host: EventLoop) -> None:
        self._host = host

    def handle_overlay_key(self, key: str) -> bool:
        host = self._host

        if host._flash is not None and getattr(host._flash, "is_active", False):
            host._flash.feed_key(key)
            host._invalidate("full")
            return True

        if (
            host._float_manager is not None
            and getattr(host._float_manager, "has_focused", False)
            and host._float_manager.feed_key(key)
        ):
            host._invalidate("full")
            return True

        if self.handle_sidebar_navigation_key(key):
            host._invalidate("full")
            return True

        if self.handle_bottom_panel_navigation_key(key):
            host._invalidate("full")
            return True

        open_trees = [handle for handle in host._tree_views if getattr(handle, "is_open", False)]
        if open_trees:
            open_trees[-1].tree.feed_key(key)
            host._invalidate("full")
            return True

        if host._picker is not None and getattr(host._picker, "is_open", False):
            host._picker.feed_key(key)
            if not getattr(host._picker, "is_open", False):
                host._render_picker_immediately()
            else:
                host._invalidate("full")
            return True

        completion_popup = host._completion_popup
        if completion_popup is not None and getattr(completion_popup, "is_open", False):
            from peovim.modal.actions import InsertText
            from peovim.modal.engine import Mode

            if host._engine.mode == Mode.INSERT:
                if key in ("<Tab>", "<CR>", "<C-y>"):
                    text = completion_popup.accept()
                    if text:
                        cur = host._dispatcher.window.cursor
                        host._dispatcher.dispatch([InsertText(cur.line, cur.col, text)])
                    host._invalidate("full")
                    return True
                if completion_popup.feed_key(key):
                    host._invalidate("full")
                    return True

        if host._sidebar is not None and getattr(host._sidebar, "feed_key", None) and host._sidebar.feed_key(key):
            host._invalidate("full")
            return True

        if (
            host._bottom_panel is not None
            and getattr(host._bottom_panel, "feed_key", None)
            and host._bottom_panel.feed_key(key)
        ):
            host._invalidate("full")
            return True

        return False

    def handle_sidebar_navigation_key(self, key: str) -> bool:
        sidebar = self._host._sidebar
        if sidebar is None or not getattr(sidebar, "visible", False):
            return False

        registry = getattr(self._host, "_binding_registry", None)

        def _is_plug_key(plug_name: str) -> bool:
            if registry is None:
                return False
            return key in registry.find_keys_for_plug(plug_name, mode="normal")

        def _activate_window(window: object) -> None:
            self._host._workspace.active_tab.focus_window(window)
            self._host._dispatcher.window = window

        if not getattr(sidebar, "focused", False):
            return False

        if _is_plug_key("SidebarFocusLeft"):
            windows = self._host._workspace.active_tab.all_windows()
            sidebar.blur()
            if windows:
                _activate_window(windows[-1])
            return True
        if _is_plug_key("SidebarFocusRight"):
            windows = self._host._workspace.active_tab.all_windows()
            sidebar.blur()
            if windows:
                _activate_window(windows[0])
            return True
        if _is_plug_key("SidebarNextPanel"):
            sidebar.next_panel(focus=True)
            return True
        if _is_plug_key("SidebarPrevPanel"):
            sidebar.prev_panel(focus=True)
            return True
        return False

    def handle_bottom_panel_navigation_key(self, key: str) -> bool:
        """Focus the bottom panel with <A-j> when it is visible but not focused."""
        host = self._host
        bp = host._bottom_panel
        if bp is None or not getattr(bp, "_visible", False) or getattr(bp, "_focused", False):
            return False

        registry = getattr(host, "_binding_registry", None)
        if registry is None:
            return False

        if key in registry.find_keys_for_plug("SidebarNextPanel", mode="normal"):
            bp.show_active_tab(focus=True)
            return True
        return False

    def render_bottom_panel(self, grid: CellGrid, bottom_panel_rect: Rect | None, theme: object | None = None) -> None:
        host = self._host
        bp = host._bottom_panel
        if bottom_panel_rect is None or bp is None or not getattr(bp, "visible", False):
            return
        if getattr(bp, "_needs_full_redraw", False):
            bp._needs_full_redraw = False
            grid.invalidate_prev_rows(bottom_panel_rect.y, bottom_panel_rect.y + bottom_panel_rect.height)
        bp_grid = CellGrid(bottom_panel_rect.width, bottom_panel_rect.height)
        try:
            resolved_theme = theme if theme is not None else host._resolve_frame_theme()
            bp.render(bp_grid, theme=resolved_theme)
            grid.blit(bp_grid, dest_x=bottom_panel_rect.x, dest_y=bottom_panel_rect.y)
        except Exception as exc:
            host._report_runtime_error("bottom panel render", exc)

    def render_which_key_panel(self, grid: CellGrid, cols: int, rows: int) -> None:
        wk_panel = self._host._which_key_panel
        if wk_panel is None or not getattr(wk_panel, "is_open", False):
            return
        # When the bottom panel is visible, which-key renders inside the "keys"
        # tab — skip the standalone slot above the status bar to avoid a double render.
        bp = self._host._bottom_panel
        if bp is not None and getattr(bp, "visible", False):
            return
        wk_height = wk_panel.panel_height(cols)
        wk_start_row = rows - 2 - wk_height
        wk_panel.render(grid, wk_start_row, cols)

    def render_sidebar(self, grid: CellGrid, sidebar_rect: Rect | None, theme: object | None = None) -> None:
        host = self._host
        if sidebar_rect is None or host._sidebar is None or not getattr(host._sidebar, "visible", False):
            return
        if getattr(host._sidebar, "_needs_full_redraw", False):
            host._sidebar._needs_full_redraw = False
            grid.invalidate_prev_rows(sidebar_rect.y, sidebar_rect.y + sidebar_rect.height)
        sidebar_grid = CellGrid(sidebar_rect.width, sidebar_rect.height)
        try:
            resolved_theme = theme if theme is not None else host._resolve_frame_theme()
            host._sidebar.render(sidebar_grid, theme=resolved_theme)
            grid.blit(sidebar_grid, dest_x=sidebar_rect.x, dest_y=sidebar_rect.y)
        except Exception as exc:
            host._report_runtime_error("sidebar render", exc)

    def render_tree_views(self, grid: CellGrid, win_rows: int) -> None:
        host = self._host
        for handle in list(host._tree_views):
            if getattr(handle, "is_open", False):
                try:
                    tree = handle.tree
                    tree_w = getattr(tree, "_width", 30)
                    tree_grid = CellGrid(tree_w, win_rows)
                    tree.render(tree_grid)
                    grid.blit(tree_grid, dest_x=0, dest_y=0)
                except Exception as exc:
                    host._report_runtime_error("tree view render", exc)

    def render_overlay_widgets(self, grid: CellGrid, tab: object, layout: dict) -> None:
        host = self._host
        if host._float_manager is not None:
            try:
                host._float_manager.render(grid)
            except Exception as exc:
                host._report_runtime_error("float render", exc)
        if host._notify_manager is not None:
            try:
                host._notify_manager.render(grid)
            except Exception as exc:
                host._report_runtime_error("notification render", exc)
        if host._picker is not None and getattr(host._picker, "is_open", False):
            try:
                bp_rect = getattr(host, "_current_bottom_panel_rect", None)
                reserved = bp_rect.height if bp_rect is not None else 0
                host._picker.render(grid, reserved_bottom=reserved)
            except Exception as exc:
                host._report_runtime_error("picker render", exc)
        self.render_completion_popup(grid, tab, layout)

    def render_completion_popup(self, grid: CellGrid, tab: object, layout: dict) -> None:
        host = self._host
        if host._completion_popup is None or not getattr(host._completion_popup, "is_open", False):
            return
        try:
            active_win = tab.active_window
            snapshot = host._snapshot_window_for_render(active_win)
            has_signs = False
            if host._editor_state is not None:
                from peovim.ui.decorations import Sign

                for decoration in host._get_window_extra_decorations(active_win):
                    if isinstance(decoration, Sign):
                        has_signs = True
                        break
            cur_screen_row = 0
            cur_screen_col = 0
            for leaf, rect in layout.items():
                if leaf.window is active_win:
                    cur_screen_row = rect.y + (active_win.cursor.line - active_win.scroll_line)
                    line_count = len(snapshot.buffer_snapshot.line_offsets)
                    gutter = _gutter_width(snapshot, line_count, has_signs=has_signs)
                    tabstop = int(snapshot.options.get("tabstop", 4) or 4)
                    line_text = active_win.document.get_line(active_win.cursor.line)
                    scroll_display_col = logical_col_to_display_col(line_text, active_win.scroll_col, tabstop)
                    cursor_display_col = logical_col_to_display_col(line_text, active_win.cursor.col, tabstop)
                    cur_screen_col = rect.x + gutter + (cursor_display_col - scroll_display_col)
                    break
            host._completion_popup.render(grid, cur_screen_row, cur_screen_col)
        except Exception as exc:
            host._report_runtime_error("completion popup render", exc)
