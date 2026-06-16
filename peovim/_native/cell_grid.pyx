# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
# cython: nonecheck=False
"""
peovim._native.cell_grid — Cython-accelerated CellGrid, Phase 3: packed storage.

Each cell is stored as 4 consecutive int32 values in a flat C array:
    [codepoint, fg_packed, bg_packed, attrs]

Color encoding:
    None  → -1
    (r,g,b) → (r << 16) | (g << 8) | b   (all values 0-255, so fits int32)

The public Python API (write/write_str/fill/blit/flush/clear/apply_default_style)
is identical to the pure-Python CellGrid.  _current and _prev are exposed as
Python list[list[Cell]] properties for test/legacy compatibility.
"""
from __future__ import annotations

from cpython.mem cimport PyMem_Malloc, PyMem_Free
from cpython.bytearray cimport PyByteArray_Resize, PyByteArray_AS_STRING, PyByteArray_GET_SIZE
from libc.string cimport memcpy, memcmp

from peovim.ui.backend import MoveCursor, PutCells

# ---------------------------------------------------------------------------
# Cell constants (kept for public API compatibility)
# ---------------------------------------------------------------------------
Cell = tuple
_BLANK = (" ", None, None, 0)

# Sentinel meaning "color is None"
cdef int COLOR_NONE = -1

# Number of int32 fields per cell
cdef int CELL_STRIDE = 4


# ---------------------------------------------------------------------------
# Helpers: encode / decode color and cell
# ---------------------------------------------------------------------------

cdef inline int _encode_color(object color) noexcept:
    """Encode (r,g,b) tuple → packed int32. None → COLOR_NONE."""
    if color is None:
        return COLOR_NONE
    return ((<int>color[0]) << 16) | ((<int>color[1]) << 8) | (<int>color[2])


cdef inline object _decode_color(int packed) noexcept:
    """Decode packed int32 → (r,g,b) tuple or None."""
    if packed == COLOR_NONE:
        return None
    return ((<int>(packed >> 16)) & 0xFF,
            (<int>(packed >> 8))  & 0xFF,
            (<int> packed)        & 0xFF)


# ---------------------------------------------------------------------------
# ANSI attribute bit flags (must match peovim.ui.backend)
# ---------------------------------------------------------------------------

cdef int ATTR_BOLD          = 1        # SGR 1
cdef int ATTR_ITALIC        = 2        # SGR 3
cdef int ATTR_UNDERLINE     = 4        # SGR 4
cdef int ATTR_BLINK         = 8        # SGR 5
cdef int ATTR_REVERSE       = 16       # SGR 7
cdef int ATTR_STRIKETHROUGH = 32       # SGR 9
cdef int ATTR_DIM           = 64       # SGR 2


# ---------------------------------------------------------------------------
# C-level ANSI encoding helpers (used by flush_ansi)
# ---------------------------------------------------------------------------

cdef int _write_uint(char* buf, unsigned int v) noexcept:
    """Write unsigned decimal integer into buf. Returns byte count."""
    cdef char tmp[12]
    cdef int n = 0, i
    if v == 0:
        buf[0] = 48       # ord('0')
        return 1
    while v:
        tmp[n] = 48 + (v % 10)
        n += 1
        v //= 10
    for i in range(n):
        buf[i] = tmp[n - 1 - i]
    return n


cdef int _write_cursor_ansi(char* buf, int row, int col) noexcept:
    """Write ESC[row+1;col+1H into buf. Returns byte count."""
    cdef int pos = 0
    buf[pos] = 0x1b; pos += 1   # ESC
    buf[pos] = 91;   pos += 1   # '['
    pos += _write_uint(buf + pos, row + 1)
    buf[pos] = 59;   pos += 1   # ';'
    pos += _write_uint(buf + pos, col + 1)
    buf[pos] = 72;   pos += 1   # 'H'
    return pos


cdef int _write_style_ansi(char* buf, int fg, int bg, int attrs) noexcept:
    """Write SGR reset + optional fg/bg/attrs sequences into buf. Returns byte count."""
    cdef int pos = 0
    cdef bint first

    # Reset all attributes: ESC[0m
    buf[pos] = 0x1b; pos += 1
    buf[pos] = 91;   pos += 1   # '['
    buf[pos] = 48;   pos += 1   # '0'
    buf[pos] = 109;  pos += 1   # 'm'

    # Foreground color: ESC[38;2;R;G;Bm
    if fg != COLOR_NONE:
        buf[pos] = 0x1b; pos += 1
        buf[pos] = 91;   pos += 1   # '['
        buf[pos] = 51;   pos += 1   # '3'
        buf[pos] = 56;   pos += 1   # '8'
        buf[pos] = 59;   pos += 1   # ';'
        buf[pos] = 50;   pos += 1   # '2'
        buf[pos] = 59;   pos += 1   # ';'
        pos += _write_uint(buf + pos, (fg >> 16) & 0xFF)
        buf[pos] = 59;   pos += 1   # ';'
        pos += _write_uint(buf + pos, (fg >> 8) & 0xFF)
        buf[pos] = 59;   pos += 1   # ';'
        pos += _write_uint(buf + pos, fg & 0xFF)
        buf[pos] = 109;  pos += 1   # 'm'

    # Background color: ESC[48;2;R;G;Bm
    if bg != COLOR_NONE:
        buf[pos] = 0x1b; pos += 1
        buf[pos] = 91;   pos += 1   # '['
        buf[pos] = 52;   pos += 1   # '4'
        buf[pos] = 56;   pos += 1   # '8'
        buf[pos] = 59;   pos += 1   # ';'
        buf[pos] = 50;   pos += 1   # '2'
        buf[pos] = 59;   pos += 1   # ';'
        pos += _write_uint(buf + pos, (bg >> 16) & 0xFF)
        buf[pos] = 59;   pos += 1   # ';'
        pos += _write_uint(buf + pos, (bg >> 8) & 0xFF)
        buf[pos] = 59;   pos += 1   # ';'
        pos += _write_uint(buf + pos, bg & 0xFF)
        buf[pos] = 109;  pos += 1   # 'm'

    # Text attributes: ESC[1;2;...m
    if attrs:
        buf[pos] = 0x1b; pos += 1
        buf[pos] = 91;   pos += 1   # '['
        first = True
        if attrs & ATTR_BOLD:
            buf[pos] = 49; pos += 1; first = False  # '1'
        if attrs & ATTR_DIM:
            if not first: buf[pos] = 59; pos += 1  # ';'
            buf[pos] = 50; pos += 1; first = False  # '2'
        if attrs & ATTR_ITALIC:
            if not first: buf[pos] = 59; pos += 1
            buf[pos] = 51; pos += 1; first = False  # '3'
        if attrs & ATTR_UNDERLINE:
            if not first: buf[pos] = 59; pos += 1
            buf[pos] = 52; pos += 1; first = False  # '4'
        if attrs & ATTR_BLINK:
            if not first: buf[pos] = 59; pos += 1
            buf[pos] = 53; pos += 1; first = False  # '5'
        if attrs & ATTR_REVERSE:
            if not first: buf[pos] = 59; pos += 1
            buf[pos] = 55; pos += 1; first = False  # '7'
        if attrs & ATTR_STRIKETHROUGH:
            if not first: buf[pos] = 59; pos += 1
            buf[pos] = 57; pos += 1              # '9'
        buf[pos] = 109; pos += 1                 # 'm'

    return pos


cdef int _write_utf8_char(char* buf, int cp) noexcept:
    """Encode a Unicode codepoint as UTF-8 into buf. Returns byte count."""
    if cp < 0x80:
        buf[0] = cp & 0xFF
        return 1
    elif cp < 0x800:
        buf[0] = 0xC0 | ((cp >> 6) & 0x1F)
        buf[1] = 0x80 | (cp & 0x3F)
        return 2
    elif cp < 0x10000:
        buf[0] = 0xE0 | ((cp >> 12) & 0x0F)
        buf[1] = 0x80 | ((cp >> 6) & 0x3F)
        buf[2] = 0x80 | (cp & 0x3F)
        return 3
    else:
        buf[0] = 0xF0 | ((cp >> 18) & 0x07)
        buf[1] = 0x80 | ((cp >> 12) & 0x3F)
        buf[2] = 0x80 | ((cp >> 6) & 0x3F)
        buf[3] = 0x80 | (cp & 0x3F)
        return 4


# ---------------------------------------------------------------------------
# CellGrid
# ---------------------------------------------------------------------------

cdef class CellGrid:
    """
    2D array of cells with double-buffering for minimal terminal writes.

    Internal storage: two flat int32 C arrays (_cur_buf / _prv_buf) of size
    width * height * CELL_STRIDE.  The public Python API is unchanged.

    _current and _prev are computed properties that materialise the buffers
    as list[list[tuple]] for test/legacy callers that inspect them directly.
    """

    cdef public int width
    cdef public int height
    cdef int       _size          # width * height
    cdef int      *_cur_buf       # current frame  [size * CELL_STRIDE]
    cdef int      *_prv_buf       # previous frame [size * CELL_STRIDE]
    cdef public set _dirty_rows

    def __cinit__(self, int width, int height):
        cdef int n = width * height * CELL_STRIDE
        self._cur_buf = <int *>PyMem_Malloc(n * sizeof(int))
        self._prv_buf = <int *>PyMem_Malloc(n * sizeof(int))
        if not self._cur_buf or not self._prv_buf:
            raise MemoryError()
        self.width  = width
        self.height = height
        self._size  = width * height
        self._dirty_rows = set()
        # Fill both buffers with blank cells: (space=0x20, COLOR_NONE, COLOR_NONE, 0)
        self._fill_buf(self._cur_buf, 0x20, COLOR_NONE, COLOR_NONE, 0)
        self._fill_buf(self._prv_buf, 0x20, COLOR_NONE, COLOR_NONE, 0)

    def __dealloc__(self):
        PyMem_Free(self._cur_buf)
        PyMem_Free(self._prv_buf)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    cdef void _fill_buf(self, int *buf, int cp, int fg, int bg, int attrs) noexcept:
        cdef int i, base
        cdef int total = self._size
        for i in range(total):
            base = i * CELL_STRIDE
            buf[base]     = cp
            buf[base + 1] = fg
            buf[base + 2] = bg
            buf[base + 3] = attrs

    cdef inline int _idx(self, int row, int col) noexcept:
        return (row * self.width + col) * CELL_STRIDE

    # ------------------------------------------------------------------
    # _current / _prev properties (materialise for test/legacy callers)
    # ------------------------------------------------------------------

    @property
    def _current(self):
        return self._materialise(self._cur_buf)

    @_current.setter
    def _current(self, value):
        self._write_from_list(self._cur_buf, value)

    @property
    def _prev(self):
        return self._materialise(self._prv_buf)

    @_prev.setter
    def _prev(self, value):
        self._write_from_list(self._prv_buf, value)

    cdef list _materialise(self, int *buf):
        cdef int row, col, base
        cdef list rows = []
        cdef list row_list
        for row in range(self.height):
            row_list = []
            for col in range(self.width):
                base = (row * self.width + col) * CELL_STRIDE
                cp   = buf[base]
                fg   = _decode_color(buf[base + 1])
                bg   = _decode_color(buf[base + 2])
                at   = buf[base + 3]
                row_list.append((chr(cp), fg, bg, at))
            rows.append(row_list)
        return rows

    cdef void _write_from_list(self, int *buf, list value):
        cdef int row, col, base
        cdef object cell
        cdef list row_list
        for row in range(self.height):
            row_list = value[row]
            for col in range(self.width):
                cell = row_list[col]
                base = (row * self.width + col) * CELL_STRIDE
                buf[base]     = ord(cell[0])
                buf[base + 1] = _encode_color(cell[1])
                buf[base + 2] = _encode_color(cell[2])
                buf[base + 3] = <int>cell[3]

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def write(self, int row, int col, str char,
              fg=None, bg=None, int attrs=0):
        """Write a single character cell. Silently ignores out-of-bounds."""
        cdef int base
        if 0 <= row < self.height and 0 <= col < self.width:
            base = self._idx(row, col)
            self._cur_buf[base]     = ord(char)
            self._cur_buf[base + 1] = _encode_color(fg)
            self._cur_buf[base + 2] = _encode_color(bg)
            self._cur_buf[base + 3] = attrs
            self._dirty_rows.add(row)

    def write_str(self, int row, int col, str text,
                  fg=None, bg=None, int attrs=0):
        """Write a string of characters starting at (row, col). Clips at width."""
        cdef int start, span, i, base, efg, ebg
        if row < 0 or row >= self.height or not text:
            return
        start = col if col >= 0 else 0
        if start >= self.width:
            return
        text = text[start - col: start - col + (self.width - start)]
        if not text:
            return
        efg  = _encode_color(fg)
        ebg  = _encode_color(bg)
        span = len(text)
        for i in range(span):
            base = self._idx(row, start + i)
            self._cur_buf[base]     = ord(text[i])
            self._cur_buf[base + 1] = efg
            self._cur_buf[base + 2] = ebg
            self._cur_buf[base + 3] = attrs
        self._dirty_rows.add(row)

    def write_padded(self, int row, int col, str text, int width,
                     fg=None, bg=None, int attrs=0):
        """Write text and fill the remaining span with spaces using the same style."""
        cdef int fill_start, remaining
        if row < 0 or row >= self.height or width <= 0:
            return
        text = text[:width]
        if text:
            self.write_str(row, col, text, fg=fg, bg=bg, attrs=attrs)
        fill_start = col + len(text)
        remaining = width - len(text)
        if remaining > 0:
            self.fill(row, fill_start, remaining, fg=fg, bg=bg, attrs=attrs)

    def fill(self, int row, int col, int width, str char=" ",
             fg=None, bg=None, int attrs=0):
        """Fill `width` cells starting at (row, col) with the same style."""
        cdef int start, span, i, base, efg, ebg, cp
        if row < 0 or row >= self.height or width <= 0:
            return
        start = col if col >= 0 else 0
        if start >= self.width:
            return
        span = min(width - (start - col), self.width - start)
        if span <= 0:
            return
        cp  = ord(char)
        efg = _encode_color(fg)
        ebg = _encode_color(bg)
        for i in range(span):
            base = self._idx(row, start + i)
            self._cur_buf[base]     = cp
            self._cur_buf[base + 1] = efg
            self._cur_buf[base + 2] = ebg
            self._cur_buf[base + 3] = attrs
        self._dirty_rows.add(row)

    def invalidate_prev_rows(self, int start_row, int end_row):
        """
        Force all cells in rows [start_row, end_row) to appear changed on the
        next flush by filling those rows in _prv_buf with a null sentinel cell.
        Used to guarantee a full row re-output (e.g. after clearing overlays).
        """
        cdef int row, col, base
        cdef int r_start = max(0, start_row)
        cdef int r_end   = min(end_row, self.height)
        for row in range(r_start, r_end):
            for col in range(self.width):
                base = self._idx(row, col)
                self._prv_buf[base]     = 0       # null sentinel char
                self._prv_buf[base + 1] = COLOR_NONE
                self._prv_buf[base + 2] = COLOR_NONE
                self._prv_buf[base + 3] = 0
            self._dirty_rows.add(row)

    def read_cell(self, int row, int col):
        """Return the cell tuple (char, fg, bg, attrs) at (row, col). Bounds-checked."""
        cdef int base
        if 0 <= row < self.height and 0 <= col < self.width:
            base = self._idx(row, col)
            return (chr(self._cur_buf[base]),
                    _decode_color(self._cur_buf[base + 1]),
                    _decode_color(self._cur_buf[base + 2]),
                    self._cur_buf[base + 3])
        return (" ", None, None, 0)

    def paint_style_range(self, int row, int start_col, int end_col,
                          fg=None, bg=None, int attrs=0):
        """
        Overlay fg/bg/attrs onto existing cells in [start_col, end_col) on row.
        None fg/bg means keep existing; attrs=0 means keep existing.
        """
        cdef int col, base, efg, ebg
        cdef int cur_fg, cur_bg, cur_attrs
        if row < 0 or row >= self.height:
            return
        if start_col < 0:
            start_col = 0
        if end_col > self.width:
            end_col = self.width
        if start_col >= end_col:
            return
        efg = _encode_color(fg)  # COLOR_NONE if fg is None
        ebg = _encode_color(bg)
        for col in range(start_col, end_col):
            base = self._idx(row, col)
            if efg != COLOR_NONE:
                self._cur_buf[base + 1] = efg
            if ebg != COLOR_NONE:
                self._cur_buf[base + 2] = ebg
            if attrs != 0:
                self._cur_buf[base + 3] = attrs
        self._dirty_rows.add(row)

    def clear(self):
        """Reset all cells in _current to blank."""
        self._fill_buf(self._cur_buf, 0x20, COLOR_NONE, COLOR_NONE, 0)
        self._dirty_rows.update(range(self.height))

    def apply_default_style(self, *, fg=None, bg=None):
        """Replace missing fg/bg values with provided defaults in the current frame."""
        cdef int row, col, base, cur_fg, cur_bg, def_fg, def_bg
        cdef bint changed
        if fg is None and bg is None:
            return
        def_fg = _encode_color(fg)
        def_bg = _encode_color(bg)
        for row in range(self.height):
            changed = False
            for col in range(self.width):
                base   = self._idx(row, col)
                cur_fg = self._cur_buf[base + 1]
                cur_bg = self._cur_buf[base + 2]
                if cur_fg == COLOR_NONE and def_fg != COLOR_NONE:
                    self._cur_buf[base + 1] = def_fg
                    changed = True
                if cur_bg == COLOR_NONE and def_bg != COLOR_NONE:
                    self._cur_buf[base + 2] = def_bg
                    changed = True
            if changed:
                self._dirty_rows.add(row)

    def blit(self, CellGrid src, int dest_x, int dest_y):
        """Copy src._current into self._current at offset (dest_x, dest_y)."""
        cdef int sr, dr, src_start, dest_start, span, s_base, d_base
        for sr in range(src.height):
            dr = dest_y + sr
            if dr < 0:
                continue
            if dr >= self.height:
                break
            src_start  = 0 if dest_x >= 0 else -dest_x
            dest_start = dest_x if dest_x >= 0 else 0
            span = min(src.width - src_start, self.width - dest_start)
            if span <= 0:
                continue
            s_base = (sr * src.width  + src_start)  * CELL_STRIDE
            d_base = (dr * self.width + dest_start) * CELL_STRIDE
            memcpy(&self._cur_buf[d_base], &src._cur_buf[s_base], span * CELL_STRIDE * sizeof(int))
            self._dirty_rows.add(dr)

    # ------------------------------------------------------------------
    # Flush (diff → RenderOps)
    # ------------------------------------------------------------------

    def flush(self):
        """
        Diff _current against _prev. Return minimal list[RenderOp].
        Groups consecutive dirty cells on the same row and same style into
        a single PutCells run to minimize MoveCursor emissions.
        After flush, _prev is updated to match _current.
        """
        cdef int row, col, base
        cdef int run_start
        cdef int run_fg, run_bg, run_attrs
        cdef int fg, bg, attrs, cp
        cdef list run_chars, ops
        cdef int row_stride = self.width * CELL_STRIDE
        cdef int *cur_row_ptr
        cdef int *prv_row_ptr

        if not self._dirty_rows:
            return []

        ops = []
        append_op = ops.append

        for row in sorted(self._dirty_rows):
            cur_row_ptr = self._cur_buf + row * row_stride
            prv_row_ptr = self._prv_buf + row * row_stride

            # Quick whole-row equality check via memcmp
            if memcmp(cur_row_ptr, prv_row_ptr, row_stride * sizeof(int)) == 0:
                continue

            run_start = -1
            run_chars = []
            run_fg    = COLOR_NONE
            run_bg    = COLOR_NONE
            run_attrs = 0

            for col in range(self.width):
                base  = col * CELL_STRIDE
                cp    = cur_row_ptr[base]
                fg    = cur_row_ptr[base + 1]
                bg    = cur_row_ptr[base + 2]
                attrs = cur_row_ptr[base + 3]

                # Compare against previous
                if (cp    == prv_row_ptr[base]     and
                    fg    == prv_row_ptr[base + 1] and
                    bg    == prv_row_ptr[base + 2] and
                    attrs == prv_row_ptr[base + 3]):
                    # Unchanged — flush any pending run
                    if run_start >= 0:
                        append_op(MoveCursor(row, run_start))
                        append_op(PutCells("".join(run_chars),
                                           _decode_color(run_fg),
                                           _decode_color(run_bg),
                                           run_attrs))
                        run_start = -1
                        run_chars = []
                else:
                    if run_start < 0:
                        run_start = col
                        run_fg    = fg
                        run_bg    = bg
                        run_attrs = attrs
                        run_chars = [chr(cp)]
                    elif fg == run_fg and bg == run_bg and attrs == run_attrs:
                        run_chars.append(chr(cp))
                    else:
                        append_op(MoveCursor(row, run_start))
                        append_op(PutCells("".join(run_chars),
                                           _decode_color(run_fg),
                                           _decode_color(run_bg),
                                           run_attrs))
                        run_start = col
                        run_fg    = fg
                        run_bg    = bg
                        run_attrs = attrs
                        run_chars = [chr(cp)]

            if run_start >= 0:
                append_op(MoveCursor(row, run_start))
                append_op(PutCells("".join(run_chars),
                                   _decode_color(run_fg),
                                   _decode_color(run_bg),
                                   run_attrs))

            # Update prev row from cur row
            memcpy(prv_row_ptr, cur_row_ptr, row_stride * sizeof(int))

        self._dirty_rows.clear()
        return ops

    def flush_ansi(self, bytearray out):
        """
        Like flush() but writes ANSI escape sequences directly into `out` (cleared first).
        Eliminates Python object allocation in the hot path: no MoveCursor/PutCells
        namedtuples, no list[RenderOp], no f-strings, no str.join.

        Encoding matches PromptToolkitBackend.write(): ESC[0m reset + optional
        38;2;r;g;b fg + 48;2;r;g;b bg + attr codes, followed by UTF-8 chars.
        """
        cdef int row, col, base
        cdef int run_start, run_fg, run_bg, run_attrs
        cdef int fg, bg, attrs, cp
        cdef int row_stride = self.width * CELL_STRIDE
        cdef int *cur_row_ptr
        cdef int *prv_row_ptr
        cdef int pos
        cdef Py_ssize_t old_size

        # Scratch buffer: worst-case per row is every cell a style-break run:
        #   cursor(10) + style(60) + char(4) per cell = 74 bytes × width
        # Using width×80 gives a comfortable safety margin.
        cdef int scratch_cap = (self.width + 2) * 80
        cdef char *scratch = <char *>PyMem_Malloc(scratch_cap)
        if not scratch:
            raise MemoryError()

        # Clear output buffer
        if PyByteArray_GET_SIZE(out) > 0:
            PyByteArray_Resize(out, 0)

        try:
            for row in sorted(self._dirty_rows):
                cur_row_ptr = self._cur_buf + row * row_stride
                prv_row_ptr = self._prv_buf + row * row_stride

                if memcmp(cur_row_ptr, prv_row_ptr, row_stride * sizeof(int)) == 0:
                    continue

                pos       = 0
                run_start = -1
                run_fg    = COLOR_NONE
                run_bg    = COLOR_NONE
                run_attrs = 0

                for col in range(self.width):
                    base  = col * CELL_STRIDE
                    cp    = cur_row_ptr[base]
                    fg    = cur_row_ptr[base + 1]
                    bg    = cur_row_ptr[base + 2]
                    attrs = cur_row_ptr[base + 3]

                    if (cp    == prv_row_ptr[base]     and
                        fg    == prv_row_ptr[base + 1] and
                        bg    == prv_row_ptr[base + 2] and
                        attrs == prv_row_ptr[base + 3]):
                        # Unchanged cell — end any active run (bytes already written)
                        run_start = -1
                    else:
                        if run_start < 0:
                            # Start a new run: emit cursor + style, then first char
                            run_start = col
                            run_fg    = fg
                            run_bg    = bg
                            run_attrs = attrs
                            pos += _write_cursor_ansi(scratch + pos, row, col)
                            pos += _write_style_ansi(scratch + pos, fg, bg, attrs)
                            pos += _write_utf8_char(scratch + pos, cp)
                        elif fg == run_fg and bg == run_bg and attrs == run_attrs:
                            # Same style — extend the run
                            pos += _write_utf8_char(scratch + pos, cp)
                        else:
                            # Style break — start a new run inline
                            run_start = col
                            run_fg    = fg
                            run_bg    = bg
                            run_attrs = attrs
                            pos += _write_cursor_ansi(scratch + pos, row, col)
                            pos += _write_style_ansi(scratch + pos, fg, bg, attrs)
                            pos += _write_utf8_char(scratch + pos, cp)

                # Append this row's bytes to the output bytearray
                if pos > 0:
                    old_size = PyByteArray_GET_SIZE(out)
                    PyByteArray_Resize(out, old_size + pos)
                    memcpy(PyByteArray_AS_STRING(out) + old_size, scratch, pos)

                # Sync prev row
                memcpy(prv_row_ptr, cur_row_ptr, row_stride * sizeof(int))

            self._dirty_rows.clear()

        finally:
            PyMem_Free(scratch)
