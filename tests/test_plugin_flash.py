"""Tests for the flash jump plugin."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.jumplist import JumpList
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine, Mode


def _make_api(active_path: Path) -> EditorAPI:
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
        jumplist=JumpList(),
        editor_state=editor_state,
        workspace=workspace,
    )
    dispatcher._command_registry = command_registry
    return EditorAPI(workspace, engine, dispatcher, editor_state, command_registry)


def _split_with_buffer(api: EditorAPI, path: Path) -> tuple[object, object]:
    api.commands.execute("vsplit")
    api.open_buffer(path)
    windows = {win.buffer().path: win for win in api.list_windows()}
    return windows[next(p for p in windows if p != path.resolve())], windows[path.resolve()]


class TestFlashPlugin:
    def test_start_uses_status_without_notifications(self, tmp_path):
        from peovim.plugins.flash import setup

        active = tmp_path / "active.txt"
        active.write_text("alpha\n", encoding="utf-8")

        api = _make_api(active)
        api.ui.notify = MagicMock()
        setup(api)

        api.flash_plugin.start()

        assert api._editor_state.message == "flash> "
        api.ui.notify.assert_not_called()

    def test_flash_jump_focuses_target_window_and_records_jump(self, tmp_path):
        from peovim.plugins.flash import setup

        left = tmp_path / "left.txt"
        right = tmp_path / "right.txt"
        left.write_text("left\n", encoding="utf-8")
        right.write_text("prefix ab suffix\n", encoding="utf-8")

        api = _make_api(left)
        left_window, right_window = _split_with_buffer(api, right)
        api.activate_window(left_window)
        api.ui.notify = MagicMock()
        setup(api)

        flash = api.flash_plugin
        flash.start()
        flash.feed_key("a")
        flash.feed_key("b")
        flash.feed_key("a")

        assert api.active_window().win_id == right_window.win_id
        assert api.active_buffer().path == right.resolve()
        assert api.active_window().cursor == (0, 7)
        assert api._dispatcher.jumplist.current() == (str(left.resolve()), 0, 0, 0)
        assert api._editor_state.message == ""
        api.ui.notify.assert_not_called()

    def test_flash_labels_are_window_scoped(self, tmp_path):
        from peovim.plugins.flash import setup

        active = tmp_path / "active.txt"
        active.write_text("ab here\n", encoding="utf-8")

        api = _make_api(active)
        api.commands.execute("vsplit")
        windows = api.list_windows()
        left_window, right_window = windows[0], windows[1]
        api.activate_window(left_window)
        setup(api)

        flash = api.flash_plugin
        flash.start()
        flash.feed_key("a")
        flash.feed_key("b")

        left_labels = api._editor_state.decorations.get_for_namespace(left_window.win_id, "flash:labels")
        right_labels = api._editor_state.decorations.get_for_namespace(right_window.win_id, "flash:labels")

        assert left_labels
        assert right_labels

    def test_flash_reports_no_matches_without_notifications(self, tmp_path):
        from peovim.plugins.flash import setup

        active = tmp_path / "active.txt"
        active.write_text("alpha\n", encoding="utf-8")

        api = _make_api(active)
        api.ui.notify = MagicMock()
        setup(api)

        flash = api.flash_plugin
        flash.start()
        flash.feed_key("z")
        flash.feed_key("z")

        assert api._editor_state.message == "flash: no matches"
        assert flash.is_active is False
        api.ui.notify.assert_not_called()

    def test_flash_visual_jump_creates_selection_from_original_cursor(self, tmp_path):
        from peovim.plugins.flash import setup

        active = tmp_path / "active.txt"
        active.write_text("zero alpha beta\n", encoding="utf-8")

        api = _make_api(active)
        setup(api)

        api._dispatcher.dispatch(api._engine.feed_key("v"))

        api._dispatcher.dispatch(api._engine.feed_key("s"))
        assert api._engine.mode == Mode.NORMAL

        flash = api.flash_plugin
        flash.feed_key("b")
        flash.feed_key("e")
        flash.feed_key("a")

        assert api._engine.mode == Mode.VISUAL_CHAR
        assert api._engine._visual_anchor == (0, 0)
        assert api.active_window().cursor == (0, 11)
        assert api._engine.visual_selection_regions() == [(0, 0, 0, 12)]

    def test_flash_visual_cancel_restores_selection_and_scope(self, tmp_path):
        from peovim.plugins.flash import setup

        left = tmp_path / "left.txt"
        right = tmp_path / "right.txt"
        left.write_text("ab left\n", encoding="utf-8")
        right.write_text("ab right\n", encoding="utf-8")

        api = _make_api(left)
        left_window, right_window = _split_with_buffer(api, right)
        api.activate_window(left_window)
        setup(api)

        api._dispatcher.dispatch(api._engine.feed_key("v"))
        api.active_window().set_cursor(0, 1)

        api._dispatcher.dispatch(api._engine.feed_key("s"))
        assert api._engine.mode == Mode.NORMAL

        flash = api.flash_plugin
        flash.feed_key("a")
        flash.feed_key("b")

        left_labels = api._editor_state.decorations.get_for_namespace(left_window.win_id, "flash:labels")
        right_labels = api._editor_state.decorations.get_for_namespace(right_window.win_id, "flash:labels")

        assert left_labels
        assert not right_labels

        flash.feed_key("<Esc>")

        assert api._engine.mode == Mode.VISUAL_CHAR
        assert api._engine._visual_anchor == (0, 0)
        assert api.active_window().win_id == left_window.win_id
        assert api.active_window().cursor == (0, 1)
        assert api._engine.visual_selection_regions() == [(0, 0, 0, 2)]
