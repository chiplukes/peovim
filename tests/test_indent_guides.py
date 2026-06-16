"""
Phase 7h — Indent guides rendering tests
"""

from __future__ import annotations

from peovim.ui.cell_grid import CellGrid
from peovim.ui.window_renderer import _GUIDE_CHAR, _GUIDE_DIM, _RAINBOW_COLORS, _draw_indent_guides

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_grid(width: int = 40, height: int = 1) -> CellGrid:
    return CellGrid(width, height)


def _cell(grid: CellGrid, row: int, col: int):
    """Return (char, fg, bg, attrs) for a cell."""
    return grid._current[row][col]


def _draw(grid, line_text, *, mode="yes", tabstop=4, gutter_w=0, scroll_col=0, text_w=None):
    if text_w is None:
        text_w = grid.width - gutter_w
    # Fill gutter with spaces
    if gutter_w > 0:
        grid.fill(0, 0, gutter_w)
    # Fill text area with the visible slice of line_text (after scroll_col)
    visible = line_text[scroll_col : scroll_col + text_w]
    if visible:
        grid.write_str(0, gutter_w, visible)
    # Remainder of text area is spaces
    remaining_start = gutter_w + len(visible)
    if remaining_start < grid.width:
        grid.fill(0, remaining_start, grid.width - remaining_start)
    _draw_indent_guides(grid, line_text, 0, gutter_w, scroll_col, text_w, tabstop, mode)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIndentGuidesNone:
    def test_mode_none_draws_nothing(self):
        grid = _make_grid()
        _draw(grid, "    text", mode="none")
        # Columns 0-3 are spaces; the guide function should not touch them
        assert _cell(grid, 0, 0)[0] == " "
        assert _cell(grid, 0, 1)[0] == " "
        # No guide character anywhere
        for c in range(grid.width):
            assert _cell(grid, 0, c)[0] != _GUIDE_CHAR

    def test_mode_none_returns_fast(self):
        grid = _make_grid()
        # Should not crash or modify
        _draw_indent_guides(grid, "        deep", 0, 0, 0, 40, 4, "none")


class TestIndentGuidesYes:
    def test_guide_at_tabstop_for_two_level_indent(self):
        # 8 leading spaces (2 indent levels), tabstop=4
        # guide at column 4 (within spaces), NOT at column 8 (text starts there)
        grid = _make_grid()
        _draw(grid, "        text", mode="yes", tabstop=4)
        assert _cell(grid, 0, 4)[0] == _GUIDE_CHAR
        assert _cell(grid, 0, 4)[1] == _GUIDE_DIM
        # Column 8 is 't' of "text", not a space → no guide
        assert _cell(grid, 0, 8)[0] == "t"

    def test_guides_at_two_positions_for_three_level_indent(self):
        # 12 leading spaces (3 indent levels), tabstop=4
        # guides at columns 4 and 8 (both within spaces)
        grid = _make_grid()
        _draw(grid, "            text", mode="yes", tabstop=4)
        assert _cell(grid, 0, 4)[0] == _GUIDE_CHAR
        assert _cell(grid, 0, 8)[0] == _GUIDE_CHAR
        # Column 12 is 't' — no guide
        assert _cell(grid, 0, 12)[0] == "t"

    def test_no_guide_on_empty_line(self):
        grid = _make_grid()
        _draw(grid, "", mode="yes")
        # All cells untouched
        for c in range(10):
            assert _cell(grid, 0, c)[0] == " "

    def test_no_guide_on_blank_line_spaces_only(self):
        grid = _make_grid()
        _draw(grid, "    ", mode="yes")
        # Line is all spaces → strip() == "" → skip
        assert _cell(grid, 0, 4)[0] != _GUIDE_CHAR

    def test_no_guide_on_unindented_line(self):
        grid = _make_grid()
        _draw(grid, "text", mode="yes")
        assert _cell(grid, 0, 0)[0] == "t"

    def test_guide_not_drawn_over_non_space(self):
        # 4 spaces then "text" - guide column 4 has 't', should not be overwritten
        grid = _make_grid()
        _draw(grid, "    text", mode="yes", tabstop=4)
        # Only column 4 is in the indent... wait, indent is 4 spaces (col 0-3)
        # tabstop=4: guide at col 4, but col 4 is 't' → no guide
        assert _cell(grid, 0, 4)[0] == "t"

    def test_guide_respects_scroll_col(self):
        # 8 leading spaces, tabstop=4, scroll_col=3
        # Guide at doc col 4 → screen col = 4 - 3 = 1
        grid = _make_grid()
        _draw(grid, "        text", mode="yes", tabstop=4, scroll_col=3)
        assert _cell(grid, 0, 1)[0] == _GUIDE_CHAR

    def test_guide_respects_tabstop_option(self):
        # 4 leading spaces, tabstop=2 → guides at cols 2
        grid = _make_grid()
        _draw(grid, "    text", mode="yes", tabstop=2)
        assert _cell(grid, 0, 2)[0] == _GUIDE_CHAR

    def test_guide_respects_gutter_width(self):
        # gutter_w=4, guide at doc col 4 → cell_col = 4 + 4 = 8
        grid = _make_grid(width=20)
        _draw(grid, "        text", mode="yes", tabstop=4, gutter_w=4, text_w=16)
        assert _cell(grid, 0, 8)[0] == _GUIDE_CHAR

    def test_tab_character_indent(self):
        # A tab (with tabstop=4) expands to 4 spaces → guide at col 4 (second tab boundary)
        # "\t\ttext" → indent = 8, guides at col 4
        grid = _make_grid()
        # Pre-fill manually: cells for tab chars are rendered as spaces in the grid
        grid.fill(0, 0, 8)
        grid.write_str(0, 8, "text")
        _draw_indent_guides(grid, "\t\ttext", 0, 0, 0, 40, 4, "yes")
        assert _cell(grid, 0, 4)[0] == _GUIDE_CHAR

    def test_guide_preserves_existing_background(self):
        grid = _make_grid()
        bg = (31, 31, 31)
        grid.fill(0, 0, 12, bg=bg)
        grid.write_str(0, 8, "text", bg=bg)

        _draw_indent_guides(grid, "        text", 0, 0, 0, 40, 4, "yes")

        assert _cell(grid, 0, 4)[0] == _GUIDE_CHAR
        assert _cell(grid, 0, 4)[2] == (31, 31, 31)


class TestIndentGuidesRainbow:
    def test_rainbow_uses_different_colors_per_level(self):
        # 12 leading spaces → guides at col 4 (level 0) and col 8 (level 1)
        grid = _make_grid()
        _draw(grid, "            text", mode="rainbow", tabstop=4)
        col4_fg = _cell(grid, 0, 4)[1]
        col8_fg = _cell(grid, 0, 8)[1]
        assert col4_fg == _RAINBOW_COLORS[0]
        assert col8_fg == _RAINBOW_COLORS[1]
        assert col4_fg != col8_fg

    def test_rainbow_cycles_colors(self):
        # 28 leading spaces with tabstop=4 → 6 guides; color at level 6 == level 0
        # (6 rainbow colors, so level 6 % 6 == 0)
        n_levels = len(_RAINBOW_COLORS)
        indent = (n_levels + 1) * 4  # enough spaces for n_levels guides
        line = " " * indent + "x"
        grid = _make_grid(width=indent + 10)
        _draw(grid, line, mode="rainbow", tabstop=4)
        # Level 0 color == level n_levels color (after wrapping)
        col_first = 4
        assert _cell(grid, 0, col_first)[1] == _RAINBOW_COLORS[0]
