"""
Mark lines added after a buffer was opened using gutter signs.

Implemented against the public peovim.api — no internal imports.

Usage in init.py:
    options.set('session_additions_enabled', True)
    options.set('session_additions_sign_char', '+')
    options.set('session_additions_sign_color', '80,200,80')
    plugins.load('peovim.plugins.session_additions')
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

_NAMESPACE = "session_additions"
_SIGN_TYPE = "session_additions.add"

_baseline_by_buf_id: dict[int, list[str]] = {}
_tokens: list[int] = []


def setup(api: EditorAPI) -> None:
    """Register the session additions plugin with the editor."""
    global _baseline_by_buf_id, _tokens
    _baseline_by_buf_id = {}
    _tokens = []

    api.options.define(
        "session_additions_enabled",
        bool,
        True,
        doc="Show gutter markers for lines added after the buffer was opened.",
    )
    api.options.define(
        "session_additions_sign_char",
        str,
        "+",
        doc="Sign character used for lines added after the buffer was opened.",
    )
    api.options.define(
        "session_additions_sign_color",
        str,
        "80,200,80",
        doc="RGB or hex color for the session additions sign, e.g. '80,200,80' or '#50c850'.",
    )

    _register_sign_type(api)

    tok1 = api.events.on("buffer_opened", lambda **kwargs: _on_buffer_opened(api, **kwargs))
    tok2 = api.events.on("buffer_changed", lambda **kwargs: _on_buffer_changed(api, **kwargs))
    _tokens.extend([tok1, tok2])

    for buf in api.list_buffers():
        _baseline_by_buf_id[buf.buf_id] = _buffer_lines(buf)
        _update_signs(api, buf)


def teardown() -> None:
    """No-op: event tokens are cleaned up by PluginManager on unload."""


def _buffer_lines(buf: Any) -> list[str]:
    return [buf.get_line(i) for i in range(buf.line_count())]


def _buffer_by_id(api: Any, buf_id: int) -> Any | None:
    for buf in api.list_buffers():
        if buf.buf_id == buf_id:
            return buf
    return None


def _enabled(api: Any) -> bool:
    value = api.options.get("session_additions_enabled")
    return True if value is None else bool(value)


def _sign_char(api: Any) -> str:
    value = str(api.options.get("session_additions_sign_char") or "+")
    return value[:2] or "+"


def _sign_color(api: Any) -> tuple[int, int, int]:
    raw = str(api.options.get("session_additions_sign_color") or "80,200,80").strip()
    if raw.startswith("#") and len(raw) == 7:
        try:
            return tuple(int(raw[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
        except ValueError:
            return (80, 200, 80)
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) == 3:
        try:
            return tuple(max(0, min(255, int(part))) for part in parts)  # type: ignore[return-value]
        except ValueError:
            return (80, 200, 80)
    return (80, 200, 80)


def _register_sign_type(api: Any) -> None:
    from peovim.core.style import Style

    api.register_sign_type(_SIGN_TYPE, _sign_char(api), Style(fg=_sign_color(api)))


def _added_line_numbers(baseline: list[str], current: list[str]) -> list[int]:
    added: list[int] = []
    current_line = 0
    for entry in difflib.ndiff(baseline, current):
        prefix = entry[:2]
        if prefix == "  ":
            current_line += 1
        elif prefix == "+ ":
            added.append(current_line)
            current_line += 1
    return added


def _on_buffer_opened(api: Any, **kwargs: Any) -> None:
    buf_id = kwargs.get("buf_id")
    if buf_id is None:
        return
    buf = _buffer_by_id(api, buf_id)
    if buf is None:
        return
    _baseline_by_buf_id[buf_id] = _buffer_lines(buf)
    _update_signs(api, buf)


def _on_buffer_changed(api: Any, **kwargs: Any) -> None:
    buf_id = kwargs.get("buf_id")
    if buf_id is None:
        return
    buf = _buffer_by_id(api, buf_id)
    if buf is None:
        return
    _update_signs(api, buf)


def _update_signs(api: Any, buf: Any) -> None:
    """Recompute and place signs for lines added since buffer open."""
    _register_sign_type(api)
    buf.clear_namespace(_NAMESPACE)
    if not _enabled(api):
        return

    baseline = _baseline_by_buf_id.get(buf.buf_id)
    current = _buffer_lines(buf)
    if baseline is None:
        _baseline_by_buf_id[buf.buf_id] = current
        return

    for lineno in _added_line_numbers(baseline, current):
        buf.add_sign(_NAMESPACE, lineno, _SIGN_TYPE)
