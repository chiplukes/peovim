"""
plugins.session — Session save/restore plugin

Registers :Session, :SessionLoad, :SessionList, :SessionDelete commands
and leader key bindings for session management.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI


def setup(api: EditorAPI) -> None:
    """Register session commands and key bindings."""

    def _auto_save(**kwargs) -> None:
        with contextlib.suppress(Exception):
            api.session.save("autosave")

    api.events.on("editor_shutdown", _auto_save)

    def _save_default() -> None:
        try:
            api.session.save("default")
            api.ui.notify("Session saved: default", level="info")
        except Exception as e:
            api.ui.notify(f"Session save failed: {e}", level="error")

    def _restore_last() -> None:
        sessions = api.session.list_sessions()
        name = "autosave" if "autosave" in sessions else (sessions[0] if sessions else None)
        if name is None:
            api.ui.notify("No sessions found", level="warn")
            return
        try:
            api.session.restore(name)
            api.ui.notify(f"Session restored: {name}", level="info")
        except Exception as e:
            api.ui.notify(f"Session restore failed: {e}", level="error")

    api.keymap.define_plug("SessionSave", lambda: _save_default(), desc="Session: save default")
    api.keymap.define_plug("SessionRestore", lambda: _restore_last(), desc="Session: restore last")
    api.keymap.nmap("<leader>qs", "<Plug>SessionSave", desc="Session: save default")
    api.keymap.nmap("<leader>qr", "<Plug>SessionRestore", desc="Session: restore last")

    # Ex commands — handler signature: (cmd, ctx) -> None
    def _cmd_session(cmd, ctx) -> None:
        args = getattr(cmd, "args", "") or ""
        name = args.strip() or "default"
        try:
            api.session.save(name)
            api.ui.notify(f"Session saved: {name}", level="info")
        except Exception as e:
            api.ui.notify(f"Session save failed: {e}", level="error")

    def _cmd_session_load(cmd, ctx) -> None:
        args = getattr(cmd, "args", "") or ""
        name = args.strip() or "default"
        try:
            api.session.restore(name)
            api.ui.notify(f"Session restored: {name}", level="info")
        except Exception as e:
            api.ui.notify(f"Session restore failed: {e}", level="error")

    def _cmd_session_list(cmd, ctx) -> None:
        sessions = api.session.list_sessions()
        if not sessions:
            api.ui.notify("No sessions", level="info")
            return
        api.ui.open_picker(
            "Sessions",
            sessions,
            on_confirm=lambda name: api.session.restore(name),
        )

    def _cmd_session_delete(cmd, ctx) -> None:
        args = getattr(cmd, "args", "") or ""
        name = args.strip()
        if not name:
            api.ui.notify("Usage: SessionDelete <name>", level="warn")
            return
        try:
            api.session.delete(name)
            api.ui.notify(f"Session deleted: {name}", level="info")
        except Exception as e:
            api.ui.notify(f"Session delete failed: {e}", level="error")

    api.commands.register("Session", _cmd_session, min_abbrev=4)
    api.commands.register("SessionLoad", _cmd_session_load, min_abbrev=8)
    api.commands.register("SessionList", _cmd_session_list, min_abbrev=8)
    api.commands.register("SessionDelete", _cmd_session_delete, min_abbrev=8)
