"""
Convert tab characters to spaces on buffer open and before save.

This is intentionally blunt: any literal tab character in the buffer is
expanded using the effective tabstop for the active buffer/window.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI


def setup(api: EditorAPI) -> None:
    """Subscribe to open/save hooks and normalize existing buffers."""
    api.events.on("buffer_opened", lambda **kw: _on_buffer_opened(api, **kw))
    api.events.on("buffer_pre_save", lambda **kw: _on_buffer_pre_save(api, **kw))
    for buf in api.list_buffers():
        _normalize_buffer(api, buf)


def teardown() -> None:
    pass


def _on_buffer_opened(api: Any, **kwargs: Any) -> None:
    if not _event_enabled(api, "tabs_to_spaces_on_open", True):
        return
    buf_id = kwargs.get("buf_id")
    api.defer(lambda: _normalize_from_event(api, buf_id))


def _on_buffer_pre_save(api: Any, **kwargs: Any) -> None:
    if not _event_enabled(api, "tabs_to_spaces_on_save", True):
        return
    _normalize_from_event(api, kwargs.get("buf_id"))


def _normalize_from_event(api: Any, buf_id: int | None) -> None:
    if buf_id is None:
        return
    for buf in api.list_buffers():
        if buf.buf_id == buf_id:
            _normalize_buffer(api, buf)
            return


def _normalize_buffer(api: Any, buf: Any) -> bool:
    text = buf.get_text()
    if "\t" not in text:
        return False

    tabstop = _resolve_tabstop(api, buf)
    normalized = text.expandtabs(tabstop)
    if normalized == text:
        return False

    active_buf = _safe_active_buffer(api)
    active_win = _safe_active_window(api)
    next_cursor: tuple[int, int] | None = None
    if active_buf is not None and active_win is not None and active_buf.buf_id == buf.buf_id:
        next_cursor = _normalized_cursor(buf, active_win.cursor, tabstop)

    line_count = buf.line_count()
    last_line_index = max(0, line_count - 1)
    last_line_len = len(buf.get_line(last_line_index)) if line_count > 0 else 0
    buf.replace(0, 0, last_line_index, last_line_len, normalized)

    if next_cursor is not None and active_win is not None:
        active_win.set_cursor(*next_cursor)
    return True


def _event_enabled(api: Any, option_name: str, default: bool) -> bool:
    try:
        value = api.options.get(option_name)
    except Exception:
        return default
    if value is None:
        return default
    return bool(value)


def _resolve_tabstop(api: Any, buf: Any) -> int:
    active_buf = _safe_active_buffer(api)
    if active_buf is not None and active_buf.buf_id == buf.buf_id:
        active_win = _safe_active_window(api)
        if active_win is not None:
            try:
                value = active_win.get_option("tabstop")
                if value:
                    return int(value)
            except Exception:
                pass
    try:
        value = api.options.get("tabstop")
        if value:
            return int(value)
    except Exception:
        pass
    return 4


def _normalized_cursor(buf: Any, cursor: tuple[int, int], tabstop: int) -> tuple[int, int]:
    line, col = cursor
    if line < 0 or line >= buf.line_count():
        return (line, col)
    line_text = buf.get_line(line)
    display_col = len(line_text[:col].expandtabs(tabstop))
    expanded_line = line_text.expandtabs(tabstop)
    return (line, min(display_col, len(expanded_line)))


def _safe_active_buffer(api: Any) -> Any | None:
    try:
        return api.active_buffer()
    except Exception:
        return None


def _safe_active_window(api: Any) -> Any | None:
    try:
        return api.active_window()
    except Exception:
        return None
