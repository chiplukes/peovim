"""plugins.vcssigns — VCS-agnostic sign placement and hunk navigation.

Shared helpers used by gitsigns and svnsigns. Each VCS plugin supplies a
``get_hunks_fn`` callable that returns ``[{"type", "start", "end"}, ...]``
(0-based line numbers); this module provides the VCS-agnostic implementations.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any


def register_sign_defs(api: Any, sign_defs: dict[str, tuple[str, tuple[int, int, int]]]) -> None:
    """Register sign types from a ``{name: (char, color)}`` dict.

    Uses ``contextlib.suppress`` so duplicate registration is idempotent.
    """
    from peovim.core.style import Style

    for name, (char, color) in sign_defs.items():
        with contextlib.suppress(Exception):
            api.register_sign_type(name, char, Style(fg=color))


def update_signs(
    api: Any,
    buf: Any,
    namespace: str,
    hunk_type_to_sign: dict[str, str],
    get_hunks_fn: Callable,
) -> None:
    """Clear *namespace* then place one sign per hunk line from *get_hunks_fn*."""
    buf.clear_namespace(namespace)
    if buf.path is None:
        return
    try:
        hunks = get_hunks_fn(buf.path)
    except Exception:
        return
    for hunk in hunks:
        sign_type = hunk_type_to_sign.get(hunk.get("type", ""), f"{namespace}.change")
        start = hunk.get("start", 0)
        end = hunk.get("end", start)
        for lineno in range(start, end + 1):
            buf.add_sign(namespace, lineno, sign_type)


def current_hunks(api: Any, get_hunks_fn: Callable) -> list[dict]:
    """Return hunks for the active buffer, or [] on any error."""
    try:
        buf = api.active_buffer()
        if buf.path is None:
            return []
        return get_hunks_fn(buf.path)
    except Exception:
        return []


def next_hunk(api: Any, get_hunks_fn: Callable) -> None:
    """Move cursor to the start of the next hunk after the current line."""
    try:
        win = api.active_window()
        cursor_line = win.cursor[0]
        for hunk in sorted(current_hunks(api, get_hunks_fn), key=lambda h: h["start"]):
            if hunk["start"] > cursor_line:
                win.set_cursor(hunk["start"], 0)
                return
    except Exception:
        pass


def prev_hunk(api: Any, get_hunks_fn: Callable) -> None:
    """Move cursor to the start of the previous hunk before the current line."""
    try:
        win = api.active_window()
        cursor_line = win.cursor[0]
        for hunk in sorted(current_hunks(api, get_hunks_fn), key=lambda h: h["start"], reverse=True):
            if hunk["start"] < cursor_line:
                win.set_cursor(hunk["start"], 0)
                return
    except Exception:
        pass
