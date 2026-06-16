"""
Phase 6c — EditorAPI, sub-APIs, PluginManager, BindingRegistry tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import peovim.api as public_api
from peovim.api import PluginVersionError
from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.jumplist import JumpList
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.actions import SaveBuffer
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine, Mode

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_api(content: str = "hello\nworld\nfoo") -> EditorAPI:
    doc = Document()
    doc.load_string(content)
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


# ---------------------------------------------------------------------------
# EditorAPI basic
# ---------------------------------------------------------------------------


class TestEditorAPI:
    def test_version(self):
        api = _make_api()
        assert api.VERSION == (0, 1, 0)

    def test_requires_version_accepts_current_version(self):
        api = _make_api()
        api.requires_version("0.1.0")

    def test_requires_version_rejects_newer_version(self):
        api = _make_api()
        with pytest.raises(PluginVersionError):
            api.requires_version("0.2.0")

    def test_namespace_status_reports_git_as_experimental(self):
        api = _make_api()
        assert api.namespace_status("git").status == "experimental"

    def test_public_api_package_exports_status_metadata(self):
        assert public_api.VERSION == (0, 1, 0)
        assert public_api.API_NAMESPACE_STATUS["completion"].status == "planned"
        assert public_api.namespace_status("editor").status == "implemented"

    def test_active_buffer(self):
        api = _make_api()
        buf = api.active_buffer()
        assert buf is not None
        assert buf.line_count() == 3

    def test_active_window(self):
        api = _make_api()
        win = api.active_window()
        assert win is not None
        assert win.cursor == (0, 0)

    def test_active_mode(self):
        api = _make_api()

        assert getattr(api.active_mode, "value", None) == "normal"

    def test_list_buffers(self):
        api = _make_api()
        bufs = api.list_buffers()
        assert len(bufs) >= 1

    def test_list_buffers_includes_hidden_workspace_documents(self, tmp_path):
        api = _make_api("alpha\n")
        visible_doc = api._workspace.active_window.document
        visible_doc.path = tmp_path / "visible.txt"
        visible_doc.insert(0, 0, "x")

        hidden_path = tmp_path / "hidden.txt"
        hidden_path.write_text("hidden\n", encoding="utf-8")
        hidden_doc = Document(path=hidden_path)
        hidden_doc.load(hidden_path)
        hidden_doc.insert(0, 0, "!")
        api._workspace.add_document(hidden_doc)

        bufs = api.list_buffers()

        assert {buf.path for buf in bufs} >= {visible_doc.path, hidden_path.resolve()}
        hidden_buf = next(buf for buf in bufs if buf.path == hidden_path.resolve())
        assert hidden_buf.is_modified() is True

    def test_buffer_by_id(self):
        api = _make_api()
        active = api.active_buffer()

        assert api.buffer_by_id(active.buf_id) is not None
        assert api.buffer_by_id(-1) is None

    def test_list_tab_windows_and_window_by_id(self):
        from peovim.modal.actions import SplitWindow

        api = _make_api()
        api._dispatcher.dispatch([SplitWindow("v")])

        windows = api.list_tab_windows()

        assert len(windows) == 2
        assert api.window_by_id(windows[0].win_id, active_tab_only=True) is not None

    def test_sidebar_focus_left_cycles_through_sidebar_and_wraps_to_rightmost(self):
        from peovim.modal.actions import SplitWindow

        class _Sidebar:
            def __init__(self) -> None:
                self.visible = True
                self.focused = False

            def focus(self) -> None:
                self.focused = True

            def blur(self) -> None:
                self.focused = False

        api = _make_api()
        api._dispatcher.dispatch([SplitWindow("v")])
        api._dispatcher.dispatch([SplitWindow("v")])
        left, middle, _right = api._workspace.active_tab.all_windows()
        sidebar = _Sidebar()
        api._event_loop = SimpleNamespace(_sidebar=sidebar)

        api._workspace.active_tab.focus_window(middle)
        api._dispatcher.window = middle
        api._dispatch_sidebar_action("focus_left")
        assert api._workspace.active_window is left
        assert sidebar.focused is False

        api._dispatcher.window = left
        api._dispatch_sidebar_action("focus_left")
        assert api._workspace.active_window is left
        assert sidebar.focused is True

        api._dispatch_sidebar_action("focus_left")
        assert api._workspace.active_window is _right
        assert sidebar.focused is False

    def test_sidebar_focus_right_cycles_through_sidebar_and_wraps_to_leftmost(self):
        from peovim.modal.actions import SplitWindow

        class _Sidebar:
            def __init__(self) -> None:
                self.visible = True
                self.focused = False

            def focus(self) -> None:
                self.focused = True

            def blur(self) -> None:
                self.focused = False

        api = _make_api()
        api._dispatcher.dispatch([SplitWindow("v")])
        api._dispatcher.dispatch([SplitWindow("v")])
        left, middle, right = api._workspace.active_tab.all_windows()
        sidebar = _Sidebar()
        api._event_loop = SimpleNamespace(_sidebar=sidebar)

        api._workspace.active_tab.focus_window(middle)
        api._dispatcher.window = middle
        api._dispatch_sidebar_action("focus_right")
        assert api._workspace.active_window is right
        assert sidebar.focused is False

        api._dispatcher.window = right
        api._dispatch_sidebar_action("focus_right")
        assert api._workspace.active_window is right
        assert sidebar.focused is True

        api._dispatch_sidebar_action("focus_right")
        assert api._workspace.active_window is left
        assert sidebar.focused is False

    def test_set_compare_status(self):
        api = _make_api()

        api.set_compare_status({"left": "a", "right": "b"})
        assert api._editor_state.compare_status == {"left": "a", "right": "b"}

        api.set_compare_status(None)
        assert api._editor_state.compare_status is None

    def test_get_logger(self):
        import logging

        api = _make_api()
        logger = api.get_logger("test")
        assert isinstance(logger, logging.Logger)
        assert "test" in logger.name

    @pytest.mark.asyncio
    async def test_set_interval_cancels_tasks_on_editor_shutdown(self):
        api = _make_api()

        handle = api.set_interval(lambda: None, 1000)

        assert handle is not None
        assert handle in api._interval_handles

        api._editor_state.event_bus.emit("editor_shutdown")
        await asyncio.sleep(0)

        assert handle.cancelled()
        assert api._interval_handles == set()

    def test_set_interval_before_event_loop_is_queued(self):
        api = _make_api()

        handle = api.set_interval(lambda: None, 1000)

        assert handle is not None
        assert api._interval_handles == set()
        assert len(api._pending_intervals) == 1

    @pytest.mark.asyncio
    async def test_queued_interval_starts_on_editor_ready_and_cancels_on_shutdown(self, monkeypatch):
        api = _make_api()
        fired: list[str] = []

        monkeypatch.setattr("asyncio.get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError()))
        handle = api.set_interval(lambda: fired.append("tick"), 1000)
        monkeypatch.undo()

        api._editor_state.event_bus.emit("editor_ready")
        await asyncio.sleep(0)

        assert handle in api._interval_handles
        assert api._pending_intervals == []

        api._editor_state.event_bus.emit("editor_shutdown")
        await asyncio.sleep(0)

        assert handle.cancelled()

    def test_register_sign_type(self):
        api = _make_api()
        from peovim.core.style import Style

        api.register_sign_type("todo", "T", Style(fg=(255, 200, 0)))
        st = api._editor_state.sign_registry.get("todo")
        assert st is not None
        assert st.char == "T"

    def test_find_files_no_crash(self):
        api = _make_api()
        # Should return a list (may be empty if no root found)
        result = api.find_files("*.py", root=None)
        assert isinstance(result, list)

    def test_grep_no_crash(self):
        api = _make_api()
        import pathlib

        result = api.grep("def ", root=pathlib.Path("."))
        assert isinstance(result, list)

    def test_open_buffer_switches_active_window_document(self, tmp_path):
        api = _make_api("alpha\n")
        target = tmp_path / "target.txt"
        target.write_text("beta\n", encoding="utf-8")

        api.open_buffer(target, line=0, col=2)

        assert api.active_buffer().path == target.resolve()
        assert api.active_window().cursor == (0, 2)

    def test_alternate_file_helpers_restore_previous_cursor(self, tmp_path):
        api = _make_api("alpha\n")
        source = tmp_path / "source.txt"
        target = tmp_path / "target.txt"
        source.write_text("alpha\n", encoding="utf-8")
        target.write_text("beta\n", encoding="utf-8")

        api.open_buffer(source, line=0, col=1)
        api.open_buffer(target, line=0, col=2)

        alt_path, alt_cursor = api.alternate_file()

        assert alt_path == source.resolve()
        assert alt_cursor == (0, 1)
        assert api.open_alternate_buffer() is True
        assert api.active_buffer().path == source.resolve()
        assert api.active_window().cursor == (0, 1)

    def test_register_helpers_round_trip(self):
        api = _make_api()

        api.set_register("a", "value", "char")

        assert api.get_register("a") == ("value", "char")

    def test_paste_register_dispatches_action(self):
        api = _make_api()
        api._dispatcher.dispatch = MagicMock()

        api.paste_register("0", before=True)

        dispatched = api._dispatcher.dispatch.call_args.args[0]
        assert len(dispatched) == 1
        assert type(dispatched[0]).__name__ == "PasteRegister"
        assert dispatched[0].register == "0"
        assert dispatched[0].before is True

    def test_window_action_helpers_dispatch_actions(self):
        api = _make_api()
        api._dispatcher.dispatch = MagicMock()

        api.split_window("v")
        api.close_window()
        api.only_window()
        api.equalize_windows()

        dispatched = [call.args[0][0].__class__.__name__ for call in api._dispatcher.dispatch.call_args_list]
        assert dispatched == ["SplitWindow", "CloseWindow", "OnlyWindow", "EqualizeWindows"]

    def test_goto_location_records_destination_for_jumpback(self, tmp_path):
        api = _make_api("alpha\n")
        source = tmp_path / "source.txt"
        target = tmp_path / "target.txt"
        source.write_text("alpha\n", encoding="utf-8")
        target.write_text("beta\n", encoding="utf-8")

        api.open_buffer(source, line=0, col=1)
        api.goto_location(target, line=0, col=2)

        assert api._dispatcher.jumplist.current() == (str(target.resolve()), 0, 2, 0)

    def test_open_cmdline_uses_event_loop_widget(self):
        api = _make_api()
        cmdline = SimpleNamespace(
            enter=MagicMock(),
            set_completion_source=MagicMock(),
        )
        api._event_loop = SimpleNamespace(
            _cmdline=cmdline,
            _list_available_commands=lambda: ["write", "quit"],
            _invalidate_cmdline=MagicMock(),
        )

        api.open_cmdline("AlignChar ")

        cmdline.enter.assert_called_once_with(":", "AlignChar ")
        cmdline.set_completion_source.assert_called_once_with(["write", "quit"])
        api._event_loop._invalidate_cmdline.assert_called_once()

    def test_open_cmdline_noops_without_event_loop(self):
        api = _make_api()

        api.open_cmdline("AlignChar ")

    def test_set_status_updates_editor_message(self):
        api = _make_api()

        api.set_status("Ready")

        assert api._editor_state.message == "Ready"

    def test_set_status_notifies_by_default(self):
        api = _make_api()
        api.ui.notify = MagicMock()

        api.set_status("Saved")

        api.ui.notify.assert_called_once_with("Saved", level="info", title="", timeout=3.0)
        assert api._editor_state.message == "Saved"

    def test_set_status_suppresses_notification_errors(self):
        api = _make_api()
        api.ui.notify = MagicMock(side_effect=RuntimeError("boom"))

        api.set_status("Still shown")

        assert api._editor_state.message == "Still shown"

    def test_set_status_can_skip_notifications(self):
        api = _make_api()
        api.ui.notify = MagicMock()

        api.set_status("Transient", notify=False)

        api.ui.notify.assert_not_called()
        assert api._editor_state.message == "Transient"

    def test_activate_window_switches_active_window(self, tmp_path):
        api = _make_api("alpha\n")
        target = tmp_path / "target.txt"
        target.write_text("beta\n", encoding="utf-8")

        api.commands.execute("vsplit")
        api.open_buffer(target)
        target_window = next(win for win in api.list_windows() if win.buffer().path == target.resolve())

        api.activate_window(target_window)

        assert api.active_window().win_id == target_window.win_id
        assert api.active_buffer().path == target.resolve()

    def test_window_overlay_helpers_use_window_identity(self):
        from peovim.core.style import Style
        from peovim.ui.decorations import OverlayChar

        api = _make_api()
        win = api.active_window()
        overlay = OverlayChar(line=0, col=0, display_char="x", style=Style(fg=(255, 0, 0)))

        api.add_window_overlay(win, "window:test", overlay)

        assert api._editor_state.decorations.get_for_namespace(win.win_id, "window:test") == [overlay]

        api.clear_window_namespace(win, "window:test")

        assert api._editor_state.decorations.get_for_namespace(win.win_id, "window:test") == []

    def test_push_recent_file_updates_recent_files(self, tmp_path):
        api = _make_api()
        target = tmp_path / "recent.txt"

        api.push_recent_file(target)

        assert api.recent_files()[0] == target

    def test_active_buffer_set_text_replaces_contents(self):
        api = _make_api("alpha\nbeta")

        api.active_buffer().set_text("one\ntwo")

        assert api.active_buffer().get_text() == "one\ntwo"

    def test_resize_window_dispatches_resize_action(self):
        from peovim.core.workspace import VSplitNode
        from peovim.modal.actions import SplitWindow

        api = _make_api()
        api._dispatcher.dispatch([SplitWindow("v")])

        api.resize_window("h", 2)

        assert isinstance(api._workspace.active_tab.root, VSplitNode)
        assert api._workspace.active_tab.root.ratio < 0.5

    def test_toggle_window_expand_dispatches_action(self):
        from peovim.core.workspace import VSplitNode
        from peovim.modal.actions import SplitWindow

        api = _make_api()
        api._dispatcher.dispatch([SplitWindow("v")])

        api.toggle_window_expand(0.75)

        assert isinstance(api._workspace.active_tab.root, VSplitNode)
        assert api._workspace.active_tab.root.ratio < 0.3

    def test_repeat_last_command_repeats_window_shrink(self):
        from peovim.modal.actions import SplitWindow
        from peovim.plugins import editor_utils

        api = _make_api()
        api.options.set("leader", " ")
        editor_utils.setup(api)
        remember = api.remember
        api.keymap.nmap("<leader>w-", remember(lambda: api.resize_window("v", -1)), desc="Shrink window height")

        original = api._workspace.active_window
        api._dispatcher.dispatch([SplitWindow("h")])
        api._workspace.active_tab.focus_window(original)
        root = api._workspace.active_tab.root
        assert hasattr(root, "ratio")

        def _press(*keys: str) -> None:
            for key in keys:
                actions = api._engine.feed_key(key)
                if actions:
                    api._dispatcher.dispatch(actions)

        _press(" ", "w", "-")
        first_ratio = api._workspace.active_tab.root.ratio
        _press(" ", " ")
        second_ratio = api._workspace.active_tab.root.ratio

        assert first_ratio < 0.5
        assert second_ratio < first_ratio

    def test_remember_can_wrap_plug_mapping(self):
        from peovim.plugins import editor_utils

        api = _make_api()
        api.options.set("leader", " ")
        editor_utils.setup(api)
        remember = api.remember
        calls: list[str] = []
        api.keymap.define_plug("TestRememberPlug", lambda: calls.append("ran"), desc="Test remember plug")
        api.keymap.nmap("<leader>x", remember("<Plug>TestRememberPlug"), desc="Run test plug")

        def _press(*keys: str) -> None:
            for key in keys:
                actions = api._engine.feed_key(key)
                if actions:
                    api._dispatcher.dispatch(actions)

        _press(" ", "x")
        api.repeat()

        assert calls == ["ran", "ran"]

    def test_recent_files_reads_from_shada(self):
        api = _make_api()
        api._editor_state.shada.push_recent_file("/tmp/a.txt")
        api._editor_state.shada.push_recent_file("/tmp/b.txt")

        result = api.recent_files()

        assert [path.as_posix() for path in result] == ["/tmp/b.txt", "/tmp/a.txt"]

    def test_list_diagnostics_collects_lsp_decorations(self):
        from peovim.core.style import Style
        from peovim.ui.decorations import Sign, VirtualText

        api = _make_api("alpha\n")
        buf = api.active_buffer()
        api._editor_state.decorations.add(
            buf.buf_id,
            "lsp:diag:signs",
            Sign(line=0, char="E", style=Style(fg=(255, 0, 0))),
        )
        api._editor_state.decorations.add(
            buf.buf_id,
            "lsp:diag:text",
            VirtualText(line=0, text=" broken", style=Style(fg=(255, 0, 0))),
        )
        buf._doc.path = Path("/tmp/example.py")

        result = api.list_diagnostics()

        assert len(result) == 1
        assert result[0]["severity"] == "E"
        assert result[0]["message"] == "broken"

    def test_commands_execute_uses_dispatcher_context(self):
        api = _make_api()

        api.commands.execute("set number")

        assert api.active_window().get_option("number") is True

    def test_commands_list_commands_returns_registered_names(self):
        api = _make_api()

        commands = api.commands.list_commands()

        assert "write" in commands


# ---------------------------------------------------------------------------
# BufferAPI
# ---------------------------------------------------------------------------


class TestBufferAPI:
    def test_get_line(self):
        api = _make_api("hello\nworld")
        buf = api.active_buffer()
        assert buf.get_line(0) == "hello"
        assert buf.get_line(1) == "world"

    def test_get_lines(self):
        api = _make_api("a\nb\nc")
        buf = api.active_buffer()
        assert buf.get_lines() == ["a", "b", "c"]
        assert buf.get_lines(1, 3) == ["b", "c"]

    def test_get_text(self):
        api = _make_api("a\nb")
        buf = api.active_buffer()
        assert buf.get_text() == "a\nb"

    def test_line_count(self):
        api = _make_api("a\nb\nc")
        buf = api.active_buffer()
        assert buf.line_count() == 3

    def test_is_valid(self):
        api = _make_api()
        assert api.active_buffer().is_valid()

    def test_is_modified_false_initially(self):
        api = _make_api()
        # Freshly loaded buffer with no changes
        assert not api.active_buffer().is_modified()

    def test_buf_id_is_int(self):
        api = _make_api()
        assert isinstance(api.active_buffer().buf_id, int)

    def test_add_and_clear_highlight(self):
        api = _make_api("hello world")
        buf = api.active_buffer()
        from peovim.core.style import Style

        dec_id = buf.add_highlight("test_ns", 0, 0, 0, 5, Style(fg=(255, 0, 0)))
        assert isinstance(dec_id, int)
        # Verify it's in the store
        decs = api._editor_state.decorations.get_for_buffer(buf.buf_id)
        assert len(decs) >= 1
        buf.clear_namespace("test_ns")
        decs = api._editor_state.decorations.get_for_buffer(buf.buf_id)
        assert len(decs) == 0

    def test_add_sign_raw(self):
        api = _make_api("a\nb\nc")
        buf = api.active_buffer()
        from peovim.core.style import Style

        dec_id = buf.add_sign_raw("signs_ns", 1, "T", Style(fg=(255, 200, 0)))
        assert dec_id >= 0
        decs = api._editor_state.decorations.get_for_buffer(buf.buf_id)
        assert len(decs) >= 1

    def test_add_sign_from_registry(self):
        api = _make_api("a\nb")
        from peovim.core.style import Style

        api.register_sign_type("warn", "W", Style(fg=(255, 0, 0)))
        buf = api.active_buffer()
        dec_id = buf.add_sign("warn_ns", 0, "warn")
        assert dec_id >= 0

    def test_add_sign_unknown_type_returns_minus1(self):
        api = _make_api("a")
        buf = api.active_buffer()
        assert buf.add_sign("ns", 0, "nonexistent_type") == -1

    def test_add_virtual_text(self):
        api = _make_api("a\nb")
        buf = api.active_buffer()
        from peovim.core.style import Style

        dec_id = buf.add_virtual_text("vt_ns", 0, "← note", Style(fg=(120, 120, 120)))
        assert dec_id >= 0

    def test_remove_highlight(self):
        api = _make_api("hello")
        buf = api.active_buffer()
        from peovim.core.style import Style

        did = buf.add_highlight("ns", 0, 0, 0, 5, Style(fg=(255, 0, 0)))
        buf.remove_highlight("ns", did)
        decs = api._editor_state.decorations.get_for_buffer(buf.buf_id)
        assert all(getattr(d, "start_line", None) != 0 for d in decs)

    def test_buffer_pre_save_handler_can_mutate_buffer(self, tmp_path):
        api = _make_api("\talpha\n")
        target = tmp_path / "saved.py"

        def _normalize(**_kwargs):
            buf = api.active_buffer()
            text = buf.get_text()
            last_line_index = max(0, buf.line_count() - 1)
            last_line_len = len(buf.get_line(last_line_index))
            buf.replace(0, 0, last_line_index, last_line_len, text.expandtabs(4))

        api.events.on("buffer_pre_save", _normalize)

        api._dispatcher.dispatch([SaveBuffer(path=str(target))])

        assert "\t" not in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# WindowAPI
# ---------------------------------------------------------------------------


class TestWindowAPI:
    def test_cursor(self):
        api = _make_api("a\nb")
        win = api.active_window()
        assert win.cursor == (0, 0)

    def test_set_cursor(self):
        api = _make_api("a\nb\nc")
        win = api.active_window()
        win.set_cursor(1, 0)
        assert win.cursor == (1, 0)

    def test_set_cursor_allows_end_of_line_in_insert_mode(self):
        api = _make_api("hell")
        api._engine.set_mode(Mode.INSERT)
        win = api.active_window()
        win.set_cursor(0, 4)
        assert win.cursor == (0, 4)

    def test_visible_range(self):
        api = _make_api("a\nb\nc")
        win = api.active_window()
        start, end = win.visible_range()
        assert start == 0
        assert end >= 0

    def test_is_valid(self):
        api = _make_api()
        assert api.active_window().is_valid()

    def test_buffer_accessor(self):
        api = _make_api("x")
        win = api.active_window()
        assert win.buffer().get_line(0) == "x"

    def test_get_set_option(self):
        api = _make_api()
        win = api.active_window()
        win.set_option("number", True)
        assert win.get_option("number") is True


# ---------------------------------------------------------------------------
# EventsAPI
# ---------------------------------------------------------------------------


class TestEventsAPI:
    def test_on_and_emit(self):
        api = _make_api()
        received = []
        api.events.on("test_event", lambda **kw: received.append(kw))
        api.events.emit("test_event", value=42)
        assert received == [{"value": 42}]

    def test_off(self):
        api = _make_api()
        received = []
        tok = api.events.on("test_event", lambda **kw: received.append(kw))
        api.events.off(tok)
        api.events.emit("test_event")
        assert received == []

    def test_once(self):
        api = _make_api()
        received = []
        api.events.once("test_event", lambda **kw: received.append(kw))
        api.events.emit("test_event")
        api.events.emit("test_event")
        assert len(received) == 1


# ---------------------------------------------------------------------------
# OptionsAPI
# ---------------------------------------------------------------------------


class TestOptionsAPI:
    def test_get_default(self):
        api = _make_api()
        assert api.options.get("number") is False

    def test_set_and_get(self):
        api = _make_api()
        api.options.set("number", True)
        assert api.options.get("number") is True

    def test_define_plugin_option(self):
        api = _make_api()
        api.options.define("myplugin_x", int, 0)
        assert api.options.get("myplugin_x") == 0
        api.options.set("myplugin_x", 7)
        assert api.options.get("myplugin_x") == 7


# ---------------------------------------------------------------------------
# CommandsAPI
# ---------------------------------------------------------------------------


class TestCommandsAPI:
    def test_register_and_execute(self):
        api = _make_api()
        called = []
        api.commands.register("TestCmd", lambda cmd, ctx: called.append(cmd.args))
        api.commands.execute("TestCmd hello")
        assert called == ["hello"]

    def test_unregister(self):
        api = _make_api()
        api.commands.register("TempCmd", lambda cmd, ctx: None)
        api.commands.unregister("TempCmd")
        # Executing after unregister should not raise
        api.commands.execute("TempCmd x")


# ---------------------------------------------------------------------------
# KeymapAPI + BindingRegistry
# ---------------------------------------------------------------------------


class TestKeymapAPI:
    def test_nmap_string_registers_binding(self):
        api = _make_api()
        api.keymap.nmap("<leader>t", ":echo hi<CR>", desc="test")
        info = api._binding_registry.lookup("normal", "<leader>t")
        assert info is not None
        assert info.desc == "test"

    def test_nmap_callable_registers_binding(self):
        api = _make_api()
        called = []
        api.keymap.nmap("<leader>c", lambda: called.append(True))
        info = api._binding_registry.lookup("normal", "<leader>c")
        assert info is not None

    def test_define_vplug_and_vmap_register_visual_binding(self):
        api = _make_api()

        api.keymap.define_vplug("VisualTest", lambda ctx: None)
        api.keymap.vmap("ga", "<Plug>VisualTest", desc="visual test")

        info = api._binding_registry.lookup("visual", "ga")
        assert info is not None
        assert info.desc == "visual test"

    def test_invoke_plug_executes_contextual_callback(self):
        api = _make_api()
        calls: list[tuple[str, tuple[int, int] | None]] = []

        def _plug(ctx) -> None:
            calls.append((ctx.mode, ctx.cursor))

        api.keymap.define_plug("InvokePlugTest", _plug, desc="invoke plug")

        assert api.keymap.invoke_plug("<Plug>InvokePlugTest") is True
        assert calls == [("normal", (0, 0))]

    def test_invoke_plug_returns_false_for_unknown_plug(self):
        api = _make_api()

        assert api.keymap.invoke_plug("<Plug>Missing") is False

    def test_get_bindings_reads_registry(self):
        api = _make_api()
        api.keymap.nmap("x", lambda: None, desc="test")

        bindings = api.keymap.get_bindings("normal")

        assert any(binding.keys == "x" for binding in bindings)

    def test_get_group_name_reads_registry(self):
        api = _make_api()
        api.keymap.ngroup("<leader>g", "Git")

        assert api.keymap.get_group_name("<leader>g") == "Git"

    def test_callable_stored_in_dispatcher(self):
        api = _make_api()

        def fn() -> None:
            return None

        api.keymap.nmap("zX", fn)
        # The callback should be registered in the dispatcher
        assert fn in api._dispatcher._plugin_callbacks.values()

    def test_get_bindings_filtered(self):
        api = _make_api()
        api.keymap.nmap("a", lambda: None)
        api.keymap.imap("b", lambda: None)
        normal_bindings = api._binding_registry.get_bindings("normal")
        assert any(b.keys == "a" for b in normal_bindings)
        assert not any(b.keys == "b" for b in normal_bindings)

    def test_get_bindings_all(self):
        api = _make_api()
        api.keymap.nmap("x", lambda: None)
        api.keymap.imap("y", lambda: None)
        all_bindings = api._binding_registry.get_bindings()
        assert len(all_bindings) >= 2

    def test_leader_returns_string(self):
        api = _make_api()
        assert isinstance(api.keymap.leader, str)

    def test_local_leader_returns_string(self):
        api = _make_api()
        assert isinstance(api.keymap.local_leader, str)


class TestUIAPI:
    def test_show_and_hide_which_key_forward_to_panel(self):
        api = _make_api()
        api.ui._which_key_panel = MagicMock()

        api.ui.show_which_key([("f", "Find files")], title="Which Key")
        api.ui.hide_which_key()

        api.ui._which_key_panel.show.assert_called_once_with([("f", "Find files")], title="Which Key")
        api.ui._which_key_panel.hide.assert_called_once()


# ---------------------------------------------------------------------------
# PluginManager
# ---------------------------------------------------------------------------


class TestPluginManager:
    def test_load_nonexistent_is_noop(self):
        from peovim.plugins.manager import PluginManager

        api = _make_api()
        pm = PluginManager(api)
        pm.load("nonexistent_module_xyz_123")  # should not raise
        assert "nonexistent_module_xyz_123" not in pm.list_loaded()

    def test_list_loaded_empty_initially(self):
        from peovim.plugins.manager import PluginManager

        api = _make_api()
        pm = PluginManager(api)
        assert pm.list_loaded() == []

    def test_load_calls_setup(self, tmp_path, monkeypatch):
        # Create a minimal plugin module in a temp directory
        plugin_file = tmp_path / "test_plugin_abc.py"
        plugin_file.write_text("def setup(api): api._test_loaded = True\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        from peovim.plugins.manager import PluginManager

        api = _make_api()
        pm = PluginManager(api)
        pm.load("test_plugin_abc")
        assert getattr(api, "_test_loaded", False) is True
        assert "test_plugin_abc" in pm.list_loaded()

    def test_unload_calls_teardown(self, tmp_path, monkeypatch):
        plugin_file = tmp_path / "test_plugin_teardown.py"
        plugin_file.write_text(
            "torn_down = False\ndef setup(api): pass\ndef teardown(): global torn_down; torn_down = True\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        from peovim.plugins.manager import PluginManager

        api = _make_api()
        pm = PluginManager(api)
        pm.load("test_plugin_teardown")
        pm.unload("test_plugin_teardown")
        import importlib

        m = importlib.import_module("test_plugin_teardown")
        assert m.torn_down is True

    def test_get_returns_module(self, tmp_path, monkeypatch):
        plugin_file = tmp_path / "test_plugin_get.py"
        plugin_file.write_text("def setup(api): pass\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        from peovim.plugins.manager import PluginManager

        api = _make_api()
        pm = PluginManager(api)
        pm.load("test_plugin_get")
        assert pm.get("test_plugin_get") is not None
        assert pm.get("nonexistent") is None


# ---------------------------------------------------------------------------
# RunPlugin dispatch integration
# ---------------------------------------------------------------------------


class TestRunPluginDispatch:
    def test_callable_nmap_fires_on_keypress(self):
        api = _make_api("hello")
        called = []
        api.keymap.nmap("zQ", lambda: called.append(True))
        # Simulate pressing 'z' then 'Q'
        actions1 = api._engine.feed_key("z")
        api._dispatcher.dispatch(actions1)
        actions2 = api._engine.feed_key("Q")
        api._dispatcher.dispatch(actions2)
        assert called == [True]
