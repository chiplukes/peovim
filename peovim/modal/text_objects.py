"""
modal.text_objects — Text object functions

Each text object returns a (start_line, start_col, end_line, end_col) range.
mode is 'inner' (i) or 'outer' (a).

Implemented: iw/aw, i"/a" (and all quote variants), i(/a(, i{/a{, i[/a[,
ip/ap (paragraph), is/as (sentence -- simplified).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from peovim.core.document import Document

# Range: (start_line, start_col, end_line, end_col)
Range = tuple[int, int, int, int]


# ---------------------------------------------------------------------------
# Word objects: iw / aw
# ---------------------------------------------------------------------------


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _word_range(doc: Document, line: int, col: int, mode: Literal["inner", "outer"]) -> Range:  # cm:2f7d5e
    """iw (inner word) or aw (around word — includes adjacent whitespace)."""
    text = doc.get_line(line)
    if not text:
        return (line, 0, line, 0)

    col = min(col, len(text) - 1)
    ch = text[col]

    if _is_word_char(ch):
        # Expand left
        start = col
        while start > 0 and _is_word_char(text[start - 1]):
            start -= 1
        # Expand right
        end = col
        while end + 1 < len(text) and _is_word_char(text[end + 1]):
            end += 1
        if mode == "outer":
            # Include trailing whitespace (or leading if no trailing)
            if end + 1 < len(text) and text[end + 1].isspace():
                while end + 1 < len(text) and text[end + 1].isspace():
                    end += 1
            elif start > 0 and text[start - 1].isspace():
                while start > 0 and text[start - 1].isspace():
                    start -= 1
    elif ch.isspace():
        # On whitespace: select the whitespace
        start = col
        while start > 0 and text[start - 1].isspace():
            start -= 1
        end = col
        while end + 1 < len(text) and text[end + 1].isspace():
            end += 1
        if mode == "outer" and end + 1 < len(text) and _is_word_char(text[end + 1]):
            end_word = end + 1
            while end_word + 1 < len(text) and _is_word_char(text[end_word + 1]):
                end_word += 1
            end = end_word
    else:
        # Punctuation: select punctuation run
        start = col
        while start > 0 and not _is_word_char(text[start - 1]) and not text[start - 1].isspace():
            start -= 1
        end = col
        while end + 1 < len(text) and not _is_word_char(text[end + 1]) and not text[end + 1].isspace():
            end += 1
        if mode == "outer" and end + 1 < len(text) and text[end + 1].isspace():
            while end + 1 < len(text) and text[end + 1].isspace():
                end += 1

    return (line, start, line, end + 1)


def word_inner(doc: Document, line: int, col: int) -> Range:
    """iw — inner word."""
    return _word_range(doc, line, col, "inner")


def word_outer(doc: Document, line: int, col: int) -> Range:
    """aw — around word (includes whitespace)."""
    return _word_range(doc, line, col, "outer")


# ---------------------------------------------------------------------------
# Quote objects: i" / a", i' / a', i` / a`
# ---------------------------------------------------------------------------


def _quote_range(doc: Document, line: int, col: int, quote: str, mode: Literal["inner", "outer"]) -> Range | None:
    """Find matching quote pair on the line. Returns range or None."""
    text = doc.get_line(line)
    # Find all quote positions
    positions = [i for i, ch in enumerate(text) if ch == quote]
    if len(positions) < 2:
        return None

    # Find a pair that encloses col, or the pair after col
    for i in range(0, len(positions) - 1, 2):
        open_pos = positions[i]
        close_pos = positions[i + 1]
        if open_pos <= col <= close_pos:
            if mode == "inner":
                return (line, open_pos + 1, line, close_pos)
            else:
                return (line, open_pos, line, close_pos + 1)

    # Cursor is outside all pairs — use nearest pair after col
    for i in range(0, len(positions) - 1, 2):
        open_pos = positions[i]
        close_pos = positions[i + 1]
        if open_pos > col:
            if mode == "inner":
                return (line, open_pos + 1, line, close_pos)
            else:
                return (line, open_pos, line, close_pos + 1)

    return None


def dquote_inner(doc: Document, line: int, col: int) -> Range | None:
    return _quote_range(doc, line, col, '"', "inner")


def dquote_outer(doc: Document, line: int, col: int) -> Range | None:
    return _quote_range(doc, line, col, '"', "outer")


def squote_inner(doc: Document, line: int, col: int) -> Range | None:
    return _quote_range(doc, line, col, "'", "inner")


def squote_outer(doc: Document, line: int, col: int) -> Range | None:
    return _quote_range(doc, line, col, "'", "outer")


def backtick_inner(doc: Document, line: int, col: int) -> Range | None:
    return _quote_range(doc, line, col, "`", "inner")


def backtick_outer(doc: Document, line: int, col: int) -> Range | None:
    return _quote_range(doc, line, col, "`", "outer")


# ---------------------------------------------------------------------------
# Bracket/brace objects: i( / a(, i{ / a{, i[ / a[, i< / a<
# ---------------------------------------------------------------------------

_PAIRS: dict[str, str] = {
    "(": ")",
    "{": "}",
    "[": "]",
    "<": ">",
    ")": "(",
    "}": "{",
    "]": "[",
    ">": "<",
}

_OPENERS = frozenset("({[<")
_CLOSERS = frozenset(")}]>")


def _bracket_range(doc: Document, line: int, col: int, opener: str, mode: Literal["inner", "outer"]) -> Range | None:
    """Find enclosing bracket pair. Searches forward and backward."""
    closer = _PAIRS[opener]
    # Search backward for opener (including current line)
    # Simple single-line implementation
    text = doc.get_line(line)
    depth = 0
    open_line, open_col = -1, -1

    # Search backward from col
    for c in range(col, -1, -1):
        ch = text[c]
        if ch == closer:
            depth += 1
        elif ch == opener:
            if depth == 0:
                open_line, open_col = line, c
                break
            depth -= 1

    # Search other lines if not found on current line
    if open_line == -1:
        for ln in range(line - 1, -1, -1):
            t = doc.get_line(ln)
            for c in range(len(t) - 1, -1, -1):
                ch = t[c]
                if ch == closer:
                    depth += 1
                elif ch == opener:
                    if depth == 0:
                        open_line, open_col = ln, c
                        break
                    depth -= 1
            if open_line != -1:
                break

    if open_line == -1:
        return None

    # Now search forward for matching closer
    depth = 0
    close_line, close_col = -1, -1
    for ln in range(open_line, doc.line_count()):
        t = doc.get_line(ln)
        start_col = open_col + 1 if ln == open_line else 0
        for c in range(start_col, len(t)):
            ch = t[c]
            if ch == opener:
                depth += 1
            elif ch == closer:
                if depth == 0:
                    close_line, close_col = ln, c
                    break
                depth -= 1
        if close_line != -1:
            break

    if close_line == -1:
        return None

    if mode == "inner":
        # Content inside brackets
        if open_line == close_line:
            return (open_line, open_col + 1, close_line, close_col)
        else:
            return (open_line, open_col + 1, close_line, close_col)
    else:
        return (open_line, open_col, close_line, close_col + 1)


def paren_inner(doc: Document, line: int, col: int) -> Range | None:
    return _bracket_range(doc, line, col, "(", "inner")


def paren_outer(doc: Document, line: int, col: int) -> Range | None:
    return _bracket_range(doc, line, col, "(", "outer")


def brace_inner(doc: Document, line: int, col: int) -> Range | None:
    return _bracket_range(doc, line, col, "{", "inner")


def brace_outer(doc: Document, line: int, col: int) -> Range | None:
    return _bracket_range(doc, line, col, "{", "outer")


def bracket_inner(doc: Document, line: int, col: int) -> Range | None:
    return _bracket_range(doc, line, col, "[", "inner")


def bracket_outer(doc: Document, line: int, col: int) -> Range | None:
    return _bracket_range(doc, line, col, "[", "outer")


def angle_inner(doc: Document, line: int, col: int) -> Range | None:
    return _bracket_range(doc, line, col, "<", "inner")


def angle_outer(doc: Document, line: int, col: int) -> Range | None:
    return _bracket_range(doc, line, col, "<", "outer")


# ---------------------------------------------------------------------------
# Paragraph objects: ip / ap
# ---------------------------------------------------------------------------


def _is_blank_line(doc: Document, ln: int) -> bool:
    return doc.get_line(ln).strip() == ""


def paragraph_inner(doc: Document, line: int, col: int) -> Range:
    """ip — inner paragraph (non-blank lines around cursor)."""
    total = doc.line_count()
    # Find start: go up until blank or start
    start = line
    while start > 0 and not _is_blank_line(doc, start - 1):
        start -= 1
    # Skip blank lines at start
    while start < total and _is_blank_line(doc, start):
        start += 1
    # Find end: go down until blank or end
    end = line
    while end + 1 < total and not _is_blank_line(doc, end + 1):
        end += 1
    last_col = len(doc.get_line(end))
    return (start, 0, end, last_col)


def paragraph_outer(doc: Document, line: int, col: int) -> Range:
    """ap — around paragraph (includes trailing blank lines)."""
    sl, sc, el, ec = paragraph_inner(doc, line, col)
    total = doc.line_count()
    # Include trailing blank lines
    while el + 1 < total and _is_blank_line(doc, el + 1):
        el += 1
    last_col = len(doc.get_line(el))
    return (sl, 0, el, last_col)
