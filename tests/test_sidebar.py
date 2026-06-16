from __future__ import annotations

from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.style import Style
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine
from peovim.modal.keybindings import BindingRegistry
from peovim.syntax.themes import Theme
from peovim.ui.backends.headless import HeadlessBackend
from peovim.ui.cell_grid import CellGrid
from peovim.ui.event_loop import EventLoop
from peovim.ui.layout import Rect
from peovim.ui.sidebar import SidebarHost, TreeSidebarPanel
from peovim.ui.tree_view import TreeNode, TreeView

_SIDEBAR_PLUG_DEFAULTS = [
    ("<A-h>", "SidebarFocusLeft"),
    ("<A-l>", "SidebarFocusRight"),
    ("<A-j>", "SidebarNextPanel"),
    ("<A-k>", "SidebarPrevPanel"),
]


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
    dispatcher = ActionDispatcher(engine, window, registers, editor_state=editor_state, workspace=workspace)
    dispatcher._command_registry = command_registry
    loop = EventLoop(HeadlessBackend(cols=cols, rows=rows), engine, dispatcher, workspace, editor_state=editor_state)
    # Wire binding registry with default sidebar nav bindings so nav tests work
    binding_registry = BindingRegistry(engine, dispatcher)
    for key_seq, plug_name in _SIDEBAR_PLUG_DEFAULTS:
        binding_registry.register("normal", key_seq, f"<Plug>{plug_name}")
    loop._binding_registry = binding_registry
    return loop, workspace


class _DummyPanel:
    def __init__(self, width: int = 24) -> None:
        self.width = width
        self.keys: list[str] = []
        self.rendered_widths: list[int] = []

    def render(self, grid: CellGrid) -> None:
        self.rendered_widths.append(grid.width)
        grid.write_str(0, 0, "Sidebar")

    def feed_key(self, key: str) -> bool:
        self.keys.append(key)
        return True


class _ThemeDefaultPanel:
    def __init__(self, width: int = 24) -> None:
        self.width = width

    def render(self, grid: CellGrid) -> None:
        grid.write_str(0, 0, "Panel")

    def feed_key(self, key: str) -> bool:
        return True


class TestSidebarHost:
    def test_show_panel_tracks_visibility_and_focus(self):
        host = SidebarHost()
        panel = _DummyPanel(width=22)

        host.show_panel("nav", panel, focus=True)

        assert host.visible
        assert host.focused
        assert host.active_panel_name == "nav"
        assert host.reserved_width(80) == 22

    def test_toggle_panel_hides_then_reopens(self):
        host = SidebarHost()
        panel = _DummyPanel()

        assert host.toggle_panel("nav", panel, focus=True)
        assert not host.toggle_panel("nav", panel, focus=True)
        assert not host.visible
        assert host.toggle_panel("nav", panel, focus=True)
        assert host.visible

    def test_show_active_panel_restores_last_selected_panel_after_hide(self):
        host = SidebarHost()
        first = _DummyPanel()
        second = _DummyPanel()

        host.register_panel("explorer", first)
        host.register_panel("outline", second)
        host.show_panel("outline", second, focus=True)
        host.hide()

        restored = host.show_active_panel(focus=True)

        assert restored is second
        assert host.visible
        assert host.active_panel_name == "outline"

    def test_click_header_selects_panel_and_focuses_sidebar(self):
        host = SidebarHost()
        first = _DummyPanel()
        second = _DummyPanel()
        host.register_panel("explorer", first)
        host.register_panel("outline", second)
        host.show_panel("explorer", first, focus=False)

        assert host.click(1, 0)
        assert host.active_panel_name == "outline"
        assert host.focused

    def test_feed_key_routes_only_while_focused(self):
        host = SidebarHost()
        panel = _DummyPanel()
        host.show_panel("nav", panel, focus=False)

        assert not host.feed_key("j")
        host.focus()
        assert host.feed_key("j")
        assert panel.keys == ["j"]

    def test_escape_hides_sidebar(self):
        host = SidebarHost()
        panel = _DummyPanel()
        host.show_panel("nav", panel, focus=True)

        assert host.feed_key("<Esc>")
        assert not host.visible

    def test_register_and_list_panels_preserve_order(self):
        host = SidebarHost()
        host.register_panel("explorer", _DummyPanel())
        host.register_panel("outline", _DummyPanel())

        assert host.list_panels() == ["explorer", "outline"]

    def test_next_and_prev_panel_cycle_registered_entries(self):
        host = SidebarHost()
        first = _DummyPanel()
        second = _DummyPanel()
        host.register_panel("explorer", first)
        host.register_panel("outline", second)
        host.show_panel("explorer", first, focus=True)

        host.next_panel(focus=True)
        assert host.active_panel_name == "outline"

        host.prev_panel(focus=True)
        assert host.active_panel_name == "explorer"

    def test_render_adds_accordion_headers_above_expanded_body(self):
        host = SidebarHost()
        panel = _DummyPanel(width=22)
        # Need enough rows for: 2 headers + separator + panel body + 7-line footer
        grid = CellGrid(22, 12)
        host.register_panel("git-status", _DummyPanel(width=22))

        host.show_panel("outline", panel, focus=True)

        assert host.render(grid)
        first_header = "".join(grid._current[0][col][0] for col in range(grid.width))
        second_header = "".join(grid._current[1][col][0] for col in range(grid.width))
        separator = "".join(grid._current[2][col][0] for col in range(grid.width))
        body = "".join(grid._current[3][col][0] for col in range(grid.width))
        assert "git status" in first_header
        assert "outline" in second_header
        assert "▼" in second_header
        assert "─" in separator
        assert body.startswith("Sidebar")
        assert panel.rendered_widths == [22]

    def test_render_applies_theme_defaults_to_panel_body_cells(self):
        host = SidebarHost()
        panel = _ThemeDefaultPanel(width=12)
        # Need enough rows for: header + separator + panel body + 7-line footer
        grid = CellGrid(12, 10)
        theme = Theme(name="test", groups={}, default_fg=(200, 200, 200), default_bg=(31, 31, 31))

        host.show_panel("outline", panel, focus=False)

        assert host.render(grid, theme=theme)
        assert grid._current[2][0][1] == (200, 200, 200)
        assert grid._current[2][0][2] == (31, 31, 31)
        assert grid._current[2][6][2] == (31, 31, 31)

    def test_render_uses_configured_sidebar_background_override(self):
        host = SidebarHost()
        panel = _ThemeDefaultPanel(width=12)
        grid = CellGrid(12, 4)
        theme = Theme(name="test", groups={}, default_fg=(200, 200, 200), default_bg=(31, 31, 31))
        host.set_style(background="#252526")

        host.show_panel("outline", panel, focus=False)

        assert host.render(grid, theme=theme)
        assert grid._current[2][0][2] == (37, 37, 38)
        assert grid._current[2][6][2] == (37, 37, 38)

    def test_render_uses_theme_sidebar_header_groups(self):
        host = SidebarHost()
        host.register_panel("explorer", _DummyPanel(width=12))
        host.register_panel("outline", _DummyPanel(width=12))
        host.show_panel("explorer", host.get_panel("explorer"), focus=False)
        grid = CellGrid(12, 4)
        theme = Theme(
            name="test",
            groups={
                "sidebar.header.active": Style(fg="#111111", bg="#223344"),
                "sidebar.header.inactive": Style(fg="#556677", bg="#8899AA"),
            },
            default_fg=(200, 200, 200),
            default_bg=(31, 31, 31),
        )

        assert host.render(grid, theme=theme)
        assert grid._current[0][0][1] == (17, 17, 17)
        assert grid._current[0][0][2] == (34, 51, 68)
        assert grid._current[1][0][1] == (85, 102, 119)
        assert grid._current[1][0][2] == (136, 153, 170)


class TestTreeSidebarPanel:
    def test_render_uses_tree_view(self):
        tree = TreeView([TreeNode(label="alpha")], title="Tree", width=20)
        panel = TreeSidebarPanel(tree, width=20)
        grid = CellGrid(20, 6)

        panel.render(grid)

        rendered = "\n".join(
            "".join(grid._current[row][col][0] for col in range(grid.width)) for row in range(grid.height)
        )
        assert "alpha" in rendered
        assert "Tree" in rendered


class TestEventLoopSidebarLayout:
    def test_sidebar_reserves_left_width_from_workspace_layout(self):
        loop, workspace = _make_event_loop(cols=80, rows=24)
        host = SidebarHost()
        host.show_panel("nav", _DummyPanel(width=20), focus=False)
        loop._sidebar = host

        layout, separators, win_rows, sidebar_rect, _bp_rect = loop._compute_frame_layout(workspace.active_tab, 80, 24)

        leaf = workspace.active_tab.root
        assert sidebar_rect is not None
        assert sidebar_rect.width == 20
        assert layout[leaf].x == 21
        assert any(sep.x == 20 and sep.width == 1 for sep in separators)

    def test_alt_h_is_not_intercepted_when_sidebar_is_visible_but_not_focused(self):
        loop, _workspace = _make_event_loop(cols=80, rows=24)
        host = SidebarHost()
        host.show_panel("nav", _DummyPanel(width=20), focus=False)
        loop._sidebar = host

        assert not loop._handle_sidebar_navigation_key("<A-h>")
        assert not host.focused

    def test_alt_h_wraps_from_focused_sidebar_to_rightmost_editor_window(self):
        loop, workspace = _make_event_loop(cols=80, rows=24)
        workspace.active_tab.split_vertical()
        leftmost = workspace.active_tab.all_windows()[0]
        rightmost = workspace.active_tab.all_windows()[-1]
        workspace.active_tab.focus_window(leftmost)
        loop._dispatcher.window = workspace.active_window
        host = SidebarHost()
        host.show_panel("nav", _DummyPanel(width=20), focus=True)
        loop._sidebar = host

        assert loop._handle_sidebar_navigation_key("<A-h>")
        assert not host.focused
        assert workspace.active_window is rightmost

    def test_alt_l_wraps_from_focused_sidebar_to_leftmost_editor_window(self):
        loop, workspace = _make_event_loop(cols=80, rows=24)
        workspace.active_tab.split_vertical()
        leftmost = workspace.active_tab.all_windows()[0]
        workspace.active_tab.focus_window(workspace.active_tab.all_windows()[-1])
        loop._dispatcher.window = workspace.active_window
        host = SidebarHost()
        host.show_panel("nav", _DummyPanel(width=20), focus=True)
        loop._sidebar = host

        assert loop._handle_sidebar_navigation_key("<A-l>")
        assert not host.focused
        assert workspace.active_window is leftmost

    def test_alt_j_and_alt_k_cycle_panels_only_when_sidebar_focused(self):
        loop, _workspace = _make_event_loop(cols=80, rows=24)
        host = SidebarHost()
        first = _DummyPanel(width=20)
        second = _DummyPanel(width=24)
        host.register_panel("explorer", first)
        host.register_panel("outline", second)
        host.show_panel("explorer", first, focus=True)
        loop._sidebar = host

        assert loop._handle_sidebar_navigation_key("<A-j>")
        assert host.active_panel_name == "outline"

        assert loop._handle_sidebar_navigation_key("<A-k>")
        assert host.active_panel_name == "explorer"

    def test_render_sidebar_uses_provided_theme_without_re_resolving(self):
        loop, _workspace = _make_event_loop(cols=80, rows=24)
        host = SidebarHost()
        host.show_panel("nav", _DummyPanel(width=20), focus=False)
        loop._sidebar = host
        grid = CellGrid(80, 24)
        theme = Theme(name="test", groups={}, default_fg=(200, 200, 200), default_bg=(31, 31, 31))

        loop._resolve_frame_theme = lambda: (_ for _ in ()).throw(AssertionError("theme should be reused"))

        loop._render_sidebar(grid, Rect(0, 0, 20, 22), theme)

        rendered = "".join(grid._current[0][col][0] for col in range(20))
        assert "nav" in rendered or "Sidebar" in "\n".join(
            "".join(grid._current[row][col][0] for col in range(20)) for row in range(24)
        )

    def test_compute_frame_layout_reuses_cached_layout_when_signature_matches(self, monkeypatch):
        loop, workspace = _make_event_loop(cols=80, rows=24)
        calls: list[int] = []

        original = __import__("peovim.ui.frame_controller", fromlist=["compute_layout"]).compute_layout

        def fake_compute_layout(root, rect):
            calls.append(rect.width)
            return original(root, rect)

        monkeypatch.setattr("peovim.ui.frame_controller.compute_layout", fake_compute_layout)

        first = loop._compute_frame_layout(workspace.active_tab, 80, 24)
        second = loop._compute_frame_layout(workspace.active_tab, 80, 24)

        assert first == second
        assert len(calls) == 1

    def test_compute_frame_layout_cache_invalidates_when_split_ratio_changes(self, monkeypatch):
        loop, workspace = _make_event_loop(cols=80, rows=24)
        workspace.active_tab.split_vertical()
        root = workspace.active_tab.root
        calls: list[int] = []

        original = __import__("peovim.ui.frame_controller", fromlist=["compute_layout"]).compute_layout

        def fake_compute_layout(tree, rect):
            calls.append(rect.width)
            return original(tree, rect)

        monkeypatch.setattr("peovim.ui.frame_controller.compute_layout", fake_compute_layout)

        loop._compute_frame_layout(workspace.active_tab, 80, 24)
        root.ratio = 0.3
        loop._compute_frame_layout(workspace.active_tab, 80, 24)

        assert len(calls) == 2


class TestSidebarFullRedrawFlag:
    def test_flag_set_when_panel_switches(self):
        host = SidebarHost()
        panel_a = _DummyPanel(width=20)
        panel_b = _DummyPanel(width=20)
        host.show_panel("a", panel_a, focus=False)
        host._needs_full_redraw = False  # clear after first show

        host.show_panel("b", panel_b, focus=False)

        assert host._needs_full_redraw is True

    def test_flag_set_on_hide(self):
        host = SidebarHost()
        host.show_panel("a", _DummyPanel(width=20), focus=False)
        host._needs_full_redraw = False

        host.hide()

        assert host._needs_full_redraw is True

    def test_flag_not_set_when_showing_same_panel_again(self):
        host = SidebarHost()
        panel_a = _DummyPanel(width=20)
        host.show_panel("a", panel_a, focus=False)
        host._needs_full_redraw = False

        host.show_panel("a", panel_a, focus=False)

        assert host._needs_full_redraw is False

    def test_render_sidebar_invalidates_prev_rows_on_panel_switch(self):
        loop, _workspace = _make_event_loop(cols=80, rows=24)
        host = SidebarHost()
        host.show_panel("nav", _DummyPanel(width=20), focus=False)
        host._needs_full_redraw = False
        host.show_panel("other", _DummyPanel(width=20), focus=False)  # sets flag
        loop._sidebar = host
        grid = CellGrid(80, 24)
        # Plant sentinel-free prev so we can detect invalidation
        for row in range(22):
            grid._prev[row] = [("X", (0, 0, 0), (0, 0, 0), 0)] * 80

        loop._render_sidebar(grid, Rect(0, 0, 20, 22))

        # After render, flag is cleared
        assert host._needs_full_redraw is False
        # All sidebar rows should be dirty (invalidated)
        sidebar_rows = list(range(22))
        assert all(r in grid._dirty_rows for r in sidebar_rows)
