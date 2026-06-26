"""
Phase 7c — MouseDispatcher tests
"""

from __future__ import annotations

from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine
from peovim.ui.backend import MouseEvent
from peovim.ui.layout import Rect
from peovim.ui.mouse_dispatcher import MouseDispatcher
from peovim.ui.sidebar import SidebarHost

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SidebarPanel:
    def __init__(self, width: int = 20) -> None:
        self.width = width

    def render(self, grid) -> None:
        return None

    def feed_key(self, key: str) -> bool:
        return True


def _make_dispatcher(
    content: str = "line1\nline2\nline3\n",
    *,
    layout_rect: Rect | None = None,
    sidebar: SidebarHost | None = None,
    sidebar_rect: Rect | None = None,
    global_options: dict | None = None,
    window_options: dict | None = None,
):
    doc = Document()
    doc.load_string(content)
    window = Window(doc, width=80, height=24)
    if window_options:
        window.options.update(window_options)
    workspace = Workspace(window)
    engine = ModalEngine()
    engine.set_document(doc)
    registers = RegisterStore()
    editor_state = EditorState()
    if global_options:
        for name, value in global_options.items():
            editor_state.options.set(name, value)
    command_registry = CommandRegistry()
    register_builtins(command_registry)
    action_dispatcher = ActionDispatcher(engine, window, registers, editor_state=editor_state)
    action_dispatcher._command_registry = command_registry

    leaf = workspace.active_tab.all_leaves()[0]
    layout = {leaf: layout_rect or Rect(0, 0, 80, 22)}

    mouse_d = MouseDispatcher(
        workspace,
        engine,
        action_dispatcher,
        get_layout_fn=lambda: layout,
        get_sidebar_rect_fn=lambda: sidebar_rect,
        get_sidebar_fn=lambda: sidebar,
    )
    return mouse_d, workspace, window, layout, leaf, action_dispatcher


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMouseDispatcher:
    def test_click_moves_cursor(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher(global_options={"signcolumn": "yes"})
        # Click on row 2, col 5 (within the single window, gutter_w=2)
        event = MouseEvent(row=2, col=5, button=0, pressed=True)
        md.handle(event)
        # line = row - rect.y + scroll = 2 - 0 + 0 = 2
        # col = col - rect.x - gutter_w + scroll_col = 5 - 0 - 2 + 0 = 3
        assert window.cursor.line == 2
        assert window.cursor.col == 3

    def test_click_accounts_for_gutter_width(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher(
            "abcdefghij\n",
            global_options={"signcolumn": "yes"},
        )
        event = MouseEvent(row=0, col=2, button=0, pressed=True)  # exactly at gutter edge
        md.handle(event)
        # col = 2 - 0 - 2 = 0
        assert window.cursor.col == 0

    def test_click_clamps_to_buffer_line_count(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher("only one line\n")
        event = MouseEvent(row=10, col=2, button=0, pressed=True)
        md.handle(event)
        # line 10 > line_count-1; document with trailing newline has 2 lines (line 0 and empty line 1)
        line_count = window.document.line_count()
        assert window.cursor.line == line_count - 1

    def test_click_clamps_col_to_line_length(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher("abc\n")
        event = MouseEvent(row=0, col=20, button=0, pressed=True)
        md.handle(event)
        # col clamped to len("abc") - 1 = 2
        assert window.cursor.col == 2

    def test_click_maps_tab_expanded_columns_back_to_logical_col(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher(
            "\tabc\n",
            global_options={"signcolumn": "yes"},
        )
        event = MouseEvent(row=0, col=6, button=0, pressed=True)
        md.handle(event)
        assert window.cursor.col == 1

    def test_drag_maps_tab_expanded_columns_back_to_logical_col(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher(
            "\tabc\n",
            global_options={"signcolumn": "yes"},
        )
        press = MouseEvent(row=0, col=2, button=0, pressed=True)
        drag = MouseEvent(row=0, col=6, button=0, pressed=True, dragging=True)
        md.handle(press)
        md.handle(drag)
        assert window.cursor.col == 1

    def test_click_uses_number_gutter_width(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher("abcdefghij\n", global_options={"number": True})
        event = MouseEvent(row=0, col=4, button=0, pressed=True)
        md.handle(event)
        assert window.cursor.col == 0

    def test_scroll_up_dispatches_scroll_view_negative(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher("a\nb\nc\nd\ne\n")
        window.scroll_line = 3
        event = MouseEvent(row=0, col=0, button=3, pressed=True)  # button 3 = scroll up
        md.handle(event)
        # scroll_line should have decreased (clamped to 0)
        assert window.scroll_line == 0

    def test_scroll_down_dispatches_scroll_view_positive(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher("\n".join(["line"] * 20) + "\n")
        window.scroll_line = 0
        event = MouseEvent(row=0, col=0, button=4, pressed=True)  # button 4 = scroll down
        md.handle(event)
        assert window.scroll_line > 0

    def test_window_at_returns_none_outside_windows(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher()
        # Click outside the layout (row=25 is beyond height 22)
        result = md._window_at(5, 25, layout)
        assert result is None

    def test_window_at_returns_correct_window(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher()
        result = md._window_at(10, 5, layout)
        assert result is not None
        assert result[0] is leaf

    def test_click_focuses_different_window(self):
        doc = Document()
        doc.load_string("hello\nworld\n")
        win1 = Window(doc, width=40, height=22)
        workspace = Workspace(win1)
        tab = workspace.active_tab

        doc2 = Document()
        doc2.load_string("other\n")
        tab.split_vertical()
        win2 = tab.active_window

        leaves = tab.all_leaves()
        leaf1, leaf2 = leaves[0], leaves[1]

        layout = {
            leaf1: Rect(0, 0, 40, 22),
            leaf2: Rect(40, 0, 40, 22),
        }

        engine = ModalEngine()
        engine.set_document(doc)
        registers = RegisterStore()
        editor_state = EditorState()
        command_registry = CommandRegistry()
        disp = ActionDispatcher(engine, win1, registers, editor_state=editor_state)
        disp._command_registry = command_registry

        md = MouseDispatcher(workspace, engine, disp, get_layout_fn=lambda: layout)

        # Initially win1 is active; click on right half (win2 territory)
        event = MouseEvent(row=1, col=45, button=0, pressed=True)
        md.handle(event)
        # win2 should be focused
        assert tab.active_window is win2

    def test_click_on_sidebar_header_selects_panel_without_moving_cursor(self):
        sidebar = SidebarHost()
        first = _SidebarPanel(width=20)
        second = _SidebarPanel(width=20)
        sidebar.register_panel("explorer", first)
        sidebar.register_panel("outline", second)
        sidebar.show_panel("explorer", first, focus=False)

        md, workspace, window, layout, leaf, disp = _make_dispatcher(
            layout_rect=Rect(21, 0, 59, 22),
            sidebar=sidebar,
            sidebar_rect=Rect(0, 0, 20, 22),
        )

        event = MouseEvent(row=1, col=5, button=0, pressed=True)
        md.handle(event)

        assert sidebar.active_panel_name == "outline"
        assert sidebar.focused
        assert window.cursor.line == 0
        assert window.cursor.col == 0

    def test_click_outside_all_windows_is_noop(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher()
        initial_line = window.cursor.line
        # No window at row=50 (beyond layout)
        event = MouseEvent(row=50, col=0, button=0, pressed=True)
        md.handle(event)
        assert window.cursor.line == initial_line

    def test_right_click_is_ignored(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher()
        initial_line = window.cursor.line
        event = MouseEvent(row=1, col=5, button=2, pressed=True)
        md.handle(event)
        # right-click not handled — cursor shouldn't move
        assert window.cursor.line == initial_line

    def test_scroll_respects_window_bounds(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher("line1\n")
        window.scroll_line = 0
        event = MouseEvent(row=0, col=0, button=3, pressed=True)  # scroll up from 0
        md.handle(event)
        assert window.scroll_line >= 0  # should not go negative

    def test_scrollbar_click_scrolls_without_moving_cursor(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher(
            "\n".join(f"line{i}" for i in range(60)) + "\n",
            layout_rect=Rect(0, 0, 20, 10),
            global_options={"scrollbar": True},
        )
        event = MouseEvent(row=9, col=19, button=0, pressed=True)
        md.handle(event)
        assert window.scroll_line > 0
        assert window.cursor.line == 0
        assert window.cursor.col == 0

    def test_scrollbar_drag_updates_scroll_line(self):
        md, workspace, window, layout, leaf, disp = _make_dispatcher(
            "\n".join(f"line{i}" for i in range(60)) + "\n",
            layout_rect=Rect(0, 0, 20, 10),
            global_options={"scrollbar": True},
        )
        press = MouseEvent(row=0, col=19, button=0, pressed=True)
        drag = MouseEvent(row=6, col=19, button=0, pressed=True, dragging=True)
        md.handle(press)
        md.handle(drag)
        assert window.scroll_line > 0

    def test_drag_that_moves_cursor_keeps_visual_mode(self):
        from peovim.modal.engine import Mode

        md, workspace, window, layout, leaf, disp = _make_dispatcher("hello world\n")
        press = MouseEvent(row=0, col=0, button=0, pressed=True)
        drag = MouseEvent(row=0, col=5, button=0, pressed=True, dragging=True)
        release = MouseEvent(row=0, col=5, button=0, pressed=False)
        md.handle(press)
        md.handle(drag)
        md.handle(release)
        assert disp.engine.mode == Mode.VISUAL_CHAR

    def test_accidental_drag_with_no_movement_exits_visual_mode(self):
        from peovim.modal.engine import Mode

        md, workspace, window, layout, leaf, disp = _make_dispatcher("hello world\n")
        # Click at col 2 (gutter=0, so cursor lands at col 2)
        press = MouseEvent(row=0, col=2, button=0, pressed=True)
        # Drag without leaving the same cell (same position)
        drag = MouseEvent(row=0, col=2, button=0, pressed=True, dragging=True)
        release = MouseEvent(row=0, col=2, button=0, pressed=False)
        md.handle(press)
        md.handle(drag)
        md.handle(release)
        # Should have exited visual mode since anchor == cursor
        assert disp.engine.mode == Mode.NORMAL
