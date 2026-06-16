"""
plugins.repl — REPL integration: send line/selection/block/cell to named terminal

Registers <leader>sl/ss/sb key bindings and :Python/:Terminal commands.
"""

from __future__ import annotations

import contextlib
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI


def setup(api: EditorAPI) -> None:
    """Register REPL commands and keybindings."""

    with contextlib.suppress(Exception):
        api.options.define("repl_terminal", str, "repl")

    def _get_repl_name() -> str:
        try:
            return api.options.get("repl_terminal") or "repl"
        except Exception:
            return "repl"

    def _get_or_open_repl():
        name = _get_repl_name()
        tb = api.ui.get_terminal(name)
        if tb is None or not tb.is_open:
            tb = api.ui.open_terminal(name, cmd=[sys.executable, "-i"])
        return tb

    def _send_line() -> None:
        buf = api.active_buffer()
        cursor = api.active_window().cursor
        line_text = buf.get_line(cursor[0])
        tb = _get_or_open_repl()
        if tb is not None:
            tb.write(line_text + "\n")

    def _send_block() -> None:
        buf = api.active_buffer()
        cursor = api.active_window().cursor
        row = cursor[0]
        line_count = buf.line_count()
        # Find paragraph: scan up for blank lines
        start = row
        while start > 0 and buf.get_line(start - 1).strip():
            start -= 1
        end = row
        while end < line_count - 1 and buf.get_line(end + 1).strip():
            end += 1
        lines = [buf.get_line(i) for i in range(start, end + 1)]
        text = "\n".join(lines) + "\n"
        tb = _get_or_open_repl()
        if tb is not None:
            tb.write(text)

    api.keymap.define_plug("ReplSendLine", _send_line, desc="REPL: send current line")
    api.keymap.define_plug("ReplSendBlock", _send_block, desc="REPL: send selection/block")
    api.keymap.nmap("<leader>rl", "<Plug>ReplSendLine", desc="REPL: send current line")
    api.keymap.nmap("<leader>rb", "<Plug>ReplSendBlock", desc="REPL: send selection/block")

    # Ex commands
    def _cmd_python(cmd, ctx) -> None:
        name = _get_repl_name()
        api.ui.open_terminal(name, cmd=[sys.executable, "-i"])
        api.ui.notify(f"Python REPL opened: {name}", level="info")

    def _cmd_terminal(cmd, ctx) -> None:
        args = getattr(cmd, "args", "") or ""
        name = args.strip() or "terminal"
        cmd_args = args.strip().split() if args.strip() else None
        api.ui.open_terminal(name, cmd=cmd_args)
        api.ui.notify(f"Terminal opened: {name}", level="info")

    api.commands.register("Python", _cmd_python, min_abbrev=3)
    api.commands.register("Terminal", _cmd_terminal, min_abbrev=4)
