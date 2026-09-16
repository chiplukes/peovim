"""Tests for Window.scroll_to_cursor() — vertical and horizontal scroll adjustment."""

from peovim.core.document import Document
from peovim.core.window import Window


def _make_window(content: str, width: int = 20, height: int = 5) -> Window:
    doc = Document()
    doc.insert(0, 0, content)
    win = Window(doc, width=width, height=height)
    return win


class TestScrollToCursorVertical:
    def test_no_scroll_needed_when_cursor_visible(self):
        win = _make_window("\n".join(f"line{i}" for i in range(10)), height=5)
        win.cursor.line = 2
        win.scroll_to_cursor()
        assert win.scroll_line == 0

    def test_scrolls_down_when_cursor_below_view(self):
        win = _make_window("\n".join(f"line{i}" for i in range(20)), height=5)
        win.cursor.line = 8
        win.scroll_to_cursor()
        assert win.scroll_line == 4  # cursor at line 8, height 5 → scroll so last visible is 8

    def test_scrolls_up_when_cursor_above_scroll(self):
        win = _make_window("\n".join(f"line{i}" for i in range(20)), height=5)
        win.scroll_line = 10
        win.cursor.line = 3
        win.scroll_to_cursor()
        assert win.scroll_line == 3

    def test_respects_scrolloff(self):
        win = _make_window("\n".join(f"line{i}" for i in range(20)), height=5)
        win.options["scrolloff"] = 2
        win.cursor.line = 8
        win.scroll_to_cursor()
        # cursor + scrolloff = 10, height 5 → scroll_line = 10 - 5 + 1 = 6
        assert win.scroll_line == 6

    def test_scrolloff_param_overrides_window_local_options(self):
        """Window.options is a window-local *override* dict that nothing ever
        populates from the global OptionsStore — the scrolloff/sidescrolloff/
        tabstop params exist so callers with access to the real OptionsStore
        (see effective_scroll_options()) can pass the actual global value in,
        instead of scroll_to_cursor() silently treating scrolloff as 0/default
        for every window (the bug this covers — see notes/architecture.md).
        """
        win = _make_window("\n".join(f"line{i}" for i in range(20)), height=5)
        # No win.options["scrolloff"] set at all — matches every real window,
        # since nothing ever populates that dict from the global setting.
        win.cursor.line = 8
        win.scroll_to_cursor(scrolloff=2)
        assert win.scroll_line == 6

    def test_scrolloff_param_falls_back_to_window_options_when_not_passed(self):
        """Backward compatibility: callers that don't pass scrolloff (existing
        test-construction code, etc.) keep today's self.options-only behavior."""
        win = _make_window("\n".join(f"line{i}" for i in range(20)), height=5)
        win.options["scrolloff"] = 2
        win.cursor.line = 8
        win.scroll_to_cursor()
        assert win.scroll_line == 6


class TestScrollToCursorHorizontal:
    def test_no_horizontal_scroll_when_cursor_in_view(self):
        win = _make_window("hello world", width=20)
        win.cursor.line = 0
        win.cursor.col = 5
        win.scroll_to_cursor(text_width=20)
        assert win.scroll_col == 0

    def test_scrolls_right_when_cursor_past_right_edge(self):
        """Typing past the visible right edge should scroll scroll_col right."""
        content = "a" * 50
        win = _make_window(content, width=20)
        win.cursor.line = 0
        win.cursor.col = 40  # well past width=20
        win.scroll_to_cursor(text_width=20)
        assert win.scroll_col > 0
        assert win.scroll_col <= win.cursor.col
        # cursor must be visible: scroll_col <= cursor_col < scroll_col + text_width
        assert win.scroll_col <= win.cursor.col < win.scroll_col + 20

    def test_scrolls_right_accounting_for_gutter(self):
        """text_width excl. gutter ensures cursor is fully visible past gutter area."""
        content = "a" * 50
        win = _make_window(content, width=20)
        text_width = 15  # 20 - 5 char gutter
        win.cursor.line = 0
        win.cursor.col = 14  # at the right edge of text area
        win.scroll_to_cursor(text_width=text_width)
        assert win.scroll_col <= win.cursor.col < win.scroll_col + text_width

    def test_scrolls_left_when_cursor_before_scroll_col(self):
        content = "a" * 50
        win = _make_window(content, width=20)
        win.scroll_col = 30
        win.cursor.line = 0
        win.cursor.col = 5
        win.scroll_to_cursor(text_width=20)
        assert win.scroll_col <= 5

    def test_scroll_col_scrolls_back_on_short_line(self):
        """Moving cursor before stale scroll_col should scroll back so cursor is visible."""
        win = _make_window("short\n" + "a" * 50, width=20)
        win.scroll_col = 30
        win.cursor.line = 0
        win.cursor.col = 3
        win.scroll_to_cursor(text_width=20)
        assert win.scroll_col <= win.cursor.col < win.scroll_col + 20

    def test_respects_sidescrolloff(self):
        content = "a" * 50
        win = _make_window(content, width=20)
        win.options["sidescrolloff"] = 3
        win.cursor.line = 0
        win.cursor.col = 40
        win.scroll_to_cursor(text_width=20)
        scroll_dcol = win.scroll_col  # ASCII: byte col == display col
        cursor_dcol = win.cursor.col
        assert cursor_dcol < scroll_dcol + 20 - 3 + 1  # within bound

    def test_tab_character_display_col_handling(self):
        """scroll_col should be computed in display space (tabs count as tabstop cells)."""
        win = _make_window("\tsome long text here\n", width=10)
        win.options["tabstop"] = 4
        win.cursor.line = 0
        win.cursor.col = 15  # well past the visible area
        win.scroll_to_cursor(text_width=10)
        assert win.scroll_col > 0  # must have scrolled


class TestEffectiveScrollOptions:
    """effective_scroll_options() — the fix for scrolloff/sidescrolloff/tabstop
    silently never applying outside the diff view (see notes/architecture.md).
    """

    def test_reads_global_option_values(self):
        from peovim.core.editor_state import EditorState
        from peovim.core.window import effective_scroll_options

        es = EditorState()
        es.options.set("scrolloff", 8)
        es.options.set("sidescrolloff", 3)
        result = effective_scroll_options(es)
        assert result["scrolloff"] == 8
        assert result["sidescrolloff"] == 3
        assert "tabstop" in result  # has a default even when never explicitly set

    def test_none_editor_state_returns_empty_dict(self):
        """Falls back to scroll_to_cursor()'s own self.options-only behavior
        (via **{} being a no-op) when there's no OptionsStore to read —
        matches startup code that runs before EditorState exists (main.py)."""
        from peovim.core.window import effective_scroll_options

        assert effective_scroll_options(None) == {}

    def test_result_feeds_directly_into_scroll_to_cursor(self):
        from peovim.core.editor_state import EditorState
        from peovim.core.window import effective_scroll_options

        es = EditorState()
        es.options.set("scrolloff", 2)
        win = _make_window("\n".join(f"line{i}" for i in range(20)), height=5)
        win.cursor.line = 8

        win.scroll_to_cursor(**effective_scroll_options(es))

        assert win.scroll_line == 6  # same as test_respects_scrolloff's expectation
