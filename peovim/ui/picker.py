"""
ui.picker — Fuzzy picker widget

Full-height float covering the bottom portion of the terminal.
Prompt line at top, filtered item list below (with optional preview pane).
Input interception: when open, the EventLoop routes all keystrokes here.

Fuzzy filtering uses rapidfuzz if available; falls back to substring match.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

from peovim.core.style import Style
from peovim.ui.cell_grid import CellGrid
from peovim.ui.float_manager import draw_border

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

_BG: tuple = (25, 25, 38)
_BORDER_FG: tuple = (80, 80, 120)
_PROMPT_FG: tuple = (200, 200, 255)
_ITEM_FG: tuple = (180, 180, 180)
_SEL_FG: tuple = (255, 255, 255)
_SEL_BG: tuple = (50, 50, 90)
_MATCH_FG: tuple = (255, 200, 80)
_PREVIEW_FG: tuple = (140, 160, 140)
_COUNT_FG: tuple = (100, 100, 140)

PreviewSegment = tuple[str, Style]
PreviewLine = str | list[PreviewSegment]

# Keys that move focus
_UP_KEYS = {"<Up>", "<C-k>", "<C-p>"}
_DOWN_KEYS = {"<Down>", "<C-j>", "<C-n>"}
_CONFIRM_KEYS = {"<Enter>", "<CR>"}
_CLOSE_KEYS = {"<Esc>", "<C-c>"}
_BACKSPACE_KEYS = {"<Backspace>", "<C-h>", "<BS>"}


# ---------------------------------------------------------------------------
# PickerWidget
# ---------------------------------------------------------------------------


class PickerWidget:  # cm:1f6e5b
    """
    Stateful picker UI.

    open() activates it; feed_key() handles input; render() draws to grid.
    """

    def __init__(self) -> None:
        self._open: bool = False
        self._title: str = ""
        self._items: list[Any] = []
        self._source: Any = None  # list or callable(query)->list
        self._filtered: list[Any] = []  # display strings after filter
        self._query: str = ""
        self._sel: int = 0
        self._on_confirm: Any = None
        self._on_close: Any = None
        self._preview_fn: Any = None
        self._item_style: Any = None  # callable(item) -> (fg, bg?) | None
        self._multi_select: bool = False
        self._selected_set: set[int] = set()  # multi-select indices
        self._keymap: dict[str, Any] = {}
        # Async / debounced source support
        self._debounce_ms: int = 0
        self._async_timer: threading.Timer | None = None
        self._search_id: int = 0
        self._async_queue: deque[tuple[int, str, list[Any]]] = deque()

    # ------------------------------------------------------------------
    # Open / close
    # ------------------------------------------------------------------

    def open(
        self,
        title: str,
        source: Any,
        *,
        on_confirm: Any = None,
        on_close: Any = None,
        multi_select: bool = False,
        preview: Any = None,
        keymap: dict | None = None,
        item_style: Any = None,
        debounce_ms: int = 0,
    ) -> None:
        self._title = title
        self._source = source
        self._on_confirm = on_confirm
        self._on_close = on_close
        self._multi_select = multi_select
        self._preview_fn = preview
        self._item_style = item_style
        self._keymap = keymap or {}
        self._query = ""
        self._sel = 0
        self._selected_set = set()
        self._debounce_ms = debounce_ms or 0
        self._search_id = 0
        if self._async_timer is not None:
            self._async_timer.cancel()
            self._async_timer = None
        self._async_queue.clear()
        self._open = True
        self._refresh()

    def close(self) -> None:
        if not self._open:
            return
        if self._async_timer is not None:
            self._async_timer.cancel()
            self._async_timer = None
        self._async_queue.clear()
        self._search_id += 1  # invalidate in-flight results
        self._open = False
        if self._on_close is not None:
            import contextlib

            with contextlib.suppress(Exception):
                self._on_close()

    @property
    def is_open(self) -> bool:
        return self._open

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def feed_key(self, key: str) -> None:
        if not self._open:
            return

        # Custom keymap overrides
        if key in self._keymap:
            import contextlib

            with contextlib.suppress(Exception):
                self._keymap[key]()
            return

        if key in _CLOSE_KEYS:
            self.close()
        elif key in _CONFIRM_KEYS:
            self._confirm()
        elif key in _UP_KEYS:
            self._move(-1)
        elif key in _DOWN_KEYS:
            self._move(1)
        elif key in _BACKSPACE_KEYS:
            if self._query:
                self._query = self._query[:-1]
                self._refresh()
        elif key == "<Tab>" and self._multi_select:
            if self._filtered:
                idx = self._sel
                if idx in self._selected_set:
                    self._selected_set.discard(idx)
                else:
                    self._selected_set.add(idx)
                self._move(1)
        elif len(key) == 1 and key.isprintable():
            self._query += key
            self._refresh()
        # else: ignored key

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render(self, grid: CellGrid, reserved_bottom: int = 0) -> None:
        if not self._open:
            return

        rows, cols = grid.height - reserved_bottom, grid.width
        h = min(rows, max(10, rows * 2 // 5))
        w = cols
        y = rows - h
        x = 0

        has_preview = self._preview_fn is not None and len(self._filtered) > 0
        list_w = (w - 2) // 2 if has_preview else w - 2
        preview_x = x + 1 + list_w + 1

        # Outer border
        draw_border(grid, x, y, w, h, self._title, fg=_BORDER_FG, bg=_BG)

        # Prompt row
        prompt_y = y + 1
        prompt_inner = w - 4
        grid.fill(prompt_y, x + 1, w - 2, bg=_BG)
        prompt_text = ("> " + self._query)[:prompt_inner]
        grid.write_str(prompt_y, x + 2, prompt_text, fg=_PROMPT_FG, bg=_BG)

        # Count
        count_str = f"{len(self._filtered)}/{len(self._items)}"
        grid.write_str(prompt_y, x + w - 2 - len(count_str), count_str, fg=_COUNT_FG, bg=_BG)

        # Divider
        div_y = y + 2
        if div_y < y + h - 1:
            grid.write(div_y, x, "├", fg=_BORDER_FG, bg=_BG)
            grid.write_str(div_y, x + 1, "─" * (w - 2), fg=_BORDER_FG, bg=_BG)
            grid.write(div_y, x + w - 1, "┤", fg=_BORDER_FG, bg=_BG)
            if has_preview:
                mid = x + 1 + list_w
                grid.write(div_y, mid, "┬", fg=_BORDER_FG, bg=_BG)

        # Item list area
        list_y = div_y + 1
        list_h = h - 4  # border-top + prompt + divider + border-bottom
        if list_h < 1:
            return

        # Scroll window around selection
        scroll = max(0, self._sel - list_h + 1)
        visible = self._filtered[scroll : scroll + list_h]

        for i, item in enumerate(visible):
            row_y = list_y + i
            if row_y >= y + h - 1:
                break
            abs_idx = scroll + i
            is_sel = abs_idx == self._sel
            is_multi = abs_idx in self._selected_set
            label = str(item)[: list_w - 2]
            prefix = "▶ " if is_sel else ("● " if is_multi else "  ")
            fg = _SEL_FG if is_sel else (_MATCH_FG if is_multi else _ITEM_FG)
            bg = _SEL_BG if is_sel else _BG
            if self._item_style is not None and not is_sel and not is_multi:
                try:
                    override = self._item_style(item)
                    if override:
                        fg = override[0]
                        if len(override) > 1:
                            bg = override[1]
                except Exception:
                    pass
            grid.fill(row_y, x + 1, list_w, bg=bg)
            grid.write_str(row_y, x + 1, prefix + label, fg=fg, bg=bg)

        # Fill remaining list rows
        for i in range(len(visible), list_h):
            row_y = list_y + i
            if row_y >= y + h - 1:
                break
            grid.fill(row_y, x + 1, list_w, bg=_BG)

        # Preview pane
        if has_preview and list_w < w - 3:
            preview_w = w - 2 - list_w - 1
            sel_item = self._filtered[self._sel] if self._filtered else None
            preview_lines: list[PreviewLine] = []
            if sel_item is not None:
                try:
                    result = self._preview_fn(sel_item)
                    if isinstance(result, str):
                        preview_lines = result.splitlines()
                    elif isinstance(result, list):
                        preview_lines = list(result)
                except Exception:
                    preview_lines = ["(preview error)"]

            # Vertical divider
            for i in range(list_h):
                row_y = list_y + i
                if row_y >= y + h - 1:
                    break
                mid = preview_x - 1
                grid.write(row_y, mid, "│", fg=_BORDER_FG, bg=_BG)

            # Preview content
            for i in range(list_h):
                row_y = list_y + i
                if row_y >= y + h - 1:
                    break
                grid.fill(row_y, preview_x, preview_w, bg=_BG)
                if i < len(preview_lines):
                    _write_preview_line(grid, row_y, preview_x, preview_w, preview_lines[i])

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """Reload items from source and filter by current query."""
        if callable(self._source) and self._debounce_ms > 0:
            self._refresh_async()
            return
        if callable(self._source):
            try:
                items = self._source(self._query)
            except Exception:
                items = []
        else:
            items = list(self._source) if self._source else []
        self._items = items
        self._filtered = _filter(self._query, items)
        self._sel = max(0, min(self._sel, len(self._filtered) - 1))

    def _refresh_async(self) -> None:
        """Debounced async refresh: cancel previous timer, start new one."""
        if self._async_timer is not None:
            self._async_timer.cancel()
            self._async_timer = None

        self._search_id += 1
        sid = self._search_id
        query = self._query

        def _do_search() -> None:
            try:
                items = self._source(query)
            except Exception:
                items = []
            self._async_queue.append((sid, query, items))

        self._async_timer = threading.Timer(self._debounce_ms / 1000.0, _do_search)
        self._async_timer.daemon = True
        self._async_timer.start()

    def poll_async_result(self) -> bool:
        """Apply pending async results on the main thread.

        Called by the event loop every frame. Returns True if items changed.
        """
        if not self._async_queue:
            return False
        changed = False
        while self._async_queue:
            sid, query, items = self._async_queue.popleft()
            if sid != self._search_id:
                continue
            if query != self._query:
                continue
            self._items = items
            self._filtered = _filter(query, items)
            self._sel = max(0, min(self._sel, len(self._filtered) - 1))
            changed = True
        return changed

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _move(self, delta: int) -> None:
        if not self._filtered:
            return
        self._sel = max(0, min(self._sel + delta, len(self._filtered) - 1))

    def _confirm(self) -> None:
        if self._on_confirm is None:
            self.close()
            return
        if self._multi_select and self._selected_set:
            selected = [self._filtered[i] for i in sorted(self._selected_set) if i < len(self._filtered)]
        else:
            selected = self._filtered[self._sel] if self._filtered else None
        self.close()
        import contextlib

        with contextlib.suppress(Exception):
            self._on_confirm(selected)


# ---------------------------------------------------------------------------
# Fuzzy filtering (rapidfuzz if available, else substring)
# ---------------------------------------------------------------------------


def _filter(query: str, items: list[Any]) -> list[Any]:
    if not query:
        return list(items)
    q = query.lower()
    try:
        from rapidfuzz import fuzz as _fuzz

        scored: list[tuple[float, Any]] = []
        for item in items:
            score = _fuzz.partial_ratio(q, str(item).lower())
            if score >= 40:
                scored.append((score, item))
        scored.sort(key=lambda t: -t[0])
        return [item for _, item in scored]
    except ImportError:
        return [item for item in items if q in str(item).lower()]


def _write_preview_line(grid: CellGrid, row: int, col: int, width: int, line: PreviewLine) -> None:
    """Render a preview line, supporting both plain text and styled segments."""
    if width <= 0:
        return
    if isinstance(line, str):
        grid.write_str(row, col, line[:width], fg=_PREVIEW_FG, bg=_BG)
        return

    remaining = width
    cursor = col
    for text, style in line:
        if remaining <= 0 or not text:
            continue
        piece = text[:remaining]
        fg = style.fg if style.fg is not None else _PREVIEW_FG
        bg = style.bg if style.bg is not None else _BG
        grid.write_str(row, cursor, piece, fg=fg, bg=bg, attrs=style.attrs)
        cursor += len(piece)
        remaining -= len(piece)
