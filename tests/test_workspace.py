"""
Split tree operations: split, close, focus, resize; tab pages.
"""

import pytest

from peovim.core.document import Document
from peovim.core.window import Window
from peovim.core.workspace import HSplitNode, TabPage, VSplitNode, WindowLeaf, Workspace


def make_window(content: str = "") -> Window:
    doc = Document()
    doc.load_string(content)
    return Window(doc, width=80, height=24)


# ---------------------------------------------------------------------------
# WindowLeaf
# ---------------------------------------------------------------------------


class TestWindowLeaf:
    def test_leaf_wraps_window(self):
        win = make_window("hello")
        leaf = WindowLeaf(win)
        assert leaf.window is win

    def test_leaf_is_leaf(self):
        leaf = WindowLeaf(make_window())
        assert leaf.is_leaf

    def test_split_node_is_not_leaf(self):
        a = WindowLeaf(make_window())
        b = WindowLeaf(make_window())
        node = HSplitNode(a, b)
        assert not node.is_leaf


# ---------------------------------------------------------------------------
# TabPage
# ---------------------------------------------------------------------------


class TestTabPage:
    def test_initial_window(self):
        win = make_window("hello")
        page = TabPage(root=WindowLeaf(win))
        assert page.active_window is win

    def test_split_horizontal(self):
        win = make_window("hello")
        page = TabPage(root=WindowLeaf(win))
        new_win = page.split_horizontal()
        assert isinstance(page.root, HSplitNode)
        assert page.active_window is new_win

    def test_split_vertical(self):
        win = make_window("hello")
        page = TabPage(root=WindowLeaf(win))
        new_win = page.split_vertical()
        assert isinstance(page.root, VSplitNode)
        assert page.active_window is new_win

    def test_all_windows_after_splits(self):
        win = make_window("a")
        page = TabPage(root=WindowLeaf(win))
        page.split_horizontal()
        page.split_vertical()
        wins = page.all_windows()
        assert len(wins) == 3

    def test_close_split(self):
        win = make_window("a")
        page = TabPage(root=WindowLeaf(win))
        page.split_horizontal()
        assert len(page.all_windows()) == 2
        page.close_active()
        assert len(page.all_windows()) == 1

    def test_close_last_window_raises(self):
        win = make_window("a")
        page = TabPage(root=WindowLeaf(win))
        with pytest.raises(ValueError):
            page.close_active()

    def test_focus_next(self):
        win = make_window("a")
        page = TabPage(root=WindowLeaf(win))
        new_win = page.split_horizontal()
        assert page.active_window is new_win
        page.focus_next()
        assert page.active_window is win

    def test_focus_prev(self):
        win = make_window("a")
        page = TabPage(root=WindowLeaf(win))
        page.split_horizontal()
        page.focus_prev()
        assert page.active_window is win

    def test_close_active_splices_nested_parent_and_preserves_remaining_ratios(self):
        win_a = make_window("a")
        win_b = make_window("b")
        win_c = make_window("c")
        inner = HSplitNode(WindowLeaf(win_a), WindowLeaf(win_b), ratio=0.7)
        page = TabPage(root=VSplitNode(inner, WindowLeaf(win_c), ratio=0.6))
        page.focus_window(win_b)

        page.close_active()

        assert isinstance(page.root, VSplitNode)
        assert page.root.ratio == 0.6
        assert isinstance(page.root.left, WindowLeaf)
        assert page.root.left.window is win_a
        assert isinstance(page.root.right, WindowLeaf)
        assert page.root.right.window is win_c


# ---------------------------------------------------------------------------
# Workspace (tabs)
# ---------------------------------------------------------------------------


class TestWorkspace:
    def test_initial_tab(self):
        win = make_window("hello")
        ws = Workspace(initial_window=win)
        assert ws.active_tab_index == 0
        assert ws.active_window is win

    def test_new_tab(self):
        win = make_window()
        ws = Workspace(initial_window=win)
        win2 = make_window()
        ws.new_tab(win2)
        assert ws.active_tab_index == 1
        assert ws.active_window is win2

    def test_switch_tab(self):
        win = make_window()
        ws = Workspace(initial_window=win)
        win2 = make_window()
        ws.new_tab(win2)
        ws.goto_tab(0)
        assert ws.active_window is win

    def test_close_tab(self):
        win = make_window()
        ws = Workspace(initial_window=win)
        ws.new_tab(make_window())
        ws.close_tab(1)
        assert len(ws.tabs) == 1

    def test_close_last_tab_raises(self):
        ws = Workspace(initial_window=make_window())
        with pytest.raises(ValueError):
            ws.close_tab(0)

    def test_tab_count(self):
        ws = Workspace(initial_window=make_window())
        ws.new_tab(make_window())
        ws.new_tab(make_window())
        assert len(ws.tabs) == 3


# ---------------------------------------------------------------------------
# TabPage.focus_direction and only_window
# ---------------------------------------------------------------------------


class TestFocusDirection:
    def test_focus_right_vsplit(self):
        """l moves from left to right in a VSplit."""
        win_left = make_window("left")
        page = TabPage(root=WindowLeaf(win_left))
        # focus is on left after split_vertical; active becomes the new (right) win
        win_right = page.split_vertical()
        page.focus_window(win_left)
        page.focus_direction("l")
        assert page.active_window is win_right

    def test_focus_left_vsplit(self):
        """h moves from right to left in a VSplit."""
        win_left = make_window("left")
        page = TabPage(root=WindowLeaf(win_left))
        page.split_vertical()  # active = win_right
        page.focus_direction("h")
        assert page.active_window is win_left

    def test_focus_down_hsplit(self):
        """j moves from top to bottom in an HSplit."""
        win_top = make_window("top")
        page = TabPage(root=WindowLeaf(win_top))
        win_bottom = page.split_horizontal()  # active = win_bottom
        page.focus_window(win_top)
        page.focus_direction("j")
        assert page.active_window is win_bottom

    def test_focus_up_hsplit(self):
        """k moves from bottom to top in an HSplit."""
        win_top = make_window("top")
        page = TabPage(root=WindowLeaf(win_top))
        page.split_horizontal()  # active = win_bottom
        page.focus_direction("k")
        assert page.active_window is win_top

    def test_focus_direction_no_move_at_edge(self):
        """h on the leftmost window does nothing."""
        win = make_window()
        page = TabPage(root=WindowLeaf(win))
        page.split_horizontal()  # still only one column
        page.focus_window(win)
        page.focus_direction("h")
        assert page.active_window is win

    def test_focus_direction_invalid_direction_ignored(self):
        """Unknown direction is silently ignored."""
        win = make_window()
        page = TabPage(root=WindowLeaf(win))
        page.focus_direction("x")
        assert page.active_window is win


class TestOnlyWindow:
    def test_only_window_removes_splits(self):
        win = make_window("main")
        page = TabPage(root=WindowLeaf(win))
        page.split_horizontal()
        page.split_vertical()
        assert len(page.all_windows()) == 3
        page.focus_window(win)
        page.only_window()
        assert len(page.all_windows()) == 1
        assert page.active_window is win

    def test_only_window_single_window_is_noop(self):
        win = make_window()
        page = TabPage(root=WindowLeaf(win))
        page.only_window()
        assert page.active_window is win


class TestResizeWindow:
    def test_resize_active_vertical_adjusts_ratio(self):
        win = make_window("main")
        page = TabPage(root=WindowLeaf(win))
        right = page.split_vertical()
        page.focus_window(win)

        resized = page.resize_active("h", 1)

        assert resized is True
        assert isinstance(page.root, VSplitNode)
        assert page.root.ratio > 0.5
        page.focus_window(right)
        page.resize_active("h", 1)
        assert page.root.ratio < 0.55

    def test_equalize_window_sizes_resets_ratios(self):
        win = make_window("main")
        page = TabPage(root=WindowLeaf(win))
        page.split_vertical()
        page.resize_active("h", 2)

        page.equalize_window_sizes()

        assert isinstance(page.root, VSplitNode)
        assert page.root.ratio == 0.5

    def test_toggle_expand_active_width_biases_layout(self):
        win = make_window("main")
        page = TabPage(root=WindowLeaf(win))
        page.split_vertical()
        page.focus_window(win)

        expanded = page.toggle_expand_active_width(0.75)

        assert expanded is True
        assert isinstance(page.root, VSplitNode)
        assert page.root.ratio > 0.7

        expanded = page.toggle_expand_active_width(0.75)

        assert expanded is False
        assert page.root.ratio == 0.5

    def test_toggle_expand_requires_direct_vertical_split(self):
        win = make_window("main")
        page = TabPage(root=WindowLeaf(win))
        right = page.split_vertical()
        page.split_horizontal()
        page.focus_window(right)

        expanded = page.toggle_expand_active_width(0.75)

        assert expanded is False
        assert isinstance(page.root, VSplitNode)
        assert page.root.ratio == 0.5

    def test_resize_active_requires_direct_enclosing_split(self):
        win = make_window("main")
        page = TabPage(root=WindowLeaf(win))
        right = page.split_vertical()
        page.split_horizontal()
        page.focus_window(right)

        resized = page.resize_active("h", 1)

        assert resized is False
        assert isinstance(page.root, VSplitNode)
        assert page.root.ratio == 0.5


# ---------------------------------------------------------------------------
# Window management dispatcher integration
# ---------------------------------------------------------------------------


class TestWindowDispatcher:
    def _make_dispatcher(self):
        from peovim.core.registers import RegisterStore
        from peovim.modal.dispatcher import ActionDispatcher
        from peovim.modal.engine import ModalEngine

        win = make_window("hello\nworld")
        ws = Workspace(win)
        engine = ModalEngine()
        engine.set_document(win.document)
        disp = ActionDispatcher(engine, win, RegisterStore(), workspace=ws)
        return disp, ws, win

    def test_split_horizontal_action(self):
        from peovim.modal.actions import SplitWindow

        disp, ws, _win = self._make_dispatcher()
        disp.dispatch([SplitWindow("h")])
        assert len(ws.active_tab.all_windows()) == 2

    def test_split_vertical_action(self):
        from peovim.modal.actions import SplitWindow

        disp, ws, _win = self._make_dispatcher()
        disp.dispatch([SplitWindow("v")])
        assert len(ws.active_tab.all_windows()) == 2

    def test_close_window_action(self):
        from peovim.modal.actions import CloseWindow, SplitWindow

        disp, ws, _win = self._make_dispatcher()
        disp.dispatch([SplitWindow("h")])
        assert len(ws.active_tab.all_windows()) == 2
        disp.dispatch([CloseWindow()])
        assert len(ws.active_tab.all_windows()) == 1

    def test_focus_window_action(self):
        from peovim.modal.actions import FocusWindow, SplitWindow

        disp, ws, win_orig = self._make_dispatcher()
        disp.dispatch([SplitWindow("v")])
        win_new = ws.active_window
        assert win_new is not win_orig
        disp.dispatch([FocusWindow("h")])
        assert ws.active_window is win_orig

    def test_smart_focus_window_wraps_across_nested_vertical_splits(self):
        from peovim.modal.actions import SmartFocusWindow, SplitWindow

        disp, ws, win_left = self._make_dispatcher()
        disp.dispatch([SplitWindow("v")])
        win_middle = ws.active_window
        disp.dispatch([SplitWindow("v")])
        win_right = ws.active_window

        assert ws.active_tab.all_windows() == [win_left, win_middle, win_right]

        disp.dispatch([SmartFocusWindow("l")])
        assert ws.active_window is win_left

    def test_smart_focus_window_wraps_backwards_across_nested_vertical_splits(self):
        from peovim.modal.actions import SmartFocusWindow, SplitWindow

        disp, ws, win_left = self._make_dispatcher()
        disp.dispatch([SplitWindow("v")])
        disp.dispatch([SplitWindow("v")])
        win_right = ws.active_window

        ws.active_tab.focus_window(win_left)
        disp.window = win_left
        disp.dispatch([SmartFocusWindow("h")])

        assert ws.active_window is win_right

    def test_only_window_action(self):
        from peovim.modal.actions import OnlyWindow, SplitWindow

        disp, ws, _win = self._make_dispatcher()
        disp.dispatch([SplitWindow("h")])
        disp.dispatch([SplitWindow("v")])
        assert len(ws.active_tab.all_windows()) == 3
        disp.dispatch([OnlyWindow()])
        assert len(ws.active_tab.all_windows()) == 1

    def test_new_tab_action(self):
        from peovim.modal.actions import NewTab

        disp, ws, _win = self._make_dispatcher()
        disp.dispatch([NewTab()])
        assert len(ws.tabs) == 2

    def test_tab_close_action(self):
        from peovim.modal.actions import NewTab, TabClose

        disp, ws, _win = self._make_dispatcher()
        disp.dispatch([NewTab()])
        assert len(ws.tabs) == 2
        disp.dispatch([TabClose()])
        assert len(ws.tabs) == 1

    def test_next_prev_tab_action(self):
        from peovim.modal.actions import NewTab, NextTab, PrevTab

        disp, ws, _win = self._make_dispatcher()
        disp.dispatch([NewTab()])
        disp.dispatch([NewTab()])
        assert ws.active_tab_index == 2
        disp.dispatch([PrevTab()])
        assert ws.active_tab_index == 1
        disp.dispatch([NextTab()])
        assert ws.active_tab_index == 2

    def test_dispatcher_window_synced_after_split(self):
        """dispatcher.window should point to active window after a split."""
        from peovim.modal.actions import SplitWindow

        disp, ws, win_orig = self._make_dispatcher()
        disp.dispatch([SplitWindow("v")])
        assert disp.window is ws.active_window
        assert disp.window is not win_orig

    def test_resize_window_action_updates_ratio(self):
        from peovim.modal.actions import ResizeWindow, SplitWindow

        disp, ws, _win = self._make_dispatcher()
        disp.dispatch([SplitWindow("v")])

        disp.dispatch([ResizeWindow("h", 1)])

        assert isinstance(ws.active_tab.root, VSplitNode)
        assert ws.active_tab.root.ratio < 0.5

    def test_resize_window_action_reports_missing_local_split(self):
        from peovim.core.editor_state import EditorState
        from peovim.core.registers import RegisterStore
        from peovim.modal.actions import ResizeWindow, SplitWindow
        from peovim.modal.dispatcher import ActionDispatcher
        from peovim.modal.engine import ModalEngine

        win = make_window("hello\nworld")
        ws = Workspace(win)
        engine = ModalEngine()
        engine.set_document(win.document)
        editor_state = EditorState()
        disp = ActionDispatcher(engine, win, RegisterStore(), editor_state=editor_state, workspace=ws)
        disp.dispatch([SplitWindow("v")])
        right = ws.active_window
        disp.dispatch([SplitWindow("h")])
        ws.active_tab.focus_window(right)

        disp.dispatch([ResizeWindow("h", 1)])

        assert editor_state.message == "No local horizontal split to resize"

    def test_toggle_window_expand_action_updates_ratio(self):
        from peovim.modal.actions import SplitWindow, ToggleWindowExpand

        disp, ws, win_orig = self._make_dispatcher()
        disp.dispatch([SplitWindow("v")])
        ws.active_tab.focus_window(win_orig)

        disp.dispatch([ToggleWindowExpand(0.75)])

        assert isinstance(ws.active_tab.root, VSplitNode)
        assert ws.active_tab.root.ratio > 0.7

    def test_toggle_window_expand_reports_missing_local_vertical_split(self):
        from peovim.core.editor_state import EditorState
        from peovim.core.registers import RegisterStore
        from peovim.modal.actions import SplitWindow, ToggleWindowExpand
        from peovim.modal.dispatcher import ActionDispatcher
        from peovim.modal.engine import ModalEngine

        win = make_window("hello\nworld")
        ws = Workspace(win)
        engine = ModalEngine()
        engine.set_document(win.document)
        editor_state = EditorState()
        disp = ActionDispatcher(engine, win, RegisterStore(), editor_state=editor_state, workspace=ws)
        disp.dispatch([SplitWindow("v")])
        right = ws.active_window
        disp.dispatch([SplitWindow("h")])
        ws.active_tab.focus_window(right)

        disp.dispatch([ToggleWindowExpand(0.75)])

        assert editor_state.message == "No local vertical split to expand"


# ---------------------------------------------------------------------------
# Engine keybindings for window management
# ---------------------------------------------------------------------------


class TestWindowEngineKeys:
    def _make_engine(self):
        from peovim.modal.engine import ModalEngine

        eng = ModalEngine()
        return eng

    def test_ctrl_w_s_splits_horizontal(self):
        from peovim.modal.actions import SplitWindow

        eng = self._make_engine()
        eng.feed_key("<C-w>")
        actions = eng.feed_key("s")
        assert any(isinstance(a, SplitWindow) and a.direction == "h" for a in actions)

    def test_ctrl_w_v_splits_vertical(self):
        from peovim.modal.actions import SplitWindow

        eng = self._make_engine()
        eng.feed_key("<C-w>")
        actions = eng.feed_key("v")
        assert any(isinstance(a, SplitWindow) and a.direction == "v" for a in actions)

    def test_ctrl_w_q_closes_window(self):
        from peovim.modal.actions import CloseWindow

        eng = self._make_engine()
        eng.feed_key("<C-w>")
        actions = eng.feed_key("q")
        assert any(isinstance(a, CloseWindow) for a in actions)

    def test_ctrl_w_h_focuses_left(self):
        from peovim.modal.actions import FocusWindow

        eng = self._make_engine()
        eng.feed_key("<C-w>")
        actions = eng.feed_key("h")
        assert any(isinstance(a, FocusWindow) and a.direction == "h" for a in actions)

    def test_ctrl_w_equal_equalizes(self):
        from peovim.modal.actions import EqualizeWindows

        eng = self._make_engine()
        eng.feed_key("<C-w>")
        actions = eng.feed_key("=")
        assert any(isinstance(a, EqualizeWindows) for a in actions)

    def test_ctrl_w_greater_resizes_width(self):
        from peovim.modal.actions import ResizeWindow

        eng = self._make_engine()
        eng.feed_key("<C-w>")
        actions = eng.feed_key(">")

        assert any(isinstance(a, ResizeWindow) and a.direction == "h" and a.delta == 1 for a in actions)

    def test_ctrl_w_plus_resizes_height(self):
        from peovim.modal.actions import ResizeWindow

        eng = self._make_engine()
        eng.feed_key("<C-w>")
        actions = eng.feed_key("+")

        assert any(isinstance(a, ResizeWindow) and a.direction == "v" and a.delta == 1 for a in actions)

    def test_gt_goes_to_next_tab(self):
        from peovim.modal.actions import NextTab

        eng = self._make_engine()
        eng.feed_key("g")
        actions = eng.feed_key("t")
        assert any(isinstance(a, NextTab) for a in actions)

    def test_gT_goes_to_prev_tab(self):
        from peovim.modal.actions import PrevTab

        eng = self._make_engine()
        eng.feed_key("g")
        actions = eng.feed_key("T")
        assert any(isinstance(a, PrevTab) for a in actions)
