"""
Startup Dashboard — displayed when editor starts with no file.

Shows recent files and sessions.  Navigate with normal vim motions (j/k/gg/G),
press <CR> to open the item under the cursor.

Shortcuts:  e / q = close
"""

from __future__ import annotations

import contextlib
import pathlib
from functools import partial
from typing import TYPE_CHECKING

from peovim.config.loader import preferred_user_config_path

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI
    from peovim.api.window_api import WindowAPI

_LOGO = """\
  ███████╗██████╗
  ██╔════╝██╔══██╗
  █████╗  ██║  ██║
  ██╔══╝  ██║  ██║
  ███████╗██████╔╝
  ╚══════╝╚═════╝"""


def setup(api: EditorAPI) -> None:
    api.options.define("dashboard_logo", str, "", doc="Custom ASCII art logo for the dashboard (empty = built-in)")

    dashboard = _Dashboard(api)

    def _on_ready(**kwargs) -> None:
        buffers = api.list_buffers()
        if len(buffers) == 1:
            buf = buffers[0]
            if buf.path is None and buf.line_count() <= 1:
                with contextlib.suppress(Exception):
                    text = buf.get_text()
                    if text.strip():
                        return
                dashboard.show()

    api.events.on("editor_ready", _on_ready)

    # Track recently opened files
    def _on_buf_opened(**kwargs) -> None:
        # Explorer, :edit, and other openers bypass the dashboard's own actions.
        # Release its temporary keys before the first key in the opened file.
        if dashboard._active and not dashboard.is_active():
            dashboard._deactivate()
        buf_id = kwargs.get("buf_id")
        if buf_id is None:
            return
        for b in api.list_buffers():
            if b.buf_id == buf_id and b.path is not None:
                with contextlib.suppress(Exception):
                    api.push_recent_file(b.path)

    api.events.on("buffer_opened", _on_buf_opened)

    # <CR> opens the item on the cursor line — safe because <CR> has no
    # builtin normal-mode action in this engine.
    def _on_cr(**_kw) -> None:
        if dashboard.is_active():
            dashboard.open_selected()

    api.keymap.nmap("<CR>", _on_cr, desc="Dashboard: open item")


class _Dashboard:
    """Manages the dashboard buffer and item lookup."""

    def __init__(self, api: EditorAPI) -> None:
        self._api = api
        self._buf_id: int | None = None
        self._active = False
        self._window: WindowAPI | None = None
        self._was_modifiable = True
        # List of (kind, value) for selectable rows
        self._items: list[tuple[str, str]] = []
        # line index → item index
        self._line_to_item: dict[int, int] = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def show(self) -> None:
        if self._active:
            self._deactivate()
        win = self._api.active_window()
        buf = self._api.active_buffer()
        self._window = win
        modifiable = win.get_option("modifiable")
        self._was_modifiable = True if modifiable is None else bool(modifiable)
        self._buf_id = buf.buf_id
        self._active = True
        self._items = []
        self._line_to_item = {}
        content = self._render()
        buf.set_text(content)
        win.set_option("modifiable", False)
        # Place cursor on first selectable item
        if self._line_to_item:
            first_line = min(self._line_to_item)
            first_text_col = len(content.splitlines()[first_line]) - len(content.splitlines()[first_line].lstrip())
            win.set_cursor(first_line, first_text_col)
        # Register dashboard-only keys that shadow vim motions
        for key in ("e", "q", "i"):
            desc = "Dashboard: open init" if key == "i" else "Dashboard: close"
            self._api.keymap.nmap(key, partial(self._handle_shortcut, key), desc=desc)

    def is_active(self) -> bool:
        if not self._active:
            return False
        return self._buf_id is not None and self._api.active_buffer().buf_id == self._buf_id

    def close(self) -> None:
        self._deactivate()
        self._api.active_buffer().set_text("")

    def open_selected(self) -> None:
        """Open the item on the current cursor line."""
        cursor_line = self._api.active_window().cursor[0]
        item_idx = self._line_to_item.get(cursor_line)
        if item_idx is None:
            return
        kind, value = self._items[item_idx]
        if kind == "file":
            self._open_file(value)
        elif kind == "session":
            self._load_session(value)
        elif kind == "config":
            self.open_config()

    def open_recent_file(self, n: int) -> None:
        """Open the nth (1-based) recent file (used by tests)."""
        file_items = [(k, v) for k, v in self._items if k == "file"]
        if n < 1 or n > len(file_items):
            return
        self._open_file(file_items[n - 1][1])

    def open_config(self) -> None:
        config_path = preferred_user_config_path().resolve()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if not config_path.exists():
            config_path.write_text("", encoding="utf-8")
        self._open_file(str(config_path))

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _handle_shortcut(self, key: str) -> None:
        if not self.is_active():
            # Window switches need not emit buffer_opened. If one left a stale
            # shortcut behind, clean up and process this same key normally.
            self._deactivate()
            self._api.keymap.feed_keys(key)
        elif key == "i":
            self.open_config()
        else:
            self.close()

    def _deactivate(self) -> None:
        """Clear active state and unregister dashboard-only key bindings."""
        if self._window is not None:
            with contextlib.suppress(Exception):
                self._window.set_option("modifiable", self._was_modifiable)
            self._window = None
        self._active = False
        self._buf_id = None
        self._items = []
        self._line_to_item = {}
        # Each nunmap in its own suppress so a failure on one doesn't skip the rest.
        with contextlib.suppress(Exception):
            self._api.keymap.nunmap("e")
        with contextlib.suppress(Exception):
            self._api.keymap.nunmap("q")
        with contextlib.suppress(Exception):
            self._api.keymap.nunmap("i")

    def _open_file(self, path_str: str) -> None:
        file_path = pathlib.Path(path_str).resolve()
        if not file_path.exists():
            return
        self._deactivate()
        self._api.open_buffer(file_path)

    def _load_session(self, name: str) -> None:
        if "SessionLoad" not in self._api.commands.list_commands():
            self._api.ui.notify("Session plugin not available (add peovim.plugins.session to init.py)", level="warn")
            return
        self._deactivate()
        self._api.commands.execute(f"SessionLoad {name}")

    def _render(self) -> str:
        lines: list[str] = []

        custom_logo = self._api.options.get("dashboard_logo") or ""
        logo = custom_logo.strip("\n") if custom_logo else _LOGO
        lines.extend(logo.splitlines())
        lines.append("")

        config_path = preferred_user_config_path()
        lines.append("  Quick Actions")
        lines.append("  " + "\u2500" * 40)
        item_idx = len(self._items)
        self._items.append(("config", str(config_path)))
        self._line_to_item[len(lines)] = item_idx
        lines.append(f"    Open init.py  ({config_path})")
        lines.append("")

        # Recent Files
        with contextlib.suppress(Exception):
            recent_files = self._api.recent_files()
            if recent_files:
                lines.append("  Recent Files")
                lines.append("  " + "\u2500" * 40)
                for fpath in recent_files[:9]:
                    item_idx = len(self._items)
                    self._items.append(("file", str(fpath)))
                    self._line_to_item[len(lines)] = item_idx
                    lines.append(f"    {fpath}")
                lines.append("")

        # Sessions
        with contextlib.suppress(Exception):
            sessions = self._api.session.list_sessions()
            if sessions:
                lines.append("  Sessions")
                lines.append("  " + "\u2500" * 40)
                for name in sessions[:9]:
                    item_idx = len(self._items)
                    self._items.append(("session", name))
                    self._line_to_item[len(lines)] = item_idx
                    lines.append(f"    {name}")
                lines.append("")

        lines.append("  j/k navigate   <CR> open   i init   e/q close   h checkhealth")
        return "\n".join(lines)
