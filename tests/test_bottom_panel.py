"""Tests for BottomPanelHost and related classes."""

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
from peovim.modal.keybindings import BindingRegistry
from peovim.ui.backends.headless import HeadlessBackend
from peovim.ui.bottom_panel import BottomPanelHost, LogOutputTab
from peovim.ui.cell_grid import CellGrid
from peovim.ui.event_loop import EventLoop

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event_loop(cols: int = 80, rows: int = 24) -> tuple[EventLoop, Workspace]:
    doc = Document()
    doc.load_string("hello")
    window = Window(doc)
    workspace = Workspace(window)
    registers = RegisterStore()
    editor_state = EditorState()
    command_registry = CommandRegistry()
    register_builtins(command_registry)
    engine = ModalEngine()
    engine.set_document(doc)
    dispatcher = ActionDispatcher(engine, window, registers, editor_state=editor_state)
    dispatcher._command_registry = command_registry
    loop = EventLoop(HeadlessBackend(cols=cols, rows=rows), engine, dispatcher, workspace, editor_state=editor_state)
    binding_registry = BindingRegistry(engine, dispatcher)
    loop._binding_registry = binding_registry
    return loop, workspace


class _DummyTab:
    """Minimal tab for testing."""

    def __init__(self, title: str = "test") -> None:
        self.title = title
        self.keys: list[str] = []
        self.rendered: list[tuple[int, int]] = []

    def render(self, grid: CellGrid) -> None:
        self.rendered.append((grid.width, grid.height))
        grid.write_str(0, 0, self.title)

    def feed_key(self, key: str) -> bool:
        self.keys.append(key)
        return True


# ---------------------------------------------------------------------------
# BottomPanelHost
# ---------------------------------------------------------------------------


class TestBottomPanelHost:
    def test_show_tab_makes_panel_visible_and_focused(self):
        bp = BottomPanelHost()
        tab = _DummyTab("alpha")

        bp.show_tab("alpha", tab, focus=True)

        assert bp.visible
        assert bp.focused
        assert bp.active_tab_name == "alpha"

    def test_hide_clears_visibility_and_focus(self):
        bp = BottomPanelHost()
        bp.show_tab("alpha", _DummyTab(), focus=True)
        bp.hide()

        assert not bp.visible
        assert not bp.focused

    def test_toggle_opens_and_closes(self):
        bp = BottomPanelHost()
        bp.register_tab("alpha", _DummyTab())

        assert bp.toggle(focus=True)
        assert bp.visible
        assert not bp.toggle(focus=True)
        assert not bp.visible

    def test_reserved_height_zero_when_hidden(self):
        bp = BottomPanelHost(default_height=10)
        assert bp.reserved_height(40) == 0

    def test_reserved_height_constrained_to_half_screen(self):
        bp = BottomPanelHost(default_height=30)
        bp.show_tab("t", _DummyTab())
        assert bp.reserved_height(24) <= 12

    def test_reserved_height_min_enforced(self):
        bp = BottomPanelHost(default_height=2)
        bp.show_tab("t", _DummyTab())
        assert bp.reserved_height(40) >= bp._MIN_HEIGHT

    def test_next_and_prev_tab_cycle(self):
        bp = BottomPanelHost()
        bp.register_tab("a", _DummyTab("a"))
        bp.register_tab("b", _DummyTab("b"))
        bp.show_tab("a", focus=True)

        bp.next_tab()
        assert bp.active_tab_name == "b"

        bp.prev_tab()
        assert bp.active_tab_name == "a"

    def test_tab_cycle_skips_keys_tab(self):
        bp = BottomPanelHost()
        bp.register_tab("a", _DummyTab("a"))
        bp.register_tab("keys", _DummyTab("keys"))
        bp.register_tab("b", _DummyTab("b"))
        bp.show_tab("a", focus=True)

        bp.next_tab()
        assert bp.active_tab_name == "b"

    def test_feed_key_routes_to_active_tab_when_focused(self):
        bp = BottomPanelHost()
        tab = _DummyTab()
        bp.show_tab("t", tab, focus=True)

        assert bp.feed_key("x")
        assert tab.keys == ["x"]

    def test_feed_key_ignored_when_not_focused(self):
        bp = BottomPanelHost()
        tab = _DummyTab()
        bp.show_tab("t", tab, focus=False)

        assert not bp.feed_key("x")
        assert tab.keys == []

    def test_escape_blurs_panel_via_fallback(self):
        bp = BottomPanelHost()
        bp.show_tab("t", _DummyTab(), focus=True)

        assert bp.feed_key("<Esc>")
        assert not bp.focused
        assert bp.visible  # blur keeps panel open

    def test_q_closes_panel_via_fallback(self):
        bp = BottomPanelHost()
        bp.show_tab("t", _DummyTab(), focus=True)

        assert bp.feed_key("q")
        assert not bp.visible

    def test_bracket_keys_resize_via_fallback(self):
        bp = BottomPanelHost(default_height=10)
        bp.show_tab("t", _DummyTab(), focus=True)

        bp.feed_key("[")
        assert bp._height == 10 - bp._RESIZE_STEP

        bp.feed_key("]")
        bp.feed_key("]")
        assert bp._height == 10 - bp._RESIZE_STEP + bp._RESIZE_STEP * 2

    def test_render_produces_tab_bar_and_body(self):
        bp = BottomPanelHost()
        tab = _DummyTab("alpha")
        bp.show_tab("alpha", tab, focus=True)

        grid = CellGrid(40, 8)
        assert bp.render(grid)

        # Row 0 should contain tab title
        row0 = "".join(grid._current[0][col][0] for col in range(40))
        assert "alpha" in row0

        # Row 2+ should contain tab body content
        body = "".join(grid._current[2][col][0] for col in range(40))
        assert "alpha" in body

    def test_render_returns_false_when_hidden(self):
        bp = BottomPanelHost()
        grid = CellGrid(40, 8)
        assert not bp.render(grid)

    def test_click_tab_bar_switches_tab(self):
        bp = BottomPanelHost()
        bp.register_tab("first", _DummyTab("first"))
        bp.register_tab("second", _DummyTab("second"))
        bp.show_tab("first", focus=True)

        # Tab bar col 0 should be within "first" label " first "
        bp.click(0, 0)
        assert bp.active_tab_name == "first"

        # " first " is 7 chars, then "│" is 1, so "second" starts at col 8
        bp.click(8, 0)
        assert bp.active_tab_name == "second"

    def test_needs_full_redraw_set_on_tab_switch(self):
        bp = BottomPanelHost()
        bp.register_tab("a", _DummyTab())
        bp.register_tab("b", _DummyTab())
        bp.show_tab("a", focus=False)
        bp._needs_full_redraw = False

        bp.show_tab("b", focus=False)

        assert bp._needs_full_redraw is True

    def test_needs_full_redraw_set_on_hide(self):
        bp = BottomPanelHost()
        bp.show_tab("a", _DummyTab(), focus=False)
        bp._needs_full_redraw = False

        bp.hide()

        assert bp._needs_full_redraw is True


# ---------------------------------------------------------------------------
# LogOutputTab
# ---------------------------------------------------------------------------


class TestLogOutputTab:
    def test_add_line_and_render(self):
        tab = LogOutputTab()
        tab.add_line("hello world", 20)  # logging.INFO = 20

        grid = CellGrid(40, 4)
        tab.render(grid)

        row0 = "".join(grid._current[0][col][0] for col in range(40))
        assert "hello world" in row0

    def test_empty_output_shows_placeholder(self):
        tab = LogOutputTab()
        grid = CellGrid(40, 4)
        tab.render(grid)

        row0 = "".join(grid._current[0][col][0] for col in range(40))
        assert "no output" in row0

    def test_scroll_keys(self):
        tab = LogOutputTab()
        for i in range(20):
            tab.add_line(f"line {i}", 20)
        tab._cursor = 0
        tab._scroll = 0
        tab._auto_scroll = False

        assert tab.feed_key("j")
        assert tab._cursor == 1

        assert tab.feed_key("k")
        assert tab._cursor == 0

        assert tab.feed_key("G")
        assert tab._cursor == 19

        assert tab.feed_key("g")
        assert tab._cursor == 0

    def test_clear(self):
        tab = LogOutputTab()
        tab.add_line("x", 20)
        tab.feed_key("c")
        assert len(tab._lines) == 0

    def test_visual_mode_start_and_cancel(self):
        tab = LogOutputTab()
        for i in range(5):
            tab.add_line(f"line {i}", 20)
        tab._cursor = 2
        tab._auto_scroll = False

        assert tab.feed_key("V")
        assert tab._visual
        assert tab._visual_anchor == 2

        assert tab.feed_key("<Esc>")
        assert not tab._visual

    def test_lowercase_v_enters_visual_mode(self):
        tab = LogOutputTab()
        for i in range(5):
            tab.add_line(f"line {i}", 20)
        tab._cursor = 1
        tab._auto_scroll = False

        assert tab.feed_key("v")
        assert tab._visual
        assert tab._visual_anchor == 1

    def test_yank_current_line(self):
        yanked: list[str] = []
        tab = LogOutputTab()
        tab.yank_fn = yanked.append
        tab.add_line("alpha", 20)
        tab.add_line("beta", 20)
        tab._cursor = 0
        tab._auto_scroll = False

        tab.feed_key("y")

        assert yanked == ["alpha"]

    def test_yank_visual_selection(self):
        yanked: list[str] = []
        tab = LogOutputTab()
        tab.yank_fn = yanked.append
        for i in range(5):
            tab.add_line(f"line {i}", 20)
        tab._cursor = 1
        tab._auto_scroll = False

        tab.feed_key("V")  # visual at line 1
        tab.feed_key("j")  # extend to line 2
        tab.feed_key("j")  # extend to line 3
        tab.feed_key("y")

        assert len(yanked) == 1
        assert yanked[0] == "line 1\nline 2\nline 3"
        assert not tab._visual  # visual mode cleared after yank

    def test_visual_selection_survives_deque_overflow(self):
        """Visual anchor/cursor must stay on the same items when old lines are dropped."""
        yanked: list[str] = []
        tab = LogOutputTab()
        tab.yank_fn = yanked.append

        # Fill deque to capacity
        for i in range(tab._MAX_LINES):
            tab.add_line(f"old {i}", 20)

        # User navigates to an item near the end and enters visual mode
        tab._auto_scroll = False
        tab._cursor = tab._MAX_LINES - 3  # near the end
        tab.feed_key("V")  # anchor = _MAX_LINES - 3
        tab.feed_key("j")  # cursor = _MAX_LINES - 2
        tab.feed_key("j")  # cursor = _MAX_LINES - 1

        # Now overflow: add more lines, dropping old items from the front
        for i in range(5):
            tab.add_line(f"new {i}", 20)

        # Selection should still span 3 lines at the (adjusted) anchor+cursor
        tab.feed_key("y")

        assert len(yanked) == 1
        lines = yanked[0].splitlines()
        assert len(lines) == 3

    def test_yank_all_lines(self):
        yanked: list[str] = []
        tab = LogOutputTab()
        tab.yank_fn = yanked.append
        tab.add_line("a", 20)
        tab.add_line("b", 20)
        tab.add_line("c", 20)
        tab._auto_scroll = False

        tab.feed_key("Y")

        assert yanked == ["a\nb\nc"]

    def test_escape_in_visual_mode_consumed_by_panel(self):
        """<Esc> while in visual mode should cancel visual, not blur the panel."""
        bp = BottomPanelHost()
        tab = LogOutputTab()
        for i in range(3):
            tab.add_line(f"line {i}", 20)
        tab._auto_scroll = False
        bp.show_tab("output", tab, focus=True)

        # Enter visual mode
        bp.feed_key("V")
        assert tab._visual

        # <Esc> should cancel visual, panel stays visible and focused
        bp.feed_key("<Esc>")
        assert not tab._visual
        assert bp.focused  # panel still focused
        assert bp.visible  # panel still visible

        # Second <Esc> now blurs
        bp.feed_key("<Esc>")
        assert not bp.focused


# ---------------------------------------------------------------------------
# EventLoop integration
# ---------------------------------------------------------------------------


class TestEventLoopBottomPanelLayout:
    def test_bottom_panel_reserves_height_in_layout(self):
        loop, workspace = _make_event_loop(cols=80, rows=24)
        bp = BottomPanelHost(default_height=8)
        bp.show_tab("t", _DummyTab(), focus=False)
        loop._bottom_panel = bp

        _, _, win_rows, _, bottom_panel_rect = loop._compute_frame_layout(workspace.active_tab, 80, 24)

        assert bottom_panel_rect is not None
        assert bottom_panel_rect.height == 8
        assert win_rows == 24 - 2 - 8  # -2 status+cmdline, -8 panel

    def test_bottom_panel_rect_positioned_above_status_bar(self):
        loop, workspace = _make_event_loop(cols=80, rows=24)
        bp = BottomPanelHost(default_height=6)
        bp.show_tab("t", _DummyTab(), focus=False)
        loop._bottom_panel = bp

        _, _, _, _, bottom_panel_rect = loop._compute_frame_layout(workspace.active_tab, 80, 24)

        # Status row is at rows-2 = 22, so panel top should be at 22-6 = 16
        assert bottom_panel_rect is not None
        assert bottom_panel_rect.y == 24 - 2 - 6

    def test_no_bottom_panel_rect_when_hidden(self):
        loop, workspace = _make_event_loop(cols=80, rows=24)
        bp = BottomPanelHost()
        loop._bottom_panel = bp  # not visible

        _, _, _, _, bottom_panel_rect = loop._compute_frame_layout(workspace.active_tab, 80, 24)

        assert bottom_panel_rect is None
