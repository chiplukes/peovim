from __future__ import annotations

import io
import os
from contextlib import contextmanager

import pytest

from peovim.ui.backend import KeyEvent


class FakeInput:
    def __init__(self, batches: list[list[object]] | None = None) -> None:
        self._batches = batches or []
        self.attached_callback = None
        self.entered_raw_mode = False
        self.exited_raw_mode = False

    @contextmanager
    def raw_mode(self):
        self.entered_raw_mode = True
        try:
            yield self
        finally:
            self.exited_raw_mode = True

    @contextmanager
    def attach(self, callback):
        self.attached_callback = callback
        if self._batches:
            callback()
        yield self

    def read_keys(self):
        if not self._batches:
            return []
        return self._batches.pop(0)

    def flush_keys(self):
        return []


class DelayedFlushInput(FakeInput):
    def __init__(self, delayed_batches: list[list[object]]) -> None:
        super().__init__(batches=[])
        self._delayed_batches = delayed_batches
        self._flush_count = 0

    def read_keys(self):
        return []

    def flush_keys(self):
        self._flush_count += 1
        if self._delayed_batches:
            return self._delayed_batches.pop(0)
        return []


class FakeKeyPress:
    def __init__(self, key: str, data: str | None = None) -> None:
        self.key = key
        self.data = data if data is not None else key


class EncodingLimitedStdout:
    def __init__(self, encoding: str = "cp1252") -> None:
        self.encoding = encoding
        self.buffer = io.BytesIO()
        self.writes: list[str] = []
        self.flush_calls = 0

    def write(self, text: str) -> int:
        text.encode(self.encoding)
        self.writes.append(text)
        return len(text)

    def flush(self) -> None:
        self.flush_calls += 1


def test_prompt_toolkit_backend_write_flush_and_pending_output(monkeypatch: pytest.MonkeyPatch) -> None:
    import peovim.ui.backends.prompt_toolkit as backend_module

    output = io.StringIO()
    fake_input = FakeInput()
    monkeypatch.setattr(backend_module, "create_input", lambda: fake_input)
    monkeypatch.setattr(backend_module.sys, "stdout", output)

    backend = backend_module.PromptToolkitBackend()
    backend.write([])
    assert backend.has_pending_output() is False

    from peovim.ui.backend import MoveCursor, PutCells, SetCursorStyle

    backend.write([MoveCursor(1, 2), PutCells("hi"), SetCursorStyle(shape="bar", blink=True)])

    assert backend.has_pending_output() is True

    backend.flush()

    assert backend.has_pending_output() is False
    assert "\x1b[2;3H" in output.getvalue()
    assert "hi" in output.getvalue()
    assert "\x1b[5 q" in output.getvalue()


def test_prompt_toolkit_backend_flush_falls_back_on_encoding_errors(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    import peovim.ui.backends.prompt_toolkit as backend_module

    output = EncodingLimitedStdout("cp1252")
    fake_input = FakeInput()
    monkeypatch.setattr(backend_module, "create_input", lambda: fake_input)
    monkeypatch.setattr(backend_module.sys, "stdout", output)

    backend = backend_module.PromptToolkitBackend()
    backend.write([backend_module.PutCells("left ↔ right")])

    backend.flush()

    assert backend.has_pending_output() is False
    assert output.buffer.getvalue().decode("cp1252") == "\x1b[0mleft ? right"
    assert "falling back to replacement characters" in caplog.text
    assert output.flush_calls == 1


def test_prompt_toolkit_backend_get_size_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import peovim.ui.backends.prompt_toolkit as backend_module

    monkeypatch.setattr(backend_module, "create_input", lambda: FakeInput())
    monkeypatch.setattr(backend_module.os, "get_terminal_size", lambda: (_ for _ in ()).throw(OSError()))

    backend = backend_module.PromptToolkitBackend()

    assert backend.get_size() == (80, 24)


def test_prompt_toolkit_backend_get_size_uses_terminal_size(monkeypatch: pytest.MonkeyPatch) -> None:
    import peovim.ui.backends.prompt_toolkit as backend_module

    monkeypatch.setattr(backend_module, "create_input", lambda: FakeInput())
    monkeypatch.setattr(backend_module.os, "get_terminal_size", lambda: os.terminal_size((120, 50)))

    backend = backend_module.PromptToolkitBackend()

    assert backend.get_size() == (120, 50)


def test_prompt_toolkit_backend_raw_mode_writes_terminal_sequences(monkeypatch: pytest.MonkeyPatch) -> None:
    import peovim.ui.backends.prompt_toolkit as backend_module

    output = io.StringIO()
    fake_input = FakeInput()
    monkeypatch.setattr(backend_module, "create_input", lambda: fake_input)
    monkeypatch.setattr(backend_module.sys, "stdout", output)

    backend = backend_module.PromptToolkitBackend()
    backend.enter_raw_mode()
    backend.exit_raw_mode()

    text = output.getvalue()
    assert fake_input.entered_raw_mode is True
    assert fake_input.exited_raw_mode is True
    assert "\x1b[2J\x1b[H\x1b[?25l" in text
    assert "\x1b[?1000h\x1b[?1002h\x1b[?1006h" in text
    assert "\x1b[?2004h" in text
    assert "\x1b[?2004l" in text
    assert "\x1b[2 q\x1b[0m\x1b[?25h\x1b[2J\x1b[H" in text


@pytest.mark.asyncio
async def test_prompt_toolkit_backend_read_events_translates_alt_combo(monkeypatch: pytest.MonkeyPatch) -> None:
    import peovim.ui.backends.prompt_toolkit as backend_module

    fake_input = FakeInput(batches=[[FakeKeyPress("escape", "\x1b"), FakeKeyPress("x", "x")]])
    monkeypatch.setattr(backend_module, "create_input", lambda: fake_input)

    backend = backend_module.PromptToolkitBackend()
    events = backend.read_events()

    assert await anext(events) == KeyEvent("<A-x>")


@pytest.mark.asyncio
async def test_prompt_toolkit_backend_read_events_polls_lone_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    import peovim.ui.backends.prompt_toolkit as backend_module

    fake_input = DelayedFlushInput([[FakeKeyPress("escape", "\x1b")]])
    monkeypatch.setattr(backend_module, "create_input", lambda: fake_input)

    backend = backend_module.PromptToolkitBackend()
    events = backend.read_events()

    assert await anext(events) == KeyEvent("<Esc>")


@pytest.mark.asyncio
async def test_prompt_toolkit_backend_read_events_emits_bracketed_paste(monkeypatch: pytest.MonkeyPatch) -> None:
    from prompt_toolkit.keys import Keys

    import peovim.ui.backends.prompt_toolkit as backend_module

    fake_input = FakeInput(batches=[[FakeKeyPress(Keys.BracketedPaste, "C:\\temp\\demo.v")]])
    monkeypatch.setattr(backend_module, "create_input", lambda: fake_input)

    backend = backend_module.PromptToolkitBackend()
    events = backend.read_events()

    assert await anext(events) == KeyEvent("<BracketedPaste>", text="C:\\temp\\demo.v")


def test_prompt_toolkit_backend_can_capture_cmdline_escape_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    import peovim.ui.backends.prompt_toolkit as backend_module
    from peovim.core.document import Document
    from peovim.core.editor_state import EditorState
    from peovim.core.registers import RegisterStore
    from peovim.core.window import Window
    from peovim.core.workspace import Workspace
    from peovim.modal.dispatcher import ActionDispatcher
    from peovim.modal.engine import ModalEngine
    from peovim.ui.event_loop import EventLoop

    output = io.StringIO()
    monkeypatch.setattr(backend_module, "create_input", lambda: FakeInput())
    monkeypatch.setattr(backend_module.sys, "stdout", output)
    monkeypatch.setattr(backend_module.os, "get_terminal_size", lambda: os.terminal_size((40, 8)))

    backend = backend_module.PromptToolkitBackend()

    doc = Document()
    doc.load_string("abc\n")
    window = Window(doc, width=40, height=8)
    workspace = Workspace(window)
    engine = ModalEngine()
    registers = RegisterStore()
    editor_state = EditorState()
    dispatcher = ActionDispatcher(
        engine,
        window,
        registers,
        editor_state=editor_state,
        workspace=workspace,
    )
    event_loop = EventLoop(backend, engine, dispatcher, workspace, editor_state=editor_state)
    event_loop._syntax_engine.submit = lambda *args, **kwargs: None

    event_loop._handle_key_event(backend_module.KeyEvent(":"))
    event_loop._render({"full"})
    backend.flush()
    colon_output = output.getvalue()

    event_loop._handle_key_event(backend_module.KeyEvent("<Esc>"))
    backend.flush()

    clear_output = output.getvalue()[len(colon_output) :]
    assert clear_output == "\x1b[8;1H\x1b[0m" + (" " * 40)
