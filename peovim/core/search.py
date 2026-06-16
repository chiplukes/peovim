"""
core.search — Pure search utilities (no UI imports)

Provides pattern compilation with Vim-style case rules, word-under-cursor
pattern building, and forward/backward incremental search across a Document.
"""

from __future__ import annotations

import bisect
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.core.document import Document


def compile_pattern(pattern: str, ignorecase: bool = False, smartcase: bool = False) -> re.Pattern:
    """Compile pattern with Vim-style case rules.

    smartcase: if pattern contains any uppercase letter, force case-sensitive
    regardless of the ignorecase setting.
    """
    flags = 0
    if ignorecase:
        # smartcase overrides: if any uppercase present, be case-sensitive
        if smartcase and any(c.isupper() for c in pattern):
            pass  # case-sensitive
        else:
            flags |= re.IGNORECASE
    return re.compile(pattern, flags)


def build_word_pattern(word: str, whole_word: bool = True) -> str:
    r"""Wrap word in \b...\b for * / # search. whole_word=False for g*/g#."""
    escaped = re.escape(word)
    if whole_word:
        return r"\b" + escaped + r"\b"
    return escaped


def _line_starts(text: str) -> list[int]:
    """Return a list of the char offset at which each line begins."""
    starts = [0]
    for i, c in enumerate(text):
        if c == "\n":
            starts.append(i + 1)
    return starts


def search_next(
    doc: Document,
    pattern: re.Pattern,
    from_line: int,
    from_col: int,
    direction: str,  # "forward" | "backward"
    wrapscan: bool = True,
) -> tuple[int, int] | None:
    """Find next match after (from_line, from_col) in the given direction.

    Operates over the full document text in a single pass, enabling multiline
    patterns (e.g. ``\\n`` in the regex) to match across line boundaries.

    Forward: finds the first match strictly after the cursor; wraps to the
    first match at or before the cursor when wrapscan is True.
    Backward: finds the last match strictly before the cursor; wraps to the
    last match at or after the cursor when wrapscan is True.

    Returns (line, col) of match start, or None if not found.
    """
    text = doc.get_text()
    if not text:
        return None

    starts = _line_starts(text)
    if from_line >= len(starts):
        return None

    from_offset = min(starts[from_line] + from_col, len(text))

    def to_line_col(offset: int) -> tuple[int, int]:
        ln = bisect.bisect_right(starts, offset) - 1
        return (ln, offset - starts[ln])

    if direction == "forward":
        for m in pattern.finditer(text, from_offset + 1):
            return to_line_col(m.start())
        # Wrap: first match in the document whose start is at or before the cursor.
        # We cannot use endpos=from_offset+1 because endpos truncates the searchable
        # string — a match that starts at from_offset but extends past it would be missed.
        if wrapscan:
            m = next(pattern.finditer(text), None)
            if m is not None and m.start() <= from_offset:
                return to_line_col(m.start())
        return None

    else:  # backward
        last: re.Match | None = None
        for m in pattern.finditer(text, 0, from_offset):
            last = m
        if last is not None:
            return to_line_col(last.start())
        if wrapscan:
            last = None
            for m in pattern.finditer(text, from_offset):
                last = m
            if last is not None:
                return to_line_col(last.start())
        return None


def search_all_in_line(line_text: str, pattern: re.Pattern) -> list[tuple[int, int]]:
    """Return list of (col_start, col_end) for all matches on a single line."""
    return [(m.start(), m.end()) for m in pattern.finditer(line_text)]
