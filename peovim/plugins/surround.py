"""
ys / cs / ds — add, change, and delete surrounding pairs.

Normal-mode bindings:
  ysiw(   surround inner word with ()     (space inside)
  ysiw)   surround inner word with ()     (no space inside)
  ysiw[   surround inner word with [ ]
  ysiw{   surround inner word with { }
  ysiw"   surround inner word with ""
  ysiw'   surround inner word with ''
  ysiw`   surround inner word with ``
  ysiw<t> surround inner word with <t>...</t>  (basic tag)

  cs("    change surrounding () to ""
  ds(     delete surrounding ()

The implementation uses get_line + replace; no feed_keys needed.

Implemented against the public peovim.api — no internal imports.
See notes/plugins.md for plugin development.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

# char → (open, close) pairs
_PAIRS: dict[str, tuple[str, str]] = {
    "(": ("( ", " )"),
    ")": ("(", ")"),
    "[": ("[ ", " ]"),
    "]": ("[", "]"),
    "{": ("{ ", " }"),
    "}": ("{", "}"),
    '"': ('"', '"'),
    "'": ("'", "'"),
    "`": ("`", "`"),
    "<": ("<", ">"),
    ">": ("<", ">"),
    "b": ("(", ")"),  # vim alias
    "B": ("{", "}"),  # vim alias
    "r": ("[", "]"),  # vim alias
}

# All openers/closers for finding existing surrounds
_ALL_OPENERS = {"(", "[", "{", '"', "'", "`", "<"}
_CLOSER_MAP = {"(": ")", "[": "]", "{": "}", "<": ">"}
_OPENER_MAP = {v: k for k, v in _CLOSER_MAP.items()}
_SYMMETRIC = {'"', "'", "`"}


def setup(api: EditorAPI) -> None:
    """Register ys*, cs*, ds* normal-mode bindings."""
    # ysiw<char> — surround inner word
    for char in list(_PAIRS.keys()) + ['"', "'"]:
        c = char

        def _make_ysiw(ch: str = c) -> Any:
            return lambda ctx: _ysiw(ctx, api, ch)

        api.keymap.nmap(f"ysiw{char}", _make_ysiw(), desc=f"Surround: add {char}")

    # cs<old><new> — change surround
    for old in list(_PAIRS.keys()):
        for new in list(_PAIRS.keys()):
            o, n = old, new

            def _make_cs(old_ch: str = o, new_ch: str = n) -> Any:
                return lambda ctx: _cs(ctx, api, old_ch, new_ch)

            api.keymap.nmap(f"cs{old}{new}", _make_cs(), desc=f"Surround: change {old}→{new}")

    # ds<char> — delete surround
    for char in list(_PAIRS.keys()):
        c = char

        def _make_ds(ch: str = c) -> Any:
            return lambda ctx: _ds(ctx, api, ch)

        api.keymap.nmap(f"ds{char}", _make_ds(), desc=f"Surround: delete {char}")


def teardown() -> None:
    pass


# ---------------------------------------------------------------------------
# Core operations (public for testing)
# ---------------------------------------------------------------------------


def surround_word(buf: Any, line: int, col: int, open_str: str, close_str: str) -> None:
    """Surround the word under (line, col) with open_str...close_str."""
    text = buf.get_line(line)
    # Find word boundaries
    word_start = col
    while word_start > 0 and (text[word_start - 1].isalnum() or text[word_start - 1] == "_"):
        word_start -= 1
    word_end = col
    while word_end < len(text) and (text[word_end].isalnum() or text[word_end] == "_"):
        word_end += 1
    if word_start == word_end:
        # Fallback: surround single character
        word_end = min(col + 1, len(text))
        word_start = col
    new_text = text[:word_start] + open_str + text[word_start:word_end] + close_str + text[word_end:]
    buf.replace(line, 0, line, len(text), new_text)


def change_surround(buf: Any, line: int, old_open: str, old_close: str, new_open: str, new_close: str) -> bool:
    """
    Find old_open...old_close on line and replace with new_open...new_close.
    Returns True if a substitution was made.
    """
    text = buf.get_line(line)
    start = text.find(old_open)
    if start == -1:
        return False
    # Find matching closer from after the opener
    search_from = start + len(old_open)
    end = text.find(old_close, search_from)
    if end == -1:
        return False
    new_text = text[:start] + new_open + text[start + len(old_open) : end] + new_close + text[end + len(old_close) :]
    buf.replace(line, 0, line, len(text), new_text)
    return True


def delete_surround(buf: Any, line: int, open_str: str, close_str: str) -> bool:
    """Remove the nearest open_str...close_str pair on line. Returns True on success."""
    text = buf.get_line(line)
    start = text.find(open_str)
    if start == -1:
        return False
    search_from = start + len(open_str)
    end = text.find(close_str, search_from)
    if end == -1:
        return False
    new_text = text[:start] + text[start + len(open_str) : end] + text[end + len(close_str) :]
    buf.replace(line, 0, line, len(text), new_text)
    return True


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _ysiw(ctx: Any, api: Any, char: str) -> None:
    try:
        buf = api.active_buffer()
        line, col = ctx.cursor
        open_str, close_str = _PAIRS.get(char, (char, char))
        surround_word(buf, line, col, open_str, close_str)
    except Exception:
        pass


def _cs(ctx: Any, api: Any, old_char: str, new_char: str) -> None:
    try:
        buf = api.active_buffer()
        line, _ = ctx.cursor
        old_open, old_close = _PAIRS.get(old_char, (old_char, old_char))
        new_open, new_close = _PAIRS.get(new_char, (new_char, new_char))
        change_surround(buf, line, old_open, old_close, new_open, new_close)
    except Exception:
        pass


def _ds(ctx: Any, api: Any, char: str) -> None:
    try:
        buf = api.active_buffer()
        line, _ = ctx.cursor
        open_str, close_str = _PAIRS.get(char, (char, char))
        delete_surround(buf, line, open_str, close_str)
    except Exception:
        pass
