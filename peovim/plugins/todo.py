"""
Highlight TODO/FIXME/HACK/NOTE/XXX/BUG/PERF/WARN keywords in comments;
provide :TodoList picker.

Implemented against the public peovim.api — no internal imports.
See notes/plugins.md for plugin development.

Usage in init.py:
    plugins.load('peovim.plugins.todo')

Configuration (via api.options before loading):
    options.set('todo_keywords', ['TODO', 'FIXME', 'HACK', 'NOTE', 'WARN'])
    options.set('todo_signs_enabled', True)
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

# ---------------------------------------------------------------------------
# Keyword → fg_color table
# ---------------------------------------------------------------------------

_KEYWORD_STYLES: dict[str, tuple[int, int, int]] = {
    "TODO": (255, 215, 0),
    "FIXME": (255, 90, 90),
    "BUG": (255, 90, 90),
    "HACK": (255, 165, 0),
    "WARN": (255, 165, 0),
    "NOTE": (100, 180, 255),
    "INFO": (100, 180, 255),
    "PERF": (190, 120, 255),
    "XXX": (220, 80, 220),
}

_NAMESPACE = "todo"
_DEBOUNCE_DELAY_SECONDS = 0.1

# ---------------------------------------------------------------------------
# Module-level state (reset each time setup() is called)
# ---------------------------------------------------------------------------

_tokens: list[int] = []
_debounce_timers: dict[int, Any] = {}


def setup(api: EditorAPI) -> None:
    """Register todo-comments plugin with the editor."""
    global _tokens
    _tokens = []
    _debounce_timers.clear()

    # Subscribe to buffer events
    tok1 = api.events.on("buffer_opened", lambda **kw: _on_immediate(api, **kw))
    tok2 = api.events.on("buffer_changed", lambda **kw: _on_debounced(api, **kw))
    _tokens.extend([tok1, tok2])

    # Register :TodoList command
    api.commands.register("TodoList", lambda cmd, ctx: _todo_list(api), min_abbrev=4)

    # Keybinding: <leader>xt → :TodoList
    api.keymap.define_plug("TodoList", lambda: _todo_list(api), desc="Todo: list all todos")
    api.keymap.nmap("<leader>xt", "<Plug>TodoList", desc="Todo: list all todos")

    # Scan already-open buffers
    for buf in api.list_buffers():
        _scan_buffer(api, buf)


def teardown() -> None:
    """Cancel pending debounce timers."""
    for handle in _debounce_timers.values():
        with contextlib.suppress(Exception):
            handle.cancel()
    _debounce_timers.clear()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_keywords(api: Any) -> list[str]:
    """Return the active keyword list from options, or the default set."""
    try:
        kws = api.options.get("todo_keywords")
        if isinstance(kws, list) and kws:
            return kws
    except Exception:
        pass
    return list(_KEYWORD_STYLES.keys())


def _on_immediate(api: Any, **kwargs: Any) -> None:
    """Handle immediate todo scans for buffer_opened."""
    buf_id: int | None = kwargs.get("buf_id")
    if buf_id is None:
        return
    for buf in api.list_buffers():
        if buf.buf_id == buf_id:
            _scan_buffer(api, buf)
            return


def _on_debounced(api: Any, **kwargs: Any) -> None:
    """Handle debounced todo scans for buffer_changed."""
    buf_id: int | None = kwargs.get("buf_id")
    if buf_id is None:
        return

    old = _debounce_timers.pop(buf_id, None)
    if old is not None:
        with contextlib.suppress(Exception):
            old.cancel()

    target_buf = None
    for buf in api.list_buffers():
        if buf.buf_id == buf_id:
            target_buf = buf
            break
    if target_buf is None:
        return

    try:
        loop = asyncio.get_event_loop()
        handle = loop.call_later(_DEBOUNCE_DELAY_SECONDS, lambda: _run_debounced_scan(api, target_buf))
        _debounce_timers[buf_id] = handle
    except RuntimeError:
        _scan_buffer(api, target_buf)


def _run_debounced_scan(api: Any, buf: Any) -> None:
    buf_id_val = getattr(buf, "buf_id", None)
    if buf_id_val is not None:
        _debounce_timers.pop(buf_id_val, None)
    _scan_buffer(api, buf)


def _scan_buffer(api: Any, buf: Any) -> None:
    """Scan all lines in buf and place highlights for TODO keywords."""
    from peovim.core.style import Style

    keywords = _get_keywords(api)
    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in keywords) + r")\b")
    buf.clear_namespace(_NAMESPACE)

    count = buf.line_count()
    for lineno in range(count):
        line = buf.get_line(lineno)
        for m in pattern.finditer(line):
            kw = m.group(1)
            col_start = m.start()
            col_end = m.end()
            color = _KEYWORD_STYLES.get(kw, (200, 200, 200))
            style = Style(fg=color)
            buf.add_highlight(_NAMESPACE, lineno, col_start, lineno, col_end, style)


def _todo_list(api: Any) -> None:
    """Open a picker showing all TODO items across open buffers."""
    items: list[dict[str, Any]] = []
    for buf in api.list_buffers():
        keywords = _get_keywords(api)
        pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in keywords) + r")\b")
        count = buf.line_count()
        for lineno in range(count):
            line = buf.get_line(lineno)
            for m in pattern.finditer(line):
                kw = m.group(1)
                path_str = str(buf.path) if buf.path else "<no file>"
                items.append(
                    {
                        "label": f"{path_str}:{lineno + 1}  [{kw}]  {line.strip()}",
                        "buf": buf,
                        "line": lineno,
                        "kw": kw,
                    }
                )

    def _on_confirm(item: dict[str, Any]) -> None:
        # Jump to the selected todo location
        try:
            win = api.active_window()
            win.set_cursor(item["line"], 0)
        except Exception:
            pass

    api.ui.open_picker(
        "Todo List",
        [it["label"] for it in items],
        on_confirm=lambda label: _on_confirm(next((i for i in items if i["label"] == label), {})),
    )
