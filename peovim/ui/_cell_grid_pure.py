"""
ui.cell_grid — CellGrid: 2D cell buffer with per-cell dirty tracking

Each cell stores (char, fg, bg, attrs). flush() diffs against the previous
frame and emits only the changed cells as a list[RenderOp]. This minimises
terminal writes to the true diff.

See notes/architecture.md §Hot Code Paths — CellGrid is ~300-800µs per frame.
"""

from __future__ import annotations

from peovim.ui.backend import Color, MoveCursor, PutCells, RenderOp

# Attribute bit flags (must match peovim.ui.backend)
_ATTR_BOLD = 1
_ATTR_ITALIC = 2
_ATTR_UNDERLINE = 4
_ATTR_BLINK = 8
_ATTR_REVERSE = 16
_ATTR_STRIKETHROUGH = 32
_ATTR_DIM = 64

# ---------------------------------------------------------------------------
# Cell representation
# ---------------------------------------------------------------------------

# Cell = (char, fg, bg, attrs) — plain tuple for fast comparison and allocation
Cell = tuple  # tuple[str, Color, Color, int]
_BLANK: Cell = (" ", None, None, 0)

# Cell interning cache: (fg, bg, attrs) -> {char -> cell_tuple}
# Reusing existing tuple objects instead of creating new ones each frame
# eliminates GC pressure from ~1500 newly-tracked tuples per render.
_CELL_CACHE: dict[tuple, dict[str, Cell]] = {}


# ---------------------------------------------------------------------------
# CellGrid
# ---------------------------------------------------------------------------


class CellGrid:
    """
    2D array of cells with double-buffering for minimal terminal writes.

    write() / write_str() / fill() modify _current.
    flush() diffs _current against _prev and returns list[RenderOp] covering
    only changed cells. After flush, _prev matches _current.
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._current: list[list[Cell]] = [[_BLANK] * width for _ in range(height)]
        self._prev: list[list[Cell]] = [[_BLANK] * width for _ in range(height)]
        self._dirty_rows: set[int] = set()
        self._sorted_dirty: list[int] = []
        self._ops: list[RenderOp] = []
        self._run_chars: list[str] = []
        self._ansi_str_buf: list[str] = []

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def write(self, row: int, col: int, char: str, fg: Color = None, bg: Color = None, attrs: int = 0) -> None:
        """Write a single character cell. Silently ignores out-of-bounds."""
        if 0 <= row < self.height and 0 <= col < self.width:
            style_key = (fg, bg, attrs)
            char_map = _CELL_CACHE.get(style_key)
            if char_map is None:
                char_map = {}
                _CELL_CACHE[style_key] = char_map
            cell = char_map.get(char)
            if cell is None:
                cell = (char, fg, bg, attrs)
                char_map[char] = cell
            self._current[row][col] = cell
            self._dirty_rows.add(row)

    def write_str(self, row: int, col: int, text: str, fg: Color = None, bg: Color = None, attrs: int = 0) -> None:
        """Write a string of characters starting at (row, col). Clips at width."""
        if row < 0 or row >= self.height or not text:
            return
        start = max(col, 0)
        if start >= self.width:
            return
        text = text[start - col : start - col + (self.width - start)]
        if not text:
            return
        style_key = (fg, bg, attrs)
        char_map = _CELL_CACHE.get(style_key)
        if char_map is None:
            char_map = {}
            _CELL_CACHE[style_key] = char_map
        get_cell = char_map.get
        row_data = self._current[row]
        for i, ch in enumerate(text):
            cell = get_cell(ch)
            if cell is None:
                cell = (ch, fg, bg, attrs)
                char_map[ch] = cell
            row_data[start + i] = cell
        self._dirty_rows.add(row)

    def write_padded(
        self, row: int, col: int, text: str, width: int, fg: Color = None, bg: Color = None, attrs: int = 0
    ) -> None:
        """Write text and fill the remaining span with spaces using the same style."""
        if row < 0 or row >= self.height or width <= 0:
            return
        text = text[:width]
        if text:
            self.write_str(row, col, text, fg=fg, bg=bg, attrs=attrs)
        fill_start = col + len(text)
        remaining = width - len(text)
        if remaining > 0:
            self.fill(row, fill_start, remaining, fg=fg, bg=bg, attrs=attrs)

    def fill(
        self, row: int, col: int, width: int, char: str = " ", fg: Color = None, bg: Color = None, attrs: int = 0
    ) -> None:
        """Fill `width` cells starting at (row, col) with the same style."""
        if row < 0 or row >= self.height or width <= 0:
            return
        start = max(col, 0)
        if start >= self.width:
            return
        span = min(width - (start - col), self.width - start)
        if span <= 0:
            return
        style_key = (fg, bg, attrs)
        char_map = _CELL_CACHE.get(style_key)
        if char_map is None:
            char_map = {}
            _CELL_CACHE[style_key] = char_map
        cell = char_map.get(char)
        if cell is None:
            cell = (char, fg, bg, attrs)
            char_map[char] = cell
        self._current[row][start : start + span] = [cell] * span
        self._dirty_rows.add(row)

    def invalidate_prev_rows(self, start_row: int, end_row: int) -> None:
        """
        Force all cells in rows [start_row, end_row) to appear changed on the
        next flush by filling those rows in _prev with a null sentinel cell.
        Used to guarantee a full row re-output (e.g. after clearing overlays).
        """
        sentinel = [("\0", None, None, 0)] * self.width
        for row in range(max(0, start_row), min(end_row, self.height)):
            self._prev[row] = list(sentinel)
            self._dirty_rows.add(row)

    def read_cell(self, row: int, col: int) -> tuple:
        """Return the cell tuple (char, fg, bg, attrs) at (row, col). Bounds-checked."""
        if 0 <= row < self.height and 0 <= col < self.width:
            return self._current[row][col]
        return _BLANK

    def paint_style_range(
        self, row: int, start_col: int, end_col: int, fg: Color = None, bg: Color = None, attrs: int = 0
    ) -> None:
        """
        Overlay fg/bg/attrs onto existing cells in [start_col, end_col) on row.
        None fg/bg means keep existing; attrs=0 means keep existing.
        """
        if row < 0 or row >= self.height:
            return
        start_col = max(start_col, 0)
        end_col = min(end_col, self.width)
        if start_col >= end_col:
            return
        row_cells = self._current[row]
        for col in range(start_col, end_col):
            existing = row_cells[col]
            row_cells[col] = (
                existing[0],
                fg if fg is not None else existing[1],
                bg if bg is not None else existing[2],
                attrs if attrs != 0 else existing[3],
            )
        self._dirty_rows.add(row)

    def clear(self) -> None:
        """Reset all cells in _current to blank."""
        for row in range(self.height):
            self._current[row] = [_BLANK] * self.width
        self._dirty_rows.update(range(self.height))

    def apply_default_style(self, *, fg: Color = None, bg: Color = None) -> None:
        """Replace missing fg/bg values with provided defaults in the current frame."""
        if fg is None and bg is None:
            return
        for row in range(self.height):
            changed = False
            updated_row = self._current[row].copy()
            for col, (char, current_fg, current_bg, attrs) in enumerate(updated_row):
                resolved_fg = fg if current_fg is None else current_fg
                resolved_bg = bg if current_bg is None else current_bg
                if resolved_fg != current_fg or resolved_bg != current_bg:
                    updated_row[col] = (char, resolved_fg, resolved_bg, attrs)
                    changed = True
            if changed:
                self._current[row] = updated_row
                self._dirty_rows.add(row)

    def blit(self, src: CellGrid, dest_x: int, dest_y: int) -> None:
        """Copy src._current into self._current at offset (dest_x, dest_y)."""
        for sr in range(src.height):
            dr = dest_y + sr
            if dr < 0:
                continue
            if dr >= self.height:
                break
            src_start = max(0, -dest_x)
            dest_start = max(dest_x, 0)
            span = min(src.width - src_start, self.width - dest_start)
            if span <= 0:
                continue
            self._current[dr][dest_start : dest_start + span] = src._current[sr][src_start : src_start + span]
            self._dirty_rows.add(dr)

    # ------------------------------------------------------------------
    # Flush (diff → RenderOps)
    # ------------------------------------------------------------------

    def flush(self) -> list[RenderOp]:
        """
        Diff _current against _prev. Return minimal list[RenderOp].
        Groups consecutive dirty cells on the same row and same style into
        a single PutCells run to minimize MoveCursor emissions.
        After flush, _prev is updated to match _current.
        """
        if not self._dirty_rows:
            return []

        ops = self._ops
        ops.clear()
        append_op = ops.append

        sorted_dirty = self._sorted_dirty
        sorted_dirty.clear()
        sorted_dirty.extend(self._dirty_rows)
        sorted_dirty.sort()

        for row in sorted_dirty:
            cur_row = self._current[row]
            prev_row = self._prev[row]
            if cur_row == prev_row:
                continue

            run_start: int | None = None
            run_chars = self._run_chars
            run_chars.clear()
            run_fg: Color = None
            run_bg: Color = None
            run_attrs: int = 0

            for col in range(self.width):
                curr = cur_row[col]
                prev = prev_row[col]

                if curr == prev:
                    # Unchanged cell — flush any pending run
                    if run_start is not None:
                        append_op(MoveCursor(row, run_start))
                        append_op(PutCells("".join(run_chars), run_fg, run_bg, run_attrs))
                        run_start = None
                        run_chars.clear()
                else:
                    char, fg, bg, attrs = curr
                    if run_start is None:
                        # Start a new run
                        run_start = col
                        run_fg = fg
                        run_bg = bg
                        run_attrs = attrs
                        run_chars.append(char)
                    elif fg == run_fg and bg == run_bg and attrs == run_attrs:
                        # Same style — extend the run
                        run_chars.append(char)
                    else:
                        # Style break — flush current run, start a new one
                        append_op(MoveCursor(row, run_start))
                        append_op(PutCells("".join(run_chars), run_fg, run_bg, run_attrs))
                        run_start = col
                        run_fg = fg
                        run_bg = bg
                        run_attrs = attrs
                        run_chars.clear()
                        run_chars.append(char)

            # End of row — flush any pending run
            if run_start is not None:
                append_op(MoveCursor(row, run_start))
                append_op(PutCells("".join(run_chars), run_fg, run_bg, run_attrs))

            self._prev[row][:] = cur_row

        self._dirty_rows.clear()
        return ops

    def flush_ansi(self, out: bytearray) -> None:
        """Like flush() but writes ANSI escape sequences directly into out (cleared first)."""
        ops = self.flush()
        out.clear()
        buf = self._ansi_str_buf
        buf.clear()
        for op in ops:
            if isinstance(op, MoveCursor):
                buf.append(f"\x1b[{op.row + 1};{op.col + 1}H")
            elif isinstance(op, PutCells):
                buf.append("\x1b[0m")
                if op.fg is not None:
                    r, g, b = op.fg
                    buf.append(f"\x1b[38;2;{r};{g};{b}m")
                if op.bg is not None:
                    r, g, b = op.bg
                    buf.append(f"\x1b[48;2;{r};{g};{b}m")
                if op.attrs:
                    codes: list[str] = []
                    if op.attrs & _ATTR_BOLD:
                        codes.append("1")
                    if op.attrs & _ATTR_DIM:
                        codes.append("2")
                    if op.attrs & _ATTR_ITALIC:
                        codes.append("3")
                    if op.attrs & _ATTR_UNDERLINE:
                        codes.append("4")
                    if op.attrs & _ATTR_BLINK:
                        codes.append("5")
                    if op.attrs & _ATTR_REVERSE:
                        codes.append("7")
                    if op.attrs & _ATTR_STRIKETHROUGH:
                        codes.append("9")
                    buf.append(f"\x1b[{';'.join(codes)}m")
                buf.append(op.text)
        out.extend("".join(buf).encode("utf-8", errors="replace"))
