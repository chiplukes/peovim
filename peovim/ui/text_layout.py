"""
ui.text_layout - shared helpers for terminal display columns.

Tabs occupy multiple display cells but only one logical character column.
These helpers keep rendering, cursor placement, and hit testing aligned.
"""

from __future__ import annotations


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
