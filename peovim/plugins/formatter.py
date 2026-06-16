"""
External formatter runner (conform-style).

Runs a configured external formatter on the buffer content before save.
Subscribes to the `buffer_pre_save` event; replaces buffer content with
formatted output if the formatter exits cleanly.

Configuration via api.options (set before loading):
    options.set('formatters', {
        'python': ['ruff format -'],
        'javascript': ['prettier --stdin-filepath {filename}'],
    })
    options.set('format_on_save', True)   # default True

If `format_on_save` is False the formatter only runs on :Format command.

Implemented against the public peovim.api — no internal imports.
See notes/plugins.md for plugin development.
"""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

_log = logging.getLogger(__name__)

_DEFAULT_FORMATTERS: dict[str, list[str]] = {
    "python": ["ruff", "format", "-"],
    "javascript": ["prettier", "--stdin-filepath", "{filename}"],
    "typescript": ["prettier", "--stdin-filepath", "{filename}"],
    "jsx": ["prettier", "--stdin-filepath", "{filename}"],
    "tsx": ["prettier", "--stdin-filepath", "{filename}"],
    "rust": ["rustfmt", "--edition", "2021"],
    "go": ["gofmt"],
    "lua": ["stylua", "-"],
    "json": ["prettier", "--stdin-filepath", "{filename}"],
    "yaml": ["prettier", "--stdin-filepath", "{filename}"],
    "css": ["prettier", "--stdin-filepath", "{filename}"],
    "html": ["prettier", "--stdin-filepath", "{filename}"],
    "markdown": ["prettier", "--stdin-filepath", "{filename}"],
    "toml": ["taplo", "fmt", "-"],
}


def setup(api: EditorAPI) -> None:
    """Subscribe to buffer_pre_save; register :Format command."""
    api.events.on("buffer_pre_save", lambda **kw: _on_pre_save(api, **kw))
    api.commands.register("format", lambda cmd, ctx: _cmd_format(api), min_abbrev=3)
    api.keymap.define_plug("FormatterFormat", lambda: _cmd_format(api), desc="Formatter: format buffer")
    api.keymap.nmap("<leader>F", "<Plug>FormatterFormat", desc="Formatter: format buffer")


def teardown() -> None:
    pass


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _on_pre_save(api: Any, **kwargs: Any) -> None:
    try:
        enabled = api.options.get("format_on_save")
        if enabled is False:
            return
    except Exception:
        pass
    buf_id: int | None = kwargs.get("buf_id")
    if buf_id is None:
        return
    for buf in api.list_buffers():
        if buf.buf_id == buf_id:
            _format_buffer(api, buf)
            return


def _cmd_format(api: Any) -> None:
    try:
        buf = api.active_buffer()
        result = _format_buffer(api, buf)
        if result:
            api.ui.notify("Formatted", level="info")
        else:
            api.ui.notify("No formatter configured for this filetype", level="info")
    except Exception:
        pass


def _format_buffer(api: Any, buf: Any) -> bool:
    """Run the formatter for buf's filetype. Returns True if formatting ran."""
    ft = getattr(buf, "filetype", "") or ""
    cmd = _get_formatter_cmd(api, ft)
    if not cmd:
        return False

    text = buf.get_text()
    filename = str(buf.path) if buf.path else f"unnamed.{ft}"

    # Substitute {filename} placeholder
    resolved = [part.replace("{filename}", filename) for part in cmd]

    _log.debug("format %s  ft=%s  cmd=%s", filename, ft, resolved[0])
    try:
        result = subprocess.run(
            resolved,
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except FileNotFoundError:
        _log.warning("format %s  cmd not found: %s", filename, resolved[0])
        return False
    except (subprocess.TimeoutExpired, OSError) as e:
        _log.warning("format %s  failed: %s", filename, e)
        return False

    if result.returncode != 0:
        _log.warning(
            "format %s  returncode=%d  stderr=%r",
            filename,
            result.returncode,
            result.stderr[:200] if result.stderr else "",
        )
        return False

    formatted = result.stdout
    if formatted == text:
        _log.debug("format %s  no change", filename)
        return True  # no change

    # Replace the entire buffer content
    _log.info("format %s  changed", filename)
    line_count = buf.line_count()
    buf.replace(0, 0, max(0, line_count - 1), len(buf.get_line(max(0, line_count - 1))), formatted)
    return True


def _get_formatter_cmd(api: Any, filetype: str) -> list[str] | None:
    """Return the formatter command list for filetype, or None."""
    try:
        user_formatters = api.options.get("formatters")
        if isinstance(user_formatters, dict) and filetype in user_formatters:
            cmd = user_formatters[filetype]
            if isinstance(cmd, list):
                return cmd
            if isinstance(cmd, str):
                return cmd.split()
    except Exception:
        pass
    return _DEFAULT_FORMATTERS.get(filetype)
