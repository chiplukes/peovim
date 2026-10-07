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
    def test_selector_flash_matches_only_visible_short_labels(self):
        host = SidebarHost()
        host.register_panel("git-status", _DummyPanel())
        host.register_panel("file_history", _DummyPanel())
        host.show_panel("explorer", _DummyPanel(), focus=True)
        host.render(CellGrid(38, 2))
        assert host.flash_matches("fi") == [(2, 1)]
        assert host.flash_matches("HI") == [(1, 1)]
        assert host.flash_matches("gi") == []
        assert host.flash_matches("ex") == []
        host.render(CellGrid(2, 2))
        assert host.flash_matches("fi") == []
        host.hide()
        assert host.flash_matches("hi") == []

    def test_h_j_k_l_moves_between_selector_and_panel_without_forwarding_selector_keys(self):
        host = SidebarHost()
        first = _DummyPanel()
        second = _DummyPanel()
        host.register_panel("explorer", first)
        host.register_panel("diagnostics", second)
        host.show_panel("explorer", first, focus=True)
        host.feed_key("h")
        assert host.selector_focused
        host.feed_key("j")
        assert host.active_panel_name == "diagnostics"
        host.feed_key("k")
        assert host.active_panel_name == "explorer"
        host.feed_key("k")
        assert host.active_panel_name == "diagnostics"
        assert host.selector_focused
        assert first.keys == second.keys == []
        host.feed_key("l")
        assert not host.selector_focused
        host.feed_key("j")
        assert second.keys == ["j"]

    def test_selector_blurs_tree_until_content_is_focused(self):
        host = SidebarHost()
        tree = TreeView([TreeNode("file.py")])
        panel = TreeSidebarPanel(tree)
        host.show_panel("explorer", panel, focus=True)
        host.feed_key("h")
        host.render(CellGrid(38, 12))
        assert not tree.focused
        host.feed_key("l")
        host.render(CellGrid(38, 12))
        assert tree.focused

    def test_left_arrow_collapses_tree_while_h_focuses_selector(self):
        folder = TreeNode("folder", children_fn=lambda: [TreeNode("file")], expanded=True)
        tree = TreeView([folder])
        host = SidebarHost()
        host.show_panel("explorer", TreeSidebarPanel(tree), focus=True)
        host.feed_key("h")
        assert folder.expanded
        host.feed_key("l")
        host.feed_key("<Left>")
        assert not folder.expanded
        assert not host.selector_focused

    def test_selector_scrolls_to_active_panel_and_mouse_uses_scroll_offset(self):
        host = SidebarHost()
        for index in range(12):
            host.register_panel(f"panel{index}", _DummyPanel())
        host.show_panel("panel0", host.get_panel("panel0"), focus=True)
        host.feed_key("h")
        for _ in range(10):
            host.feed_key("j")
        host.render(CellGrid(32, 4))
        assert host.cursor_row(4) == 3
        assert host._selector_scroll == 7
        host.click(0, 1)
        assert host.active_panel_name == "panel7"
        assert host.selector_focused

    def test_selector_labels_and_full_content_height(self):
        from peovim.ui.sidebar import _format_panel_label

        assert _format_panel_label("explorer") == "file"
        assert _format_panel_label("verilog_hierarchy") == "vlog"
        assert _format_panel_label("file_history") == "hist"
        host = SidebarHost()

        class FullHeightPanel(_DummyPanel):
            def render(self, grid):
                grid.write_str(grid.height - 1, 0, "last")

        host.show_panel("explorer", FullHeightPanel(), focus=True)
        grid = CellGrid(32, 4)
        host.render(grid)
        assert "".join(cell[0] for cell in grid._current[3][8:12]) == "last"
        assert "".join(cell[0] for cell in grid._current[0][:7]).strip() == "file"

    def test_show_panel_tracks_visibility_and_focus(self):
        host = SidebarHost()
        panel = _DummyPanel(width=22)

        host.show_panel("nav", panel, focus=True)

        assert host.visible
        assert host.focused
        assert host.active_panel_name == "nav"
        assert host.reserved_width(80) == 30

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

    def test_first_open_defaults_to_explorer_regardless_of_registration_order(self):
        host = SidebarHost()
        git = _DummyPanel()
        files = _DummyPanel()
        host.register_panel("git-status", git)
        host.register_panel("explorer", files)
        host.register_panel("diagnostics", _DummyPanel())

        assert not host.visible
        assert host.show_active_panel(focus=True) is files
        assert host.active_panel_name == "explorer"
        assert host.focused

    def test_initial_toggle_defaults_to_explorer_with_unified_registration(self):
        host = SidebarHost()
        host.register_tab("git-status", _DummyPanel())
        host.register_tab("explorer", _DummyPanel())

        assert host.toggle(focus=False)
        assert host.active_panel_name == "explorer"
        assert not host.focused

    def test_default_falls_back_to_first_panel_without_explorer(self):
        host = SidebarHost()
        first = _DummyPanel()
        host.register_panel("git-status", first)
        host.register_panel("diagnostics", _DummyPanel())

        assert host.show_active_panel(focus=False) is first
        assert host.active_panel_name == "git-status"

    def test_click_selector_selects_panel_and_focuses_selector(self):
        host = SidebarHost()
        first = _DummyPanel()
        second = _DummyPanel()
        host.register_panel("explorer", first)
        host.register_panel("outline", second)
        host.show_panel("explorer", first, focus=False)

        assert host.click(1, 0)
        assert host.active_panel_name == "outline"
        assert host.focused

    def test_click_body_forwards_to_panel_click(self):
        host = SidebarHost()
        clicks: list[tuple[int, int]] = []

        class _ClickablePanel(_DummyPanel):
            def click(self, row: int, col: int) -> bool:
                clicks.append((row, col))
                return True

        panel = _ClickablePanel()
        host.register_panel("git-status", _DummyPanel())
        host.show_panel("explorer", panel, focus=False)

        # Content starts at column 8 and row 0.
        assert host.click(0, 12)
        assert clicks == [(0, 4)]
        assert host.focused

    def test_click_body_via_tree_sidebar_panel_toggles_folder(self):
        host = SidebarHost()
        child = TreeNode(label="child.py")
        folder = TreeNode(label="dir", children_fn=lambda: [child])
        tree = TreeView([folder], title="Explorer", width=20)
        panel = TreeSidebarPanel(tree, width=20)
        host.show_panel("explorer", panel, focus=False)

        # row 0 = tree title, row 1 = first node
        assert host.click(1, 8)
        assert folder.expanded
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

    def test_render_places_short_selector_labels_beside_full_height_content(self):
        host = SidebarHost()
        panel = _DummyPanel(width=22)
        grid = CellGrid(30, 12)
        host.register_panel("git-status", _DummyPanel(width=22))
        host.show_panel("outline", panel, focus=True)
        assert host.render(grid)
        first = "".join(cell[0] for cell in grid._current[0])
        second = "".join(cell[0] for cell in grid._current[1])
        assert first.startswith(" git   │Sidebar")
        assert second.startswith(" oline │")
        assert grid._current[1][0][2] != grid._current[0][0][2]
        assert grid._current[-1][7][0] == "│"
        assert panel.rendered_widths == [22]

    def test_render_applies_theme_defaults_to_panel_body_cells(self):
        host = SidebarHost()
        panel = _ThemeDefaultPanel(width=12)
        # Content uses the full height beside the selector.
        grid = CellGrid(12, 10)
        theme = Theme(name="test", groups={}, default_fg=(200, 200, 200), default_bg=(31, 31, 31))

        host.show_panel("outline", panel, focus=False)

        assert host.render(grid, theme=theme)
        assert grid._current[0][8][1] == (200, 200, 200)
        assert grid._current[0][8][2] == (31, 31, 31)
        assert grid._current[0][10][2] == (31, 31, 31)

    def test_render_uses_configured_sidebar_background_override(self):
        host = SidebarHost()
        panel = _ThemeDefaultPanel(width=12)
        grid = CellGrid(12, 4)
        theme = Theme(name="test", groups={}, default_fg=(200, 200, 200), default_bg=(31, 31, 31))
        host.set_style(background="#252526")

        host.show_panel("outline", panel, focus=False)

        assert host.render(grid, theme=theme)
        assert grid._current[0][8][2] == (37, 37, 38)
        assert grid._current[0][10][2] == (37, 37, 38)

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
        assert grid._current[1][0][2] == (31, 31, 31)


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
        assert sidebar_rect.width == 28
        assert layout[leaf].x == 29
        assert any(sep.x == 28 and sep.width == 1 for sep in separators)

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


class TestSidebarLeaderPassthrough:
    def test_flash_key_falls_through_when_sidebar_focused(self):
        from unittest.mock import MagicMock

        loop, _workspace = _make_event_loop(cols=80, rows=24)
        host = SidebarHost()
        panel = _DummyPanel(width=20)
        host.show_panel("nav", panel, focus=True)
        loop._sidebar = host
        loop._flash = MagicMock(is_active=False)

        assert loop._handle_overlay_key("s") is False
        assert panel.keys == []

    def test_leader_key_falls_through_when_sidebar_focused(self):
        loop, _workspace = _make_event_loop(cols=80, rows=24)
        host = SidebarHost()
        panel = _DummyPanel(width=20)
        host.show_panel("nav", panel, focus=True)
        loop._sidebar = host

        assert loop._handle_overlay_key("\\") is False
        assert panel.keys == []

    def test_regular_key_still_consumed_by_focused_sidebar(self):
        loop, _workspace = _make_event_loop(cols=80, rows=24)
        host = SidebarHost()
        panel = _DummyPanel(width=20)
        host.show_panel("nav", panel, focus=True)
        loop._sidebar = host

        assert loop._handle_overlay_key("j") is True
        assert panel.keys == ["j"]

    def test_pending_prefix_key_falls_through_when_sidebar_focused(self):
        loop, _workspace = _make_event_loop(cols=80, rows=24)
        host = SidebarHost()
        panel = _DummyPanel(width=20)
        host.show_panel("nav", panel, focus=True)
        loop._sidebar = host
        loop._engine._state.key_buffer = ["\\"]

        assert loop._handle_overlay_key("w") is False
        assert panel.keys == []

    def test_leader_key_not_intercepted_when_sidebar_unfocused(self):
        loop, _workspace = _make_event_loop(cols=80, rows=24)
        host = SidebarHost()
        panel = _DummyPanel(width=20)
        host.show_panel("nav", panel, focus=False)
        loop._sidebar = host

        assert loop._handle_overlay_key("\\") is False
        assert panel.keys == []


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
