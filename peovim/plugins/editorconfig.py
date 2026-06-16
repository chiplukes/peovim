"""
Auto-apply .editorconfig settings on buffer open.

Reads .editorconfig properties via the `editorconfig` package and applies
them as per-window options so settings are buffer-scoped (matching Neovim's
behavior). Gracefully skips if the `editorconfig` package is not installed.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

_log = logging.getLogger("peovim.plugins.editorconfig")


def setup(api: EditorAPI) -> None:
    try:
        import editorconfig  # noqa: F401
    except ImportError:
        _log.debug("editorconfig package not installed — skipping EditorConfig plugin")
        return

    def _on_buffer_opened(**kwargs: Any) -> None:
        buf_id = kwargs.get("buf_id")
        if buf_id is None:
            return
        for buf in api.list_buffers():
            if buf.buf_id == buf_id and buf.path is not None:
                _apply(api, buf)
                return

    api.events.on("buffer_opened", _on_buffer_opened)


def _apply(api: EditorAPI, buf: Any) -> None:
    """Apply editorconfig properties to the window displaying buf."""
    if buf.path is None:
        return
    try:
        import editorconfig

        props: dict[str, str] = editorconfig.get_properties(str(buf.path))
    except Exception as exc:
        _log.warning("EditorConfig error for %s: %s", buf.path, exc)
        return

    # We apply to the currently active window (the one that just opened the file)
    win = api.active_window()

    with contextlib.suppress(Exception):
        if props.get("indent_style") == "space":
            win.set_option("expandtab", True)
        elif props.get("indent_style") == "tab":
            win.set_option("expandtab", False)

    with contextlib.suppress(ValueError, Exception):
        if "indent_size" in props and props["indent_size"] not in ("tab", ""):
            size = int(props["indent_size"])
            win.set_option("tabstop", size)
            win.set_option("shiftwidth", size)

    with contextlib.suppress(ValueError, Exception):
        if "tab_width" in props:
            win.set_option("tabstop", int(props["tab_width"]))

    with contextlib.suppress(Exception):
        eol_map = {"lf": "unix", "crlf": "dos", "cr": "mac"}
        fmt = eol_map.get(props.get("end_of_line", ""))
        if fmt:
            win.set_option("fileformat", fmt)

    with contextlib.suppress(Exception):
        if props.get("trim_trailing_whitespace", "").lower() == "true":
            win.set_option("trim_trailing_whitespace", True)

    with contextlib.suppress(Exception):
        charset = props.get("charset", "")
        if charset.startswith("utf-8"):
            win.set_option("fileencoding", "utf-8")
