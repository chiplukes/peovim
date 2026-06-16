from __future__ import annotations

from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.modal.actions import DeleteRange, PluginContext, RepeatLastChange, RunPlugin
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
    return doc, window, dispatcher


def test_run_plugin_invokes_plain_callback() -> None:
    _doc, _window, dispatcher = _session("hello\n")
    called: list[str] = []

    dispatcher._plugin_callbacks[1] = lambda: called.append("called")

    dispatcher.dispatch([RunPlugin(1)])

    assert called == ["called"]


def test_run_plugin_passes_context_when_callback_requires_it() -> None:
    _doc, _window, dispatcher = _session("hello\n")
    seen: list[PluginContext] = []
    context = PluginContext(
        mode="normal",
        visual_range=None,
        count=3,
        register="a",
        cursor=(0, 2),
        is_repeat=False,
        visual_line_count=1,
    )

    def callback(ctx: PluginContext) -> None:
        seen.append(ctx)

    dispatcher._plugin_callbacks[2] = callback

    dispatcher.dispatch([RunPlugin(2, context)])

    assert seen == [context]


def test_repeat_last_change_replays_plugin_with_repeat_context() -> None:
    _doc, window, dispatcher = _session("hello\n")
    seen: list[PluginContext] = []
    initial_context = PluginContext(
        mode="normal",
        visual_range=None,
        count=2,
        register="b",
        cursor=(0, 1),
        is_repeat=False,
        visual_line_count=4,
    )

    def callback(ctx: PluginContext) -> None:
        seen.append(ctx)

    dispatcher._plugin_callbacks[3] = callback

    dispatcher.dispatch([RunPlugin(3, initial_context)])
    window.cursor.move_to(0, 4)
    dispatcher.dispatch([RepeatLastChange()])

    assert len(seen) == 2
    assert seen[0] == initial_context
    assert seen[1].is_repeat is True
    assert seen[1].count == 2
    assert seen[1].register == "b"
    assert seen[1].cursor == (0, 4)
    assert seen[1].visual_line_count == 4


def test_repeat_last_change_replays_plain_action() -> None:
    doc, _window, dispatcher = _session("hello\n")
    dispatcher._dot_repeat = DeleteRange(0, 0, 0, 1)

    dispatcher.dispatch([RepeatLastChange()])

    assert doc.get_line(0) == "ello"


def test_repeat_last_change_without_history_is_noop() -> None:
    doc, _window, dispatcher = _session("hello\n")

    dispatcher.dispatch([RepeatLastChange()])

    assert doc.get_line(0) == "hello"
