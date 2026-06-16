"""
Auto-close brackets and quotes in insert mode.

When an opening delimiter is typed, inserts the matching closing delimiter
and leaves the cursor between them. When <BS> is pressed inside an empty
pair, deletes both characters.

Implemented against the public peovim.api — no internal imports.
See notes/plugins.md for plugin development.

Pairs: () [] {} "" '' ``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

# opener → closer
_PAIRS: dict[str, str] = {
    "(": ")",
    "[": "]",
    "{": "}",
    '"': '"',
    "'": "'",
    "`": "`",
}

# All closing chars (for skip-over logic)
_CLOSERS: set[str] = set(_PAIRS.values())


def setup(api: EditorAPI) -> None:
    """Register insert-mode bindings for all auto-pair characters."""
    for opener, closer in _PAIRS.items():
        _register_opener(api, opener, closer)
        # Skip-over: typing a closer when it's already the next char
        _register_closer(api, closer)

    # Smart backspace: delete both chars when inside empty pair
    api.keymap.imap("<BS>", lambda: _backspace(api), desc="Autopairs: smart backspace")


def teardown() -> None:
    pass


# ---------------------------------------------------------------------------
# Binding helpers
# ---------------------------------------------------------------------------


def _register_opener(api: Any, opener: str, closer: str) -> None:
    def _handler(o: str = opener, c: str = closer) -> None:
        _insert_pair(api, o, c)

    api.keymap.imap(opener, _handler, desc=f"Autopairs: {opener}{closer}")


def _register_closer(api: Any, closer: str) -> None:
    def _handler(c: str = closer) -> None:
        _skip_or_insert(api, c)

    # Only register if closer isn't also an opener that was already handled
    # (quotes are both; their opener handler already registered this key)
    if closer not in _PAIRS:
        api.keymap.imap(closer, _handler, desc=f"Autopairs: skip {closer}")


# ---------------------------------------------------------------------------
# Core operations (also used by tests directly)
# ---------------------------------------------------------------------------


def _insert_pair(api: Any, opener: str, closer: str) -> None:
    """Insert opener + closer and leave cursor between them."""
    try:
        win = api.active_window()
        buf = api.active_buffer()
        line, col = win.cursor
        # Insert the pair: "opener" at col, then "closer" at col+1
        buf.insert(line, col, opener + closer)
        win.set_cursor(line, col + 1)
    except Exception:
        # Fallback: just insert the opener character
        try:
            win = api.active_window()
            buf = api.active_buffer()
            line, col = win.cursor
            buf.insert(line, col, opener)
            win.set_cursor(line, col + 1)
        except Exception:
            pass


def _skip_or_insert(api: Any, closer: str) -> None:
    """If char at cursor is already 'closer', skip over it; else insert it."""
    try:
        win = api.active_window()
        buf = api.active_buffer()
        line, col = win.cursor
        line_text = buf.get_line(line)
        if col < len(line_text) and line_text[col] == closer:
            win.set_cursor(line, col + 1)
        else:
            buf.insert(line, col, closer)
            win.set_cursor(line, col + 1)
    except Exception:
        pass


def _backspace(api: Any) -> None:
    """Delete the char before cursor; if inside empty pair, delete both."""
    try:
        win = api.active_window()
        buf = api.active_buffer()
        line, col = win.cursor
        if col == 0:
            # Join with previous line — standard behaviour; feed <BS> as normal
            api.keymap.feed_keys("<BS>", remap=False)
            return
        line_text = buf.get_line(line)
        prev_char = line_text[col - 1] if col > 0 else ""
        next_char = line_text[col] if col < len(line_text) else ""
        if prev_char in _PAIRS and _PAIRS[prev_char] == next_char:
            # Inside empty pair: delete both
            buf.delete(line, col - 1, line, col + 1)
            win.set_cursor(line, col - 1)
        else:
            # Normal backspace
            buf.delete(line, col - 1, line, col)
            win.set_cursor(line, col - 1)
    except Exception:
        pass
