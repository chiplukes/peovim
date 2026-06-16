"""Tests for compare selection keymaps and controller state."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import VSplitNode, Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine
from peovim.ui.decorations import HighlightRegion, Sign, VirtualLine


def _make_api(path: Path | None = None) -> MagicMock:
    api = MagicMock()
    api.active_buffer.return_value = SimpleNamespace(path=path)
    api._editor_state = SimpleNamespace(message="")
    api.find_root.return_value = Path("/workspace")

    def _set_status(
        message: str, *, notify: bool = True, level: str = "info", title: str = "", timeout: float = 3.0
    ) -> None:
        api._editor_state.message = message
        if notify:
            api.ui.notify(message, level=level, title=title, timeout=timeout)

    api.set_status.side_effect = _set_status
    return api


def _make_real_api(tmp_path: Path, active_path: Path) -> EditorAPI:
    doc = Document(path=active_path)
    doc.load(active_path)
    window = Window(doc)
    workspace = Workspace(window)
    registers = RegisterStore()
    editor_state = EditorState()
    command_registry = CommandRegistry()
    register_builtins(command_registry)
    engine = ModalEngine()
    engine.set_document(doc)
    dispatcher = ActionDispatcher(
        engine,
        window,
        registers,
        editor_state=editor_state,
        workspace=workspace,
    )
    dispatcher._command_registry = command_registry
    return EditorAPI(workspace, engine, dispatcher, editor_state, command_registry)


def _doc_lines(doc: Document) -> list[str]:
    return [doc.get_line(i) for i in range(doc.line_count())]


def _feed_normal_keys(api: EditorAPI, sequence: str) -> None:
    for key in sequence:
        actions = api._engine.feed_key(key)
        api._dispatcher.dispatch(actions)


class TestSetup:
    def test_registers_compare_keymaps(self):
        from peovim.plugins.compare import setup

        api = _make_api(Path("/workspace/src/a.py"))

        setup(api)

        keys = [call.args[0] for call in api.keymap.nmap.call_args_list]
        assert "<leader>c1" in keys
        assert "<leader>c2" in keys
        assert "<leader>cc" in keys
        assert "]c" in keys
        assert "[c" in keys
        assert "<leader>cj" in keys
        assert "<leader>ck" in keys
        assert "<leader>cs" in keys
        assert "<leader>m12" in keys
        assert "<leader>m21" in keys

    def test_registers_compare_commands(self):
        from peovim.plugins.compare import setup

        api = _make_api(Path("/workspace/src/a.py"))

        setup(api)

        commands = [call.args[0] for call in api.commands.register.call_args_list]
        assert "CompareSelect1" in commands
        assert "CompareSelect2" in commands
        assert "Compare" in commands
        assert "CompareNext" in commands
        assert "ComparePrev" in commands
        assert "CompareStop" in commands
        assert "CompareDebug" in commands
        assert "CompareMerge12" in commands
        assert "CompareMerge21" in commands
        assert "DiffSelect1" in commands
        assert "DiffSelect2" in commands
        assert "Diff" in commands
        assert "DiffNext" in commands
        assert "DiffPrev" in commands
        assert "DiffStop" in commands
        assert "DiffDebug" in commands
        assert "DiffMerge12" in commands
        assert "DiffMerge21" in commands


class TestCompareController:
    def test_select_slot_uses_active_file(self):
        from peovim.plugins import compare as compare_mod

        api = _make_api(Path("/workspace/src/a.py"))
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)

        assert compare_mod._controller.slot_summary() == (str(Path("src/a.py")), None)
        assert api._editor_state.message == f"Diff 1: {Path('src/a.py')}"

    def test_select_slot_requires_file_buffer(self):
        from peovim.plugins import compare as compare_mod

        api = _make_api(None)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(2)

        assert compare_mod._controller.slot_summary() == (None, None)
        assert api._editor_state.message == "Diff 2 requires a file-backed buffer"

    def test_compare_selected_requires_both_slots(self):
        from peovim.plugins import compare as compare_mod

        api = _make_api(Path("/workspace/src/a.py"))
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        compare_mod._controller.compare_selected()

        assert api._editor_state.message == "Select diff targets first (c2)"

    def test_compare_selected_emits_ready_event(self):
        from peovim.plugins import compare as compare_mod

        api = _make_api(Path("/workspace/src/a.py"))
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.active_buffer.return_value = SimpleNamespace(path=Path("/workspace/src/b.py"))
        compare_mod._controller.select_slot(2)

        compare_mod._controller.compare_selected()

        api.events.emit.assert_called_once_with(
            "diff_selection_ready",
            left=str(Path("/workspace/src/a.py").resolve()),
            right=str(Path("/workspace/src/b.py").resolve()),
        )
        assert api._editor_state.message == f"Diff ready: {Path('src/a.py')} ↔ {Path('src/b.py')}"


class TestCompareLayout:
    def test_compare_selected_normalizes_reversed_slot_order_to_current_window_order(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("left\n", encoding="utf-8")
        right.write_text("right\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        api.commands.execute("vsplit")
        compare_mod._controller._activate_window(api._workspace.active_tab.active_window)
        api.open_buffer(right)

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        compare_mod._controller._activate_window(windows_by_path[right.resolve()])
        compare_mod._controller.select_slot(1)
        compare_mod._controller._activate_window(windows_by_path[left.resolve()])
        compare_mod._controller.select_slot(2)

        compare_mod._controller.compare_selected()

        session = compare_mod._controller._session
        assert session is not None
        left_window, right_window = compare_mod._controller._session_windows()
        assert left_window is not None
        assert right_window is not None
        assert left_window.buffer().path == left.resolve()
        assert right_window.buffer().path == right.resolve()

    def test_compare_ready_opens_vertical_split_for_selected_files(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("alpha\nbeta\nshared\n", encoding="utf-8")
        right.write_text("alpha\nBETA\nshared\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()

        tab = api._workspace.active_tab
        assert isinstance(tab.root, VSplitNode)
        windows = tab.all_windows()
        assert len(windows) == 2
        paths = {window.document.path for window in windows}
        assert paths == {left.resolve(), right.resolve()}
        assert api.active_buffer().path == left.resolve()
        summary = compare_mod._controller.session_summary()
        assert summary is not None
        assert summary["blocks"] == 1
        assert summary["left"].endswith("left.py")
        assert summary["right"].endswith("right.py")

    def test_compare_ready_decorates_diff_blocks_and_hints(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("same\nold\nshared\n", encoding="utf-8")
        right.write_text("same\nnew\nshared\nextra\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        left_doc = windows_by_path[left.resolve()].document
        right_doc = windows_by_path[right.resolve()].document
        decorations = api._editor_state.decorations
        left_compare = decorations.get_for_namespace(id(left_doc), "compare")
        left_hints = decorations.get_for_namespace(id(left_doc), "compare.hints")
        right_compare = decorations.get_for_namespace(id(right_doc), "compare")

        assert any(isinstance(dec, HighlightRegion) for dec in left_compare)
        assert any(isinstance(dec, Sign) and dec.char == "~" for dec in left_compare)
        assert any(isinstance(dec, VirtualLine) and dec.count >= 1 for dec in left_hints)
        assert any(isinstance(dec, HighlightRegion) for dec in right_compare)
        assert any(isinstance(dec, Sign) and dec.char in {"~", "+"} for dec in right_compare)

        right_highlights = [dec for dec in right_compare if isinstance(dec, HighlightRegion)]
        assert any(dec.style.bg == (70, 84, 34) for dec in right_highlights)


class TestCompareNavigation:
    def test_next_diff_moves_both_compare_windows(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("same\nold-one\nkeep\nold-two\nend\n", encoding="utf-8")
        right.write_text("same\nnew-one\nkeep\nnew-two\nend\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()
        compare_mod._controller.next_diff()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        assert windows_by_path[left.resolve()].cursor.line == 3
        assert windows_by_path[right.resolve()].cursor.line == 3
        assert api.active_buffer().path == left.resolve()
        assert api._editor_state.message.startswith("Diff next:")

    def test_prev_diff_uses_active_compare_side_and_moves_back(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("same\nold-one\nkeep\nold-two\nend\n", encoding="utf-8")
        right.write_text("same\nnew-one\nkeep\nnew-two\nend\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()
        compare_mod._controller.next_diff()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        right_window = windows_by_path[right.resolve()]
        compare_mod._controller._activate_window(right_window)
        compare_mod._controller.prev_diff()

        assert windows_by_path[left.resolve()].cursor.line == 1
        assert windows_by_path[right.resolve()].cursor.line == 1
        assert api.active_buffer().path == right.resolve()
        assert api._editor_state.message.startswith("Diff prev:")

    def test_next_diff_requires_active_compare_session(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        left.write_text("same\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.next_diff()

        assert api._editor_state.message == "No active diff"


class TestCompareMerge:
    def test_merge_left_to_right_replaces_active_change_block(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("same\nleft-value\nkeep\n", encoding="utf-8")
        right.write_text("same\nright-value\nkeep\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()
        compare_mod._controller.merge_left_to_right()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        right_doc = windows_by_path[right.resolve()].document
        assert right_doc.get_line(1) == "left-value"
        summary = compare_mod._controller.session_summary()
        assert summary is not None
        assert summary["blocks"] == 0
        assert api.active_buffer().path == left.resolve()
        assert api._editor_state.message == "Merged left → right"

    def test_merge_right_to_left_applies_insert_block(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("same\nshared\n", encoding="utf-8")
        right.write_text("same\nextra\nshared\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()
        compare_mod._controller.merge_right_to_left()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        left_doc = windows_by_path[left.resolve()].document
        right_doc = windows_by_path[right.resolve()].document
        assert left_doc.get_line(1) == "extra"
        assert left_doc.get_line(2) == "shared"
        assert right_doc.get_line(1) == "extra"
        assert right_doc.get_line(2) == "shared"
        summary = compare_mod._controller.session_summary()
        assert summary is not None
        assert summary["blocks"] == 0
        assert api._editor_state.message == "Merged right → left"

    def test_merge_uses_navigated_insert_block_when_anchor_overlaps_prior_change(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("same\nold\nkeep\n", encoding="utf-8")
        right.write_text("same\nnew\n# extra comment\nkeep\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()
        compare_mod._controller.next_diff()
        compare_mod._controller.merge_right_to_left()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        left_doc = windows_by_path[left.resolve()].document
        left_window = windows_by_path[left.resolve()]
        right_window = windows_by_path[right.resolve()]
        right_doc = right_window.document
        assert left_doc.get_line(1) == "old"
        assert left_doc.get_line(2) == "# extra comment"
        assert left_doc.get_line(3) == "keep"
        assert right_doc.get_line(1) == "new"
        assert right_doc.get_line(2) == "# extra comment"
        assert right_doc.get_line(3) == "keep"
        assert left_window.cursor.line == 2
        assert right_window.cursor.line == 2
        assert left_window.scroll_line == right_window.scroll_line

    def test_merge_right_to_left_from_left_anchor_keeps_right_buffer_unchanged(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("same\nshared\n", encoding="utf-8")
        right.write_text("same\n# extra comment\nshared\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()
        compare_mod._controller.merge_right_to_left()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        left_doc = windows_by_path[left.resolve()].document
        right_doc = windows_by_path[right.resolve()].document
        assert left_doc.get_line(1) == "# extra comment"
        assert left_doc.get_line(2) == "shared"
        assert right_doc.get_line(1) == "# extra comment"
        assert right_doc.get_line(2) == "shared"

    def test_merge_right_to_left_matches_user_comment_repro_from_left_pane(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.v"
        right = tmp_path / "right.v"
        left.write_text(
            "assign busy = busy_reg;\n\nalways @* begin\n    state_next = STATE_IDLE;\n",
            encoding="utf-8",
        )
        right.write_text(
            "assign busy = busy_reg;\n\n// new comment\n\nalways @* begin\n    state_next = STATE_IDLE;\n",
            encoding="utf-8",
        )

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()
        compare_mod._controller.merge_right_to_left()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        left_doc = windows_by_path[left.resolve()].document
        right_doc = windows_by_path[right.resolve()].document
        left_window = windows_by_path[left.resolve()]
        right_window = windows_by_path[right.resolve()]

        assert _doc_lines(left_doc) == [
            "assign busy = busy_reg;",
            "",
            "// new comment",
            "",
            "always @* begin",
            "    state_next = STATE_IDLE;",
            "",
        ]
        assert _doc_lines(right_doc) == [
            "assign busy = busy_reg;",
            "",
            "// new comment",
            "",
            "always @* begin",
            "    state_next = STATE_IDLE;",
            "",
        ]
        assert left_window.cursor.line == 2
        assert right_window.cursor.line == 2

    def test_merge_right_to_left_matches_user_comment_repro_from_right_pane(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.v"
        right = tmp_path / "right.v"
        left.write_text(
            "assign busy = busy_reg;\n\nalways @* begin\n    state_next = STATE_IDLE;\n",
            encoding="utf-8",
        )
        right.write_text(
            "assign busy = busy_reg;\n\n// new comment\n\nalways @* begin\n    state_next = STATE_IDLE;\n",
            encoding="utf-8",
        )

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        compare_mod._controller._activate_window(windows_by_path[right.resolve()])
        compare_mod._controller.merge_right_to_left()

        left_doc = windows_by_path[left.resolve()].document
        right_doc = windows_by_path[right.resolve()].document
        left_window = windows_by_path[left.resolve()]
        right_window = windows_by_path[right.resolve()]

        assert _doc_lines(left_doc) == [
            "assign busy = busy_reg;",
            "",
            "// new comment",
            "",
            "always @* begin",
            "    state_next = STATE_IDLE;",
            "",
        ]
        assert _doc_lines(right_doc) == [
            "assign busy = busy_reg;",
            "",
            "// new comment",
            "",
            "always @* begin",
            "    state_next = STATE_IDLE;",
            "",
        ]
        assert left_window.cursor.line == 2
        assert right_window.cursor.line == 2

    def test_leader_m21_key_sequence_merges_right_to_left(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.v"
        right = tmp_path / "right.v"
        left.write_text("same\nshared\n", encoding="utf-8")
        right.write_text("same\n// new comment\nshared\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        api.options.set("leader", " ")
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        compare_mod._controller._activate_window(windows_by_path[right.resolve()])

        _feed_normal_keys(api, " m21")

        left_doc = windows_by_path[left.resolve()].document
        right_doc = windows_by_path[right.resolve()].document
        assert _doc_lines(left_doc) == ["same", "// new comment", "shared", ""]
        assert _doc_lines(right_doc) == ["same", "// new comment", "shared", ""]

    def test_leader_m21_after_two_compare_nexts_merges_mdio_style_comment_block(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.v"
        right = tmp_path / "right.v"
        left.write_text(
            "".join(
                [
                    "header\n",
                    *[f"left_a_{i}\n" for i in range(28)],
                    "mid_a\n",
                    *[f"left_b_{i}\n" for i in range(40)],
                    "mid_b\n",
                    *[f"left_c_{i}\n" for i in range(37)],
                    "assign busy = busy_reg;\n",
                    "\n",
                    "always @* begin\n",
                    "    state_next = STATE_IDLE;\n",
                    *[f"tail_{i}\n" for i in range(84)],
                    "endmodule\n",
                ]
            ),
            encoding="utf-8",
        )
        right.write_text(
            "".join(
                [
                    "header\n",
                    *[f"left_a_{i}\n" for i in range(28)],
                    "insert a1\n",
                    "insert a2\n",
                    "insert a3\n",
                    "insert a4\n",
                    "insert a5\n",
                    "mid_a\n",
                    *[f"left_b_{i}\n" for i in range(40)],
                    "insert b1\n",
                    "insert b2\n",
                    "insert b3\n",
                    "insert b4\n",
                    "insert b5\n",
                    "insert b6\n",
                    "insert b7\n",
                    "mid_b\n",
                    *[f"left_c_{i}\n" for i in range(37)],
                    "assign busy = busy_reg;\n",
                    "\n",
                    "// new comment\n",
                    "\n",
                    "always @* begin\n",
                    "    state_next = STATE_IDLE;\n",
                    *[f"tail_{i}\n" for i in range(84)],
                    "tail insert\n",
                    "endmodule\n",
                ]
            ),
            encoding="utf-8",
        )

        api = _make_real_api(tmp_path, left)
        api.options.set("leader", " ")
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        compare_mod._controller._activate_window(windows_by_path[right.resolve()])

        _feed_normal_keys(api, " cj")
        _feed_normal_keys(api, " cj")
        _feed_normal_keys(api, " m21")

        left_doc = windows_by_path[left.resolve()].document
        right_doc = windows_by_path[right.resolve()].document
        assert any(line == "// new comment" for line in _doc_lines(left_doc))
        assert any(line == "// new comment" for line in _doc_lines(right_doc))
        assert left_doc.dirty is True
        assert right_doc.dirty is False
        assert api._editor_state.message == "Merged right → left"


class TestCompareSessionUx:
    def test_compare_debug_reports_current_session_state(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("same\nleft\n", encoding="utf-8")
        right.write_text("same\nright\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()
        compare_mod._controller.debug_session()

        assert "DiffDebug:" in api._editor_state.message
        assert "blocks=1" in api._editor_state.message
        assert "active_side=left" in api._editor_state.message

    def test_compare_publishes_statusline_state_and_clears_on_stop(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("same\nleft\n", encoding="utf-8")
        right.write_text("same\nright\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()

        assert api._editor_state.compare_status is not None
        assert api._editor_state.compare_status["left"].endswith("left.py")
        assert api._editor_state.compare_status["right"].endswith("right.py")

        compare_mod._controller.stop_compare()

        assert api._editor_state.compare_status is None

    def test_stop_compare_clears_session_and_decorations(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("same\nleft\n", encoding="utf-8")
        right.write_text("same\nright\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        left_doc = windows_by_path[left.resolve()].document
        right_doc = windows_by_path[right.resolve()].document
        assert api._editor_state.decorations.get_for_namespace(id(left_doc), "compare")
        assert api._editor_state.decorations.get_for_namespace(id(right_doc), "compare")

        compare_mod._controller.stop_compare()

        assert compare_mod._controller.session_summary() is None
        assert api._editor_state.decorations.get_for_namespace(id(left_doc), "compare") == []
        assert api._editor_state.decorations.get_for_namespace(id(right_doc), "compare") == []
        assert api._editor_state.message.startswith("Diff stopped:")

    def test_save_refreshes_compare_session_and_updates_block_count(self, tmp_path):
        from peovim.plugins import compare as compare_mod

        left = tmp_path / "left.py"
        right = tmp_path / "right.py"
        left.write_text("same\nleft\n", encoding="utf-8")
        right.write_text("same\nright\n", encoding="utf-8")

        api = _make_real_api(tmp_path, left)
        compare_mod.setup(api)

        assert compare_mod._controller is not None
        compare_mod._controller.select_slot(1)
        api.open_buffer(right)
        compare_mod._controller.select_slot(2)
        compare_mod._controller.compare_selected()

        windows_by_path = {window.document.path: window for window in api._workspace.active_tab.all_windows()}
        right_window = windows_by_path[right.resolve()]
        right_window.document.replace(1, 0, 1, len(right_window.document.get_line(1)), "left")
        compare_mod._controller.on_buffer_saved(path=str(right.resolve()))

        summary = compare_mod._controller.session_summary()
        assert summary is not None
        assert summary["blocks"] == 0
        assert api._editor_state.message.startswith("Diff refreshed:")
