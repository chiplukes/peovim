from __future__ import annotations

from peovim.commands.builtin import register_builtins
from peovim.commands.parser import ParsedCommand
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.modal.actions import EnterInsertMode, RepeatLastExCommand, RunExCommand, RunNormalKeys
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine


def _session(content: str = ""):
    doc = Document()
    doc.load_string(content)
    window = Window(doc)
    editor_state = EditorState()
    engine = ModalEngine()
    engine.set_document(doc)
    engine.set_cursor(0, 0)
    engine.set_line_count(doc.line_count())
    dispatcher = ActionDispatcher(
        engine,
        window,
        RegisterStore(),
        editor_state=editor_state,
    )
    registry = CommandRegistry()
    register_builtins(registry)
    dispatcher._command_registry = registry
    return doc, window, dispatcher


def test_run_ex_command_executes_via_dispatcher() -> None:
    doc, _window, dispatcher = _session("hello world")

    dispatcher.dispatch([RunExCommand("s/world/there")])

    assert doc.get_line(0) == "hello there"


def test_repeat_last_ex_command_replays_previous_command() -> None:
    doc, window, dispatcher = _session("foo\nfoo\n")

    dispatcher.dispatch([RunExCommand("s/foo/bar/")])
    window.cursor.move_to(1, 0)
    dispatcher.dispatch([RepeatLastExCommand()])

    assert doc.get_line(0) == "bar"
    assert doc.get_line(1) == "bar"


def test_run_normal_keys_with_ex_command_extracts_and_runs_ex() -> None:
    _doc, window, dispatcher = _session("abc\n")

    dispatcher.dispatch([RunNormalKeys(":set number<CR>")])

    assert window.options["number"] is True


def test_run_normal_keys_replays_keys_through_engine() -> None:
    doc, _window, dispatcher = _session("abc\n")

    dispatcher.dispatch([RunNormalKeys("x")])

    assert doc.get_line(0) == "bc"


def test_run_normal_keys_without_remap_bypasses_insert_user_binding() -> None:
    doc, window, dispatcher = _session("ab")
    dispatcher.dispatch([EnterInsertMode(position="new_line_below")])

    calls: list[str] = []

    def user_backspace(_state):
        calls.append("mapped")
        return []

    dispatcher.engine.add_user_binding(dispatcher.engine.mode, "<BS>", user_backspace)
    window.cursor.move_to(1, 0)
    dispatcher.engine.set_cursor(1, 0)

    dispatcher.dispatch([RunNormalKeys("<BS>", remap=False)])

    assert calls == []
    assert doc.line_count() == 1
    assert doc.get_line(0) == "ab"


def test_run_ex_command_lazy_initializes_registry() -> None:
    doc = Document()
    doc.load_string("hello world")
    window = Window(doc)
    editor_state = EditorState()
    engine = ModalEngine()
    engine.set_document(doc)
    engine.set_cursor(0, 0)
    engine.set_line_count(doc.line_count())
    dispatcher = ActionDispatcher(
        engine,
        window,
        RegisterStore(),
        editor_state=editor_state,
    )

    assert dispatcher._command_registry is None

    dispatcher.dispatch([RunExCommand("s/world/there")])

    assert dispatcher._command_registry is not None
    assert doc.get_line(0) == "hello there"


def test_run_ex_command_handler_exception_sets_message() -> None:
    _doc, _window, dispatcher = _session("hello\n")
    registry = CommandRegistry()

    def failing_handler(_parsed: ParsedCommand, _ctx: object) -> None:
        raise RuntimeError("boom")

    registry.register("boom", failing_handler)
    dispatcher._command_registry = registry

    dispatcher.dispatch([RunExCommand("boom")])

    assert dispatcher._editor_state is not None
    assert dispatcher._editor_state.message == "E: boom"
