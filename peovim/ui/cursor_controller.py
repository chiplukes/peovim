"""Terminal cursor helpers extracted from `EventLoop`."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Literal, cast

from peovim.ui.backend import HideCursor, MoveCursor, PutCell, RenderOp, SetCursorStyle, ShowCursor
from peovim.ui.text_layout import logical_col_to_display_col
from peovim.ui.window_renderer import _gutter_width

if TYPE_CHECKING:
    from peovim.ui.event_loop import EventLoop


class TerminalCursorController:
    """Owns terminal cursor visibility, state resolution, and render-op generation for `EventLoop`."""

    def __init__(self, host: EventLoop) -> None:
        self._host = host

    def should_use_terminal_cursor(self, options: dict | None = None) -> bool:
        host = self._host
        if host._cmdline.active:
            return False
        from peovim.modal.engine import Mode

        resolved = options if options is not None else self.resolve_active_window_cursor_options()
        blink = bool(resolved.get("cursorblink", False))
        insert_shape = str(resolved.get("insertcursor", "block")).lower()
        if insert_shape == "line":
            insert_shape = "bar"
        return blink or (host._engine.mode == Mode.INSERT and insert_shape == "bar")

    def resolve_active_window_cursor_options(self) -> dict:
        host = self._host
        active_window = host._workspace.active_window
        global_opts = host._editor_state.options.global_as_dict() if host._editor_state is not None else {}
        resolved = dict(global_opts)
        resolved.update(active_window.options)
        return resolved

    def build_terminal_cursor_ops(self) -> list[RenderOp]:
        host = self._host
        state = self.resolve_terminal_cursor_state()
        if state is None:
            if not host._terminal_cursor_visible:
                return []
            host._terminal_cursor_visible = False
            return [HideCursor()]

        row, col, shape, blink = state
        ops: list[RenderOp] = []
        if (shape, blink) != (host._terminal_cursor_shape, host._terminal_cursor_blink):
            ops.append(SetCursorStyle(shape=cast(Literal["block", "bar"], shape), blink=blink))
            host._terminal_cursor_shape = shape
            host._terminal_cursor_blink = blink
        ops.append(MoveCursor(row, col))
        assert host._grid is not None
        cell = host._grid._current[row][col]
        ops.append(PutCell(cell[0], fg=cell[1], bg=cell[2], attrs=cell[3]))
        ops.append(MoveCursor(row, col))
        host._terminal_cursor_pos = (row, col)
        if not host._terminal_cursor_visible:
            ops.append(ShowCursor())
            host._terminal_cursor_visible = True
        return ops

    def resolve_terminal_cursor_state(self) -> tuple[int, int, str, bool] | None:
        host = self._host
        if host._grid is None or host._cmdline.active:
            return None

        # When the sidebar is focused, hide the terminal cursor — the sidebar
        # selection blinks via software (TreeView.focused + render-loop tick).
        sidebar = getattr(host, "_sidebar", None)
        if sidebar is not None and getattr(sidebar, "focused", False):
            return None

        active_window = host._workspace.active_window
        resolved_options = self.resolve_active_window_cursor_options()
        snapshot = replace(host._snapshot_window_for_render(active_window), options=resolved_options)
        if not self.should_use_terminal_cursor(resolved_options):
            return None

        active_rect = None
        for leaf, rect in host._current_layout.items():
            if leaf.window is active_window:
                active_rect = rect
                break
        if active_rect is None:
            return None

        line_offset = active_window.cursor.line - active_window.scroll_line
        if not (0 <= line_offset < active_rect.height):
            return None

        has_signs = False
        if host._editor_state is not None:
            from peovim.ui.decorations import Sign

            for decoration in host._get_window_extra_decorations(active_window):
                if isinstance(decoration, Sign):
                    has_signs = True
                    break

        line_count = len(snapshot.buffer_snapshot.line_offsets)
        gutter_width = _gutter_width(snapshot, line_count, has_signs=has_signs)
        text_width = active_rect.width - gutter_width
        tabstop = int(resolved_options.get("tabstop", 4) or 4)
        line_text = active_window.document.get_line(active_window.cursor.line)
        scroll_display_col = logical_col_to_display_col(line_text, active_window.scroll_col, tabstop)
        cursor_display_col = logical_col_to_display_col(line_text, active_window.cursor.col, tabstop)
        cursor_col = cursor_display_col - scroll_display_col
        if not (0 <= cursor_col < text_width):
            return None

        from peovim.modal.engine import Mode

        insert_shape = str(resolved_options.get("insertcursor", "block")).lower()
        if insert_shape == "line":
            insert_shape = "bar"
        shape = "bar" if host._engine.mode == Mode.INSERT and insert_shape == "bar" else "block"
        blink = bool(resolved_options.get("cursorblink", False))
        return active_rect.y + line_offset, active_rect.x + gutter_width + cursor_col, shape, blink
