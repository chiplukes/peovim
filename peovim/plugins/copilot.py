"""
plugins.copilot — GitHub Copilot inline completions (ghost text).

Provides AI-powered inline suggestions that appear as faded text after the
cursor while you type in insert mode.

Prerequisites
-------------
Install the Copilot language server (one-time):
    npm install -g @github/copilot-language-server

Or download the native binary (no Node.js required) and place it at:
    ~/.config/peovim/copilot/copilot-language-server[.exe]

Load in init.py
---------------
    plugins.load("peovim.plugins.copilot")

Add keymaps (in init.py)
------------------------
    from peovim.plugins import copilot

    keymap.imap("<A-Tab>", copilot.accept,      desc="Accept Copilot suggestion")
    keymap.imap("<A-]>",   copilot.cycle_next,  desc="Next Copilot suggestion")
    keymap.imap("<A-[>",   copilot.cycle_prev,  desc="Previous Copilot suggestion")
    keymap.imap("<A-\\\\>",  copilot.dismiss,     desc="Dismiss Copilot suggestion")

Commands
--------
    :CopilotAuth    — start or re-run device-flow authentication
    :CopilotStatus  — show current auth status

Notes
-----
Ghost text shows the first line of the suggestion.  Accept inserts the full
completion text at the cursor.  Any text change or cursor line movement clears
the current suggestion.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

log = logging.getLogger(__name__)

_NS = "copilot:ghost"

# ---------------------------------------------------------------------------
# Configuration — set these in init.py before or after loading the plugin.
#
#   from peovim.plugins import copilot
#   copilot.debounce_ms    = 400    # slower trigger (default 350)
#   copilot.max_ghost_lines = 5     # show up to 5 lines of suggestion (default 3)
#   copilot.auto_trigger    = False  # disable automatic suggestions (default True)
# ---------------------------------------------------------------------------

#: Milliseconds to wait after the last keystroke before requesting a suggestion.
debounce_ms: int = 350

#: Maximum number of lines of ghost text to display (continuation lines only
#: appear on empty buffer lines to avoid obscuring real content).
max_ghost_lines: int = 3

#: When False, suggestions are never requested automatically — only on demand
#: (e.g. via a keymap that calls copilot.trigger()).
auto_trigger: bool = True

# Module-level handle so init.py can reference copilot.accept etc. directly.
_plugin: _CopilotPlugin | None = None


def accept() -> None:
    """Accept the current Copilot suggestion and insert it at the cursor."""
    if _plugin is not None:
        _plugin.accept()


def cycle_next() -> None:
    """Cycle to the next Copilot suggestion."""
    if _plugin is not None:
        _plugin.cycle_next()


def cycle_prev() -> None:
    """Cycle to the previous Copilot suggestion."""
    if _plugin is not None:
        _plugin.cycle_prev()


def dismiss() -> None:
    """Dismiss the current Copilot suggestion."""
    if _plugin is not None:
        _plugin.dismiss()


def trigger() -> None:
    """Manually request a Copilot suggestion (useful when auto_trigger=False)."""
    if _plugin is not None:
        _plugin._restart_debounce()


# ---------------------------------------------------------------------------
# Plugin implementation
# ---------------------------------------------------------------------------


class _CopilotPlugin:
    def __init__(self, api: EditorAPI) -> None:
        self._api = api
        self._client: Any = None
        self._ghost: Any = None
        self._debounce_task: asyncio.Task | None = None
        self._buf_versions: dict[int, int] = {}

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        from peovim.plugins.copilot_client import CopilotClient, find_copilot_binary
        from peovim.ui.ghost_text import GhostTextManager

        self._ghost = GhostTextManager()
        self._client = CopilotClient()

        if find_copilot_binary() is None:
            self._set_message(
                "Copilot: language server not found — run: npm install -g @github/copilot-language-server"
            )
            return

        self._set_message("Copilot: starting…")
        if not await self._client.start():
            self._set_message("Copilot: failed to start language server (check log)")
            return

        await self._run_auth()
        self._sync_active_buffer(open_doc=True)

    async def _run_auth(self) -> None:
        status = await self._client.check_status()
        if status == "OK":
            self._set_message("Copilot: ready")
            log.debug("copilot: already authenticated")
            return

        result = await self._client.sign_in()
        if result is None:
            return
        user_code, verification_uri = result
        auth_msg = f"Copilot auth:\n  1. Visit: {verification_uri}\n  2. Enter code: {user_code}"
        self._notify(auth_msg, title="Copilot", timeout=300.0)
        self._set_message(f"Copilot: enter code {user_code} at {verification_uri}")
        log.info("copilot: auth required — %s  code: %s", verification_uri, user_code)
        asyncio.ensure_future(self._poll_auth(user_code))

    async def _poll_auth(self, user_code: str) -> None:
        for _ in range(150):  # 5-minute window
            await asyncio.sleep(2.0)
            if await self._client.sign_in_confirm(user_code):
                self._set_message("Copilot: authenticated")
                log.info("copilot: authentication complete")
                return
        self._set_message("Copilot: authentication timed out")

    # ------------------------------------------------------------------
    # Buffer sync helpers
    # ------------------------------------------------------------------

    def _sync_active_buffer(self, *, open_doc: bool = False) -> None:
        """Send didOpen or didChange for the currently active buffer."""
        try:
            buf = self._api.active_window().buffer()
            if buf.path is None:
                return
            path = str(buf.path)
            version = self._next_version(buf.buf_id)
            text = buf.get_text()
            from peovim.plugins.copilot_client import language_id_from_path

            lang = buf.filetype or language_id_from_path(path)
            if open_doc:
                self._client.notify_did_open(path, lang, version, text)
            else:
                self._client.notify_did_change(path, version, text)
        except Exception as exc:
            log.debug("copilot: sync error: %s", exc)

    def _next_version(self, buf_id: int) -> int:
        ver = self._buf_versions.get(buf_id, 0) + 1
        self._buf_versions[buf_id] = ver
        return ver

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_buffer_opened(self, **_kw: Any) -> None:
        if self._client is None:
            return
        self._ghost_clear_and_redraw()
        self._sync_active_buffer(open_doc=True)

    def on_text_changed(self, **_kw: Any) -> None:
        if self._client is None:
            return
        self._ghost_clear_and_redraw()
        if auto_trigger:
            self._restart_debounce()

    def on_cursor_moved(self, **_kw: Any) -> None:
        if self._ghost and self._ghost.active:
            # Clear if the cursor moved to a different line
            try:
                cursor_line = self._api.active_window().cursor[0]
                if cursor_line != self._ghost._line:
                    self._ghost_clear_and_redraw()
            except Exception:
                pass

    def _restart_debounce(self) -> None:
        if self._debounce_task is not None:
            self._debounce_task.cancel()
            self._debounce_task = None
        with contextlib.suppress(RuntimeError):
            self._debounce_task = asyncio.ensure_future(self._debounced_fetch())

    async def _debounced_fetch(self) -> None:
        await asyncio.sleep(debounce_ms / 1000.0)
        await self._fetch_completions()

    async def _fetch_completions(self) -> None:
        try:
            win = self._api.active_window()
            buf = win.buffer()
            if buf.path is None:
                return
            path = str(buf.path)
            line, col = win.cursor
            version = self._next_version(buf.buf_id)
            text = buf.get_text()
            from peovim.plugins.copilot_client import language_id_from_path

            lang = buf.filetype or language_id_from_path(path)
            tab_size = int(self._api.options.get("tabstop") or 4)
            expand_tab = bool(self._api.options.get("expandtab"))

            self._client.notify_did_change(path, version, text)
            completions = await self._client.get_completions(
                path,
                lang,
                version,
                text,
                line,
                col,
                tab_size=tab_size,
                insert_spaces=expand_tab,
            )
            if completions and self._ghost:
                self._ghost.set(line, col, completions)
                self._update_decoration()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.debug("copilot: fetch error: %s", exc)

    # ------------------------------------------------------------------
    # Ghost text decoration management
    # ------------------------------------------------------------------

    def _ghost_clear_and_redraw(self) -> None:
        if self._ghost:
            self._ghost.clear()
        self._update_decoration()

    def _update_decoration(self) -> None:
        try:
            buf = self._api.active_window().buffer()
            buf.clear_namespace(_NS)
            if self._ghost and self._ghost.active:

                def _is_empty(line_no: int) -> bool:
                    try:
                        return not buf.get_line(line_no).strip()
                    except Exception:
                        return False

                for dec in self._ghost.current_decorations(max_lines=max_ghost_lines, is_empty_line=_is_empty):
                    buf.add_decoration(_NS, dec)
        except Exception as exc:
            log.debug("copilot: decoration update error: %s", exc)

    # ------------------------------------------------------------------
    # Public actions (called from keymaps)
    # ------------------------------------------------------------------

    def accept(self) -> None:
        if self._ghost is None or not self._ghost.active:
            return
        result = self._ghost.accept()
        self._ghost.clear()
        self._update_decoration()
        if not result:
            return
        text, range_start_line, range_start_col = result
        if not text:
            return
        try:
            win = self._api.active_window()
            cur_line, cur_col = win.cursor
            # Copilot's `text` starts at range_start_col, not necessarily at the cursor.
            # Only insert the portion after the cursor to avoid re-inserting already-typed chars.
            if range_start_line == cur_line and range_start_col < cur_col:
                insert_text = text[cur_col - range_start_col :]
            else:
                insert_text = text
            if insert_text:
                win.buffer().insert(cur_line, cur_col, insert_text)
        except Exception as exc:
            log.debug("copilot: accept error: %s", exc)

    def cycle_next(self) -> None:
        if self._ghost:
            self._ghost.cycle_next()
            self._update_decoration()

    def cycle_prev(self) -> None:
        if self._ghost:
            self._ghost.cycle_prev()
            self._update_decoration()

    def dismiss(self) -> None:
        self._ghost_clear_and_redraw()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_message(self, msg: str) -> None:
        with contextlib.suppress(Exception):
            self._api.set_status(msg, notify=False)

    def _notify(self, msg: str, *, title: str = "", timeout: float = 10.0) -> None:
        with contextlib.suppress(Exception):
            self._api.ui.notify(msg, title=title, timeout=timeout)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def setup(api: EditorAPI) -> None:
    global _plugin
    _plugin = _CopilotPlugin(api)

    api.events.on("editor_ready", lambda **_kw: asyncio.ensure_future(_plugin.startup()))
    api.events.on("buffer_opened", lambda **kw: _plugin.on_buffer_opened(**kw))
    api.events.on("buffer_text_changed", lambda **kw: _plugin.on_text_changed(**kw))
    api.events.on("cursor_moved", lambda line=0, col=0, **kw: _plugin.on_cursor_moved(line=line, col=col, **kw))

    api.commands.register(
        "CopilotAuth",
        lambda _cmd, _ctx: asyncio.ensure_future(_plugin._run_auth()),
        desc="Re-run Copilot device-flow authentication",
    )
    api.commands.register(
        "CopilotStatus",
        lambda _cmd, _ctx: asyncio.ensure_future(_cmd_status(api)),
        desc="Show Copilot authentication status",
    )


async def _cmd_status(api: EditorAPI) -> None:
    if _plugin is not None and _plugin._client is not None:
        status = await _plugin._client.check_status()
        with contextlib.suppress(Exception):
            api.set_status(f"Copilot: {status}", notify=False)
    else:
        with contextlib.suppress(Exception):
            api.set_status("Copilot: not running", notify=False)
