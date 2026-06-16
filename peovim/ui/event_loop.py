"""
ui.event_loop — EventLoop: asyncio main loop

Wires input → modal engine → action dispatch → render at up to 120fps.
Manages the ThreadPoolExecutor for syntax and window render workers.
Posts background results to main thread via call_soon_threadsafe().

See notes/architecture.md §Concurrency Architecture for the full design.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import peovim.ui.runtime_controller as _runtime_controller
from peovim.syntax.engine import HighlightSpan, SyntaxEngine
from peovim.ui.backend import KeyEvent
from peovim.ui.cell_grid import CellGrid
from peovim.ui.cmdline_controller import CommandLineController
from peovim.ui.command_line import CommandLine
from peovim.ui.cursor_controller import TerminalCursorController
from peovim.ui.frame_controller import FrameController
from peovim.ui.input_controller import InputController
from peovim.ui.layout import Rect
from peovim.ui.lsp_ui_adapter import LspUiAdapter
from peovim.ui.mouse_dispatcher import MouseDispatcher
from peovim.ui.presentation_controller import OverlayPresentationController
from peovim.ui.render_cycle_controller import RenderCycleController
from peovim.ui.render_jobs import (
    RenderJobExecutor,
)
from peovim.ui.runtime_controller import EventLoopRuntimeController
from peovim.ui.window_render_controller import WindowRenderController

log = logging.getLogger(__name__)
render_runtime_diagnostics = _runtime_controller.render_runtime_diagnostics


def _index_syntax_spans_by_line(spans: tuple[HighlightSpan, ...]) -> dict[int, tuple[HighlightSpan, ...]]:
    """Build a line → spans index once per syntax result for faster viewport filtering."""
    if not spans:
        return {}

    spans_by_line: dict[int, list[HighlightSpan]] = {}
    for span in spans:
        for line in range(span.start_line, span.end_line + 1):
            spans_by_line.setdefault(line, []).append(span)
    return {line: tuple(line_spans) for line, line_spans in spans_by_line.items()}


if TYPE_CHECKING:
    from peovim.core.editor_state import EditorState
    from peovim.core.snapshot import WindowSnapshot
    from peovim.core.workspace import Workspace
    from peovim.modal.dispatcher import ActionDispatcher
    from peovim.modal.engine import ModalEngine
    from peovim.ui.backend import TerminalBackend


class EventLoop:  # cm:e4d6b5
    """
    Main asyncio event loop for the editor.

    run() is the entry point. It reads input, feeds the engine,
    dispatches actions, and renders at up to _fps frames per second.
    """

    def __init__(
        self,
        backend: TerminalBackend,
        engine: ModalEngine,
        dispatcher: ActionDispatcher,
        workspace: Workspace,
        command_line: CommandLine | None = None,
        editor_state: EditorState | None = None,
        float_manager: object | None = None,
        notify_manager: object | None = None,
        picker: object | None = None,
        which_key_panel: object | None = None,
        lsp_queue: object | None = None,
        completion_popup: object | None = None,
        recovery_store: object | None = None,
        options: object | None = None,
    ) -> None:
        self._backend = backend
        self._engine = engine
        self._dispatcher = dispatcher
        self._workspace = workspace
        self._cmdline = command_line or CommandLine()
        self._editor_state: EditorState | None = editor_state
        self._float_manager = float_manager
        self._notify_manager = notify_manager
        self._picker = picker
        self._which_key_panel = which_key_panel
        self._lsp_queue = lsp_queue  # deque of callables (es, ws) → None
        self._completion_popup = completion_popup
        self._recovery_store = recovery_store
        self._options = options
        # Mirror recovery_store onto editor_state so commands can reach it
        if recovery_store is not None and editor_state is not None:
            editor_state.recovery_store = recovery_store
        self._signature_help_handle: object | None = None
        # Track last key_buffer length to detect prefix changes
        self._last_key_buf_len: int = 0
        self._grid: CellGrid | None = None
        self._ansi_buf: bytearray = bytearray()  # reused across frames for flush_ansi
        self._dirty: bool = True
        self._pending_invalidation_reasons: set[str] = {"full"}
        self._runtime_error_last_seen: dict[str, float] = {}
        self._cmdline_just_dismissed: bool = False  # eat spurious Alt from double-ESC
        self._yank_flash_until: float = 0.0  # monotonic time when flash expires
        self._running: bool = False
        self._fps: int = 60
        self._frame_event: asyncio.Event | None = None
        self._current_layout: dict = {}
        self._mouse_dispatcher = MouseDispatcher(
            workspace,
            engine,
            dispatcher,
            get_layout_fn=lambda: self._current_layout,
            get_sidebar_rect_fn=lambda: self._current_sidebar_rect,
            get_sidebar_fn=lambda: self._sidebar,
            get_bottom_panel_rect_fn=lambda: self._current_bottom_panel_rect,
            get_bottom_panel_fn=lambda: self._bottom_panel,
        )
        # Tree views list (shared reference to UIAPI._tree_views when available)
        self._tree_views: list = []
        self._sidebar: object | None = None
        self._bottom_panel: object | None = None
        self._binding_registry: object | None = None  # wired in main.py after EditorAPI init
        self._current_sidebar_rect: Rect | None = None
        self._current_bottom_panel_rect: Rect | None = None
        self._flash: object | None = None
        self._cmdline_controller = CommandLineController(self)
        self._cursor_controller = TerminalCursorController(self)
        self._frame_controller = FrameController(self)
        self._input_controller = InputController(self)
        self._presentation = OverlayPresentationController(self)
        self._render_cycle = RenderCycleController(self)
        self._lsp_ui = LspUiAdapter(self)
        self._runtime = EventLoopRuntimeController(self)
        self._window_render_controller = WindowRenderController(self)
        # Syntax highlighting
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="peovim-syntax")
        self._render_executor = RenderJobExecutor()
        self._syntax_engine = SyntaxEngine(self._executor)
        # buffer_id → (version, spans, spans_by_line)
        self._syntax_cache: dict[int, tuple[int, tuple[HighlightSpan, ...], dict[int, tuple[HighlightSpan, ...]]]] = {}
        # buffer_id → last submitted version (avoid duplicate submits)
        self._syntax_submitted: dict[int, int] = {}
        self._render_warning_active: bool = False
        self._terminal_cursor_visible: bool = False
        self._terminal_cursor_shape: str = "block"
        self._terminal_cursor_blink: bool = False
        self._terminal_cursor_pos: tuple[int, int] = (-1, -1)
        # Key echo mode
        self._key_echo_active: bool = False
        self._key_echo_keys: list[str] = []
        self._key_echo_idle_since: float = 0.0
        self._key_echo_idle_secs: float = 3.0
        # Key log (non-intercepting, appends to file)
        self._key_log_path: str | None = None
        # Perf sampler: set by perf_panel plugin when loaded; None = no overhead
        self._perf_sampler: Any = None
        # Subscribe to buffer_opened to invalidate syntax cache on buffer swap.
        # PieceTable.load() resets version to 0, so old cache entries at v0
        # would suppress re-parsing of new content with the same version.
        if editor_state is not None:
            editor_state.event_bus.on(
                "buffer_opened",
                lambda buf_id=0, **_kw: (
                    self._syntax_cache.pop(buf_id, None),
                    self._syntax_submitted.pop(buf_id, None),
                    self._syntax_engine.remove_buffer(buf_id),
                ),
            )
            editor_state.event_bus.on("yank_done", self._on_yank_done)
            editor_state.event_bus.on("option_changed", self._on_option_changed)
            editor_state.event_bus.on("cursor_moved", self._on_cursor_moved_completion)
            editor_state.event_bus.on("buffer_text_changed", self._on_text_changed_completion)
            editor_state.event_bus.on("buffer_changed", self._on_text_changed_completion)

    # ------------------------------------------------------------------
    # Post-construction wiring
    # ------------------------------------------------------------------

    def attach_flash(self, plugin: Any) -> None:
        """Wire the flash plugin after it is loaded."""
        self._flash = plugin

    def attach_ui(self, ui: Any, binding_registry: Any) -> None:
        """Wire UI state from UIAPI and the shared BindingRegistry."""
        self._tree_views = ui._tree_views
        self._sidebar = ui._sidebar
        self._bottom_panel = ui._bottom_panel
        self._binding_registry = binding_registry
        ui._sidebar._binding_registry = binding_registry
        ui._bottom_panel._binding_registry = binding_registry

    @property
    def lsp_ui(self) -> Any:
        """LspUiAdapter for LSP float/picker/hover UI."""
        return self._lsp_ui

    def set_command_history(self, hist: list[str]) -> None:
        """Restore persisted command history into the command-line widget."""
        self._cmdline._history = list(hist)

    def _start_key_echo(self) -> None:
        self._input_controller.start_key_echo()

    def _toggle_key_log(self) -> None:
        self._input_controller.toggle_key_log()

    def _invalidate(self, reason: str = "full") -> None:
        self._render_cycle.invalidate(reason)
        if self._frame_event is not None:
            self._frame_event.set()

    def _invalidate_cmdline(self) -> None:
        self._render_cycle.invalidate_cmdline()

    def _invalidate_message(self) -> None:
        self._render_cycle.invalidate_message()

    def _consume_invalidation_reasons(self) -> set[str]:
        return self._render_cycle.consume_invalidation_reasons()

    async def run(self) -> None:  # cm:6f9a2d
        """Start the event loop. Blocks until quit."""
        self._running = True
        loop = asyncio.get_running_loop()
        previous_exception_handler = loop.get_exception_handler()
        loop.set_exception_handler(self._handle_asyncio_exception)
        cols, rows = self._backend.get_size()
        self._grid = CellGrid(cols, rows)
        self._backend.enter_raw_mode()
        # Register :KeyEcho command now that dispatcher is wired
        try:
            reg = self._dispatcher._command_registry
            if reg is not None:
                reg.register("KeyEcho", lambda _cmd, _ctx: self._start_key_echo(), min_abbrev=2)
                reg.register("KeyLog", lambda _cmd, _ctx: self._toggle_key_log(), min_abbrev=2)
                reg.register("checktime", lambda _cmd, _ctx: self._runtime.force_check_external_changes(), min_abbrev=3)
        except Exception:
            pass
        try:
            if self._editor_state is not None:
                self._maybe_warn_parallel_render_unavailable()
                self._editor_state.event_bus.emit("editor_ready")
            self._notify_recovery_orphans()
            await asyncio.gather(
                self._input_loop(),
                self._render_loop(),
            )
        finally:
            loop.set_exception_handler(previous_exception_handler)
            if self._editor_state is not None:
                self._editor_state.event_bus.emit("editor_shutdown")
            self._cleanup_recovery_on_exit()
            # Give shutdown handlers one loop turn so cancelled background tasks
            # (for example from editor.set_interval()) can finish cleanly before
            # the outer asyncio runner closes the loop.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self._backend.exit_raw_mode()
            self._render_executor.shutdown(wait=False)
            self._executor.shutdown(wait=False)

    async def _input_loop(self) -> None:
        await self._input_controller.input_loop()

    def _handle_asyncio_exception(self, loop: asyncio.AbstractEventLoop, context: dict) -> None:
        self._runtime.handle_asyncio_exception(loop, context)

    def _report_runtime_error(self, where: str, exc: BaseException, *, detail: str | None = None) -> None:
        self._runtime.report_runtime_error(where, exc, detail=detail)

    def _handle_key_event(self, event: KeyEvent) -> bool:
        if event.key == "<BracketedPaste>":
            return self._input_controller.handle_paste_event(event.text)
        return self._input_controller.handle_key_event(event)

    def _write_key_log(self, key: str) -> None:
        self._input_controller.write_key_log(key)

    def _resolve_normal_key(self, key: str, normal_key_after: str | None) -> str | None:
        return self._cmdline_controller.resolve_normal_key(key, normal_key_after)

    async def _render_loop(self) -> None:
        import time

        import peovim.ui.perf_sampler as _perf_sampler_mod

        self._frame_event = asyncio.Event()
        interval = 1.0 / self._fps
        _gc_prev = _perf_sampler_mod._gc_collections
        while self._running:
            t0 = time.perf_counter()
            lsp_q = len(self._lsp_queue) if self._lsp_queue is not None else 0  # type: ignore[arg-type]
            now = time.monotonic()
            self._run_render_maintenance(now)
            t1 = time.perf_counter()
            was_dirty = self._dirty
            self._render_if_dirty()
            t2 = time.perf_counter()
            # Sleep only the remaining frame budget; wake early if invalidated
            sleep_time = max(0.0, interval - (t2 - t0))
            self._frame_event.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._frame_event.wait(), timeout=sleep_time)
            t3 = time.perf_counter()
            if self._perf_sampler is not None:
                _gc_now = _perf_sampler_mod._gc_collections
                self._perf_sampler.push(
                    maintenance=t1 - t0,
                    render=t2 - t1,
                    idle=t3 - t2,
                    total=t3 - t0,
                    rendered=was_dirty,
                    lsp_queue=lsp_q,
                    gc_delta=_gc_now - _gc_prev,
                )
                _gc_prev = _gc_now

    def _handle_key_echo_key(self, key: str) -> bool:
        return self._input_controller.handle_key_echo_key(key)

    def _handle_overlay_key(self, key: str) -> bool:
        return self._presentation.handle_overlay_key(key)

    def _handle_sidebar_navigation_key(self, key: str) -> bool:
        return self._presentation.handle_sidebar_navigation_key(key)

    def _normalize_key_after_cmdline_dismiss(self, event: KeyEvent) -> KeyEvent:
        return self._cmdline_controller.normalize_key_after_cmdline_dismiss(event)

    def _process_active_cmdline_key(
        self,
        key: str,
        run_ex_command_type: type,
        set_search_pattern_type: type,
    ) -> tuple[bool, str | None]:
        return self._cmdline_controller.process_active_cmdline_key(
            key,
            run_ex_command_type=run_ex_command_type,
            set_search_pattern_type=set_search_pattern_type,
        )

    def _handle_cmdline_result(
        self,
        result: str,
        run_ex_command_type: type,
        set_search_pattern_type: type,
    ) -> None:
        self._cmdline_controller.handle_cmdline_result(
            result,
            run_ex_command_type=run_ex_command_type,
            set_search_pattern_type=set_search_pattern_type,
        )

    def _clear_transient_message_on_keypress(self) -> None:
        self._cmdline_controller.clear_transient_message_on_keypress()

    def _dispatch_normal_key(
        self, normal_key: str, enter_command_mode_type: type, quit_action_type: type, normal_mode
    ) -> bool:
        return self._input_controller.dispatch_normal_key(
            normal_key,
            enter_command_mode_type=enter_command_mode_type,
            quit_action_type=quit_action_type,
            normal_mode=normal_mode,
        )

    def _render_cmdline_immediately(self) -> None:
        """Repaint the cmdline area immediately after cmdline dismissal."""
        self._cmdline_controller.render_cmdline_immediately()

    def _render_picker_immediately(self) -> None:
        """Repaint the picker area immediately after picker dismissal."""
        self._cmdline_controller.render_picker_immediately()

    def _list_available_commands(self) -> list[str]:
        return self._cmdline_controller.list_available_commands()

    def _should_use_terminal_cursor(self, options: dict | None = None) -> bool:
        return self._cursor_controller.should_use_terminal_cursor(options)

    def _resolve_active_window_cursor_options(self) -> dict:
        return self._cursor_controller.resolve_active_window_cursor_options()

    def _build_terminal_cursor_ops(self) -> list[object]:
        return self._cursor_controller.build_terminal_cursor_ops()

    def _resolve_terminal_cursor_state(self) -> tuple[int, int, str, bool] | None:
        return self._cursor_controller.resolve_terminal_cursor_state()

    def _sync_active_window_to_engine(self) -> None:
        self._input_controller.sync_active_window_to_engine()

    def _on_yank_done(
        self,
        buf_id: int = 0,
        start_line: int = 0,
        start_col: int = 0,
        end_line: int = 0,
        end_col: int = 0,
        yank_type: str = "char",
        **_kw,
    ) -> None:
        """Flash yanked text with a brief highlight."""
        import time

        from peovim.core.style import Style
        from peovim.ui.decorations import HighlightRegion

        if self._editor_state is None:
            return
        ns = "yank:flash"
        flash_style = Style(bg=(80, 64, 32))
        self._editor_state.decorations.clear_namespace(buf_id, ns)
        for ln in range(start_line, end_line + 1):
            sc = start_col if ln == start_line else 0
            ec = end_col if ln == end_line else 0x7FFFFFFF
            self._editor_state.decorations.add(buf_id, ns, HighlightRegion(ln, sc, ln, ec, flash_style, priority=50))
        self._yank_flash_until = time.monotonic() + 0.2
        self._invalidate("full")

    def _run_render_maintenance(self, now: float) -> None:
        self._runtime.run_render_maintenance(now)

    def _update_key_echo_timeout(self, now: float) -> None:
        self._runtime.update_key_echo_timeout(now)

    def _expire_yank_flash(self, now: float) -> None:
        self._runtime.expire_yank_flash(now)

    def _render_if_dirty(self) -> None:
        self._runtime.render_if_dirty()

    def _drain_lsp_queue(self) -> None:
        """Apply pending LSP result callbacks on the main thread."""
        self._runtime.drain_lsp_queue()

    def _render(self, reasons: set[str] | None = None) -> None:
        self._render_cycle.render(reasons)

    def _render_body(self, grid: CellGrid, cols: int, rows: int, *, clear_grid: bool = True) -> None:
        self._frame_controller.render_body(grid, cols, rows, clear_grid=clear_grid)

    def _compute_frame_layout(
        self, tab: object, cols: int, rows: int
    ) -> tuple[dict, list[Rect], int, Rect | None, Rect | None]:
        return self._frame_controller.compute_frame_layout(tab, cols, rows)  # type: ignore[return-value]

    def _resolve_frame_theme(self):
        return self._frame_controller.resolve_frame_theme()

    def _render_window_content(self, grid: CellGrid, tab: object, layout: dict, theme: object) -> None:
        self._window_render_controller.render_window_content(grid, tab, layout, theme)

    def _resolve_render_execution_policy(self):
        return self._window_render_controller.resolve_render_execution_policy()

    def _maybe_warn_parallel_render_unavailable(self) -> None:
        self._runtime.maybe_warn_parallel_render_unavailable()

    def _on_option_changed(self, name: str = "", scope: str = "global", **_kwargs: object) -> None:
        self._runtime.on_option_changed(name=name, scope=scope, **_kwargs)

    def _on_cursor_moved_completion(self, line: int = 0, col: int = 0, **_kw: object) -> None:
        """Keep the completion popup in sync as the user types or deletes characters."""
        popup = self._completion_popup
        if popup is None or not popup.is_open:
            return
        if line != popup._anchor_line or col < popup._anchor_col:
            popup.close()
            self._invalidate("full")
            return
        try:
            line_text = self._workspace.active_window.document.get_line(line) or ""
            prefix = line_text[popup._anchor_col : col]
            popup.update_filter(prefix)
            self._invalidate("full")
        except Exception:
            popup.close()
            self._invalidate("full")

    def _on_text_changed_completion(self, **_kw: object) -> None:
        """Update completion filter when text is inserted or changed in insert mode."""
        popup = self._completion_popup
        if popup is None or not popup.is_open:
            return
        try:
            win = self._workspace.active_window
            line, col = win.cursor.line, win.cursor.col
            if line != popup._anchor_line or col < popup._anchor_col:
                popup.close()
                self._invalidate("full")
                return
            line_text = win.document.get_line(line) or ""
            prefix = line_text[popup._anchor_col : col]
            popup.update_filter(prefix)
            self._invalidate("full")
        except Exception:
            popup.close()
            self._invalidate("full")

    def _collect_window_render_jobs(
        self,
        tab: object,
        layout: dict,
        theme: object,
        loop: asyncio.AbstractEventLoop | None,
    ):
        return self._window_render_controller.collect_window_render_jobs(tab, layout, theme, loop)

    def _build_window_render_job(
        self,
        tab: object,
        leaf: object,
        rect: Rect,
        theme: object,
        loop: asyncio.AbstractEventLoop | None,
    ):
        return self._window_render_controller.build_window_render_job(tab, leaf, rect, theme, loop)

    def _sync_window_render_state(self, window: object, rect: Rect) -> None:
        self._window_render_controller.sync_window_render_state(window, rect)

    def _snapshot_window_for_render(self, window: object) -> WindowSnapshot:
        return self._window_render_controller.snapshot_window_for_render(window)

    def _submit_window_syntax(
        self,
        snapshot: WindowSnapshot,
        window: object,
        loop: asyncio.AbstractEventLoop | None,
    ) -> None:
        self._window_render_controller.submit_window_syntax(snapshot, window, loop)

    def _resolve_window_highlight_spans(
        self,
        document: object,
        visible_start: int,
        visible_end: int,
    ) -> tuple[HighlightSpan, ...]:
        return self._window_render_controller.resolve_window_highlight_spans(document, visible_start, visible_end)

    def _build_window_render_decorations(
        self,
        window: object,
        snapshot: WindowSnapshot,
        is_active: bool,
    ) -> tuple[object, ...]:
        return self._window_render_controller.build_window_render_decorations(window, snapshot, is_active)

    def _get_window_extra_decorations(self, window: object) -> list[object]:
        return self._window_render_controller.get_window_extra_decorations(window)

    def _get_syntax_callback_loop(self) -> asyncio.AbstractEventLoop | None:
        return self._window_render_controller.get_syntax_callback_loop()

    def _render_separators(self, grid: CellGrid, separators: list[Rect]) -> None:
        self._frame_controller.render_separators(grid, separators)

    def _render_which_key_panel(self, grid: CellGrid, cols: int, rows: int) -> None:
        self._presentation.render_which_key_panel(grid, cols, rows)

    def _render_bottom_panel(self, grid: CellGrid, bottom_panel_rect: Rect | None, theme: object | None = None) -> None:
        self._presentation.render_bottom_panel(grid, bottom_panel_rect, theme)

    def _render_sidebar(self, grid: CellGrid, sidebar_rect: Rect | None, theme: object | None = None) -> None:
        self._presentation.render_sidebar(grid, sidebar_rect, theme)

    def _render_tree_views(self, grid: CellGrid, win_rows: int) -> None:
        self._presentation.render_tree_views(grid, win_rows)

    def _render_overlay_widgets(self, grid: CellGrid, tab: object, layout: dict) -> None:
        self._presentation.render_overlay_widgets(grid, tab, layout)

    def _render_completion_popup(self, grid: CellGrid, tab: object, layout: dict) -> None:
        self._presentation.render_completion_popup(grid, tab, layout)

    def _on_syntax_done(self, buffer_id: int, spans: list[HighlightSpan]) -> None:
        """Called on main thread when a background parse completes."""
        self._render_cycle.on_syntax_done(buffer_id, spans)

    def _update_key_echo_display(self) -> None:
        """Update the message line with the last N key names for key echo mode."""
        self._input_controller.update_key_echo_display()

    def _check_key_prefix_events(self) -> None:
        """Emit key_prefix_pending / key_prefix_done after each normal-mode key."""
        self._input_controller.check_key_prefix_events()

    def request_quit(self) -> None:
        """Signal the event loop to stop."""
        self._running = False

    def mark_dirty(self) -> None:
        """Force a render on the next frame."""
        self._render_cycle.mark_dirty()

    def _notify_recovery_orphans(self) -> None:
        """Notify the user about recovery files from crashed sessions."""
        if self._recovery_store is None:
            return
        try:
            orphans = self._recovery_store.list_orphans()
        except Exception:
            return
        if not orphans:
            return
        count = len(orphans)
        msg = (
            f"Recovery: {count} file(s) from a crashed session — use :RecoverFile <path>"
            if count > 1
            else f"Recovery: unsaved changes for '{orphans[0][0]}' — use :RecoverFile to restore"
        )
        if self._editor_state is not None:
            self._editor_state.message = msg
        if self._notify_manager is not None and hasattr(self._notify_manager, "notify"):
            with contextlib.suppress(Exception):
                self._notify_manager.notify(msg, level="warn", title="Recovery", timeout=10.0)

    def _cleanup_recovery_on_exit(self) -> None:
        """Delete recovery files for clean exit and release the lockfile."""
        if self._recovery_store is None:
            return
        try:
            paths = [doc.path for doc in self._workspace.documents if doc.path is not None]
            self._recovery_store.cleanup_session(paths)
            self._recovery_store.delete_lockfile()
        except Exception:
            log.exception("recovery cleanup failed")


def _render_message(msg: str, rect: Rect, grid: CellGrid) -> None:
    """Render an error/info message in the cmdline row (truncated to width)."""
    from peovim.ui.backend import ATTR_BOLD

    visible = msg[: rect.width].ljust(rect.width)
    grid.write_str(rect.y, rect.x, visible, fg=(255, 80, 80), attrs=ATTR_BOLD)


def _parse_sub_preview(text: str) -> tuple[str, str | None, str, bool, bool] | None:
    """Parse an incomplete ':s/pat/rep/flags' command from cmdline text.

    Returns (pattern, replacement_or_None, flags, all_lines, visual_range) or None.
    all_lines is True for '%' range; visual_range is True for \"'<,'>\".
    """
    import re

    # Range may contain marks ('< '>), line numbers, %, ., $
    m = re.match(r"^(?P<range>[0-9,%.$ '<>]*)?s(.)(.*)$", text, re.DOTALL)
    if not m:
        return None
    range_str = m.group("range") or ""
    all_lines = "%" in range_str
    visual_range = "'<" in range_str and "'>" in range_str
    delim = m.group(2)
    if delim in ("\\", "\n", " "):
        return None  # unusual delimiters — skip preview
    rest = m.group(3)
    parts = rest.split(delim)
    pattern = parts[0]
    replacement = parts[1] if len(parts) > 1 else None
    flags = parts[2] if len(parts) > 2 else ""
    return pattern, replacement, flags, all_lines, visual_range


def _build_sub_preview_decorations(
    visible_lines: list[str],
    scroll_line: int,
    compiled,
    replacement: str | None,
    cursor_line: int = 0,
    all_lines: bool = False,
    line_range: tuple[int, int] | None = None,
) -> list:
    """Build decorations for substitute preview.

    Pattern-only: highlight matches in search yellow.
    With replacement: overlay replacement text in green; excess match chars in red.

    Only shows preview on lines that will actually be affected:
    - line_range set: only lines in that inclusive range
    - all_lines=True (%s): all visible lines
    - all_lines=False: only the cursor line
    """
    import re

    from peovim.core.search import search_all_in_line
    from peovim.ui.decorations import HighlightRegion, OverlayChar, Style

    # Styles
    match_style = Style(fg=(0, 0, 0), bg=(255, 200, 0))  # yellow — match
    del_style = Style(fg=(255, 255, 255), bg=(180, 40, 40))  # red   — deleted chars
    add_style = Style(fg=(0, 0, 0), bg=(80, 200, 80))  # green — replacement

    decorations: list = []

    for i, line_text in enumerate(visible_lines):
        doc_line = scroll_line + i
        # Only preview lines that will actually be affected by the command
        if line_range is not None:
            if not (line_range[0] <= doc_line <= line_range[1]):
                continue
        elif not all_lines and doc_line != cursor_line:
            continue
        matches = search_all_in_line(line_text, compiled)

        for col_start, col_end in matches:
            match_len = col_end - col_start

            if replacement is None:
                # Only pattern typed so far — plain highlight
                decorations.append(HighlightRegion(doc_line, col_start, doc_line, col_end, match_style))
            else:
                # Expand backreferences in replacement using the actual match
                m = compiled.search(line_text[col_start:col_end])
                if m:
                    try:
                        rep_text = m.expand(replacement)
                    except re.error:
                        rep_text = replacement
                else:
                    rep_text = replacement

                rep_len = len(rep_text)

                # Overlay replacement chars (green) over the match cells
                for j in range(min(match_len, rep_len)):
                    decorations.append(OverlayChar(doc_line, col_start + j, rep_text[j], add_style))

                if rep_len < match_len:
                    # Match is longer — show leftover original chars as deleted (red)
                    decorations.append(HighlightRegion(doc_line, col_start + rep_len, doc_line, col_end, del_style))
                elif rep_len > match_len:
                    # Replacement is longer — show extra chars after the match cell
                    # as OverlayChar using the cell immediately past the match end
                    # (best-effort: these chars don't exist in the buffer)
                    extra = rep_text[match_len:]
                    for j, ch in enumerate(extra):
                        col = col_end + j
                        if col < len(line_text):
                            decorations.append(OverlayChar(doc_line, col, ch, add_style))

    return decorations
