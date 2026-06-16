"""
WindowRenderer output: CellGrid, render_window, StatusBar, CommandLine
"""

from peovim.core.document import Document
from peovim.core.style import Style as CoreStyle
from peovim.core.window import Window
from peovim.modal.engine import Mode
from peovim.ui.cell_grid import CellGrid
from peovim.ui.decorations import HighlightRegion, OverlayChar, Sign, Style, VirtualText
from peovim.ui.layout import Rect
from peovim.ui.scrollbar import SCROLLBAR_THUMB_CHAR, SCROLLBAR_TRACK_CHAR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_doc(content: str) -> Document:
    doc = Document()
    doc.load_string(content)
    return doc


def make_window(content: str, width: int = 20, height: int = 5, cursor=(0, 0), scroll=(0, 0), options=None) -> Window:
    doc = make_doc(content)
    win = Window(doc, width=width, height=height)
    win.cursor.move_to(cursor[0], cursor[1])
    win.scroll_line = scroll[0]
    win.scroll_col = scroll[1]
    if options:
        win.options.update(options)
    return win


def grid_row_chars(grid: CellGrid, row: int) -> str:
    return "".join(cell[0] for cell in grid._current[row])


def grid_cell(grid: CellGrid, row: int, col: int):
    return grid._current[row][col]


# ---------------------------------------------------------------------------
# CellGrid tests
# ---------------------------------------------------------------------------


class TestCellGrid:
    def test_empty_grid_no_ops(self):
        grid = CellGrid(4, 2)
        assert grid.flush() == []

    def test_write_single_cell(self):
        from peovim.ui.backend import MoveCursor, PutCells

        grid = CellGrid(10, 5)
        grid.write(0, 0, "A")
        ops = grid.flush()
        assert any(isinstance(op, MoveCursor) and op.row == 0 and op.col == 0 for op in ops)
        assert any(isinstance(op, PutCells) and "A" in op.text for op in ops)

    def test_write_str_produces_single_run(self):
        from peovim.ui.backend import MoveCursor, PutCells

        grid = CellGrid(20, 5)
        grid.write_str(1, 2, "hi")
        ops = grid.flush()
        move_ops = [op for op in ops if isinstance(op, MoveCursor)]
        put_ops = [op for op in ops if isinstance(op, PutCells)]
        assert len(move_ops) == 1
        assert move_ops[0] == MoveCursor(1, 2)
        assert len(put_ops) == 1
        assert put_ops[0].text == "hi"

    def test_style_break_splits_run(self):
        from peovim.ui.backend import PutCells

        grid = CellGrid(10, 3)
        grid.write(0, 0, "A", fg=(255, 0, 0))
        grid.write(0, 1, "B", fg=(0, 255, 0))
        ops = grid.flush()
        put_ops = [op for op in ops if isinstance(op, PutCells)]
        assert len(put_ops) == 2
        assert put_ops[0].fg == (255, 0, 0)
        assert put_ops[1].fg == (0, 255, 0)

    def test_unchanged_cell_not_in_ops(self):
        grid = CellGrid(5, 3)
        grid.write(0, 0, "X")
        grid.flush()  # commit
        ops = grid.flush()  # second flush — nothing changed
        assert ops == []

    def test_clear_then_flush_produces_ops(self):
        from peovim.ui.backend import PutCells

        grid = CellGrid(5, 3)
        grid.write(0, 0, "X")
        grid.flush()
        grid.clear()
        ops = grid.flush()
        put_ops = [op for op in ops if isinstance(op, PutCells)]
        assert any(" " in op.text for op in put_ops)

    def test_blit_copies_cells(self):
        src = CellGrid(3, 1)
        src.write(0, 1, "Z")
        dst = CellGrid(10, 5)
        dst.blit(src, dest_x=2, dest_y=3)
        assert dst._current[3][3] == ("Z", None, None, 0)

    def test_write_out_of_bounds_silent(self):
        grid = CellGrid(4, 2)
        grid.write(99, 99, "X")  # must not raise
        grid.write(-1, -1, "X")

    def test_second_flush_empty_after_no_writes(self):
        grid = CellGrid(5, 2)
        grid.write(0, 0, "A")
        grid.flush()
        assert grid.flush() == []

    def test_fill_covers_range(self):
        from peovim.ui.backend import PutCells

        grid = CellGrid(10, 1)
        grid.fill(0, 2, 5, "_")
        ops = grid.flush()
        put_ops = [op for op in ops if isinstance(op, PutCells)]
        text = "".join(op.text for op in put_ops)
        assert "_____" in text

    def test_write_str_clips_negative_start_column(self):
        grid = CellGrid(4, 1)
        grid.write_str(0, -2, "abcd")
        assert grid_row_chars(grid, 0) == "cd  "

    def test_fill_clips_negative_start_column(self):
        grid = CellGrid(4, 1)
        grid.fill(0, -2, 4, "_")
        assert grid_row_chars(grid, 0) == "__  "

    def test_write_padded_fills_remaining_span(self):
        grid = CellGrid(6, 1)
        grid.write_padded(0, 0, "abc", 6)
        assert grid_row_chars(grid, 0) == "abc   "

    def test_prev_is_deep_copy(self):
        """Writes after flush must not corrupt _prev."""
        grid = CellGrid(5, 1)
        grid.write(0, 0, "A")
        grid.flush()
        grid.write(0, 0, "B")
        # _prev should still show 'A'
        assert grid._prev[0][0][0] == "A"
        assert grid._current[0][0][0] == "B"

    def test_blit_clips_to_dest_bounds(self):
        src = CellGrid(5, 5)
        src.write(0, 0, "X")
        dst = CellGrid(3, 3)
        dst.blit(src, dest_x=0, dest_y=0)  # src is larger — must not crash
        assert dst._current[0][0] == ("X", None, None, 0)


# ---------------------------------------------------------------------------
# render_window tests
# ---------------------------------------------------------------------------


class TestRenderWindow:
    def _render(self, win: Window, is_active: bool = True, decorations=None, global_options=None):
        from peovim.ui.window_renderer import render_window

        snap = win.snapshot(global_options=global_options)
        rect = Rect(0, 0, win.width, win.height)
        return render_window(snap, rect, is_active, decorations)

    def test_single_line_appears_in_grid(self):
        win = make_window("hello\n", width=20, height=5)
        grid = self._render(win)
        row = grid_row_chars(grid, 0)
        assert row[:5] == "hello"

    def test_tilde_for_lines_past_eof(self):
        # "x" (no newline) = 1 line; rows 1-3 should be tildes
        win = make_window("x", width=10, height=4)
        grid = self._render(win)
        for row in range(1, 4):
            assert grid._current[row][0][0] == "~"

    def test_cursor_cell_highlighted_active(self):
        from peovim.ui.window_renderer import CURSOR_ACTIVE

        win = make_window("abc\n", width=10, height=3, cursor=(0, 1))
        grid = self._render(win, is_active=True)
        cell = grid_cell(grid, 0, 1)
        assert cell[2] == CURSOR_ACTIVE["bg"]  # bg field

    def test_inactive_cursor_different_from_active(self):
        win = make_window("abc\n", width=10, height=3, cursor=(0, 0))
        active_grid = self._render(win, is_active=True)
        inactive_grid = self._render(win, is_active=False)
        active_bg = active_grid._current[0][0][2]
        inactive_bg = inactive_grid._current[0][0][2]
        assert active_bg != inactive_bg

    def test_line_numbers_option(self):
        win = make_window("a\nb\n", width=10, height=4, options={"number": True})
        grid = self._render(win)
        row0 = grid_row_chars(grid, 0)
        row1 = grid_row_chars(grid, 1)
        assert "1" in row0
        assert "2" in row1

    def test_no_line_numbers_by_default(self):
        win = make_window("hello\n", width=10, height=3)
        grid = self._render(win)
        assert grid._current[0][0][0] == "h"

    def test_signcolumn_reserved_by_default_without_signs(self):
        win = make_window("hello\n", width=10, height=3)
        grid = self._render(win, global_options={"signcolumn": "yes"})
        assert grid._current[0][0][0] == " "
        assert grid._current[0][1][0] == " "
        assert grid._current[0][2][0] == "h"

    def test_signcolumn_width_stays_stable_when_signs_appear(self):
        from peovim.ui.window_renderer import _gutter_width

        win = make_window("hello\n", width=10, height=3)
        snap = win.snapshot(global_options={"signcolumn": "yes"})
        line_count = len(snap.buffer_snapshot.line_offsets)

        assert _gutter_width(snap, line_count, has_signs=False) == 2
        assert _gutter_width(snap, line_count, has_signs=True) == 2

    def test_sign_renders_in_reserved_signcolumn(self):
        win = make_window("hello\n", width=10, height=3)
        grid = self._render(
            win,
            decorations=[Sign(line=0, char="E", style=Style(fg=(255, 0, 0)))],
            global_options={"signcolumn": "yes"},
        )
        assert grid._current[0][0][0] == "E"
        assert grid._current[0][2][0] == "h"

    def test_scroll_shifts_content(self):
        win = make_window("line0\nline1\nline2\n", width=10, height=2, scroll=(1, 0))
        grid = self._render(win)
        row0 = grid_row_chars(grid, 0)
        assert row0[:5] == "line1"

    def test_horizontal_scroll(self):
        win = make_window("abcdef\n", width=4, height=2, scroll=(0, 3))
        grid = self._render(win)
        assert grid._current[0][0][0] == "d"

    def test_scrollbar_uses_last_column_when_enabled(self):
        win = make_window("\n".join(f"line{i}" for i in range(40)) + "\n", width=10, height=5, scroll=(10, 0))
        grid = self._render(win, global_options={"scrollbar": True})
        chars = [grid._current[row][win.width - 1][0] for row in range(win.height)]
        assert SCROLLBAR_THUMB_CHAR in chars
        assert all(ch in (SCROLLBAR_TRACK_CHAR, SCROLLBAR_THUMB_CHAR) for ch in chars)

    def test_scrollbar_reduces_text_width_when_enabled(self):
        win = make_window("abcdef\n", width=6, height=2)
        grid = self._render(win, global_options={"scrollbar": True})
        assert grid_row_chars(grid, 0) == "abcde" + SCROLLBAR_THUMB_CHAR

    def test_leading_tabs_render_as_spaces(self):
        win = make_window("\toptions = api.options\n", width=24, height=2)
        grid = self._render(win)
        row = grid_row_chars(grid, 0)
        assert "\t" not in row
        assert row[:4] == "    "
        assert row[4] == "o"

    def test_cursor_after_tab_uses_display_column(self):
        win = make_window("\txy\n", width=8, height=2, cursor=(0, 1))
        grid = self._render(win, is_active=True)
        assert grid._current[0][4][2] is not None
        assert grid._current[0][4][0] == "x"

    def test_highlight_region_applied(self):
        win = make_window("hello world\n", width=15, height=3)
        dec = HighlightRegion(0, 6, 0, 11, Style(bg=(50, 50, 200)))
        grid = self._render(win, decorations=[dec])
        # cells 6-10 should have custom bg
        for col in range(6, 11):
            if col < win.width:
                assert grid._current[0][col][2] == (50, 50, 200), f"col {col} missing highlight"

    def test_multiline_highlight_region_applied_across_visible_lines(self):
        win = make_window("alpha\nbeta\n", width=10, height=3)
        dec = HighlightRegion(0, 1, 1, 3, Style(bg=(70, 20, 120)))
        grid = self._render(win, decorations=[dec])
        assert grid._current[0][1][2] == (70, 20, 120)
        assert grid._current[1][0][2] == (70, 20, 120)
        assert grid._current[1][2][2] == (70, 20, 120)

    def test_overlay_char_replaces_visible_cell(self):
        win = make_window("hello\n", width=10, height=3)
        dec = OverlayChar(0, 1, "*", Style(fg=(200, 100, 50)))
        grid = self._render(win, decorations=[dec])
        assert grid._current[0][1][0] == "*"
        assert grid._current[0][1][1] == (200, 100, 50)

    def test_virtual_text_appends_after_buffer_content(self):
        win = make_window("hello\n", width=16, height=3)
        dec = VirtualText(0, " : int", Style(fg=(120, 120, 120)))
        grid = self._render(win, decorations=[dec])
        row = grid_row_chars(grid, 0)
        assert row.startswith("hello : int")

    def test_synthetic_syntax_span_applies_theme_style(self):
        from peovim.syntax.engine import HighlightSpan
        from peovim.syntax.themes import Theme
        from peovim.ui.window_renderer import render_window

        win = make_window("alpha beta\n", width=12, height=3)
        snap = win.snapshot()
        rect = Rect(0, 0, win.width, win.height)
        spans = [HighlightSpan(0, 6, 0, 10, "keyword")]
        theme = Theme(name="test", groups={"keyword": CoreStyle(fg=(9, 8, 7), bg=(1, 2, 3))})

        grid = render_window(snap, rect, is_active=True, highlight_spans=spans, theme=theme)
        assert grid._current[0][6][1] == (9, 8, 7)
        assert grid._current[0][6][2] == (1, 2, 3)

    def test_theme_default_background_applies_to_plain_text(self):
        from peovim.syntax.themes import Theme
        from peovim.ui.window_renderer import render_window

        win = make_window("alpha\n", width=10, height=3)
        snap = win.snapshot()
        rect = Rect(0, 0, win.width, win.height)
        theme = Theme(name="test", groups={}, default_fg="#D4D4D4", default_bg="#1F1F1F")

        grid = render_window(snap, rect, is_active=True, theme=theme)

        assert grid._current[0][1][2] == (31, 31, 31)
        assert grid._current[0][5][2] == (31, 31, 31)

    def test_syntax_fg_only_preserves_theme_background(self):
        from peovim.syntax.engine import HighlightSpan
        from peovim.syntax.themes import Theme
        from peovim.ui.window_renderer import render_window

        win = make_window("alpha beta\n", width=12, height=3)
        snap = win.snapshot()
        rect = Rect(0, 0, win.width, win.height)
        spans = [HighlightSpan(0, 6, 0, 10, "keyword")]
        theme = Theme(name="test", groups={"keyword": CoreStyle(fg="#569CD6")}, default_bg="#1F1F1F")

        grid = render_window(snap, rect, is_active=True, highlight_spans=spans, theme=theme)

        assert grid._current[0][6][1] == (86, 156, 214)
        assert grid._current[0][6][2] == (31, 31, 31)

    def test_syntax_span_after_leading_tab_uses_display_columns(self):
        from peovim.syntax.engine import HighlightSpan
        from peovim.syntax.themes import Theme
        from peovim.ui.window_renderer import render_window

        win = make_window("\talpha\n", width=16, height=3)
        snap = win.snapshot()
        rect = Rect(0, 0, win.width, win.height)
        spans = [HighlightSpan(0, 1, 0, 6, "keyword")]
        theme = Theme(name="test", groups={"keyword": CoreStyle(fg=(9, 8, 7), bg=(1, 2, 3))})

        grid = render_window(snap, rect, is_active=True, highlight_spans=spans, theme=theme)
        assert grid._current[0][4][0] == "a"
        assert grid._current[0][4][1] == (9, 8, 7)
        assert grid._current[0][8][1] == (9, 8, 7)

    def test_multiline_syntax_span_applies_on_visible_scrolled_lines(self):
        from peovim.syntax.engine import HighlightSpan
        from peovim.syntax.themes import Theme
        from peovim.ui.window_renderer import render_window

        win = make_window('"""\nalpha\nbeta\n"""\n', width=12, height=3, scroll=(1, 0))
        snap = win.snapshot()
        rect = Rect(0, 0, win.width, win.height)
        spans = [HighlightSpan(0, 0, 3, 3, "string")]
        theme = Theme(name="test", groups={"string": CoreStyle(fg=(200, 150, 100), bg=(5, 6, 7))})

        grid = render_window(snap, rect, is_active=True, highlight_spans=spans, theme=theme)

        assert grid._current[0][0][1] == (200, 150, 100)
        assert grid._current[1][0][1] == (200, 150, 100)
        assert grid._current[2][0][1] == (200, 150, 100)

    def test_highlight_region_after_leading_tab_uses_display_columns(self):
        win = make_window("\talpha\n", width=16, height=3)
        dec = HighlightRegion(0, 1, 0, 6, Style(bg=(50, 50, 200)))
        grid = self._render(win, decorations=[dec])
        assert grid._current[0][4][2] == (50, 50, 200)
        assert grid._current[0][8][2] == (50, 50, 200)

    def test_colorcolumn_applies_background(self):
        win = make_window("hello world\n", width=20, height=3, options={"colorcolumn": "3,8"})
        grid = self._render(win)
        assert grid._current[0][2][2] == (60, 40, 40)
        assert grid._current[0][7][2] == (60, 40, 40)

    def test_indent_guides_continue_through_blank_line(self):
        win = make_window("        first\n\n        second\n", width=20, height=4, options={"indentguides": "yes"})
        grid = self._render(win)

        assert grid._current[0][4][0] == "│"
        assert grid._current[1][4][0] == "│"
        assert grid._current[2][4][0] == "│"

    def test_multiline_fills_rows(self):
        content = "aaa\nbbb\nccc\nddd\neee\n"
        win = make_window(content, width=10, height=5)
        grid = self._render(win)
        for row, expected_char in enumerate("abcde"):
            assert grid._current[row][0][0] == expected_char

    def test_empty_buffer_tilde_from_row1(self):
        # Empty string = 1 line (the empty line); rows 1-3 are tildes
        win = make_window("", width=10, height=4)
        grid = self._render(win)
        # Row 0: empty line (cursor/spaces, not tilde)
        assert grid._current[0][0][0] != "~"
        # Rows 1-3: tilde
        for row in range(1, 4):
            assert grid._current[row][0][0] == "~"

    def test_long_line_clipped(self):
        win = make_window("a" * 100 + "\n", width=10, height=2)
        self._render(win)  # must not raise IndexError

    def test_grid_dimensions(self):
        win = make_window("hello\n", width=30, height=6)
        grid = self._render(win)
        assert grid.width == 30
        assert grid.height == 6


# ---------------------------------------------------------------------------
# StatusBar tests
# ---------------------------------------------------------------------------


class TestStatusBar:
    def _render(self, win, mode, editor_state=None):
        from peovim.ui.status_bar import render_status_bar

        grid = CellGrid(40, 3)
        rect = Rect(0, 1, 40, 1)  # middle row of 3-row grid
        render_status_bar(win, mode, rect, grid, editor_state=editor_state)
        return grid

    def test_insert_mode_shows_label(self):
        win = make_window("hello\n")
        grid = self._render(win, Mode.INSERT)
        row = grid_row_chars(grid, 1)
        assert "INSERT" in row

    def test_normal_mode_no_mode_label(self):
        win = make_window("hello\n")
        grid = self._render(win, Mode.NORMAL)
        row = grid_row_chars(grid, 1)
        assert "INSERT" not in row
        assert "VISUAL" not in row

    def test_dirty_flag_shown(self):
        doc = make_doc("hello\n")
        win = Window(doc, width=40, height=5)
        doc.insert(0, 0, "x")  # make dirty
        grid = CellGrid(40, 3)
        from peovim.ui.status_bar import render_status_bar

        render_status_bar(win, Mode.NORMAL, Rect(0, 1, 40, 1), grid)
        row = grid_row_chars(grid, 1)
        assert "[+]" in row

    def test_clean_no_dirty_flag(self):
        win = make_window("hello\n")
        grid = self._render(win, Mode.NORMAL)
        row = grid_row_chars(grid, 1)
        assert "[+]" not in row

    def test_cursor_position_shown(self):
        win = make_window("hello\nworld\n", width=40, height=5, cursor=(4, 9))
        grid = self._render(win, Mode.NORMAL)
        row = grid_row_chars(grid, 1)
        assert "5:10" in row

    def test_no_name_buffer(self):
        doc = make_doc("hello\n")
        win = Window(doc, width=40, height=5)
        win.document.path = None
        from peovim.ui.status_bar import render_status_bar

        grid = CellGrid(40, 3)
        render_status_bar(win, Mode.NORMAL, Rect(0, 1, 40, 1), grid)
        row = grid_row_chars(grid, 1)
        assert "No Name" in row

    def test_full_row_covered(self):
        from peovim.ui.status_bar import STATUS_BG

        win = make_window("hello\n", width=40, height=5)
        grid = self._render(win, Mode.NORMAL)
        for col in range(40):
            assert grid._current[1][col][2] == STATUS_BG  # bg field

    def test_visual_line_mode_label(self):
        win = make_window("hello\n")
        grid = self._render(win, Mode.VISUAL_LINE)
        row = grid_row_chars(grid, 1)
        assert "VISUAL" in row

    def test_compare_mode_shows_left_center_right_labels(self):
        from peovim.core.editor_state import EditorState

        win = make_window("hello\n")
        editor_state = EditorState()
        editor_state.compare_status = {
            "left": ".tmp/mdio_master1.v",
            "right": ".tmp/mdio_master2.v",
            "left_dirty": False,
            "right_dirty": True,
        }

        grid = self._render(win, Mode.NORMAL, editor_state=editor_state)
        row = grid_row_chars(grid, 1)
        assert ".tmp/mdio" in row
        assert "DIFF" in row
        assert "master2.v [+]" in row


# ---------------------------------------------------------------------------
# CommandLine tests
# ---------------------------------------------------------------------------


class TestCommandLine:
    def make_cl(self):
        from peovim.ui.command_line import CommandLine

        return CommandLine()

    def test_enter_sets_active(self):
        cl = self.make_cl()
        cl.enter(":")
        assert cl.active
        assert cl.prompt == ":"

    def test_exit_clears_active(self):
        cl = self.make_cl()
        cl.enter(":")
        cl.exit()
        assert not cl.active

    def test_type_chars(self):
        cl = self.make_cl()
        cl.enter(":")
        cl.feed_key("w")
        cl.feed_key("q")
        assert cl.text == "wq"
        assert cl.cursor_col == 2

    def test_backspace_deletes(self):
        cl = self.make_cl()
        cl.enter(":")
        cl.feed_key("a")
        cl.feed_key("b")
        cl.feed_key("<BS>")
        assert cl.text == "a"
        assert cl.cursor_col == 1

    def test_enter_key_returns_text(self):
        cl = self.make_cl()
        cl.enter(":")
        cl.feed_key("w")
        result = cl.feed_key("<CR>")
        assert result == "w"

    def test_esc_returns_empty_string(self):
        cl = self.make_cl()
        cl.enter("/")
        cl.feed_key("f")
        cl.feed_key("o")
        result = cl.feed_key("<Esc>")
        assert result == ""

    def test_history_navigation(self):
        cl = self.make_cl()
        cl.enter(":")
        for c in "write":
            cl.feed_key(c)
        cl.feed_key("<CR>")
        cl.enter(":")
        cl.feed_key("<Up>")
        assert cl.text == "write"

    def test_history_down_restores_live(self):
        cl = self.make_cl()
        # Submit "write"
        cl.enter(":")
        for c in "write":
            cl.feed_key(c)
        cl.feed_key("<CR>")
        # Re-enter, type "x", press Up then Down
        cl.enter(":")
        cl.feed_key("x")
        cl.feed_key("<Up>")  # go to "write"
        cl.feed_key("<Down>")  # back to live
        assert cl.text == "x"

    def test_ctrl_u_clears_text(self):
        cl = self.make_cl()
        cl.enter(":")
        for c in "abc":
            cl.feed_key(c)
        cl.feed_key("<C-u>")
        assert cl.text == ""
        assert cl.cursor_col == 0

    def test_ctrl_w_deletes_word(self):
        cl = self.make_cl()
        cl.enter(":")
        for c in "foo bar":
            cl.feed_key(c)
        cl.feed_key("<C-w>")
        assert cl.text == "foo "

    def test_left_right_movement(self):
        cl = self.make_cl()
        cl.enter(":")
        for c in "abc":
            cl.feed_key(c)
        cl.feed_key("<Left>")
        cl.feed_key("<Left>")
        assert cl.cursor_col == 1
        cl.feed_key("<Right>")
        assert cl.cursor_col == 2

    def test_render_inactive_blank(self):
        cl = self.make_cl()
        grid = CellGrid(20, 1)
        rect = Rect(0, 0, 20, 1)
        cl.render(rect, grid)
        for col in range(20):
            assert grid._current[0][col][0] == " "

    def test_render_active_shows_prompt(self):
        cl = self.make_cl()
        cl.enter(":")
        cl.feed_key("w")
        grid = CellGrid(20, 1)
        rect = Rect(0, 0, 20, 1)
        cl.render(rect, grid)
        assert grid._current[0][0][0] == ":"
        assert grid._current[0][1][0] == "w"

    def test_render_active_expands_tabs(self):
        cl = self.make_cl()
        cl.enter(":", "\tset")
        grid = CellGrid(20, 1)
        rect = Rect(0, 0, 20, 1)
        cl.render(rect, grid)
        row = grid_row_chars(grid, 0)
        assert "\t" not in row
        assert row[1:5] == "    "
        assert row[5] == "s"

    def test_history_not_duplicated(self):
        cl = self.make_cl()
        for _ in range(2):
            cl.enter(":")
            for c in "write":
                cl.feed_key(c)
            cl.feed_key("<CR>")
        assert len(cl._history) == 1
