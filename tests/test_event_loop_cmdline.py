from __future__ import annotations

import asyncio

import pytest

from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.actions import OpenBuffer
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine, Mode
from peovim.syntax.engine import HighlightSpan
from peovim.ui.backend import KeyEvent
from peovim.ui.backends.headless import HeadlessBackend
from peovim.ui.cell_grid import CellGrid
from peovim.ui.decorations import HighlightRegion
from peovim.ui.event_loop import EventLoop
from peovim.ui.layout import Rect
from peovim.ui.render_jobs import RenderRuntimeDiagnostics


class FiniteHeadlessBackend(HeadlessBackend):
    def __init__(self, cols: int = 40, rows: int = 8) -> None:
        super().__init__(cols=cols, rows=rows)
        self._events: list[KeyEvent] = []

    def set_keys(self, *keys: str) -> None:
        self._events = [KeyEvent(key) for key in keys]

    async def read_events(self):
        while self._events:
            yield self._events.pop(0)


class FakePicker:
    def __init__(self) -> None:
        self.is_open = True
        self.keys: list[str] = []

    def feed_key(self, key: str) -> None:
        self.keys.append(key)


class FakeSidebar:
    def __init__(self) -> None:
        self.visible = True
        self.focused = True
        self.keys: list[str] = []

    def feed_key(self, key: str) -> bool:
        self.keys.append(key)
        return True


class FakeCompletionPopup:
    def __init__(self, accepted: str | None = None, feed_returns: bool = False) -> None:
        self.is_open = True
        self.accepted = accepted
        self.feed_returns = feed_returns
        self.fed_keys: list[str] = []
        self.render_calls: list[tuple[int, int]] = []

    def accept(self) -> str | None:
        self.is_open = False
        return self.accepted

    def feed_key(self, key: str) -> bool:
        self.fed_keys.append(key)
        return self.feed_returns

    def render(self, _grid: CellGrid, row: int, col: int) -> None:
        self.render_calls.append((row, col))


class FakeFloatManager:
    def __init__(self) -> None:
        self.has_focused = True
        self.keys: list[str] = []
        self.render_order: list[str] | None = None
        self.closed: int = 0
        self.handles: list[object] = []
        self.last_kwargs: dict | None = None

    def feed_key(self, key: str) -> bool:
        self.keys.append(key)
        return True

    def render(self, _grid: CellGrid) -> None:
        if self.render_order is not None:
            self.render_order.append("float")

    def close_all(self) -> None:
        self.closed += len(self.handles)
        self.handles.clear()

    def focus(self, _handle) -> None:
        self.has_focused = True

    def open_float(self, content, **kwargs):
        manager = self
        self.last_kwargs = kwargs

        class _Handle:
            def __init__(self) -> None:
                self.closed = False
                self.content = list(content) if isinstance(content, list) else str(content).splitlines()

            def close(self) -> None:
                self.closed = True
                manager.closed += 1

        handle = _Handle()
        self.handles.append(handle)
        return handle


class FakeTree:
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.render_order: list[str] | None = None

    def feed_key(self, key: str) -> None:
        self.keys.append(key)

    def render(self, _grid: CellGrid) -> None:
        if self.render_order is not None:
            self.render_order.append("tree")


class FakeTreeHandle:
    def __init__(self, tree: FakeTree) -> None:
        self.is_open = True
        self.tree = tree


class FakeNotifyManager:
    def __init__(self, render_order: list[str]) -> None:
        self.render_order = render_order
        self.notifications: list[dict[str, object]] = []

    def notify(self, message: str, level: str = "info", title: str = "", timeout: float = 3.0) -> None:
        self.notifications.append({"message": message, "level": level, "title": title, "timeout": timeout})

    def render(self, _grid: CellGrid) -> None:
        self.render_order.append("notify")


class FakeRenderPicker:
    def __init__(self, render_order: list[str]) -> None:
        self.is_open = True
        self.render_order = render_order

    def render(self, _grid: CellGrid, **_kwargs: object) -> None:
        self.render_order.append("picker")


def _bottom_row_text(event_loop: EventLoop) -> str:
    assert event_loop._grid is not None
    return "".join(cell[0] for cell in event_loop._grid._current[event_loop._grid.height - 1])


def _row_text(event_loop: EventLoop, row: int) -> str:
    assert event_loop._grid is not None
    return "".join(cell[0] for cell in event_loop._grid._current[row])


def _make_event_loop(text: str = "abc\n") -> tuple[EventLoop, FiniteHeadlessBackend, Document, Window]:
    doc = Document()
    doc.load_string(text)
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
    backend = FiniteHeadlessBackend()
    event_loop = EventLoop(backend, engine, dispatcher, workspace, editor_state=editor_state)
    event_loop._syntax_engine.submit = lambda *args, **kwargs: None
    event_loop._running = True
    return event_loop, backend, doc, window


def test_bracketed_paste_path_opens_buffer_in_normal_mode(tmp_path) -> None:
    event_loop, _backend, _doc, _window = _make_event_loop("abc\n")
    dropped = tmp_path / "dropped file.v"
    dropped.write_text("module dropped;\n", encoding="utf-8")

    event_loop._handle_key_event(KeyEvent("<BracketedPaste>", text=str(dropped)))

    assert event_loop._workspace.active_window.document.path == dropped.resolve()
    assert event_loop._cmdline.active is False


def test_bracketed_paste_replays_into_active_cmdline() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop("abc\n")

    event_loop._handle_key_event(KeyEvent(":"))
    event_loop._handle_key_event(KeyEvent("<BracketedPaste>", text="edit test.v"))

    assert event_loop._cmdline.active is True
    assert event_loop._cmdline.text == "edit test.v"


def test_full_render_populates_grid_on_startup() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop("hello\n")

    event_loop._render({"full"})

    assert event_loop._grid is not None


def test_visual_prefix_events_emit_visual_mode() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop("abc\n")
    seen: list[tuple[str, dict[str, object]]] = []

    event_loop._engine.register_user_binding(Mode.VISUAL_CHAR, "gc", lambda _state: [])
    event_loop._editor_state.event_bus.on(
        "key_prefix_pending",
        lambda **kwargs: seen.append(("pending", kwargs)),
    )

    event_loop._handle_key_event(KeyEvent("v"))
    seen.clear()
    event_loop._handle_key_event(KeyEvent("g"))

    assert seen == [("pending", {"prefix": "g", "mode": "visual"})]


def test_sync_window_render_state_preserves_manual_scroll_when_cursor_follow_disabled() -> None:
    event_loop, _backend, _doc, window = _make_event_loop("\n".join(f"line{i}" for i in range(60)) + "\n")
    window.cursor.move_to(0, 0)
    window.scroll_line = 24
    window.follow_cursor = False

    event_loop._window_render_controller.sync_window_render_state(window, Rect(0, 0, 40, 8))

    assert window.scroll_line == 24


def test_sync_window_render_state_keeps_cursor_visible_when_cursor_follow_enabled() -> None:
    event_loop, _backend, _doc, window = _make_event_loop("\n".join(f"line{i}" for i in range(60)) + "\n")
    window.cursor.move_to(24, 0)
    window.scroll_line = 0
    window.follow_cursor = True

    event_loop._window_render_controller.sync_window_render_state(window, Rect(0, 0, 40, 8))

    assert window.scroll_line > 0


def test_full_render_skips_clear_when_grid_is_new() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop("hello\n")
    clear_flags: list[bool] = []

    def _fake_render_body(_grid: CellGrid, _cols: int, _rows: int, *, clear_grid: bool = True) -> None:
        clear_flags.append(clear_grid)

    event_loop._render_body = _fake_render_body  # type: ignore[method-assign]
    event_loop._build_terminal_cursor_ops = lambda: []

    event_loop._render({"full"})

    assert clear_flags == [False]
    assert _backend.raw_bytes().startswith(b"\x1b[2J\x1b[H")


def test_full_render_clears_when_grid_is_reused() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop("hello\n")
    event_loop._grid = CellGrid(40, 8)
    clear_flags: list[bool] = []

    def _fake_render_body(_grid: CellGrid, _cols: int, _rows: int, *, clear_grid: bool = True) -> None:
        clear_flags.append(clear_grid)

    event_loop._render_body = _fake_render_body  # type: ignore[method-assign]
    event_loop._build_terminal_cursor_ops = lambda: []

    event_loop._render({"full"})

    assert clear_flags == [True]
    assert not _backend.raw_bytes().startswith(b"\x1b[2J\x1b[H")


def test_render_error_sets_message_and_notification(caplog: pytest.LogCaptureFixture) -> None:
    event_loop, _backend, _doc, _window = _make_event_loop("hello\n")
    notify_manager = FakeNotifyManager([])
    event_loop._notify_manager = notify_manager
    event_loop._grid = CellGrid(40, 8)
    event_loop._dirty = True
    event_loop._pending_invalidation_reasons = {"full"}
    event_loop._render = lambda _reasons=None: (_ for _ in ()).throw(RuntimeError("boom"))

    with caplog.at_level("ERROR"):
        event_loop._render_if_dirty()

    assert event_loop._editor_state.message == "Error in render: boom (see :messages / :LogView)"
    assert notify_manager.notifications[0]["message"] == "Error in render: boom (see :messages / :LogView)"
    assert "RuntimeError: boom" in caplog.text


def test_asyncio_exception_handler_sets_message_and_notification(caplog: pytest.LogCaptureFixture) -> None:
    event_loop, _backend, _doc, _window = _make_event_loop("hello\n")
    notify_manager = FakeNotifyManager([])
    event_loop._notify_manager = notify_manager

    with caplog.at_level("ERROR"):
        event_loop._handle_asyncio_exception(
            asyncio.new_event_loop(),
            {"message": "Task exception was never retrieved", "exception": RuntimeError("task boom")},
        )

    assert event_loop._editor_state.message == "Error in asyncio task: task boom (see :messages / :LogView)"
    assert notify_manager.notifications[0]["level"] == "error"
    assert "task boom" in caplog.text


@pytest.mark.asyncio
async def test_colon_enters_and_renders_command_line() -> None:
    event_loop, backend, _doc, _window = _make_event_loop()
    backend.set_keys(":")

    await event_loop._input_loop()

    assert "cmdline" in event_loop._pending_invalidation_reasons

    event_loop._render()

    assert event_loop._cmdline.active is True
    assert _bottom_row_text(event_loop).startswith(":")


@pytest.mark.asyncio
async def test_cmdline_cancel_with_escape_clears_bottom_row() -> None:
    event_loop, backend, _doc, _window = _make_event_loop()
    backend.set_keys(":")
    await event_loop._input_loop()

    assert "cmdline" in event_loop._pending_invalidation_reasons

    event_loop._render()
    assert _bottom_row_text(event_loop).startswith(":")

    backend.set_keys("<Esc>")
    await event_loop._input_loop()

    assert "cmdline" in event_loop._pending_invalidation_reasons

    event_loop._render()

    assert event_loop._cmdline.active is False
    assert _bottom_row_text(event_loop).strip() == ""


@pytest.mark.asyncio
async def test_cmdline_cancel_with_escape_clears_immediately_without_manual_render() -> None:
    event_loop, backend, _doc, _window = _make_event_loop()

    backend.set_keys(":")
    await event_loop._input_loop()
    event_loop._render()
    assert _bottom_row_text(event_loop).startswith(":")

    backend.set_keys("<Esc>")
    await event_loop._input_loop()

    assert event_loop._cmdline.active is False
    assert _bottom_row_text(event_loop).strip() == ""


@pytest.mark.asyncio
async def test_cmdline_submit_applies_command_and_clears_bottom_row() -> None:
    event_loop, backend, _doc, window = _make_event_loop()
    backend.set_keys(":", "s", "e", "t", " ", "n", "u", "m", "b", "e", "r", "<CR>")

    await event_loop._input_loop()

    assert "cmdline" in event_loop._pending_invalidation_reasons

    event_loop._render()

    assert event_loop._cmdline.active is False
    assert window.options["number"] is True
    assert _bottom_row_text(event_loop).strip() == ""


@pytest.mark.asyncio
async def test_active_cmdline_takes_precedence_over_focused_sidebar() -> None:
    event_loop, backend, _doc, _window = _make_event_loop()
    sidebar = FakeSidebar()
    event_loop._sidebar = sidebar
    event_loop._cmdline.enter(":", "ExplorerRename old.txt")
    event_loop._consume_invalidation_reasons()
    backend.set_keys(" ", "n", "e", "w")

    await event_loop._input_loop()

    assert event_loop._cmdline.text == "ExplorerRename old.txt new"
    assert sidebar.keys == []
    assert event_loop._pending_invalidation_reasons == {"cmdline", "full"}


@pytest.mark.asyncio
async def test_colon_tab_opens_command_picker() -> None:
    event_loop, backend, _doc, _window = _make_event_loop()
    backend.set_keys(":", "<Tab>")

    await event_loop._input_loop()
    event_loop._render()

    assert event_loop._cmdline.active is True
    assert event_loop._cmdline.completion_open is True
    assert _row_text(event_loop, 0).strip().startswith("!")


@pytest.mark.asyncio
async def test_colon_tab_then_typing_filters_command_picker() -> None:
    event_loop, backend, _doc, _window = _make_event_loop()
    backend.set_keys(":", "<Tab>", "w", "r")

    await event_loop._input_loop()
    event_loop._render()

    assert event_loop._cmdline.text == "wr"
    assert event_loop._cmdline.completion_open is True
    visible_rows = [_row_text(event_loop, row) for row in range(event_loop._grid.height - 1)]
    assert any("write" in row for row in visible_rows)


@pytest.mark.asyncio
async def test_colon_tab_accepts_selected_command_into_cmdline() -> None:
    event_loop, backend, _doc, _window = _make_event_loop()
    backend.set_keys(":", "<Tab>", "w", "r", "<Tab>")

    await event_loop._input_loop()

    assert event_loop._cmdline.active is True
    assert event_loop._cmdline.text == "write "
    assert event_loop._cmdline.cursor_col == len("write ")
    assert event_loop._cmdline.completion_open is False


@pytest.mark.asyncio
async def test_cmdline_escape_clears_command_picker_immediately() -> None:
    event_loop, backend, _doc, _window = _make_event_loop()
    backend.set_keys(":", "<Tab>")
    await event_loop._input_loop()
    event_loop._render()

    assert any("LogClear" in _row_text(event_loop, row) for row in range(event_loop._grid.height - 1))

    backend.set_keys("<Esc>")
    await event_loop._input_loop()

    assert event_loop._cmdline.active is False
    assert all("LogClear" not in _row_text(event_loop, row) for row in range(event_loop._grid.height))


def test_full_render_clears_stale_cells_before_compositing(monkeypatch) -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    event_loop._grid = CellGrid(40, 8)
    event_loop._grid.write_str(0, 0, "ghost text")

    monkeypatch.setattr(event_loop, "_render_window_content", lambda *args, **kwargs: None)
    monkeypatch.setattr(event_loop, "_render_separators", lambda *args, **kwargs: None)
    monkeypatch.setattr(event_loop, "_render_which_key_panel", lambda *args, **kwargs: None)
    monkeypatch.setattr(event_loop._frame_controller, "render_status_row", lambda *args, **kwargs: None)
    monkeypatch.setattr(event_loop._frame_controller, "render_cmdline_row", lambda *args, **kwargs: None)
    monkeypatch.setattr(event_loop, "_render_tree_views", lambda *args, **kwargs: None)
    monkeypatch.setattr(event_loop, "_render_overlay_widgets", lambda *args, **kwargs: None)

    event_loop._render({"full"})

    assert all(cell[0] == " " for cell in event_loop._grid._current[0])


@pytest.mark.asyncio
async def test_picker_escape_clears_picker_rows_immediately() -> None:
    from peovim.ui.picker import PickerWidget

    event_loop, backend, _doc, _window = _make_event_loop()
    picker = PickerWidget()
    picker.open("GhostPicker", ["alpha", "beta"])
    event_loop._picker = picker

    event_loop._render({"full"})
    assert any("GhostPicker" in _row_text(event_loop, row) for row in range(event_loop._grid.height))

    backend.set_keys("<Esc>")
    await event_loop._input_loop()

    assert event_loop._picker.is_open is False
    assert all("GhostPicker" not in _row_text(event_loop, row) for row in range(event_loop._grid.height))


@pytest.mark.asyncio
async def test_alt_batched_key_dismisses_cmdline_and_processes_normal_key() -> None:
    event_loop, backend, doc, _window = _make_event_loop("abc\n")
    backend.set_keys(":", "<A-x>")

    await event_loop._input_loop()
    event_loop._render()

    assert event_loop._cmdline.active is False
    assert doc.get_text() == "bc\n"
    assert _bottom_row_text(event_loop).strip() == ""


@pytest.mark.asyncio
async def test_run_shuts_down_owned_render_executor(monkeypatch) -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    render_shutdown: list[bool] = []
    syntax_shutdown: list[bool] = []
    shutdown_events: list[str] = []

    class FakeRenderExecutor:
        def shutdown(self, *, wait: bool = False) -> None:
            render_shutdown.append(wait)

    class FakeSyntaxExecutor:
        def shutdown(self, *, wait: bool = False) -> None:
            syntax_shutdown.append(wait)

    async def fake_input_loop() -> None:
        return None

    async def fake_render_loop() -> None:
        return None

    event_loop._render_executor = FakeRenderExecutor()
    event_loop._executor = FakeSyntaxExecutor()
    event_loop._editor_state.event_bus.on("editor_shutdown", lambda **_kw: shutdown_events.append("shutdown"))
    monkeypatch.setattr(event_loop, "_input_loop", fake_input_loop)
    monkeypatch.setattr(event_loop, "_render_loop", fake_render_loop)

    await event_loop.run()

    assert render_shutdown == [False]
    assert syntax_shutdown == [False]
    assert shutdown_events == ["shutdown"]


@pytest.mark.asyncio
async def test_run_drains_cancelled_shutdown_tasks(monkeypatch) -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    cancelled: list[str] = []

    class FakeRenderExecutor:
        def shutdown(self, *, wait: bool = False) -> None:
            return None

    class FakeSyntaxExecutor:
        def shutdown(self, *, wait: bool = False) -> None:
            return None

    async def fake_input_loop() -> None:
        return None

    async def fake_render_loop() -> None:
        return None

    async def _ticker() -> None:
        while True:
            await asyncio.sleep(10)

    task = asyncio.create_task(_ticker())
    task.add_done_callback(lambda _fut: cancelled.append("done"))

    def _on_shutdown(**_kw) -> None:
        task.cancel()

    event_loop._render_executor = FakeRenderExecutor()
    event_loop._executor = FakeSyntaxExecutor()
    event_loop._editor_state.event_bus.on("editor_shutdown", _on_shutdown)
    monkeypatch.setattr(event_loop, "_input_loop", fake_input_loop)
    monkeypatch.setattr(event_loop, "_render_loop", fake_render_loop)

    await event_loop.run()

    assert task.cancelled()
    assert cancelled == ["done"]


@pytest.mark.asyncio
async def test_run_warns_when_parallelrender_requested_unavailable(monkeypatch) -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    event_loop._editor_state.options.set_global("parallelrender", "on")

    async def fake_input_loop() -> None:
        return None

    async def fake_render_loop() -> None:
        return None

    monkeypatch.setattr(
        "peovim.ui.event_loop.render_runtime_diagnostics",
        lambda policy: RenderRuntimeDiagnostics(
            requested=True,
            runtime_supported=False,
            effective_parallelism=False,
            free_threaded=False,
            gil_disabled_value=0,
            worker_count=8,
            worker_source="cpu",
            reason="Python build is not free-threaded (Py_GIL_DISABLED=0)",
        ),
    )
    monkeypatch.setattr(event_loop, "_input_loop", fake_input_loop)
    monkeypatch.setattr(event_loop, "_render_loop", fake_render_loop)

    await event_loop.run()

    assert (
        event_loop._editor_state.message
        == "parallelrender=on unavailable: Python build is not free-threaded (Py_GIL_DISABLED=0); see :checkhealth"
    )


def test_parallelrender_option_change_sets_warning_message(monkeypatch) -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    event_loop._consume_invalidation_reasons()
    monkeypatch.setattr(
        "peovim.ui.event_loop.render_runtime_diagnostics",
        lambda policy: RenderRuntimeDiagnostics(
            requested=True,
            runtime_supported=False,
            effective_parallelism=False,
            free_threaded=False,
            gil_disabled_value=0,
            worker_count=8,
            worker_source="cpu",
            reason="Python build is not free-threaded (Py_GIL_DISABLED=0)",
        ),
    )

    event_loop._editor_state.options.set_global("parallelrender", "on")

    assert (
        event_loop._editor_state.message
        == "parallelrender=on unavailable: Python build is not free-threaded (Py_GIL_DISABLED=0); see :checkhealth"
    )
    assert event_loop._pending_invalidation_reasons == {"message"}


def test_parallelrender_warning_does_not_repeat_while_state_is_unchanged(monkeypatch) -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    event_loop._consume_invalidation_reasons()
    monkeypatch.setattr(
        "peovim.ui.event_loop.render_runtime_diagnostics",
        lambda policy: RenderRuntimeDiagnostics(
            requested=True,
            runtime_supported=False,
            effective_parallelism=False,
            free_threaded=False,
            gil_disabled_value=0,
            worker_count=8,
            worker_source="cpu",
            reason="Python build is not free-threaded (Py_GIL_DISABLED=0)",
        ),
    )

    event_loop._editor_state.options.set_global("parallelrender", "on")
    event_loop._consume_invalidation_reasons()

    event_loop._editor_state.options.set_global("parallelrenderworkers", 4)

    assert (
        event_loop._editor_state.message
        == "parallelrender=on unavailable: Python build is not free-threaded (Py_GIL_DISABLED=0); see :checkhealth"
    )
    assert event_loop._pending_invalidation_reasons == set()


def test_message_only_invalidation_renders_bottom_row_without_full_body() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    event_loop._grid = CellGrid(40, 8)
    event_loop._consume_invalidation_reasons()
    event_loop._editor_state.message = "hello"
    event_loop._invalidate_message()

    assert event_loop._pending_invalidation_reasons == {"message"}

    event_loop._render(event_loop._consume_invalidation_reasons())

    assert _bottom_row_text(event_loop).startswith("hello")


def test_show_hover_without_float_manager_invalidates_message() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    event_loop._editor_state.message = ""
    event_loop._consume_invalidation_reasons()

    event_loop._lsp_ui.show_hover_float("hover text")

    assert event_loop._editor_state.message == "hover text"
    assert event_loop._pending_invalidation_reasons == {"message"}


def test_show_hover_float_renders_xmlish_docs_and_supports_yank() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    float_manager = FakeFloatManager()
    event_loop._float_manager = float_manager

    event_loop._lsp_ui.show_hover_float("<summary>Returns <c>value</c>.</summary>")

    assert float_manager.handles[0].content == ["Returns value."]
    on_key = float_manager.last_kwargs["on_key"]
    assert on_key("y") is True
    assert event_loop._dispatcher.registers.get('"')[0] == "Returns value."
    assert event_loop._editor_state.message == "Yanked hover text"


def test_prompt_rename_invalidates_message_and_cmdline() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    event_loop._consume_invalidation_reasons()

    event_loop._lsp_ui.prompt_rename(object(), "file.py", 1, 2)

    assert event_loop._cmdline.active is True
    assert event_loop._pending_invalidation_reasons == {"cmdline", "message"}


def test_show_signature_help_opens_cursor_relative_float() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    float_manager = FakeFloatManager()
    event_loop._float_manager = float_manager

    event_loop._lsp_ui.show_signature_help("func(a, b)\nparameter: b")

    assert event_loop._signature_help_handle is not None
    assert float_manager.handles[0].content == ["func(a, b)", "parameter: b"]
    assert event_loop._pending_invalidation_reasons == {"full"}


def test_dismiss_signature_help_closes_existing_float() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    float_manager = FakeFloatManager()
    event_loop._float_manager = float_manager
    event_loop._lsp_ui.show_signature_help("func(a)")
    event_loop._consume_invalidation_reasons()

    event_loop._lsp_ui.dismiss_signature_help()

    assert event_loop._signature_help_handle is None
    assert float_manager.closed == 1
    assert event_loop._pending_invalidation_reasons == {"full"}


@pytest.mark.asyncio
async def test_picker_open_intercepts_key_and_marks_full_invalidation() -> None:
    event_loop, backend, _doc, _window = _make_event_loop()
    picker = FakePicker()
    event_loop._picker = picker
    event_loop._consume_invalidation_reasons()
    backend.set_keys("j")

    await event_loop._input_loop()

    assert picker.keys == ["j"]
    assert event_loop._pending_invalidation_reasons == {"full"}


@pytest.mark.asyncio
async def test_completion_accept_in_insert_mode_dispatches_insert() -> None:
    event_loop, backend, doc, _window = _make_event_loop("abc\n")
    event_loop._completion_popup = FakeCompletionPopup(accepted="XYZ")
    event_loop._engine.set_mode(event_loop._engine.mode.INSERT)
    event_loop._consume_invalidation_reasons()
    backend.set_keys("<Tab>")

    await event_loop._input_loop()

    assert doc.get_line(0) == "XYZabc"
    assert event_loop._pending_invalidation_reasons == {"full"}


@pytest.mark.asyncio
async def test_completion_ctrl_y_accepts_in_insert_mode() -> None:
    event_loop, backend, doc, _window = _make_event_loop("abc\n")
    event_loop._completion_popup = FakeCompletionPopup(accepted="XYZ")
    event_loop._engine.set_mode(event_loop._engine.mode.INSERT)
    event_loop._consume_invalidation_reasons()
    backend.set_keys("<C-y>")

    await event_loop._input_loop()

    assert doc.get_line(0) == "XYZabc"
    assert event_loop._pending_invalidation_reasons == {"full"}


def test_apply_workspace_edit_updates_open_document_and_invalidates_full(tmp_path) -> None:
    from peovim.lsp.protocol import path_to_uri

    event_loop, _backend, doc, _window = _make_event_loop("hello world\n")
    doc.path = tmp_path / "sample.py"
    event_loop._consume_invalidation_reasons()

    event_loop._lsp_ui.apply_workspace_edit(
        {
            "changes": {
                path_to_uri(str(doc.path)): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 6},
                            "end": {"line": 0, "character": 11},
                        },
                        "newText": "editor",
                    }
                ]
            }
        }
    )

    assert doc.get_line(0) == "hello editor"
    assert event_loop._pending_invalidation_reasons == {"full"}


def test_apply_workspace_edit_writes_disk_for_closed_document(tmp_path) -> None:
    from peovim.lsp.protocol import path_to_uri

    event_loop, _backend, _doc, _window = _make_event_loop()
    target = tmp_path / "other.py"
    target.write_text("value = 1\n", encoding="utf-8")

    event_loop._lsp_ui.apply_workspace_edit(
        {
            "changes": {
                path_to_uri(str(target)): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 8},
                            "end": {"line": 0, "character": 9},
                        },
                        "newText": "2",
                    }
                ]
            }
        }
    )

    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_apply_workspace_edit_updates_hidden_loaded_document_and_reopen_uses_fresh_text(tmp_path) -> None:
    from peovim.lsp.protocol import path_to_uri

    event_loop, _backend, _doc, window = _make_event_loop()
    target = tmp_path / "other.py"
    target.write_text("value = 1\n", encoding="utf-8")

    hidden_doc = Document(path=target)
    hidden_doc.load(target)
    event_loop._workspace.add_document(hidden_doc)

    event_loop._lsp_ui.apply_workspace_edit(
        {
            "changes": {
                path_to_uri(str(target)): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 8},
                            "end": {"line": 0, "character": 9},
                        },
                        "newText": "2",
                    }
                ]
            }
        }
    )

    assert hidden_doc.get_line(0) == "value = 2"

    event_loop._dispatcher.dispatch([OpenBuffer(str(target))])

    assert window.document is hidden_doc
    assert window.document.get_line(0) == "value = 2"


@pytest.mark.asyncio
async def test_transient_message_clears_and_key_still_routes_normally() -> None:
    event_loop, backend, doc, _window = _make_event_loop("abc\n")
    event_loop._editor_state.message = "temporary"
    event_loop._consume_invalidation_reasons()
    backend.set_keys("x")

    await event_loop._input_loop()

    assert event_loop._editor_state.message == ""
    assert doc.get_text() == "bc\n"
    assert event_loop._pending_invalidation_reasons == {"full", "message"}


def test_render_uses_terminal_bar_cursor_in_insert_mode_when_option_enabled() -> None:
    from peovim.ui.backend import SetCursorStyle, ShowCursor

    event_loop, backend, _doc, window = _make_event_loop("abc\n")
    window.options["insertcursor"] = "bar"
    event_loop._engine.set_mode(event_loop._engine.mode.INSERT)

    event_loop._render({"full"})

    assert any(
        isinstance(op, SetCursorStyle) and op.shape == "bar" and op.blink is False for op in backend.render_ops()
    )
    assert any(isinstance(op, ShowCursor) for op in backend.render_ops())
    assert backend.cursor_pos() == (0, 0)
    assert event_loop._grid is not None
    assert event_loop._grid._current[0][0][0] == "a"


def test_render_uses_terminal_cursor_when_enabled_via_global_options() -> None:
    from peovim.ui.backend import SetCursorStyle, ShowCursor

    event_loop, backend, _doc, _window = _make_event_loop("abc\n")
    event_loop._editor_state.options.set_global("cursorblink", True)
    event_loop._editor_state.options.set_global("insertcursor", "bar")
    event_loop._engine.set_mode(event_loop._engine.mode.INSERT)

    event_loop._render({"full"})

    assert any(isinstance(op, SetCursorStyle) and op.shape == "bar" and op.blink is True for op in backend.render_ops())
    assert any(isinstance(op, ShowCursor) for op in backend.render_ops())
    assert backend.cursor_pos() == (0, 0)


def test_render_positions_terminal_cursor_after_global_number_gutter() -> None:
    event_loop, backend, _doc, _window = _make_event_loop("abc\n")
    event_loop._editor_state.options.set_global("cursorblink", True)
    event_loop._editor_state.options.set_global("number", True)

    event_loop._render({"full"})

    assert backend.cursor_pos() == (0, 4)


def test_render_uses_blinking_block_cursor_when_option_enabled() -> None:
    from peovim.ui.backend import SetCursorStyle

    event_loop, backend, _doc, window = _make_event_loop("abc\n")
    window.options["cursorblink"] = True

    event_loop._render({"full"})

    assert any(
        isinstance(op, SetCursorStyle) and op.shape == "block" and op.blink is True for op in backend.render_ops()
    )


def test_render_repositions_terminal_cursor_when_mode_changes_without_cursor_move() -> None:
    from peovim.ui.backend import MoveCursor, PutCell, SetCursorStyle

    event_loop, backend, _doc, window = _make_event_loop("abc\n")
    window.options["cursorblink"] = True
    window.options["insertcursor"] = "bar"

    event_loop._render({"full"})
    backend.clear_ops()

    event_loop._engine.set_mode(event_loop._engine.mode.INSERT)
    event_loop._render({"full"})

    tail = backend.last_ops(4)

    assert isinstance(tail[0], SetCursorStyle)
    assert tail[0].shape == "bar"
    assert tail[0].blink is True
    assert isinstance(tail[1], MoveCursor)
    assert isinstance(tail[2], PutCell)
    assert isinstance(tail[3], MoveCursor)
    assert (tail[1].row, tail[1].col) == (0, 0)
    assert (tail[3].row, tail[3].col) == (0, 0)
    assert tail[2].char == "a"


@pytest.mark.asyncio
async def test_focused_float_intercepts_before_tree_view() -> None:
    event_loop, backend, _doc, _window = _make_event_loop()
    float_manager = FakeFloatManager()
    tree = FakeTree()
    event_loop._float_manager = float_manager
    event_loop._tree_views = [FakeTreeHandle(tree)]
    event_loop._consume_invalidation_reasons()
    backend.set_keys("j")

    await event_loop._input_loop()

    assert float_manager.keys == ["j"]
    assert tree.keys == []
    assert event_loop._pending_invalidation_reasons == {"full"}


def test_key_echo_timeout_clears_message_and_invalidates_message() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    event_loop._key_echo_active = True
    event_loop._key_echo_keys = ["a", "b"]
    event_loop._key_echo_idle_since = 0.0
    event_loop._key_echo_idle_secs = 1.0
    event_loop._editor_state.message = "KEY ECHO"
    event_loop._consume_invalidation_reasons()

    event_loop._update_key_echo_timeout(2.0)

    assert event_loop._key_echo_active is False
    assert event_loop._key_echo_keys == []
    assert event_loop._editor_state.message == ""
    assert event_loop._pending_invalidation_reasons == {"message"}


def test_yank_flash_expiry_clears_namespace_and_invalidates_full() -> None:
    event_loop, _backend, doc, _window = _make_event_loop()
    buf_id = id(doc)
    event_loop._editor_state.decorations.add(
        buf_id,
        "yank:flash",
        HighlightRegion(0, 0, 0, 1, style=None),
    )
    event_loop._yank_flash_until = 1.0
    event_loop._consume_invalidation_reasons()

    event_loop._expire_yank_flash(2.0)

    assert event_loop._yank_flash_until == 0.0
    assert event_loop._editor_state.decorations.get_for_namespace(buf_id, "yank:flash") == []
    assert event_loop._pending_invalidation_reasons == {"full"}


def test_render_body_stacks_tree_before_widgets_and_completion_last() -> None:
    event_loop, _backend, _doc, _window = _make_event_loop()
    render_order: list[str] = []
    tree = FakeTree()
    tree.render_order = render_order
    float_manager = FakeFloatManager()
    float_manager.render_order = render_order
    completion = FakeCompletionPopup()
    picker = FakeRenderPicker(render_order)
    notify = FakeNotifyManager(render_order)
    original_render = completion.render

    def record_completion(grid: CellGrid, row: int, col: int) -> None:
        render_order.append("completion")
        original_render(grid, row, col)

    completion.render = record_completion
    event_loop._tree_views = [FakeTreeHandle(tree)]
    event_loop._float_manager = float_manager
    event_loop._notify_manager = notify
    event_loop._picker = picker
    event_loop._completion_popup = completion

    event_loop._render({"full"})

    assert render_order == ["tree", "float", "notify", "picker", "completion"]
    assert completion.render_calls == [(0, 0)]


def test_completion_popup_anchor_uses_tab_expanded_display_columns() -> None:
    event_loop, _backend, _doc, window = _make_event_loop("\tabc\n")
    completion = FakeCompletionPopup()
    event_loop._completion_popup = completion
    window.cursor.move_to(0, 1)
    event_loop._render({"full"})
    assert completion.render_calls == [(0, 4)]


def test_terminal_cursor_state_uses_tab_expanded_display_columns() -> None:
    event_loop, _backend, _doc, window = _make_event_loop("\tabc\n")
    event_loop._grid = CellGrid(40, 8)
    event_loop._current_layout = {event_loop._workspace.active_tab.root: Rect(0, 0, 40, 6)}
    window.cursor.move_to(0, 1)
    window.options["cursorblink"] = True

    state = event_loop._resolve_terminal_cursor_state()

    assert state is not None
    assert state[:2] == (0, 4)


def test_collect_window_render_jobs_materializes_spans_and_editor_decorations() -> None:
    event_loop, _backend, doc, window = _make_event_loop("hello\n")
    layout = {event_loop._workspace.active_tab.root: Rect(0, 0, 40, 6)}
    highlight = HighlightSpan(0, 0, 0, 5, "keyword")
    extra = HighlightRegion(0, 0, 0, 1, style=None)
    event_loop._syntax_cache[id(doc)] = (doc.version, [highlight])
    event_loop._editor_state.decorations.add(id(doc), "test:ns", extra)
    event_loop._syntax_engine.submit = lambda *args, **kwargs: None

    jobs = event_loop._collect_window_render_jobs(
        event_loop._workspace.active_tab,
        layout,
        event_loop._resolve_frame_theme(),
        None,
    )

    assert len(jobs) == 1
    job = jobs[0]
    assert job.snapshot.buffer_snapshot.version == doc.version
    assert job.highlight_spans == (highlight,)
    assert extra in job.decorations
    assert job.rect == Rect(0, 0, 40, 6)
    assert window.width == 40
    assert window.height == 6


def test_collect_window_render_jobs_filters_cached_syntax_spans_to_visible_range() -> None:
    event_loop, _backend, doc, window = _make_event_loop("line\n" * 12)
    layout = {event_loop._workspace.active_tab.root: Rect(0, 0, 40, 4)}
    offscreen = HighlightSpan(0, 0, 0, 4, "keyword")
    visible = HighlightSpan(10, 0, 10, 4, "string")
    window.cursor.move_to(10, 0)
    window.scroll_line = 9
    event_loop._syntax_submitted[id(doc)] = doc.version
    event_loop._on_syntax_done(id(doc), [offscreen, visible])

    jobs = event_loop._collect_window_render_jobs(
        event_loop._workspace.active_tab,
        layout,
        event_loop._resolve_frame_theme(),
        None,
    )

    assert len(jobs) == 1
    assert jobs[0].highlight_spans == (visible,)


def test_collect_window_render_jobs_snapshots_global_options_once_per_frame(monkeypatch) -> None:
    event_loop, _backend, _doc, _window = _make_event_loop("line\n" * 12)
    second_window = event_loop._workspace.active_tab.split_vertical()
    second_window.document = event_loop._workspace.active_tab.active_window.document
    layout = {
        event_loop._workspace.active_tab.all_leaves()[0]: Rect(0, 0, 20, 4),
        event_loop._workspace.active_tab.all_leaves()[1]: Rect(20, 0, 20, 4),
    }
    calls: list[int] = []
    original = event_loop._editor_state.options.global_as_dict

    def _counting_global_as_dict() -> dict:
        calls.append(1)
        return original()

    monkeypatch.setattr(event_loop._editor_state.options, "global_as_dict", _counting_global_as_dict)

    jobs = event_loop._collect_window_render_jobs(
        event_loop._workspace.active_tab,
        layout,
        event_loop._resolve_frame_theme(),
        None,
    )

    assert len(jobs) == 2
    assert len(calls) == 1


def test_render_window_content_uses_parallel_capability_gate_contract(monkeypatch) -> None:
    event_loop, _backend, _doc, _window = _make_event_loop("hello\n")
    layout = {event_loop._workspace.active_tab.root: Rect(0, 0, 40, 6)}
    calls: list[tuple[bool, object | None]] = []
    event_loop._editor_state.options.set_global("parallelrender", "on")
    event_loop._editor_state.options.set_global("parallelrenderworkers", 3)
    monkeypatch.setattr(
        "peovim.ui.window_render_controller.resolve_render_strategy",
        lambda *, allow_parallel=False, policy=None: "parallel",
    )

    def fake_render_jobs(jobs, *, allow_parallel=False, policy=None):
        calls.append((allow_parallel, policy))
        return []

    event_loop._render_executor.render_jobs = fake_render_jobs

    event_loop._render_window_content(
        CellGrid(40, 8),
        event_loop._workspace.active_tab,
        layout,
        event_loop._resolve_frame_theme(),
    )

    assert len(calls) == 1
    assert calls[0][0] is True
    assert calls[0][1].parallel_enabled is True
    assert calls[0][1].max_workers == 3


def test_render_window_content_uses_direct_sequential_merge_when_parallel_unavailable(monkeypatch) -> None:
    event_loop, _backend, _doc, _window = _make_event_loop("hello\n")
    layout = {event_loop._workspace.active_tab.root: Rect(0, 0, 40, 6)}

    monkeypatch.setattr(
        "peovim.ui.window_render_controller.resolve_render_strategy",
        lambda *, allow_parallel=False, policy=None: "sequential",
    )
    executor_called = []
    monkeypatch.setattr(
        event_loop._render_executor,
        "render_jobs",
        lambda *args, **kwargs: executor_called.append(True) or [],
    )

    grid = CellGrid(40, 8)
    event_loop._render_window_content(
        grid,
        event_loop._workspace.active_tab,
        layout,
        event_loop._resolve_frame_theme(),
    )

    assert not executor_called, "executor should not run for sequential path"
