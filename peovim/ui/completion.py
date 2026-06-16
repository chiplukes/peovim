"""
ui.completion — Completion popup widget.

Cursor-anchored popup showing LSP completion items. Tab/Enter to accept,
Escape/Ctrl-e to dismiss, arrows to navigate.
"""

from __future__ import annotations

from peovim.ui.cell_grid import CellGrid

_BG = (30, 30, 46)
_FG = (205, 214, 244)
_FG_SELECTED = (166, 227, 161)
_BG_SELECTED = (49, 50, 68)
_FG_KIND = (137, 148, 186)
_FG_DETAIL = (108, 112, 134)
_BORDER = (88, 91, 112)

_MAX_VISIBLE = 10
_MIN_WIDTH = 20
_MAX_WIDTH = 50

_KIND_LABELS = {
    1: "txt",
    2: "mth",
    3: "fn ",
    4: "ctr",
    5: "fld",
    6: "var",
    7: "cls",
    8: "ifc",
    9: "mod",
    10: "prp",
    11: "unt",
    12: "val",
    13: "enm",
    14: "kw ",
    15: "snp",
    16: "col",
    17: "ref",
    18: "fil",
    19: "fld",
    20: "cst",
    21: "str",
    22: "evt",
    23: "op ",
    24: "typ",
}


class CompletionPopup:
    """
    Cursor-anchored completion popup.

    Usage:
        popup.open(items, anchor_line, anchor_col)
        # in input loop:
        if popup.is_open and popup.feed_key(key): continue
        # in render:
        popup.render(grid, cursor_screen_row, cursor_screen_col)
    """

    def __init__(self) -> None:
        self._items: list[dict] = []
        self._selected: int = 0
        self._scroll: int = 0
        self._open: bool = False
        self._anchor_line: int = 0
        self._anchor_col: int = 0
        self._filter: str = ""
        self._match_mode: str = "substring"
        self._replace_filter_on_accept: bool = False

    @property
    def is_open(self) -> bool:
        return self._open

    def open(
        self,
        items: list[dict],
        anchor_line: int,
        anchor_col: int,
        filter_text: str = "",
        *,
        match_mode: str = "substring",
        replace_filter_on_accept: bool = False,
    ) -> None:
        self._items = items
        self._anchor_line = anchor_line
        self._anchor_col = anchor_col
        self._selected = 0
        self._scroll = 0
        self._filter = filter_text
        self._match_mode = match_mode
        self._replace_filter_on_accept = replace_filter_on_accept
        self._open = bool(self._filtered_items())

    def close(self) -> None:
        self._open = False
        self._items = []
        self._filter = ""
        self._match_mode = "substring"
        self._replace_filter_on_accept = False

    def accept(self) -> str | None:
        """Return the insert text of the selected item, or None."""
        filtered = self._filtered_items()
        if not filtered or self._selected >= len(filtered):
            return None
        item = filtered[self._selected]
        text = item.get("insertText") or item.get("label", "")
        if self._replace_filter_on_accept and self._filter and text.startswith(self._filter):
            text = text[len(self._filter) :]
        self.close()
        return text

    def update_filter(self, text: str) -> None:
        """Update the filter text (e.g. as user types in insert mode)."""
        self._filter = text
        self._selected = 0
        self._scroll = 0
        if not self._filtered_items():
            self.close()

    def feed_key(self, key: str) -> bool:
        """
        Process a key. Returns True if the key was consumed.
        Caller should check accept() return value and close() state after Tab/Enter.
        """
        filtered = self._filtered_items()
        n = len(filtered)
        if key in ("<Down>", "<C-n>"):
            self._selected = (self._selected + 1) % max(1, n)
            self._ensure_scroll()
            return True
        if key in ("<Up>", "<C-p>"):
            self._selected = (self._selected - 1) % max(1, n)
            self._ensure_scroll()
            return True
        if key in ("<Esc>", "<C-e>"):
            self.close()
            return True
        # Tab and Enter are NOT consumed here — caller handles them to trigger accept()
        return False

    def _filtered_items(self) -> list[dict]:
        if not self._filter:
            return self._items
        f = self._filter.lower()
        if self._match_mode == "prefix":
            return [it for it in self._items if (it.get("filterText") or it.get("label", "")).lower().startswith(f)]
        return [it for it in self._items if f in (it.get("filterText") or it.get("label", "")).lower()]

    def _ensure_scroll(self) -> None:
        if self._selected < self._scroll:
            self._scroll = self._selected
        elif self._selected >= self._scroll + _MAX_VISIBLE:
            self._scroll = self._selected - _MAX_VISIBLE + 1

    def render(self, grid: CellGrid, cursor_screen_row: int, cursor_screen_col: int) -> None:
        """Draw the popup onto the grid, anchored near the cursor."""
        if not self._open:
            return
        filtered = self._filtered_items()
        if not filtered:
            return

        visible = filtered[self._scroll : self._scroll + _MAX_VISIBLE]
        n_visible = len(visible)

        # Compute width from items
        width = _MIN_WIDTH
        for it in visible:
            label_w = len(it.get("label", "")) + 5  # kind prefix + space
            width = max(width, min(label_w, _MAX_WIDTH))
        width = min(width, grid.width - 2)

        popup_height = n_visible + 2  # +2 for border

        # Position: prefer below cursor, flip up if not enough space
        row = cursor_screen_row + 1
        col = cursor_screen_col
        if row + popup_height > grid.height:
            row = cursor_screen_row - popup_height
        if col + width > grid.width:
            col = grid.width - width
        col = max(0, col)
        row = max(0, row)

        # Draw border
        self._draw_border(grid, row, col, width, popup_height)

        # Draw items
        for i, item in enumerate(visible):
            screen_row = row + 1 + i
            is_sel = (self._scroll + i) == self._selected
            bg = _BG_SELECTED if is_sel else _BG
            fg = _FG_SELECTED if is_sel else _FG

            kind = item.get("kind", 1)
            kind_label = _KIND_LABELS.get(kind, "   ")
            label = item.get("label", "")
            detail = item.get("detail", "")

            # Clear row
            grid.fill(screen_row, col + 1, max(0, width - 2), fg=fg, bg=bg)

            # kind [3 chars]
            x = col + 1
            if x + 3 < col + width - 1:
                grid.write_str(screen_row, x, kind_label, _FG_KIND, bg)
                x += 3
                grid.write_str(screen_row, x, " ", fg, bg)
                x += 1

            # label
            available = col + width - 1 - x
            if available > 0:
                text = label[:available]
                grid.write_str(screen_row, x, text, fg, bg)
                x += len(text)

            # detail (dimmed, right-aligned)
            if detail and x < col + width - 2:
                remaining = col + width - 2 - x
                if remaining > 3:
                    det = detail[:remaining]
                    grid.write_str(screen_row, x, " " + det, _FG_DETAIL, bg)

    def _draw_border(self, grid: CellGrid, row: int, col: int, width: int, height: int) -> None:
        """Draw a simple box border."""

        def _write(r, c, ch):
            if 0 <= r < grid.height and 0 <= c < grid.width:
                grid.write(r, c, ch, _BORDER, _BG)

        _write(row, col, "┌")
        _write(row, col + width - 1, "┐")
        _write(row + height - 1, col, "└")
        _write(row + height - 1, col + width - 1, "┘")
        for c in range(col + 1, col + width - 1):
            _write(row, c, "─")
            _write(row + height - 1, c, "─")
        for r in range(row + 1, row + height - 1):
            _write(r, col, "│")
            _write(r, col + width - 1, "│")
            grid.fill(r, col + 1, max(0, width - 2), fg=_FG, bg=_BG)
