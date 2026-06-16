from __future__ import annotations

from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.modal.actions import InsertText, RunPlugin
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
    return doc, window, dispatcher, editor_state


def test_dispatch_flushes_events_before_callbacks() -> None:
    _doc, _window, dispatcher, editor_state = _session("hello\n")
    observed: list[str] = []

    def on_change(**_kwargs) -> None:
        observed.append("event")

    def callback() -> None:
        observed.append("callback")

    editor_state.event_bus.on("buffer_changed", on_change)
    dispatcher._plugin_callbacks[1] = callback

    dispatcher.dispatch([InsertText(0, 5, "!"), RunPlugin(1)])

    assert observed == ["event", "callback"]


def test_dispatch_plugin_callback_error_sets_message() -> None:
    _doc, _window, dispatcher, editor_state = _session("hello\n")

    def callback() -> None:
        raise RuntimeError("boom")

    dispatcher._plugin_callbacks[2] = callback

    dispatcher.dispatch([RunPlugin(2)])

    assert editor_state.message == "Plugin error: boom"
