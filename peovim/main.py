"""
main — entry point: `uv run peovim [file]`

Parses CLI arguments, selects the terminal backend, and starts the editor.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import pathlib
import sys
import threading


def _init_logging(
    log: bool = False,
    log_level: str | None = None,
    log_modules: str | None = None,
    log_file: str | None = None,
) -> None:
    """
    Initialise the LogManager singleton.

    With no flags: ring buffer only at WARNING (errors always captured).
    With --log-level LEVEL: enable in-panel logging at LEVEL (no file).
    With --log: enable DEBUG logging to file + ring buffer.
    With both: file logging at the specified level.

    Modules listed in --log-modules are imported after their log levels are set,
    so modules that self-register side effects on import (e.g. gc_tracer) activate
    automatically without needing a dedicated CLI flag.
    """
    import importlib

    from peovim.core.log_manager import get_log_manager

    mgr = get_log_manager()
    if log or log_level is not None:
        modules = [m.strip() for m in log_modules.split(",")] if log_modules else None
        mgr.enable(modules=modules, level=log_level or "debug", log_path=log_file or None, write_file=log)
        for mod in modules or []:
            with contextlib.suppress(ImportError):
                importlib.import_module(mod)


def _install_global_exception_hooks() -> None:
    """Log uncaught exceptions instead of dumping raw tracebacks into the terminal UI."""

    runtime_log = logging.getLogger("peovim.runtime")

    def _log_unhandled(where: str, exc_type, exc_value, exc_traceback) -> None:
        if exc_type is KeyboardInterrupt:
            return
        runtime_log.error(where, exc_info=(exc_type, exc_value, exc_traceback))

    def _sys_excepthook(exc_type, exc_value, exc_traceback) -> None:
        _log_unhandled("Unhandled exception", exc_type, exc_value, exc_traceback)

    def _threading_excepthook(args) -> None:
        thread_name = getattr(args.thread, "name", "thread")
        _log_unhandled(f"Unhandled thread exception ({thread_name})", args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = _sys_excepthook
    threading.excepthook = _threading_excepthook


def _shada_load(shada, marks, registers, jumplist=None) -> None:
    """Populate live stores from shada after reading."""
    # Global marks A-Z
    for name in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        entry = shada.get_global_mark(name)
        if entry is not None:
            path, line, col = entry
            marks.set_global(name, str(path), line, col)
    # Numbered registers 0-9
    for i in range(10):
        text = shada.get_register(i)
        if text:
            registers.set(str(i), text, "char")
    # Jump list
    if jumplist is not None:
        for p, line, col, scroll in shada.get_jump_list():
            jumplist.push(line, col, str(p), scroll)


def _shada_save(shada, marks, registers, workspace=None, jumplist=None) -> None:
    """Copy live store data into shada before writing."""
    import pathlib

    # Global marks A-Z
    for name in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        entry = marks.get_global(name)
        if entry is not None:
            path_str, line, col = entry
            shada.set_global_mark(name, pathlib.Path(path_str or "."), line, col)
    # Numbered registers 0-9
    for i in range(10):
        text, _ = registers.get(str(i))
        if text:
            shada.set_register(i, text)
    # Last cursor position per open file
    if workspace is not None:
        for tab in workspace.tabs:
            for win in tab.all_windows():
                if win.document.path is not None:
                    shada.set_file_pos(str(win.document.path), win.cursor.line, win.cursor.col)
    # Jump list
    if jumplist is not None:
        jl_entries = [(pathlib.Path(e[0]) if e[0] else pathlib.Path("."), e[1], e[2], e[3]) for e in jumplist._entries]
        shada.set_jump_list(jl_entries)


def main() -> None:  # cm:a3f1b2
    parser = argparse.ArgumentParser(prog="peovim", description="Modal text editor")
    parser.add_argument("file", nargs="?", help="File to open")
    parser.add_argument("--log", action="store_true", help="Enable debug logging to file")
    parser.add_argument(
        "--log-level",
        default=None,
        metavar="LEVEL",
        help="Log level for the output panel: debug/info/warning/error. "
        "Enables in-panel logging without requiring --log.",
    )
    parser.add_argument(
        "--log-modules",
        default=None,
        metavar="PATTERNS",
        help="Comma-separated module patterns, e.g. peovim.ui.event_loop,peovim.core.*",
    )
    parser.add_argument(
        "--log-file", default=None, metavar="PATH", help="Log file path (default: ~/.config/peovim/peovim.log)"
    )
    args = parser.parse_args()

    _init_logging(
        log=args.log,
        log_level=args.log_level,
        log_modules=args.log_modules,
        log_file=args.log_file,
    )  # cm://a3f1b3
    _install_global_exception_hooks()

    from peovim._native import HAS_NATIVE

    logging.getLogger(__name__).info("renderer: %s", "native (Cython)" if HAS_NATIVE else "pure Python")

    from peovim.commands.builtin import register_builtins
    from peovim.commands.registry import CommandRegistry
    from peovim.core.document import Document
    from peovim.core.editor_state import EditorState
    from peovim.core.jumplist import JumpList
    from peovim.core.marks import MarkStore
    from peovim.core.registers import RegisterStore
    from peovim.core.window import Window
    from peovim.core.workspace import Workspace
    from peovim.modal.dispatcher import ActionDispatcher
    from peovim.modal.engine import ModalEngine
    from peovim.ui.backend_factory import create_backend
    from peovim.ui.event_loop import EventLoop

    path = pathlib.Path(args.file).resolve() if args.file else None
    startup_root: pathlib.Path | None = None
    if path and path.is_dir():
        # Directory argument (e.g. "peovim .") — open empty buffer but anchor project root.
        startup_root = path
        path = None
    doc = Document(path=path)
    if path and path.exists():
        doc.load(path)
    _initial_path = path

    window = Window(doc)
    window.options["fileformat"] = doc.fileformat
    workspace = Workspace(window)
    registers = RegisterStore()
    marks = MarkStore()
    jumplist = JumpList()
    editor_state = EditorState()
    if path and path.exists() and doc.had_mixed_line_endings:
        editor_state.message = f"Mixed line endings detected: {path} (saving will normalize to {doc.fileformat})"

    # Shared command registry (allows plugins to register commands)
    command_registry = CommandRegistry()
    register_builtins(command_registry)

    engine = ModalEngine()
    engine.set_context_provider(lambda: workspace.active_window)
    dispatcher = ActionDispatcher(
        engine,
        window,
        registers,
        marks=marks,
        jumplist=jumplist,
        editor_state=editor_state,
        workspace=workspace,
    )
    # Wire the shared registry so _run_ex_command uses it
    dispatcher.set_command_registry(command_registry)

    from collections import deque

    from peovim.api.editor import EditorAPI
    from peovim.lsp.manager import LspManager
    from peovim.plugins.manager import PluginManager
    from peovim.ui.completion import CompletionPopup
    from peovim.ui.float_manager import FloatManager
    from peovim.ui.notify import NotifyManager
    from peovim.ui.picker import PickerWidget
    from peovim.ui.which_key_panel import WhichKeyPanel

    float_manager = FloatManager()
    notify_manager = NotifyManager()
    picker = PickerWidget()
    which_key_panel = WhichKeyPanel()

    lsp_queue: deque = deque()
    lsp_manager = LspManager(lsp_queue)
    lsp_manager.start_background_loop()

    completion_popup = CompletionPopup()

    editor_api = EditorAPI(  # cm:9c4d7e
        workspace,
        engine,
        dispatcher,
        editor_state,
        command_registry,
        float_manager=float_manager,
        notify_manager=notify_manager,
        picker=picker,
        which_key_panel=which_key_panel,
        lsp_manager=lsp_manager,
    )
    if startup_root is not None:
        editor_api.set_project_root(startup_root)
    plugin_manager = PluginManager(editor_api)

    from peovim.config.loader import ConfigLoader

    config_loader = ConfigLoader()

    # Shada must be loaded before project config trust checks so persisted
    # trust decisions are available before evaluating <root>/.peovim/init.py.
    editor_state.shada.read()
    _shada_load(editor_state.shada, marks, registers, jumplist)

    # User config explicitly selects plugins to load. No built-ins are loaded implicitly.
    config_loader.load_user_config(editor_api, plugin_manager=plugin_manager)

    # Wire health context so :checkhealth can report plugin and config status
    editor_api.health.set_context(plugin_manager=plugin_manager, config_loader=config_loader)

    def _on_shutdown(**kwargs):
        _shada_save(editor_state.shada, marks, registers, workspace, jumplist)
        editor_state.shada.merge_write()

    editor_state.event_bus.on("editor_shutdown", _on_shutdown)

    # Restore last cursor position when a file is opened
    # Track which buf_ids have had their shada cursor position restored this session.
    # Shada restoration should only happen on the first open of each file — not on
    # every subsequent buffer_opened event (e.g. triggered by Ctrl-O navigating back),
    # which would otherwise override the jumplist-restored cursor position.
    _shada_restored_bufs: set[int] = set()

    def _on_buffer_opened(**kwargs):
        buf_id = kwargs.get("buf_id")
        if buf_id is None or buf_id in _shada_restored_bufs:
            return
        _shada_restored_bufs.add(buf_id)
        for tab in workspace.tabs:
            for win in tab.all_windows():
                if id(win.document) == buf_id and win.document.path is not None:
                    pos = editor_state.shada.get_file_pos(str(win.document.path))
                    if pos is not None:
                        line, col = pos
                        line = min(line, max(0, win.document.line_count() - 1))
                        win.cursor.line = line
                        win.cursor.col = col
                        win.cursor.clamp(win.document._table)
                        win.scroll_to_cursor()
                    return

    editor_state.event_bus.on("buffer_opened", _on_buffer_opened)

    # Emit buffer_opened for the initial file once the editor is ready
    # (plugins subscribe during load, so we defer until editor_ready)
    if _initial_path and _initial_path.exists():

        def _emit_initial_buffer(**kwargs):
            from peovim.core.filetype import detect_filetype

            ft = detect_filetype(_initial_path)
            editor_state.event_bus.emit(
                "buffer_opened",
                buf_id=id(doc),
                path=str(_initial_path),
                filetype=ft,
            )

        editor_state.event_bus.once("editor_ready", _emit_initial_buffer)

    # Set up crash-recovery store for this session
    from peovim.core.recovery import RecoveryStore

    recovery_store = RecoveryStore.for_this_session()
    try:
        recovery_store.write_lockfile()
    except Exception:
        recovery_store = None  # type: ignore[assignment]

    backend = create_backend()
    event_loop = EventLoop(  # cm:e5b6f8
        backend,
        engine,
        dispatcher,
        workspace,
        editor_state=editor_state,
        float_manager=float_manager,
        notify_manager=notify_manager,
        picker=picker,
        which_key_panel=which_key_panel,
        lsp_queue=lsp_queue,
        completion_popup=completion_popup,
        recovery_store=recovery_store,
        options=editor_state.options,
    )
    editor_api.attach_event_loop(event_loop)
    # Wire flash plugin to event loop if loaded
    if editor_api.flash_plugin is not None:
        event_loop.attach_flash(editor_api.flash_plugin)

    # Share tree-views, sidebar, bottom panel, and binding registry from the API layer
    event_loop.attach_ui(editor_api.ui, editor_api.binding_registry)
    # Wire log output tab's yank callback to the editor's register/clipboard system
    editor_api.ui.set_yank_callback(lambda text: dispatcher.registers.set("+", text, "line"))
    # Give LspAPI direct access to the UI adapter for hover floats, goto, etc.
    if editor_api.lsp is not None:
        editor_api.lsp.attach_event_loop(event_loop)

    # Restore persisted command history into the command-line widget (↑/↓ recall)
    cmd_hist = editor_state.shada.get_command_history()
    if cmd_hist:
        event_loop.set_command_history(cmd_hist)

    # On Windows, the default timer resolution is ~15ms which makes asyncio.sleep
    # imprecise enough to break the 60fps render cadence.  Request 1ms resolution
    # for the duration of the editor session.
    import sys

    if sys.platform == "win32":
        import ctypes

        with contextlib.suppress(Exception):
            ctypes.windll.winmm.timeBeginPeriod(1)
    try:
        asyncio.run(event_loop.run())  # cm:2d1a3c
    finally:
        if sys.platform == "win32":
            with contextlib.suppress(Exception):
                ctypes.windll.winmm.timeEndPeriod(1)
