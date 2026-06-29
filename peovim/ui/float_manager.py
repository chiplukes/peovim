"""
ui.float_manager — FloatManager: positioned float windows

Composites float windows over the main cell grid after the layout pass.
Manages z-ordering and the FloatHandle API (close, set_content).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from peovim.ui.cell_grid import CellGrid
from peovim.ui.text_layout import expand_for_display

FloatSegment = tuple[str, Any]
FloatLine = str | list[FloatSegment]

# ---------------------------------------------------------------------------
# Anchor types
# ---------------------------------------------------------------------------


@dataclass
class Absolute:
    """Fixed (col, row) position in terminal coordinates."""

    x: int
    y: int


@dataclass
class Centered:
    """Centered in the terminal grid."""


@dataclass
class CursorRelative:
    """Position relative to a provided cursor cell (row, col)."""

    row_offset: int = 1
    col_offset: int = 0


FloatAnchor = Absolute | Centered | CursorRelative

# ---------------------------------------------------------------------------
# Float data + FloatHandle
# ---------------------------------------------------------------------------

_BORDER_FG: tuple = (100, 100, 140)
_FLOAT_BG: tuple = (30, 30, 40)


@dataclass
class Float:
    content: list[FloatLine]
    width: int
    height: int
    border: bool
    title: str
    z_order: int
    focusable: bool
    on_close: Any
    on_key: Any = None
    anchor: Any = field(default_factory=Centered)
    scroll_offset: int = 0  # first visible content line


class FloatHandle:
    """Token returned by open_float(); lets the caller close or update the float."""

    def __init__(self, float_: Float, manager: FloatManager) -> None:
        self._float = float_
        self._manager = manager

    def close(self) -> None:
        self._manager.close_float(self)

    def set_content(self, content: list[FloatLine]) -> None:
        self._float.content = list(content)

    def set_title(self, title: str) -> None:
        self._float.title = title

    def set_anchor(self, anchor: Any) -> None:
        self._float.anchor = anchor

    def set_size(self, width: int, height: int) -> None:
        self._float.width = width
        self._float.height = height

    @property
    def is_open(self) -> bool:
        return self._float in self._manager._floats


# ---------------------------------------------------------------------------
# FloatManager
# ---------------------------------------------------------------------------


class FloatManager:  # cm:8a7c9d
    """Manages a sorted stack of floating windows composited over the main grid."""

    def __init__(self) -> None:
        self._floats: list[Float] = []
        self._focused: FloatHandle | None = None  # the float receiving keyboard input

    # ------------------------------------------------------------------
    # Open / close
    # ------------------------------------------------------------------

    def open_float(
        self,
        content: str | list[FloatLine],
        *,
        anchor: FloatAnchor | None = None,
        width: int = 60,
        height: int = 10,
        border: bool = True,
        title: str = "",
        focusable: bool = False,
        z_order: int = 0,
        on_close: Any = None,
        on_key: Any = None,
    ) -> FloatHandle:
        if isinstance(content, str):
            content = cast(list[FloatLine], content.splitlines())
        flt = Float(
            content=list(content),
            width=width,
            height=height,
            border=border,
            title=title,
            z_order=z_order,
            focusable=focusable,
            on_close=on_close,
            on_key=on_key,
            anchor=anchor or Centered(),
        )
        self._floats.append(flt)
        self._floats.sort(key=lambda f: f.z_order)
        return FloatHandle(flt, self)

    def focus(self, handle: FloatHandle) -> None:
        """Give keyboard focus to a float (enables scroll/close key routing)."""
        self._focused = handle

    def close_float(self, handle: FloatHandle) -> None:
        import contextlib

        if self._focused is handle:
            self._focused = None
        flt = handle._float
        if flt in self._floats:
            self._floats.remove(flt)
            if flt.on_close is not None:
                with contextlib.suppress(Exception):
                    flt.on_close()

    def close_all(self) -> None:
        import contextlib

        self._focused = None
        for flt in list(self._floats):
            if flt.on_close is not None:
                with contextlib.suppress(Exception):
                    flt.on_close()
        self._floats.clear()

    def feed_key(self, key: str) -> bool:
        """
        Route a key to the focused float.

        Returns True if the key was consumed (caller should not process it further).
        Keys handled:
          q / Escape / <C-c>  → close focused float
          j / <Down>          → scroll down 1
          k / <Up>            → scroll up 1
          <C-d>               → scroll down half-page
          <C-u>               → scroll up half-page
          <C-f> / <PageDown>  → scroll down full page
          <C-b> / <PageUp>    → scroll up full page
          g / gg / G          → top / bottom
        """
        h = self._focused
        if h is None or not h.is_open:
            self._focused = None
            return False

        flt = h._float
        inner_h = flt.height - (2 if flt.border else 0)
        max_scroll = max(0, len(flt.content) - inner_h)

        if flt.on_key is not None:
            try:
                if flt.on_key(key):
                    return True
            except Exception:
                pass

        if key in ("q", "<Esc>", "<C-c>"):
            h.close()
            return True
        elif key in ("j", "<Down>"):
            flt.scroll_offset = min(max_scroll, flt.scroll_offset + 1)
            return True
        elif key in ("k", "<Up>"):
            flt.scroll_offset = max(0, flt.scroll_offset - 1)
            return True
        elif key in ("<C-d>",):
            flt.scroll_offset = min(max_scroll, flt.scroll_offset + max(1, inner_h // 2))
            return True
        elif key in ("<C-u>",):
            flt.scroll_offset = max(0, flt.scroll_offset - max(1, inner_h // 2))
            return True
        elif key in ("<C-f>", "<PageDown>"):
            flt.scroll_offset = min(max_scroll, flt.scroll_offset + max(1, inner_h))
            return True
        elif key in ("<C-b>", "<PageUp>"):
            flt.scroll_offset = max(0, flt.scroll_offset - max(1, inner_h))
            return True
        elif key == "G":
            flt.scroll_offset = max_scroll
            return True
        elif key in ("g", "gg"):
            flt.scroll_offset = 0
            return True
        return False

    @property
    def has_focused(self) -> bool:
        return self._focused is not None and (self._focused.is_open)

    @property
    def has_visible(self) -> bool:
        return bool(self._floats)

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render(self, grid: CellGrid, cursor_x: int = 0, cursor_y: int = 0) -> None:
        """Composite all open floats onto the grid (sorted by z_order)."""
        focused_flt = self._focused._float if self._focused and self._focused.is_open else None
        for flt in self._floats:
            x, y = _resolve_anchor(flt.anchor, flt.width, flt.height, grid, cursor_x, cursor_y)
            _render_float(grid, flt, x, y, focused=(flt is focused_flt))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_anchor(anchor: Any, w: int, h: int, grid: CellGrid, cursor_x: int, cursor_y: int) -> tuple[int, int]:
    if isinstance(anchor, Absolute):
        return anchor.x, anchor.y
    if isinstance(anchor, CursorRelative):
        x = max(0, min(cursor_x + anchor.col_offset, grid.width - w))
        y = max(0, min(cursor_y + anchor.row_offset, grid.height - h))
        return x, y
    # Centered
    x = max(0, (grid.width - w) // 2)
    y = max(0, (grid.height - h) // 2)
    return x, y


def _render_float(grid: CellGrid, flt: Float, x: int, y: int, focused: bool = False) -> None:
    w, h = flt.width, flt.height
    border_fg = (140, 140, 200) if focused else _BORDER_FG
    if flt.border:
        title = flt.title
        if focused:
            title = (title + "  " if title else "") + "q/Esc:close  j/k:scroll  y:yank"
        draw_border(grid, x, y, w, h, title, fg=border_fg, bg=_FLOAT_BG)
        inner_x, inner_y = x + 1, y + 1
        inner_w, inner_h = w - 2, h - 2
    else:
        inner_x, inner_y = x, y
        inner_w, inner_h = w, h

    for row in range(inner_h):
        r = inner_y + row
        grid.fill(r, inner_x, inner_w, bg=_FLOAT_BG)

    visible_lines = flt.content[flt.scroll_offset :]
    for i, line in enumerate(visible_lines):
        if i >= inner_h:
            break
        _render_float_line(grid, inner_y + i, inner_x, inner_w, line)

    # Scroll indicator on the right border when there is more content
    if flt.border and len(flt.content) > inner_h:
        total = len(flt.content)
        pct = flt.scroll_offset / max(1, total - inner_h)
        bar_row = y + 1 + int(pct * max(0, inner_h - 1))
        bar_row = min(bar_row, y + h - 2)
        grid.write(bar_row, x + w - 1, "█", fg=border_fg, bg=_FLOAT_BG)


def draw_border(
    grid: CellGrid, x: int, y: int, w: int, h: int, title: str = "", fg: Any = None, bg: Any = None
) -> None:
    """Draw a single-line box border at (x, y) with dimensions w×h."""
    inner_w = w - 2
    # Top
    grid.write(y, x, "┌", fg=fg, bg=bg)
    if title and inner_w > 4:
        t = f" {title} "[:inner_w]
        pad = inner_w - len(t)
        top_inner = "─" * (pad // 2) + t + "─" * (pad - pad // 2)
    else:
        top_inner = "─" * inner_w
    grid.write_str(y, x + 1, top_inner, fg=fg, bg=bg)
    grid.write(y, x + w - 1, "┐", fg=fg, bg=bg)
    # Sides
    for row in range(1, h - 1):
        grid.write(y + row, x, "│", fg=fg, bg=bg)
        grid.write(y + row, x + w - 1, "│", fg=fg, bg=bg)
    # Bottom
    if h > 1:
        grid.write(y + h - 1, x, "└", fg=fg, bg=bg)
        grid.write_str(y + h - 1, x + 1, "─" * inner_w, fg=fg, bg=bg)
        grid.write(y + h - 1, x + w - 1, "┘", fg=fg, bg=bg)


def _render_float_line(grid: CellGrid, row: int, col: int, width: int, line: FloatLine) -> None:
    if isinstance(line, str):
        visible = expand_for_display(line, 4)[:width].ljust(width)
        grid.write_str(row, col, visible, bg=_FLOAT_BG)
        return

    remaining = width
    cursor = col
    for text, style in line:
        if remaining <= 0:
            break
        expanded = expand_for_display(text, 4)
        if not expanded:
            continue
        chunk = expanded[:remaining]
        grid.write_str(
            row,
            cursor,
            chunk,
            fg=getattr(style, "fg", None),
            bg=getattr(style, "bg", _FLOAT_BG) or _FLOAT_BG,
            attrs=getattr(style, "attrs", 0),
        )
        cursor += len(chunk)
        remaining -= len(chunk)
    if remaining > 0:
        grid.fill(row, cursor, remaining, bg=_FLOAT_BG)
