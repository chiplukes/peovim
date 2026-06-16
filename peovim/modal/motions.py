"""
modal.motions — All Vim motion functions

Each motion returns a new (line, col) given the current (line, col), count,
and document. Motions are composable with operators and usable standalone.

Signature: motion(doc, line, col, count=1) -> (line, col)

Implemented: h/l, j/k, w/W/b/B/e/E, 0/^/$/g_,
             f/F/t/T, {/}/(/)/(, H/M/L, ge/gE, +/-
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.core.document import Document


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

# (doc, line, col, count) -> (line, col)
# All motions follow this signature.


# ---------------------------------------------------------------------------
# Simple character motions
# ---------------------------------------------------------------------------


def move_h(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:  # cm:c6b4a9
    """Move left count characters."""
    return (line, max(0, col - count))


def move_l(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """Move right count characters (does NOT wrap to next line)."""
    line_len = len(doc.get_line(line))
    # In normal mode cursor can't go past last char; max_col is line_len-1 (or 0)
    max_col = max(0, line_len - 1)
    return (line, min(col + count, max_col))


def move_j(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """Move down count lines."""
    new_line = min(line + count, doc.line_count() - 1)
    return (new_line, col)


def move_k(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """Move up count lines."""
    new_line = max(0, line - count)
    return (new_line, col)


# ---------------------------------------------------------------------------
# Line beginning / end motions
# ---------------------------------------------------------------------------


def move_line_start(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """0 — move to column 0."""
    return (line, 0)


def move_first_nonblank(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """^ — move to first non-blank character on line."""
    text = doc.get_line(line)
    stripped = text.lstrip()
    indent = len(text) - len(stripped)
    return (line, indent)


def move_line_end(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """$ — move to end of line (last char, not after it)."""
    target_line = min(line + count - 1, doc.line_count() - 1) if count > 1 else line
    text = doc.get_line(target_line)
    eol = max(0, len(text) - 1)
    return (target_line, eol)


def move_last_nonblank(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """g_ — move to last non-blank character on line."""
    text = doc.get_line(line)
    stripped = text.rstrip()
    last_nonblank = max(0, len(stripped) - 1)
    return (line, last_nonblank)


# ---------------------------------------------------------------------------
# Word motions
# ---------------------------------------------------------------------------


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _is_bigword_separator(ch: str) -> bool:
    return ch == " " or ch == "\t"


def move_w(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """w — move forward to start of next word (small word)."""
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _w_once(doc, cur_line, cur_col)
    return (cur_line, cur_col)


def _w_once(doc: Document, line: int, col: int) -> tuple[int, int]:
    """Move forward one word (w)."""
    total_lines = doc.line_count()
    text = doc.get_line(line)

    # Skip current word chars
    if col < len(text) and _is_word_char(text[col]):
        while col < len(text) and _is_word_char(text[col]):
            col += 1
    elif col < len(text) and not text[col].isspace():
        # Skip punctuation
        while col < len(text) and not _is_word_char(text[col]) and not text[col].isspace():
            col += 1

    # Skip whitespace (possibly across lines)
    while True:
        text = doc.get_line(line)
        while col < len(text) and text[col].isspace():
            col += 1
        if col < len(text):
            break
        # Move to next line
        if line + 1 >= total_lines:
            return (line, max(0, len(text) - 1))
        line += 1
        col = 0

    return (line, col)


def move_W(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """W — move forward to start of next WORD (whitespace-delimited)."""
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _W_once(doc, cur_line, cur_col)
    return (cur_line, cur_col)


def _W_once(doc: Document, line: int, col: int) -> tuple[int, int]:
    total_lines = doc.line_count()
    text = doc.get_line(line)

    # Skip non-whitespace
    while col < len(text) and not text[col].isspace():
        col += 1

    # Skip whitespace (possibly across lines)
    while True:
        text = doc.get_line(line)
        while col < len(text) and text[col].isspace():
            col += 1
        if col < len(text):
            break
        if line + 1 >= total_lines:
            return (line, max(0, len(text) - 1))
        line += 1
        col = 0

    return (line, col)


def move_e(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """e — move forward to end of current/next word."""
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _e_once(doc, cur_line, cur_col)
    return (cur_line, cur_col)


def _e_once(doc: Document, line: int, col: int) -> tuple[int, int]:
    total_lines = doc.line_count()

    # Step forward one to avoid counting current char as word-end
    col += 1
    text = doc.get_line(line)
    if col >= len(text):
        if line + 1 >= total_lines:
            return (line, max(0, len(text) - 1))
        line += 1
        col = 0
        text = doc.get_line(line)

    # Skip whitespace
    while col < len(text) and text[col].isspace():
        col += 1
        if col >= len(text):
            if line + 1 >= total_lines:
                return (line, max(0, len(text) - 1))
            line += 1
            col = 0
            text = doc.get_line(line)

    text = doc.get_line(line)
    if col >= len(text):
        return (line, max(0, len(text) - 1))

    # Skip to end of word/WORD
    if _is_word_char(text[col]):
        while col + 1 < len(text) and _is_word_char(text[col + 1]):
            col += 1
    else:
        while col + 1 < len(text) and not _is_word_char(text[col + 1]) and not text[col + 1].isspace():
            col += 1

    return (line, col)


def move_E(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """E — move to end of current/next WORD (whitespace-delimited)."""
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _E_once(doc, cur_line, cur_col)
    return (cur_line, cur_col)


def _E_once(doc: Document, line: int, col: int) -> tuple[int, int]:
    total_lines = doc.line_count()
    col += 1
    text = doc.get_line(line)
    if col >= len(text):
        if line + 1 >= total_lines:
            return (line, max(0, len(text) - 1))
        line += 1
        col = 0
        text = doc.get_line(line)

    # Skip whitespace
    while col < len(text) and text[col].isspace():
        col += 1
        if col >= len(text):
            if line + 1 >= total_lines:
                return (line, max(0, len(text) - 1))
            line += 1
            col = 0
            text = doc.get_line(line)

    text = doc.get_line(line)
    if col >= len(text):
        return (line, max(0, len(text) - 1))

    while col + 1 < len(text) and not text[col + 1].isspace():
        col += 1

    return (line, col)


def move_b(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """b — move backward to start of word."""
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _b_once(doc, cur_line, cur_col)
    return (cur_line, cur_col)


def _b_once(doc: Document, line: int, col: int) -> tuple[int, int]:
    col -= 1
    if col < 0:
        if line == 0:
            return (0, 0)
        line -= 1
        text = doc.get_line(line)
        col = max(0, len(text) - 1)

    text = doc.get_line(line)
    # Skip whitespace backwards
    while col >= 0 and (col >= len(text) or text[col].isspace()):
        col -= 1
        if col < 0:
            if line == 0:
                return (0, 0)
            line -= 1
            text = doc.get_line(line)
            col = max(0, len(text) - 1)

    text = doc.get_line(line)
    if col < 0 or col >= len(text):
        return (line, 0)

    # Skip word chars backwards
    if _is_word_char(text[col]):
        while col > 0 and _is_word_char(text[col - 1]):
            col -= 1
    else:
        while col > 0 and not _is_word_char(text[col - 1]) and not text[col - 1].isspace():
            col -= 1

    return (line, col)


def move_B(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """B — move backward to start of WORD."""
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _B_once(doc, cur_line, cur_col)
    return (cur_line, cur_col)


def _B_once(doc: Document, line: int, col: int) -> tuple[int, int]:
    col -= 1
    if col < 0:
        if line == 0:
            return (0, 0)
        line -= 1
        text = doc.get_line(line)
        col = max(0, len(text) - 1)

    text = doc.get_line(line)
    # Skip whitespace backwards
    while col >= 0 and (col >= len(text) or text[col].isspace()):
        col -= 1
        if col < 0:
            if line == 0:
                return (0, 0)
            line -= 1
            text = doc.get_line(line)
            col = max(0, len(text) - 1)

    text = doc.get_line(line)
    if col < 0 or col >= len(text):
        return (line, 0)

    while col > 0 and not text[col - 1].isspace():
        col -= 1

    return (line, col)


def move_ge(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """ge — move backward to end of previous word."""
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _ge_once(doc, cur_line, cur_col)
    return (cur_line, cur_col)


def _ge_once(doc: Document, line: int, col: int) -> tuple[int, int]:
    col -= 1
    if col < 0:
        if line == 0:
            return (0, 0)
        line -= 1
        text = doc.get_line(line)
        col = max(0, len(text) - 1)

    text = doc.get_line(line)
    # Skip whitespace backwards
    while col >= 0 and (col >= len(text) or text[col].isspace()):
        col -= 1
        if col < 0:
            if line == 0:
                return (0, 0)
            line -= 1
            text = doc.get_line(line)
            col = max(0, len(text) - 1)

    return (line, col)


def move_gE(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """gE — move backward to end of previous WORD."""
    return move_ge(doc, line, col, count)  # same logic for basic implementation


# ---------------------------------------------------------------------------
# Find char motions
# ---------------------------------------------------------------------------


def move_f(doc: Document, line: int, col: int, char: str, count: int = 1) -> tuple[int, int]:
    """f{char} — move to next occurrence of char on line."""
    text = doc.get_line(line)
    found = 0
    pos = col + 1
    while pos < len(text):
        if text[pos] == char:
            found += 1
            if found == count:
                return (line, pos)
        pos += 1
    return (line, col)  # not found — stay


def move_F(doc: Document, line: int, col: int, char: str, count: int = 1) -> tuple[int, int]:
    """F{char} — move to previous occurrence of char on line."""
    text = doc.get_line(line)
    found = 0
    pos = col - 1
    while pos >= 0:
        if text[pos] == char:
            found += 1
            if found == count:
                return (line, pos)
        pos -= 1
    return (line, col)  # not found — stay


def move_t(doc: Document, line: int, col: int, char: str, count: int = 1) -> tuple[int, int]:
    """t{char} — move to one before next occurrence of char on line."""
    new_line, new_col = move_f(doc, line, col, char, count)
    if (new_line, new_col) != (line, col):
        return (new_line, max(col, new_col - 1))
    return (line, col)


def move_T(doc: Document, line: int, col: int, char: str, count: int = 1) -> tuple[int, int]:
    """T{char} — move to one after previous occurrence of char on line."""
    new_line, new_col = move_F(doc, line, col, char, count)
    if (new_line, new_col) != (line, col):
        return (new_line, min(col, new_col + 1))
    return (line, col)


# ---------------------------------------------------------------------------
# Paragraph / sentence motions
# ---------------------------------------------------------------------------


def move_paragraph_forward(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """} — move to next empty line (paragraph boundary)."""
    total = doc.line_count()
    cur = line
    for _ in range(count):
        # Skip any current blank lines
        while cur < total and doc.get_line(cur).strip() == "":
            cur += 1
        # Skip to next blank line
        while cur < total and doc.get_line(cur).strip() != "":
            cur += 1
        if cur >= total:
            cur = total - 1
            break
    return (cur, 0)


def move_paragraph_backward(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """{ — move to previous empty line (paragraph boundary)."""
    cur = line
    for _ in range(count):
        # Skip any current blank lines
        while cur > 0 and doc.get_line(cur).strip() == "":
            cur -= 1
        # Skip to previous blank line
        while cur > 0 and doc.get_line(cur).strip() != "":
            cur -= 1
        if cur <= 0:
            cur = 0
            break
    return (cur, 0)


# ---------------------------------------------------------------------------
# Screen position motions (H/M/L)
# ---------------------------------------------------------------------------


def move_H(
    doc: Document, line: int, col: int, count: int = 1, scroll_line: int = 0, window_height: int = 24
) -> tuple[int, int]:
    """H — move to top of screen (+ count - 1 lines)."""
    target = min(scroll_line + count - 1, doc.line_count() - 1)
    return (target, 0)


def move_M(
    doc: Document, line: int, col: int, count: int = 1, scroll_line: int = 0, window_height: int = 24
) -> tuple[int, int]:
    """M — move to middle of screen."""
    visible_last = min(scroll_line + window_height - 1, doc.line_count() - 1)
    mid = (scroll_line + visible_last) // 2
    return (mid, 0)


def move_L(
    doc: Document, line: int, col: int, count: int = 1, scroll_line: int = 0, window_height: int = 24
) -> tuple[int, int]:
    """L — move to bottom of screen (- count + 1 lines)."""
    visible_last = min(scroll_line + window_height - 1, doc.line_count() - 1)
    target = max(scroll_line, visible_last - count + 1)
    return (target, 0)


# ---------------------------------------------------------------------------
# Line-oriented motions
# ---------------------------------------------------------------------------


def move_plus(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """+ / <CR> — move down to first non-blank on next line."""
    new_line = min(line + count, doc.line_count() - 1)
    text = doc.get_line(new_line)
    stripped = text.lstrip()
    indent = len(text) - len(stripped)
    return (new_line, indent)


def move_minus(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """- — move up to first non-blank on previous line."""
    new_line = max(0, line - count)
    text = doc.get_line(new_line)
    stripped = text.lstrip()
    indent = len(text) - len(stripped)
    return (new_line, indent)


# ---------------------------------------------------------------------------
# Sentence motions
# ---------------------------------------------------------------------------


def move_sentence_forward(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """() — move forward to next sentence end."""
    total = doc.line_count()
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _sentence_forward(doc, cur_line, cur_col, total)
    return (cur_line, cur_col)


def _sentence_forward(doc: Document, line: int, col: int, total: int) -> tuple[int, int]:
    # Step past current position
    col += 1
    text = doc.get_line(line)
    while True:
        while col < len(text):
            if text[col] in ".!?" and (col + 1 >= len(text) or text[col + 1].isspace()):
                # Move to start of next sentence (skip whitespace)
                col += 1
                while col < len(text) and text[col].isspace():
                    col += 1
                if col < len(text):
                    return (line, col)
                # whitespace at end of line — fall to next line
                break
            col += 1
        if line + 1 >= total:
            return (line, max(0, len(text) - 1))
        line += 1
        col = 0
        text = doc.get_line(line)
        # Skip leading whitespace on new line
        while col < len(text) and text[col].isspace():
            col += 1
        if col < len(text):
            return (line, col)


def move_sentence_backward(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """( — move backward to start of sentence."""
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _sentence_backward(doc, cur_line, cur_col)
    return (cur_line, cur_col)


def _sentence_backward(doc: Document, line: int, col: int) -> tuple[int, int]:
    col -= 1
    if col < 0:
        if line == 0:
            return (0, 0)
        line -= 1
        text = doc.get_line(line)
        col = max(0, len(text) - 1)
    else:
        text = doc.get_line(line)

    # Skip whitespace backwards
    while col >= 0 and (col >= len(text) or text[col].isspace()):
        col -= 1
        if col < 0:
            if line == 0:
                return (0, 0)
            line -= 1
            text = doc.get_line(line)
            col = max(0, len(text) - 1)

    # Now find the previous sentence terminator
    while True:
        while col >= 0:
            if col < len(text) and text[col] in ".!?":
                # Found a terminator; next sentence starts after whitespace
                nc = col + 1
                t = doc.get_line(line)
                while nc < len(t) and t[nc].isspace():
                    nc += 1
                if nc < len(t):
                    return (line, nc)
                if line + 1 < doc.line_count():
                    nl = line + 1
                    nt = doc.get_line(nl)
                    nc2 = 0
                    while nc2 < len(nt) and nt[nc2].isspace():
                        nc2 += 1
                    return (nl, nc2)
            col -= 1
        if line == 0:
            return (0, 0)
        line -= 1
        text = doc.get_line(line)
        col = max(0, len(text) - 1)


# ---------------------------------------------------------------------------
# Section motions (]]  [[  ][  [])
# ---------------------------------------------------------------------------


def move_section_forward(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """]] — move forward to next { in column 0."""
    total = doc.line_count()
    cur = line + 1
    found = 0
    while cur < total:
        t = doc.get_line(cur)
        if t and t[0] == "{":
            found += 1
            if found == count:
                return (cur, 0)
        cur += 1
    # No section found — go to last non-empty line so cursor doesn't land on a
    # blank trailing newline (which would produce an empty yw and clobber the register).
    last = total - 1
    while last > 0 and not doc.get_line(last):
        last -= 1
    return (last, 0)


def move_section_backward(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """[[ — move backward to previous { in column 0."""
    cur = line - 1
    found = 0
    while cur >= 0:
        t = doc.get_line(cur)
        if t and t[0] == "{":
            found += 1
            if found == count:
                return (cur, 0)
        cur -= 1
    return (0, 0)


# ---------------------------------------------------------------------------
# Bracket matching motions  [(  [{  ])  ]}
# ---------------------------------------------------------------------------

_OPEN_BRACKETS = {"(": ")", "{": "}", "[": "]"}
_CLOSE_BRACKETS = {")": "(", "}": "{", "]": "["}


def _find_unmatched_open(doc: Document, line: int, col: int, open_ch: str, close_ch: str) -> tuple[int, int]:
    """Scan backward for unmatched open bracket."""
    depth = 0
    cur_line, cur_col = line, col - 1
    while cur_line >= 0:
        text = doc.get_line(cur_line)
        end_col = cur_col if cur_col >= 0 else len(text) - 1
        for c in range(min(end_col, len(text) - 1), -1, -1):
            ch = text[c]
            if ch == close_ch:
                depth += 1
            elif ch == open_ch:
                if depth == 0:
                    return (cur_line, c)
                depth -= 1
        cur_line -= 1
        cur_col = -1
    return (line, col)  # not found


def _find_unmatched_close(doc: Document, line: int, col: int, open_ch: str, close_ch: str) -> tuple[int, int]:
    """Scan forward for unmatched close bracket."""
    depth = 0
    total = doc.line_count()
    cur_line, cur_col = line, col + 1
    while cur_line < total:
        text = doc.get_line(cur_line)
        start_col = cur_col if cur_line == line else 0
        for c in range(start_col, len(text)):
            ch = text[c]
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                if depth == 0:
                    return (cur_line, c)
                depth -= 1
        cur_line += 1
        cur_col = 0
    return (line, col)  # not found


def move_bracket_open_paren(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """[( — move to previous unmatched (."""
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _find_unmatched_open(doc, cur_line, cur_col, "(", ")")
    return (cur_line, cur_col)


def move_bracket_open_brace(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """[{ — move to previous unmatched {."""
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _find_unmatched_open(doc, cur_line, cur_col, "{", "}")
    return (cur_line, cur_col)


def move_bracket_close_paren(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """]) — move to next unmatched )."""
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _find_unmatched_close(doc, cur_line, cur_col, "(", ")")
    return (cur_line, cur_col)


def move_bracket_close_brace(doc: Document, line: int, col: int, count: int = 1) -> tuple[int, int]:
    """]} — move to next unmatched }."""
    cur_line, cur_col = line, col
    for _ in range(count):
        cur_line, cur_col = _find_unmatched_close(doc, cur_line, cur_col, "{", "}")
    return (cur_line, cur_col)
