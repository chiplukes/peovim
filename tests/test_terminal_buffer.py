"""
Phase 7e — TerminalBuffer tests (pyte-based)
"""

from __future__ import annotations

import importlib.util

import pytest

from peovim.ui.terminal_buffer import _PYTE_AVAILABLE as TB_PYTE
from peovim.ui.terminal_buffer import TerminalBuffer

_PYTE_AVAILABLE = importlib.util.find_spec("pyte") is not None


class TestTerminalBuffer:
    def test_initial_state_not_open(self):
        tb = TerminalBuffer("test", rows=10, cols=40)
        assert not tb.is_open

    def test_name_property(self):
        tb = TerminalBuffer("myterm", rows=10, cols=40)
        assert tb.name == "myterm"

    def test_creates_pyte_screen_if_available(self):
        tb = TerminalBuffer("test", rows=10, cols=40)
        if TB_PYTE:
            assert tb._screen is not None
            assert tb._screen.columns == 40
            assert tb._screen.lines == 10

    @pytest.mark.skipif(not _PYTE_AVAILABLE, reason="pyte not installed")
    def test_feed_updates_screen(self):
        tb = TerminalBuffer("test", rows=5, cols=20)
        tb.feed(b"Hello, world!")
        text = tb.read()
        assert "Hello, world!" in text

    @pytest.mark.skipif(not _PYTE_AVAILABLE, reason="pyte not installed")
    def test_read_returns_screen_text(self):
        tb = TerminalBuffer("test", rows=3, cols=20)
        tb.feed(b"line one\r\nline two")
        text = tb.read()
        assert "line one" in text

    @pytest.mark.skipif(not _PYTE_AVAILABLE, reason="pyte not installed")
    def test_resize_updates_dimensions(self):
        tb = TerminalBuffer("test", rows=5, cols=20)
        tb.resize(10, 80)
        assert tb._rows == 10
        assert tb._cols == 80
        if TB_PYTE:
            assert tb._screen.columns == 80
            assert tb._screen.lines == 10

    def test_close_sets_not_open(self):
        tb = TerminalBuffer("test", rows=5, cols=20)
        tb._is_open = True  # simulate open
        tb.close()
        assert not tb.is_open

    @pytest.mark.skipif(not _PYTE_AVAILABLE, reason="pyte not installed")
    def test_render_returns_cell_grid_correct_size(self):
        tb = TerminalBuffer("test", rows=5, cols=20)
        grid = tb.render()
        assert grid.width == 20
        assert grid.height == 5

    @pytest.mark.skipif(not _PYTE_AVAILABLE, reason="pyte not installed")
    def test_render_maps_text_to_cells(self):
        tb = TerminalBuffer("test", rows=3, cols=20)
        tb.feed(b"Hello")
        grid = tb.render()
        # First few cells should have 'H', 'e', 'l', 'l', 'o'
        row0 = grid._current[0]
        chars = "".join(cell[0] for cell in row0[:5])
        assert chars == "Hello"

    @pytest.mark.skipif(not _PYTE_AVAILABLE, reason="pyte not installed")
    def test_render_bold_attribute(self):
        from peovim.ui.backend import ATTR_BOLD

        tb = TerminalBuffer("test", rows=3, cols=40)
        # ESC[1m sets bold
        tb.feed(b"\x1b[1mBold\x1b[0m")
        grid = tb.render()
        # Bold cells should have ATTR_BOLD set
        row0 = grid._current[0]
        bold_cells = [cell for cell in row0[:4] if cell[3] & ATTR_BOLD]
        assert len(bold_cells) > 0

    def test_two_instances_are_independent(self):
        tb1 = TerminalBuffer("term1", rows=5, cols=20)
        tb2 = TerminalBuffer("term2", rows=10, cols=40)
        if TB_PYTE:
            assert tb1._screen is not tb2._screen
        assert tb1.name != tb2.name
        assert tb1._rows != tb2._rows
