"""
ui.which_key_panel — Bottom-panel widget for which-key hints.

Renders a compact key-binding reference at the bottom of the screen,
similar to VS Code's terminal/panel area.  The panel is visible when
`is_open` is True and is rendered above the status bar.

The owning code (EventLoop) is responsible for reserving rows.
"""

from __future__ import annotations

from peovim.ui.cell_grid import CellGrid

# Colours
_FG_KEY: tuple = (255, 215, 0)  # gold — key chord
_FG_DESC: tuple = (180, 180, 180)  # dim — description
_FG_TITLE: tuple = (100, 200, 255)  # light-blue — title bar
_BG: tuple = (30, 30, 40)  # dark background
_BG_TITLE: tuple = (20, 20, 30)  # slightly darker title row

_MAX_ROWS = 10  # maximum panel height in rows
_COL_WIDTH = 36  # characters per column


class WhichKeyPanel:
    """Bottom panel that shows which-key hints for the current prefix."""

    def __init__(self) -> None:
        self.is_open: bool = False
        self._title: str = ""
        self._bindings: list[tuple[str, str]] = []  # (keys, desc) pairs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(self, bindings: list[tuple[str, str]], title: str = "") -> None:
        """Display the panel with the given bindings."""
        self._bindings = bindings
        self._title = title
        self.is_open = True

    def hide(self) -> None:
        """Hide the panel."""
        self.is_open = False
        self._bindings = []
        self._title = ""

    def panel_height(self, terminal_width: int) -> int:
        """Return how many rows this panel needs (including title row)."""
        if not self.is_open or not self._bindings:
            return 0
        rows_needed = self._rows_for_width(terminal_width)
        return min(_MAX_ROWS, rows_needed + 1)  # +1 for title row

    def render(self, grid: CellGrid, start_row: int, terminal_width: int) -> None:
        """Render the panel into *grid* starting at *start_row*."""
        if not self.is_open or not self._bindings:
            return

        height = self.panel_height(terminal_width)
        if height == 0:
            return

        # Fill background for all panel rows
        for r in range(start_row, start_row + height):
            if r >= grid.height:
                break
            grid.fill(r, 0, terminal_width, " ", None, _BG)

        # Title row
        title_row = start_row
        if title_row < grid.height:
            title_text = f" {self._title} " if self._title else " Which Key "
            grid.fill(title_row, 0, terminal_width, " ", None, _BG_TITLE)
            grid.write_str(title_row, 0, title_text, _FG_TITLE, _BG_TITLE)

        # Binding rows — multi-column layout
        body_start = start_row + 1
        body_rows = height - 1
        if body_rows <= 0:
            return

        num_cols = max(1, terminal_width // _COL_WIDTH)

        row_idx = 0
        col_idx = 0
        for keys, desc in self._bindings:
            r = body_start + row_idx
            if r >= grid.height or r >= start_row + height:
                break
            x = col_idx * _COL_WIDTH

            # Render as " key - desc" truncated to column width
            key_part = f" {keys}"
            sep = " - "
            desc_str = desc or ""
            available = _COL_WIDTH - len(key_part) - len(sep)
            if available > 0:
                desc_str = desc_str[:available]
            grid.write_str(r, x, key_part, _FG_KEY, _BG)
            grid.write_str(r, x + len(key_part), sep + desc_str, _FG_DESC, _BG)

            col_idx += 1
            if col_idx >= num_cols:
                col_idx = 0
                row_idx += 1

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _rows_for_width(self, width: int) -> int:
        if not self._bindings:
            return 0
        num_cols = max(1, width // _COL_WIDTH)
        return (len(self._bindings) + num_cols - 1) // num_cols
