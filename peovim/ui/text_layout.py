"""
ui.text_layout - shared helpers for terminal display columns.

Tabs occupy multiple display cells but only one logical character column.
These helpers keep rendering, cursor placement, and hit testing aligned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.core.window import Window


def expand_for_display(text: str, tabstop: int) -> str:
    """Expand tabs for terminal display."""
    return text.expandtabs(tabstop)


def logical_col_to_display_col(line_text: str, col: int, tabstop: int) -> int:
    """Translate a logical character column into a rendered display column."""
    if col <= 0:
        return 0
    return len(line_text[:col].expandtabs(tabstop))


def display_col_to_logical_col(line_text: str, display_col: int, tabstop: int) -> int:
    """Translate a rendered display column back into a logical character column."""
    if display_col <= 0:
        return 0

    current_display_col = 0
    for logical_col, ch in enumerate(line_text):
        next_display_col = current_display_col + (tabstop - (current_display_col % tabstop) if ch == "\t" else 1)
        if display_col < next_display_col:
            return logical_col
        current_display_col = next_display_col
    return len(line_text)


def visible_text_slice(line_text: str, scroll_display_col: int, width: int, tabstop: int) -> str:
    """Return the visible display slice for one line after tab expansion."""
    expanded = expand_for_display(line_text, tabstop)
    return expanded[scroll_display_col : scroll_display_col + width]


def wrapped_ranges(text: str, width: int, tabstop: int = 4, *, include_eol: bool = False) -> list[tuple[int, int]]:
    """Display-column ranges; preserve every character and prefer word boundaries."""
    expanded = expand_for_display(text, tabstop)
    width = max(1, width)
    ranges = []
    start = 0
    while start < len(expanded):
        end = min(len(expanded), start + width)
        if end < len(expanded):
            boundary = max((i + 1 for i in range(start, end) if expanded[i].isspace()), default=start)
            if boundary > start:
                end = boundary
        ranges.append((start, end))
        start = end
    if include_eol and ranges and ranges[-1][1] - ranges[-1][0] == width:
        ranges.append((len(expanded), len(expanded)))
    return ranges or [(0, 0)]


def wrapped_position(
    text: str, col: int, width: int, tabstop: int = 4, *, include_eol: bool = False
) -> tuple[int, int]:
    ranges = wrapped_ranges(text, width, tabstop, include_eol=include_eol)
    dcol = logical_col_to_display_col(text, col, tabstop)
    for row, (start, end) in enumerate(ranges):
        if dcol < end or row == len(ranges) - 1:
            return row, dcol - start
    return 0, 0


def window_wrap_rows(window: Window, width: int, tabstop: int = 4) -> list[tuple[int, int, int]]:
    """Cached (line, display start, display end) rows; -1 columns mark virtual padding."""
    folds = dict(window.folds.closed_folds())
    spans = getattr(window, "wrap_virtual_spans", ())
    cache_key = (
        window.document._table._original,
        window.document.version,
        width,
        tabstop,
        tuple(folds.items()),
        tuple(spans),
        getattr(window, "wrap_insert", False),
        window.cursor.line if getattr(window, "wrap_insert", False) else -1,
    )
    cached = getattr(window, "_wrap_rows_cache", None)
    if cached is not None and cached[0] == cache_key:
        return cached[1]
    virtual = dict(spans)
    rows = [(-1, -1, -1)] * virtual.get(-1, 0)
    line = 0
    while line < window.document.line_count():
        ranges = (
            [(0, 0)]
            if line in folds
            else wrapped_ranges(
                window.document.get_line(line),
                width,
                tabstop,
                include_eol=getattr(window, "wrap_insert", False) and line == window.cursor.line,
            )
        )
        rows.extend((line, start, end) for start, end in ranges)
        rows.extend((line, -1, -1) for _ in range(virtual.get(line, 0)))
        line = folds.get(line, line) + 1
    window._wrap_rows_cache = cache_key, rows
    return rows


def wrapped_cursor_offset(window: Window, width: int, tabstop: int = 4) -> tuple[int, int]:
    rows = window_wrap_rows(window, width, tabstop)
    row, col = wrapped_position(
        window.document.get_line(window.cursor.line),
        window.cursor.col,
        width,
        tabstop,
        include_eol=getattr(window, "wrap_insert", False),
    )
    cursor = next((i for i, item in enumerate(rows) if item[0] == window.cursor.line and item[1] >= 0), 0) + row
    return cursor - wrap_top_index(window, rows), col


def wrap_top_index(window: Window, rows: list[tuple[int, int, int]]) -> int:
    first = next((i for i, item in enumerate(rows) if item[0] == window.scroll_line and item[1] >= 0), 0)
    if window.scroll_virtual_skip:
        last = first
        while last + 1 < len(rows) and rows[last + 1][0] == window.scroll_line and rows[last + 1][1] >= 0:
            last += 1
        return last + window.scroll_virtual_skip
    return first + window.scroll_wrap_row


def set_wrap_top(window: Window, rows: list[tuple[int, int, int]], top: int) -> None:
    line, start, _ = rows[top]
    if line < 0:
        window.scroll_line = 0
        window.scroll_wrap_row = top - next(i for i, item in enumerate(rows) if item[0] == 0 and item[1] >= 0)
        window.scroll_virtual_skip = 0
        return
    first = next((i for i, item in enumerate(rows) if item[0] == line and item[1] >= 0), 0)
    window.scroll_line = line
    window.scroll_virtual_skip = 0
    window.scroll_wrap_row = top - first
    if start < 0:
        last = first
        while last + 1 < len(rows) and rows[last + 1][0] == line and rows[last + 1][1] >= 0:
            last += 1
        window.scroll_virtual_skip = top - last
        window.scroll_wrap_row = 0
