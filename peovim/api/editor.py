"""
EditorAPI — top-level plugin API entry point

Instantiated once in main.py and passed to each plugin's setup(api).
"""

from __future__ import annotations

import contextlib
import logging
import pathlib
from typing import TYPE_CHECKING, Any, Literal, cast

from peovim.api._metadata import API_NAMESPACE_STATUS, VERSION, namespace_status, requires_version

if TYPE_CHECKING:
    from peovim.commands.registry import CommandRegistry
    from peovim.core.editor_state import EditorState
    from peovim.core.workspace import Workspace
    from peovim.modal.dispatcher import ActionDispatcher
    from peovim.modal.engine import ModalEngine


class _ScheduledInterval:
    """Handle for an interval task that may be created lazily on editor_ready."""

    def __init__(self) -> None:
        self._task: Any = None
        self._cancelled = False

    def attach_task(self, task: Any) -> None:
        self._task = task

    def cancel(self) -> None:
        self._cancelled = True
        if self._task is not None:
            self._task.cancel()

    def cancelled(self) -> bool:
        if self._task is not None:
            return bool(self._task.cancelled())
        return self._cancelled


class EditorAPI:  # cm:6d5a2c
    """
    Top-level API object passed to plugin setup() functions.

    Sub-APIs: editor (this object), buffer, window, keymap, commands,
              events, git, ui, options, store.
    """

    VERSION = VERSION
    API_NAMESPACE_STATUS = API_NAMESPACE_STATUS

    def __init__(
        self,
        workspace: Workspace,
        engine: ModalEngine,
        dispatcher: ActionDispatcher,
        editor_state: EditorState,
        command_registry: CommandRegistry,
        event_loop: Any = None,
        float_manager: Any = None,
        notify_manager: Any = None,
        picker: Any = None,
        which_key_panel: Any = None,
        lsp_manager: Any = None,
    ) -> None:
        self._workspace = workspace
        self._engine = engine
        self._dispatcher = dispatcher
        self._editor_state = editor_state
        self._command_registry = command_registry
        self._event_loop = event_loop
        self._interval_handles: set[Any] = set()
        self._pending_intervals: list[tuple[Any, int, _ScheduledInterval]] = []
        self._project_root: pathlib.Path | None = None

        # Build sub-APIs
        from peovim.api.commands_api import CommandsAPI
        from peovim.api.events_api import EventsAPI
        from peovim.api.git_api import GitAPI
        from peovim.api.health_api import HealthAPI
        from peovim.api.keymap_api import KeymapAPI
        from peovim.api.modal_api import ModalAPI
        from peovim.api.options_api import OptionsAPI
        from peovim.api.session_api import SessionAPI
        from peovim.api.store_api import StoreAPI
        from peovim.api.ui_api import UIAPI
        from peovim.modal.keybindings import BindingRegistry

        self._binding_registry = BindingRegistry(engine, dispatcher)
        self.events = EventsAPI(editor_state.event_bus)
        self.modal = ModalAPI(engine)
        self.options = OptionsAPI(editor_state.options)
        self.store = StoreAPI()
        self.keymap = KeymapAPI(self._binding_registry)
        self.commands = CommandsAPI(command_registry, dispatcher)
        self.git = GitAPI()
        self.ui = UIAPI(float_manager, notify_manager, picker, which_key_panel=which_key_panel)
        self.git._notify_fn = self.ui.notify
        self.health = HealthAPI()
        self.session = SessionAPI(workspace, engine, dispatcher)

        # LSP API (wired in main.py after LspManager is created)
        if lsp_manager is not None:
            from peovim.api.lsp_api import LspAPI

            self.lsp: Any = LspAPI(lsp_manager, workspace, editor_state)
        else:
            self.lsp = None

        from peovim.api.workspace_api import WorkspaceAPI

        self.workspace = WorkspaceAPI(workspace, dispatcher, self)

        self._flash_plugin_ref: Any = None  # set via register_flash_plugin() by flash.py

        # Public repeat-command helpers — set by editor_utils plugin on load.
        # Exposed as flat names in init.py via config/loader.py _build_namespace().
        # Initialized to no-ops so init.py code is safe even if the plugin is not loaded.
        self.remember: Any = lambda fn: fn  # remember(fn) -> wrapped fn
        self.repeat: Any = lambda: None  # repeat() -> calls last remembered command

        # Back-reference so builtin commands can reach the API through EditorState
        editor_state._api = self
        editor_state.event_bus.on("editor_ready", self._on_editor_ready)
        editor_state.event_bus.on("editor_shutdown", self._on_editor_shutdown)

        self._register_sidebar_plugs()

    @property
    def binding_registry(self) -> Any:
        """The shared keybinding registry."""
        return self._binding_registry

    def register_flash_plugin(self, plugin: Any) -> None:
        """Called by the flash plugin to register itself for event-loop wiring."""
        self._flash_plugin_ref = plugin

    @property
    def flash_plugin(self) -> Any:
        """Returns the registered flash plugin, or None if not loaded."""
        return self._flash_plugin_ref

    def set_project_root(self, root: pathlib.Path) -> None:
        """Set the project root explicitly (e.g. when opened as a directory)."""
        self._project_root = root

    def attach_event_loop(self, event_loop: Any) -> None:
        """Wire the EventLoop reference after it is constructed."""
        self._event_loop = event_loop

    # ------------------------------------------------------------------
    # Sidebar navigation plugs
    # ------------------------------------------------------------------

    def _register_sidebar_plugs(self) -> None:
        """Register <Plug> targets and default bindings for sidebar navigation."""
        for plug_name, action_name in [
            ("SidebarFocusLeft", "focus_left"),
            ("SidebarFocusRight", "focus_right"),
            ("SidebarPrevPanel", "prev_panel"),
            ("SidebarNextPanel", "next_panel"),
            ("SidebarShrink", "shrink"),
            ("SidebarGrow", "grow"),
            ("SidebarClose", "close"),
        ]:

            def fn(_a=action_name):
                return self._dispatch_sidebar_action(_a)

            self._binding_registry.register_plug(
                "normal", plug_name, fn, desc=f"Sidebar: {action_name.replace('_', ' ')}"
            )

        # Default bindings — user init.py may override with nmap()
        for key_seq, plug_name in [
            ("<A-h>", "SidebarFocusLeft"),
            ("<A-l>", "SidebarFocusRight"),
            ("<A-j>", "SidebarNextPanel"),
            ("<A-k>", "SidebarPrevPanel"),
        ]:
            self._binding_registry.register("normal", key_seq, f"<Plug>{plug_name}", desc=f"Sidebar: {plug_name}")

        # ── Bottom panel plugs ─────────────────────────────────────────
        for plug_name, action_name in [
            ("BottomPanelToggle", "toggle"),
            ("BottomPanelFocus", "focus"),
            ("BottomPanelBlur", "blur"),
            ("BottomPanelNextTab", "next_tab"),
            ("BottomPanelPrevTab", "prev_tab"),
            ("BottomPanelShrink", "shrink"),
            ("BottomPanelGrow", "grow"),
            ("BottomPanelClose", "close"),
        ]:

            def fn(_a=action_name):
                return self._dispatch_bottom_panel_action(_a)

            self._binding_registry.register_plug(
                "normal", plug_name, fn, desc=f"Bottom panel: {action_name.replace('_', ' ')}"
            )

        self._binding_registry.register("normal", "<A-p>", "<Plug>BottomPanelToggle", desc="Toggle bottom panel")

    def _dispatch_sidebar_action(self, action: str) -> None:
        """Called by sidebar <Plug> callbacks. Late-binds to event_loop._sidebar."""
        sidebar = getattr(self._event_loop, "_sidebar", None) if self._event_loop else None
        if action == "focus_left":
            if sidebar is not None and getattr(sidebar, "focused", False):
                windows = self._workspace.active_tab.all_windows()
                sidebar.blur()
                if windows:
                    self.activate_window(windows[-1])
            else:
                from peovim.modal.actions import FocusWindow, SmartFocusWindow

                before = self._workspace.active_window
                self._dispatcher.dispatch([FocusWindow("h")])
                if self._workspace.active_window is not before:
                    return
                if sidebar is not None and getattr(sidebar, "visible", False):
                    sidebar.focus()
                    return
                self._dispatcher.dispatch([SmartFocusWindow("h")])
        elif action == "focus_right":
            if sidebar is not None and getattr(sidebar, "focused", False):
                windows = self._workspace.active_tab.all_windows()
                sidebar.blur()
                if windows:
                    self.activate_window(windows[0])
            else:
                from peovim.modal.actions import FocusWindow, SmartFocusWindow

                before = self._workspace.active_window
                self._dispatcher.dispatch([FocusWindow("l")])
                if self._workspace.active_window is not before:
                    return
                if sidebar is not None and getattr(sidebar, "visible", False):
                    sidebar.focus()
                    return
                self._dispatcher.dispatch([SmartFocusWindow("l")])
        elif action == "next_panel":
            if sidebar is not None and getattr(sidebar, "focused", False):
                sidebar.next_panel(focus=True)
            else:
                from peovim.modal.actions import SmartFocusWindow

                self._dispatcher.dispatch([SmartFocusWindow("j")])
        elif action == "prev_panel":
            if sidebar is not None and getattr(sidebar, "focused", False):
                sidebar.prev_panel(focus=True)
            else:
                from peovim.modal.actions import SmartFocusWindow

                self._dispatcher.dispatch([SmartFocusWindow("k")])
        elif action == "shrink":
            if sidebar is not None:
                sidebar._adjust_width(-sidebar._RESIZE_STEP)
        elif action == "grow":
            if sidebar is not None:
                sidebar._adjust_width(sidebar._RESIZE_STEP)
        elif action == "close" and sidebar is not None:
            sidebar.hide()

    def _dispatch_bottom_panel_action(self, action: str) -> None:
        """Called by bottom panel <Plug> callbacks."""
        bp = getattr(self._event_loop, "_bottom_panel", None) if self._event_loop else None
        if bp is None:
            return
        if action == "toggle":
            visible = getattr(bp, "visible", False)
            if visible:
                bp.hide()
            else:
                show = getattr(bp, "show_active_tab", None)
                if show:
                    show(focus=True)
                else:
                    bp._visible = True
                    bp._focused = True
        elif action == "focus":
            if getattr(bp, "visible", False):
                bp.focus()
        elif action == "blur":
            bp.blur()
        elif action == "next_tab":
            bp.next_tab()
        elif action == "prev_tab":
            bp.prev_tab()
        elif action == "shrink":
            bp._adjust_height(-bp._RESIZE_STEP)
        elif action == "grow":
            bp._adjust_height(bp._RESIZE_STEP)
        elif action == "close":
            bp.hide()

    # ------------------------------------------------------------------
    # Active buffer / window
    # ------------------------------------------------------------------

    def active_buffer(self) -> Any:
        """Return a BufferAPI for the active buffer."""
        win = self._workspace.active_window
        return self._make_buffer_api(win.document)

    def active_window(self) -> Any:
        """Return a WindowAPI for the active window."""
        return self._make_window_api(self._workspace.active_window)

    @property
    def active_mode(self) -> Any:
        """Return the current modal engine mode."""
        return getattr(self._engine, "mode", None)

    def list_windows(self) -> list[Any]:
        """Return WindowAPI for every open window across all tabs."""
        result = []
        for tab in self._workspace.tabs:
            for win in tab.all_windows():
                result.append(self._make_window_api(win))
        return result

    def list_tab_windows(self) -> list[Any]:
        """Return WindowAPI for every open window in the active tab."""
        return [self._make_window_api(win) for win in self._workspace.active_tab.all_windows()]

    def window_count(self) -> int:
        """Return the number of windows open in the active tab."""
        return len(self._workspace.active_tab.all_windows())

    def activate_window(self, window: Any) -> None:
        """Focus an existing window and sync dispatcher/engine state to it."""
        target = getattr(window, "_window", window)
        for index, tab in enumerate(self._workspace.tabs):
            if target not in tab.all_windows():
                continue
            self._workspace.active_tab_index = index
            tab.focus_window(target)
            self._dispatcher.window = target
            return
        raise ValueError("Window not found in workspace")

    def cycle_window(self, direction: str = "next") -> None:
        """Cycle focus to the next or previous window across all splits and tabs.

        Args:
            direction: ``"next"`` (default) or ``"prev"``.
        """
        from peovim.modal.actions import CycleWindow

        self._dispatcher.dispatch([CycleWindow(cast(Literal["next", "prev"], direction))])

    def focus_window(self, direction: str) -> None:
        """Move focus in a direction (h/j/k/l); wraps to next/prev window at edges.

        Args:
            direction: ``"h"`` (left), ``"j"`` (down), ``"k"`` (up), or ``"l"`` (right).
        """
        from peovim.modal.actions import SmartFocusWindow

        self._dispatcher.dispatch([SmartFocusWindow(cast(Literal["h", "j", "k", "l"], direction))])

    def resize_window(self, direction: str, delta: int = 1) -> None:
        """Resize the active split in the given axis."""
        from peovim.modal.actions import ResizeWindow

        self._dispatcher.dispatch([ResizeWindow(cast(Literal["h", "v"], direction), delta)])

    def toggle_window_expand(self, width_fraction: float = 0.75) -> None:
        """Toggle the active window between equal sizing and a wider width share."""
        from peovim.modal.actions import ToggleWindowExpand

        self._dispatcher.dispatch([ToggleWindowExpand(width_fraction)])

    def list_buffers(self) -> list[Any]:
        """Return BufferAPI for every open buffer."""
        seen: set[int] = set()
        result = []
        for tab in self._workspace.tabs:
            for win in tab.all_windows():
                bid = id(win.document)
                if bid not in seen:
                    seen.add(bid)
                    result.append(self._make_buffer_api(win.document))
        for doc in self._workspace.documents:
            bid = id(doc)
            if bid not in seen:
                seen.add(bid)
                result.append(self._make_buffer_api(doc))
        return result

    def buffer_by_id(self, buf_id: int) -> Any | None:
        """Return the open buffer matching `buf_id`, if any."""
        for buf in self.list_buffers():
            if buf.buf_id == buf_id:
                return buf
        return None

    def window_by_id(self, win_id: int, *, active_tab_only: bool = False) -> Any | None:
        """Return the open window matching `win_id`, if any."""
        windows = self.list_tab_windows() if active_tab_only else self.list_windows()
        for win in windows:
            if getattr(win, "win_id", None) == win_id:
                return win
        return None

    def open_buffer(self, path: str | pathlib.Path, line: int = 0, col: int = 0) -> None:
        """Open a file in the active window and optionally move the cursor."""
        from peovim.modal.actions import OpenBuffer

        target = pathlib.Path(path).resolve()
        self._dispatcher.dispatch([OpenBuffer(str(target))])

        win = self._workspace.active_window
        win.cursor.move_to(max(0, line), max(0, col))
        win.scroll_to_cursor()

    def open_scratch_buffer(self, text: str = "", *, filetype: str = "", name: str = "") -> Any:
        """Open scratch text in the active window and return its BufferAPI."""
        from peovim.core.document import Document

        doc = Document()
        doc.load_string(text)
        doc.path = None
        doc.filetype = filetype
        if name:
            doc.name = name
        self._workspace.add_document(doc)

        win = self._workspace.active_window
        win.document = doc
        win.scroll_line = 0
        win.scroll_col = 0
        win.cursor.move_to(0, 0)
        win.options["fileformat"] = doc.fileformat

        self._dispatcher.window = win
        return self._make_buffer_api(doc)

    def alternate_file(self) -> tuple[pathlib.Path | None, tuple[int, int]]:
        """Return the remembered alternate file path and cursor."""
        alt_path = getattr(self._editor_state, "alt_path", None)
        alt_cursor = tuple(getattr(self._editor_state, "alt_cursor", (0, 0)))
        return (pathlib.Path(alt_path) if alt_path else None, alt_cursor)

    def open_alternate_buffer(self) -> bool:
        """Open the remembered alternate file and restore its cursor when available."""
        alt_path, alt_cursor = self.alternate_file()
        if alt_path is None:
            return False
        self.open_buffer(alt_path, line=alt_cursor[0], col=alt_cursor[1])
        return True

    def set_register(self, name: str, text: str, kind: str = "char") -> None:
        """Write text into a register."""
        self._dispatcher.registers.set(name, text, cast(Literal["char", "line", "block"], kind))

    def get_register(self, name: str) -> tuple[str, str]:
        """Return the current contents of a register."""
        return self._dispatcher.registers.get(name)

    def paste_register(self, name: str = '"', *, before: bool = False) -> None:
        """Paste the given register in the active buffer."""
        from peovim.modal.actions import PasteRegister

        self._dispatcher.dispatch([PasteRegister(name, before=before)])

    def split_window(self, direction: str = "v", path: str | pathlib.Path | None = None) -> None:
        """Split the active window horizontally or vertically."""
        from peovim.modal.actions import SplitWindow

        buffer_path = str(pathlib.Path(path).resolve()) if path is not None else None
        self._dispatcher.dispatch([SplitWindow(cast(Literal["h", "v"], direction), buffer_path=buffer_path)])

    def close_window(self) -> None:
        """Close the active window."""
        from peovim.modal.actions import CloseWindow

        self._dispatcher.dispatch([CloseWindow()])

    def only_window(self) -> None:
        """Close all windows except the active one."""
        from peovim.modal.actions import OnlyWindow

        self._dispatcher.dispatch([OnlyWindow()])

    def equalize_windows(self) -> None:
        """Equalize split sizes in the active tab."""
        from peovim.modal.actions import EqualizeWindows

        self._dispatcher.dispatch([EqualizeWindows()])

    def goto_location(self, path: str | pathlib.Path, line: int = 0, col: int = 0) -> None:
        """Jump to a location, pushing source and destination onto the jumplist (Ctrl-O/I)."""
        target = pathlib.Path(path).resolve()

        # Push current (source) position before jumping so Ctrl-O returns here.
        jumplist = getattr(self._dispatcher, "jumplist", None)
        if jumplist is not None:
            src_buf = self.active_buffer()
            src_raw = getattr(src_buf, "path", None) if src_buf is not None else None
            if src_raw is not None:
                src_win = self._workspace.active_window
                jumplist.push(
                    src_win.cursor.line,
                    src_win.cursor.col,
                    str(pathlib.Path(src_raw).resolve()),
                    src_win.scroll_line,
                )

        self.open_buffer(target, line, col)

        active = self.active_buffer()
        active_path = getattr(active, "path", None)
        if active_path is None or pathlib.Path(active_path).resolve() != target:
            return

        if jumplist is not None:
            win = self._workspace.active_window
            jumplist.push(max(0, line), max(0, col), str(target), win.scroll_line)

    def terminal_size(self) -> tuple[int, int]:
        """Return current terminal dimensions as (cols, rows)."""
        grid = getattr(getattr(self, "_event_loop", None), "_grid", None)
        if grid is not None:
            return grid.width, grid.height
        import shutil

        ts = shutil.get_terminal_size()
        return ts.columns, ts.lines

    def open_cmdline(self, initial: str = "", prompt: str = ":") -> None:
        """Open the command line UI with optional initial text."""
        event_loop = self._event_loop
        if event_loop is None:
            return
        event_loop._cmdline.enter(prompt, initial)
        if prompt == ":":
            event_loop._cmdline.set_completion_source(event_loop._list_available_commands())
        event_loop._invalidate_cmdline()

    def set_status(
        self,
        message: str,
        *,
        notify: bool = True,
        level: str = "info",
        title: str = "",
        timeout: float = 3.0,
    ) -> None:
        """Update the status message and optionally mirror it to notifications."""
        self._editor_state.message = message
        if not notify:
            return
        with contextlib.suppress(Exception):
            self.ui.notify(message, level=level, title=title, timeout=timeout)

    def record_jump(self) -> None:
        """Push the current active location onto the jumplist if available."""
        jumplist = getattr(self._dispatcher, "jumplist", None)
        if jumplist is None:
            return
        win = self._workspace.active_window
        path = str(win.document.path) if win.document.path is not None else ""
        jumplist.push(win.cursor.line, win.cursor.col, path, win.scroll_line)

    def add_window_overlay(self, window: Any, namespace: str, decoration: Any) -> int:
        """Add a decoration scoped to a specific window rather than a document."""
        target = getattr(window, "_window", window)
        return self._editor_state.decorations.add(id(target), namespace, decoration)

    def clear_window_namespace(self, window: Any, namespace: str) -> None:
        """Clear a window-scoped decoration namespace."""
        target = getattr(window, "_window", window)
        self._editor_state.decorations.clear_namespace(id(target), namespace)

    def set_compare_status(self, status: dict[str, Any] | None) -> None:
        """Publish or clear compare-mode statusline state."""
        self._editor_state.compare_status = status

    # ------------------------------------------------------------------
    # Working directory
    # ------------------------------------------------------------------

    def cwd(self) -> pathlib.Path:
        """Return the current working directory."""
        return pathlib.Path.cwd()

    def set_cwd(self, path: str | pathlib.Path) -> None:
        """Change the process working directory."""
        import os

        os.chdir(path)

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def find_root(self, markers: list[str] | None = None) -> pathlib.Path | None:
        """
        Walk up from the active buffer's directory looking for marker files/dirs.
        Falls back to git root.

        The result is cached on first call with default markers so the project
        root stays pinned to the initial resolution even when the active file
        switches to a git submodule (which has its own .git file, not a dir).
        """
        using_defaults = markers is None
        if using_defaults and self._project_root is not None:
            return self._project_root

        if markers is None:
            markers = [".git", "pyproject.toml", "setup.py", "Cargo.toml"]

        win = self._workspace.active_window
        start = win.document.path
        if start is None:
            result = self.git.root()
        else:
            result = None
            current = start.parent
            while True:
                for m in markers:
                    p = current / m
                    # .git file (not dir) means this is a git submodule checkout;
                    # keep walking up to find the real superproject root.
                    if m == ".git" and p.exists() and not p.is_dir():
                        continue
                    if p.exists():
                        result = current
                        break
                if result is not None:
                    break
                parent = current.parent
                if parent == current:
                    break
                current = parent
            if result is None:
                result = self.git.root()

        if using_defaults and result is not None:
            self._project_root = result
        return result

    def find_files(self, pattern: str = "**/*", root: pathlib.Path | None = None) -> list[pathlib.Path]:
        """Return list of files matching pattern relative to root."""
        if root is None:
            root = self.find_root() or pathlib.Path.cwd()
        try:
            return [p for p in root.glob(pattern) if p.is_file()]
        except Exception:
            return []

    def grep(
        self, pattern: str, root: pathlib.Path | None = None, file_pattern: str = "*"
    ) -> list[tuple[pathlib.Path, int, str]]:
        """Return [(path, line_num, line_text), ...] for all matches.

        Uses ripgrep (rg) when available — respects .gitignore and skips binaries.
        Falls back to a Python walk that skips common non-source directories.
        """
        import re
        import subprocess

        if root is None:
            root = self.find_root() or pathlib.Path.cwd()

        # --- try ripgrep first (JSON output avoids Windows path/colon ambiguity) ---
        try:
            import json

            glob_args = ["--glob", file_pattern] if file_pattern != "*" else []
            proc = subprocess.run(
                ["rg", "--json", *glob_args, pattern, str(root)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            results: list[tuple[pathlib.Path, int, str]] = []
            for raw in proc.stdout.splitlines():
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if obj.get("type") != "match":
                    continue
                data = obj["data"]
                file_path = pathlib.Path(data["path"]["text"])
                line_num = int(data["line_number"]) - 1
                text = data["lines"]["text"].rstrip("\n\r")
                results.append((file_path, line_num, text))
            return results
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # rg not available or timed out — fall back to Python

        # --- Python fallback ---
        _SKIP_DIRS = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            "dist",
            "build",
            ".tox",
            ".mypy_cache",
            ".ruff_cache",
        }

        def _is_binary(path: pathlib.Path) -> bool:
            try:
                with open(path, "rb") as f:
                    chunk = f.read(8192)
                return b"\x00" in chunk
            except Exception:
                return True

        import fnmatch

        results = []
        try:
            compiled = re.compile(pattern)
            stack = [root]
            while stack:
                current = stack.pop()
                try:
                    for entry in current.iterdir():
                        if entry.is_dir():
                            if entry.name not in _SKIP_DIRS:
                                stack.append(entry)
                        elif entry.is_file():
                            if file_pattern != "*" and not fnmatch.fnmatch(entry.name, file_pattern):
                                continue
                            if _is_binary(entry):
                                continue
                            try:
                                with open(entry, encoding="utf-8", errors="replace") as f:
                                    for i, line in enumerate(f):
                                        if compiled.search(line):
                                            results.append((entry, i, line.rstrip()))
                            except Exception:
                                continue
                except PermissionError:
                    continue
        except Exception:
            pass
        return results

    # ------------------------------------------------------------------
    # Async / scheduling
    # ------------------------------------------------------------------

    def defer(self, fn, delay_ms: int = 0) -> None:
        """Schedule fn on the asyncio event loop."""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if delay_ms == 0:
                loop.call_soon_threadsafe(fn)
            else:
                loop.call_later(delay_ms / 1000.0, fn)
        except RuntimeError:
            pass  # no running loop in tests

    def set_interval(self, fn, interval_ms: int) -> Any:
        """Repeatedly call fn every interval_ms. Returns a handle."""
        import asyncio
        import contextlib

        async def _loop() -> None:
            try:
                while True:
                    await asyncio.sleep(interval_ms / 1000.0)
                    with contextlib.suppress(Exception):
                        fn()
            except asyncio.CancelledError:
                raise

        try:
            loop = asyncio.get_running_loop()
            interval = _ScheduledInterval()
            return self._start_interval(loop, _loop(), interval)
        except RuntimeError:
            interval = _ScheduledInterval()
            self._pending_intervals.append((fn, interval_ms, interval))
            return interval

    def _start_interval(self, loop: Any, coro: Any, interval: _ScheduledInterval) -> _ScheduledInterval:
        task = loop.create_task(coro)
        interval.attach_task(task)
        self._interval_handles.add(interval)
        task.add_done_callback(lambda _task, h=interval: self._interval_handles.discard(h))
        return interval

    def _on_editor_ready(self, **_kwargs: Any) -> None:
        import asyncio

        if not self._pending_intervals:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        pending = list(self._pending_intervals)
        self._pending_intervals.clear()
        for fn, interval_ms, interval in pending:
            if interval.cancelled():
                continue

            async def _loop(_fn=fn, _interval_ms=interval_ms) -> None:
                try:
                    while True:
                        await asyncio.sleep(_interval_ms / 1000.0)
                        with contextlib.suppress(Exception):
                            _fn()
                except asyncio.CancelledError:
                    raise

            self._start_interval(loop, _loop(), interval)

    def _on_editor_shutdown(self, **_kwargs: Any) -> None:
        for _fn, _interval_ms, interval in self._pending_intervals:
            interval.cancel()
        self._pending_intervals.clear()
        for handle in list(self._interval_handles):
            with contextlib.suppress(Exception):
                handle.cancel()
        self._interval_handles.clear()

    # ------------------------------------------------------------------
    # Shada
    # ------------------------------------------------------------------

    def shada_read(self) -> None:
        """Read shada file from disk into the editor state."""
        self._editor_state.shada.read()

    def shada_write(self) -> None:
        """Write shada state to disk."""
        self._editor_state.shada.write()

    def recent_files(self) -> list[pathlib.Path]:
        """Return recent file paths from shada, most recent first."""
        return [pathlib.Path(path) for path in self._editor_state.shada.get_recent_files() if path]

    def push_recent_file(self, path: str | pathlib.Path) -> None:
        """Record a file path in the recent-files history."""
        self._editor_state.shada.push_recent_file(str(path))

    def list_diagnostics(self) -> list[dict[str, Any]]:
        """Return current buffer diagnostics from LSP decorations across open buffers."""
        from peovim.ui.decorations import Sign, VirtualText

        diagnostics: list[dict[str, Any]] = []
        for buf in self.list_buffers():
            if buf.path is None:
                continue
            buf_id = buf.buf_id
            signs = self._editor_state.decorations.get_for_namespace(buf_id, "lsp:diag:signs")
            messages = self._editor_state.decorations.get_for_namespace(buf_id, "lsp:diag:text")
            message_by_line: dict[int, list[str]] = {}
            for dec in messages:
                if isinstance(dec, VirtualText):
                    message_by_line.setdefault(dec.line, []).append(dec.text.strip())
            for dec in signs:
                if not isinstance(dec, Sign):
                    continue
                diagnostics.append(
                    {
                        "path": buf.path,
                        "line": dec.line,
                        "col": 0,
                        "severity": dec.char,
                        "message": " | ".join(message_by_line.get(dec.line, [])),
                    }
                )
        diagnostics.sort(key=lambda item: (str(item["path"]), item["line"], item["severity"]))
        return diagnostics

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def get_logger(self, name: str) -> logging.Logger:
        """Return a logger scoped to peovim.plugin.<name>."""
        return logging.getLogger(f"peovim.plugin.{name}")

    def requires_version(self, min_version: str | tuple[int, int, int]) -> None:
        """Raise PluginVersionError if the current API is older than min_version."""
        requires_version(min_version, current_version=self.VERSION)

    def namespace_status(self, name: str) -> Any:
        """Return status metadata for a public API namespace."""
        return namespace_status(name)

    def register_sign_type(self, name: str, char: str, style: Any) -> None:
        """Register a sign type with the sign registry."""
        if not hasattr(style, "fg"):
            from peovim.core.style import Style

            style = Style(fg=style) if style else Style()
        self._editor_state.sign_registry.register(name, char, style)

    def on_filetype(self, filetype: str, handler) -> None:
        """Call handler(api) when a buffer of the given filetype is opened."""

        def _on_buf_open(**kwargs: Any) -> None:
            buf_id = kwargs.get("buf_id")
            if buf_id is None:
                return
            for b in self.list_buffers():
                if b.buf_id == buf_id and b.filetype == filetype:
                    handler(self)
                    return

        self.events.on("buffer_opened", _on_buf_open)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_buffer_api(self, document: Any) -> Any:
        from peovim.api.buffer_api import BufferAPI

        return BufferAPI(
            document,
            self._editor_state.decorations,
            self._editor_state.sign_registry,
            self._dispatcher,
        )

    def _make_window_api(self, window: Any) -> Any:
        from peovim.api.window_api import WindowAPI

        buf = self._make_buffer_api(window.document)
        return WindowAPI(window, buf, self._engine, self._dispatcher, self._workspace)
