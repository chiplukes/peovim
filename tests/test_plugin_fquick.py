from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine


def _make_buf(path: Path | None, *, modified: bool = False) -> MagicMock:
    buf = MagicMock()
    buf.path = path
    buf.is_modified.return_value = modified
    return buf


def _make_api(
    root: Path,
    *,
    active: Path | None = None,
    buffers: list[MagicMock] | None = None,
    workspace_files: list[Path] | None = None,
) -> MagicMock:
    api = MagicMock()
    api.find_root.return_value = root
    api.find_files.return_value = workspace_files or []
    api.list_buffers.return_value = buffers or []
    api.active_buffer.return_value = MagicMock()
    api.active_buffer.return_value.path = active
    api.ui = MagicMock()
    api.ui._picker = MagicMock()
    api.events = MagicMock()
    api.keymap = MagicMock()
    api.commands = MagicMock()
    api.active_window.return_value = MagicMock()
    api.active_window.return_value.cursor = (0, 0)
    api.active_window.return_value.visible_range.return_value = (0, 0)
    return api


def _make_real_api(active_path: Path) -> EditorAPI:
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


class TestSetup:
    def test_registers_fquick_keymaps_and_commands(self, tmp_path):
        from peovim.plugins import fquick

        api = _make_api(tmp_path)
        fquick.teardown()

        try:
            fquick.setup(api)
            keys = [call.args[0] for call in api.keymap.nmap.call_args_list]
            assert "fh" in keys
            assert "fl" in keys
            assert "fj" in keys
            assert "fk" in keys
            assert "f/" in keys

            plug_names = [call.args[0] for call in api.keymap.define_plug.call_args_list]
            assert "FquickOlder" in plug_names
            assert "FquickNewer" in plug_names
            assert "FquickSessionPickerDown" in plug_names
            assert "FquickSessionPickerUp" in plug_names
            assert "FquickWorkspacePicker" in plug_names

            commands = [call.args[0] for call in api.commands.register.call_args_list]
            assert "FquickSession" in commands
            assert "FquickWorkspace" in commands
        finally:
            fquick.teardown()


class TestController:
    def test_tracks_opened_files_in_mru_order(self, tmp_path):
        from peovim.plugins.fquick import _FquickController

        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        c = tmp_path / "c.txt"
        for path in (a, b, c):
            path.write_text(path.name, encoding="utf-8")

        api = _make_api(tmp_path, active=a, buffers=[_make_buf(a), _make_buf(b)])
        controller = _FquickController(api)

        controller._on_buffer_opened(path=str(c))

        assert controller._history == [c, a, b]

    def test_cycle_stays_on_stable_snapshot(self, tmp_path):
        from peovim.plugins.fquick import _FquickController

        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        c = tmp_path / "c.txt"
        for path in (a, b, c):
            path.write_text(path.name, encoding="utf-8")

        api = _make_api(tmp_path, active=a, buffers=[_make_buf(a), _make_buf(b), _make_buf(c)])
        controller = _FquickController(api)
        controller._history = [a, b, c]

        controller.cycle_older()
        assert api.open_buffer.call_args_list[-1].args[0] == b

        api.active_buffer.return_value.path = b
        controller._on_buffer_opened(path=str(b))

        controller.cycle_older()
        assert api.open_buffer.call_args_list[-1].args[0] == c

        api.active_buffer.return_value.path = c
        controller._on_buffer_opened(path=str(c))

        controller.cycle_newer()
        assert api.open_buffer.call_args_list[-1].args[0] == b

    def test_session_picker_marks_modified_buffers(self, tmp_path):
        from peovim.plugins.fquick import _FquickController

        a = tmp_path / "a.txt"
        b = tmp_path / "nested" / "b.txt"
        b.parent.mkdir()
        for path in (a, b):
            path.write_text(path.name, encoding="utf-8")

        api = _make_api(
            tmp_path,
            active=a,
            buffers=[_make_buf(a, modified=True), _make_buf(b, modified=False)],
        )
        controller = _FquickController(api)
        controller._history = [a, b]

        controller.open_session_picker()

        items = api.ui.open_picker.call_args.args[1]
        assert str(items[0]).startswith("** ")
        assert str(items[0]).endswith("a.txt")
        assert str(items[1]).endswith("nested\\b.txt") or str(items[1]) == "nested/b.txt"

    def test_session_picker_initial_step_moves_selection(self, tmp_path):
        from peovim.plugins.fquick import _FquickController

        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        for path in (a, b):
            path.write_text(path.name, encoding="utf-8")

        api = _make_api(tmp_path, active=a, buffers=[_make_buf(a), _make_buf(b)])
        controller = _FquickController(api)
        controller._history = [a, b]

        controller.open_session_picker(initial_step=1)
        api.ui._picker.feed_key.assert_called_with("<C-n>")

    def test_workspace_picker_uses_relative_labels(self, tmp_path):
        from peovim.plugins.fquick import _FquickController

        target = tmp_path / "src" / "main.py"
        target.parent.mkdir()
        target.write_text("print('ok')\n", encoding="utf-8")

        api = _make_api(tmp_path, workspace_files=[target])
        controller = _FquickController(api)

        controller.open_workspace_picker()

        items = api.ui.open_picker.call_args.args[1]
        assert str(items[0]).endswith("src\\main.py") or str(items[0]) == "src/main.py"

    def test_open_item_restores_saved_cursor_and_scroll(self, tmp_path):
        from peovim.plugins import fquick
        from peovim.plugins.fquick import _FileItem

        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        first.write_text("0\n1\n2\n3\n4\n5\n", encoding="utf-8")
        second.write_text("a\nb\nc\nd\ne\nf\n", encoding="utf-8")

        api = _make_real_api(first)
        fquick.teardown()

        try:
            fquick.setup(api)
            assert fquick._controller is not None

            api.active_window().set_cursor(4, 0)
            api.active_window().set_scroll_line(3)
            fquick._controller._on_cursor_moved(line=4, col=0)

            api.open_buffer(second, line=2, col=0)
            api.active_window().set_scroll_line(1)
            fquick._controller._on_cursor_moved(line=2, col=0)

            fquick._controller._open_item(_FileItem("first", first.resolve()))

            assert api.active_buffer().path == first.resolve()
            assert api.active_window().cursor == (4, 0)
            assert api.active_window().visible_range()[0] == 3
        finally:
            fquick.teardown()
