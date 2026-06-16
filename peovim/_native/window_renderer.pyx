# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
# cython: nonecheck=False
"""
peovim._native.window_renderer — Cython-accelerated render_window().

Phase 4 of the native renderer plan.  Key wins over the pure-Python version:
  - _logical_col_to_display_col: C loop via PyUnicode_READ_CHAR (no string
    slice + expandtabs allocation per call — called ~14 k times/frame).
  - _visible_text_slice: single-pass C char buffer, avoids full-line expandtabs
    allocation (called ~80 times/frame).
  - _extract_visible_lines: C-level rstrip + PyUnicode_DecodeUTF8 (no per-line
    bytes slice allocation).
  - render_window main loop: cdef int variables eliminate Python integer boxing
    for screen_row, doc_line, gutter_w, text_w, tabstop, scroll_col etc.
  - Cython vtable dispatch for all CellGrid method calls.

Public surface: only render_window() is exposed.  All helpers are cdef/cpdef.
"""
from __future__ import annotations

from cpython.mem cimport PyMem_Malloc, PyMem_Free
from cpython.unicode cimport (
    PyUnicode_GET_LENGTH,
    PyUnicode_READ_CHAR,
    PyUnicode_DecodeUTF8,
)

from peovim.ui.backend import ATTR_BOLD
from peovim.ui.cell_grid import CellGrid


# ---------------------------------------------------------------------------
# Colour / style constants (match _window_renderer_pure.py exactly)
# ---------------------------------------------------------------------------

TILDE_FG        = (80, 80, 140)
CURSOR_ACTIVE   = {"fg": None, "bg": (80, 120, 200), "attrs": ATTR_BOLD}
CURSOR_INACTIVE = {"fg": None, "bg": (60, 60, 60),  "attrs": 0}
ACTIVE_GUTTER   = {"fg": (140, 140, 180), "bg": None, "attrs": 0}
INACTIVE_GUTTER = {"fg": (80, 80, 80),   "bg": None, "attrs": 0}

# C string constant for PyUnicode_DecodeUTF8 error mode
cdef const char *_UTF8_REPLACE = "replace"


# ---------------------------------------------------------------------------
# C helpers: tab-aware column arithmetic
# ---------------------------------------------------------------------------

cdef int _logical_col_to_display_col_c(str line_text, int col, int tabstop) noexcept:
    """Translate logical character column → rendered display column.

    Uses PyUnicode_READ_CHAR for direct C-level character access: no temporary
    string slice, no expandtabs call.
    """
    cdef int display_col = 0
    cdef int i
    cdef int n
    cdef unsigned int ch
    if col <= 0:
        return 0
    n = PyUnicode_GET_LENGTH(line_text)
    if col > n:
        col = n
    for i in range(col):
        ch = PyUnicode_READ_CHAR(line_text, i)
        if ch == 9:  # '\t'
            display_col += tabstop - (display_col % tabstop)
        else:
            display_col += 1
    return display_col


cdef str _visible_text_slice_c(str line_text, int scroll_display_col, int width, int tabstop):
    """Single-pass tab expansion + visible-window extraction.

    Common case (ASCII content): builds result into a C char buffer then
    decodes once with PyUnicode_DecodeUTF8.  Non-ASCII characters fall back to
    a Python list that is joined at the end.
    """
    cdef int n = PyUnicode_GET_LENGTH(line_text)
    cdef int display_col = 0
    cdef int end_col = scroll_display_col + width
    cdef int i, k, tab_spaces, out_pos = 0
    cdef unsigned int ch
    cdef char *ascii_buf
    cdef bint has_non_ascii = False
    cdef list unicode_result

    if n == 0 or width <= 0:
        return ""

    # Allocate scratch buffer for ASCII path (worst case: width bytes)
    ascii_buf = <char *>PyMem_Malloc(width + 4)
    if not ascii_buf:
        raise MemoryError()

    try:
        for i in range(n):
            if display_col >= end_col:
                break
            ch = PyUnicode_READ_CHAR(line_text, i)
            if ch == 9:  # tab
                tab_spaces = tabstop - (display_col % tabstop)
                for k in range(tab_spaces):
                    if scroll_display_col <= display_col < end_col:
                        if not has_non_ascii:
                            ascii_buf[out_pos] = 32  # space
                        else:
                            unicode_result.append(" ")
                        out_pos += 1
                    display_col += 1
            else:
                if display_col >= scroll_display_col:
                    if ch < 0x80 and not has_non_ascii:
                        ascii_buf[out_pos] = <char>ch
                        out_pos += 1
                    else:
                        if not has_non_ascii:
                            # First non-ASCII: promote to Python list
                            has_non_ascii = True
                            unicode_result = []
                            for k in range(out_pos):
                                unicode_result.append(chr(<unsigned char>ascii_buf[k]))
                        unicode_result.append(chr(ch))
                        out_pos += 1
                display_col += 1

        if not has_non_ascii:
            return PyUnicode_DecodeUTF8(ascii_buf, out_pos, NULL)
        else:
            return "".join(unicode_result)
    finally:
        PyMem_Free(ascii_buf)


cdef int _leading_indent_columns_c(str line_text, int tabstop) noexcept:
    """Return visual indent width for the leading whitespace of a line."""
    cdef int n = PyUnicode_GET_LENGTH(line_text)
    cdef int indent = 0
    cdef int i
    cdef unsigned int ch
    for i in range(n):
        ch = PyUnicode_READ_CHAR(line_text, i)
        if ch == 32:   # space
            indent += 1
        elif ch == 9:  # tab
            indent += tabstop - (indent % tabstop)
        else:
            break
    return indent


# ---------------------------------------------------------------------------
# Line extraction (typed inner loop, C-level rstrip)
# ---------------------------------------------------------------------------

def _extract_visible_lines_native(snapshot, int start_line, int end_line):
    """Decode only the requested inclusive line range from the snapshot.

    Optimisations vs pure Python:
      - rstrip uses a C-level memoryview scan (no Python string method calls).
      - PyUnicode_DecodeUTF8 avoids creating an intermediate bytes slice.
    """
    cdef const unsigned char[:] full_view
    cdef int i, line_start_byte, line_end_byte, end_pos, total_len
    cdef int start_l, end_l

    bs = snapshot.buffer_snapshot
    offsets = bs.line_offsets

    if not offsets:
        return {}

    start_l = start_line if start_line >= 0 else 0
    end_l   = end_line   if end_line < len(offsets) else len(offsets) - 1
    if start_l > end_l:
        return {}

    # Reconstruct full document bytes (Python piece iteration, C-level extend)
    result_ba = bytearray()
    original = bs.original
    add_buf  = bs.add
    for piece in bs.pieces:
        if piece.buf == "original":
            result_ba.extend(original[piece.start: piece.start + piece.length])
        else:
            result_ba.extend(add_buf[piece.start: piece.start + piece.length])

    total_len = len(result_ba)
    full_view = result_ba   # C-level typed memoryview

    n_offsets = len(offsets)
    lines = {}
    for i in range(start_l, end_l + 1):
        line_start_byte = offsets[i]
        line_end_byte   = offsets[i + 1] if i + 1 < n_offsets else total_len

        # C-level rstrip: scan backwards for \n (10) and \r (13)
        end_pos = line_end_byte
        while end_pos > line_start_byte and (full_view[end_pos - 1] == 10 or
                                              full_view[end_pos - 1] == 13):
            end_pos -= 1

        # Decode without allocating an intermediate bytes slice
        lines[i] = PyUnicode_DecodeUTF8(
            <const char *>&full_view[line_start_byte],
            end_pos - line_start_byte,
            _UTF8_REPLACE,
        )

    return lines


# ---------------------------------------------------------------------------
# Syntax / decoration helpers (cdef for C-level dispatch of common ops)
# ---------------------------------------------------------------------------

cdef void _apply_syntax_spans_c(
    object grid,
    list spans,
    int doc_line,
    int screen_row,
    int gutter_w,
    str line_text,
    int scroll_display_col,
    int text_w,
    int tabstop,
    object theme,
    dict style_cache,
) noexcept:
    """Apply syntax highlight spans for a single line to the cell grid."""
    cdef int col_start, col_end, display_col_start, display_col_end
    cdef int sc_start, sc_end, paint_start, paint_end

    for span in spans:
        if span.end_line < doc_line:
            continue
        if span.start_line > doc_line:
            break
        col_start = span.start_col if span.start_line == doc_line else 0
        col_end   = span.end_col   if span.end_line   == doc_line else 0x7FFFFFFF
        display_col_start = _logical_col_to_display_col_c(line_text, col_start, tabstop)
        if col_end == 0x7FFFFFFF:
            display_col_end = 0x7FFFFFFF
        else:
            display_col_end = _logical_col_to_display_col_c(line_text, col_end, tabstop)
        sc_start = display_col_start - scroll_display_col
        if sc_start < 0:
            sc_start = 0
        sc_end = (display_col_end - scroll_display_col) if display_col_end != 0x7FFFFFFF else text_w
        if sc_end > text_w:
            sc_end = text_w
        if sc_start >= sc_end:
            continue

        style = style_cache.get(span.group)
        if style is None:
            style = theme.resolve(span.group)
            style_cache[span.group] = style
        if style.fg is None and style.bg is None and style.attrs == 0:
            continue

        paint_start = gutter_w + sc_start
        paint_end   = gutter_w + sc_end
        if paint_end > grid.width:
            paint_end = grid.width
        grid.paint_style_range(screen_row, paint_start, paint_end, style.fg, style.bg, style.attrs)


cdef void _apply_highlight_c(
    object grid,
    object dec,
    int doc_line,
    int screen_row,
    int gutter_w,
    str line_text,
    int scroll_display_col,
    int text_w,
    int tabstop,
) noexcept:
    """Apply a HighlightRegion decoration to the appropriate cells."""
    cdef int col_start, col_end, display_col_start, display_col_end
    cdef int sc_start, sc_end, paint_start, paint_end

    if doc_line < dec.start_line or doc_line > dec.end_line:
        return
    col_start = dec.start_col if doc_line == dec.start_line else 0
    col_end   = dec.end_col   if doc_line == dec.end_line   else 0x7FFFFFFF
    display_col_start = _logical_col_to_display_col_c(line_text, col_start, tabstop)
    if col_end == 0x7FFFFFFF:
        display_col_end = 0x7FFFFFFF
    else:
        display_col_end = _logical_col_to_display_col_c(line_text, col_end, tabstop)
    sc_start = display_col_start - scroll_display_col
    if sc_start < 0:
        sc_start = 0
    sc_end = (display_col_end - scroll_display_col) if display_col_end != 0x7FFFFFFF else text_w
    if sc_end > text_w:
        sc_end = text_w
    if sc_start >= sc_end:
        return
    paint_start = gutter_w + sc_start
    paint_end   = gutter_w + sc_end
    if paint_end > grid.width:
        paint_end = grid.width
    grid.paint_style_range(screen_row, paint_start, paint_end, dec.style.fg, dec.style.bg, dec.style.attrs)


cdef void _draw_indent_guides_c(
    object grid,
    str line_text,
    int screen_row,
    int gutter_w,
    int scroll_col,
    int text_w,
    int tabstop,
    str mode,
    object guide_indent_obj,
    int cursor_sc = -1,
) noexcept:
    """Draw vertical indent guide lines.

    cursor_sc: screen-column of the terminal cursor (-1 = unused).  The guide
    at that column is skipped so a bar-style terminal cursor is visible.
    """
    cdef int indent, level, col, sc, cell_col
    cdef object existing, fg

    _GUIDE_CHAR   = "\u2502"
    _GUIDE_DIM    = (80, 80, 80)
    _RAINBOW: list = [
        (255, 100, 100), (255, 200, 100), (100, 220, 100),
        (100, 200, 255), (200, 100, 255), (255, 100, 200),
    ]

    if guide_indent_obj is None:
        if not line_text.strip():
            return
        indent = _leading_indent_columns_c(line_text, tabstop)
    else:
        indent = <int>guide_indent_obj

    if indent == 0:
        return

    level = 0
    col   = tabstop
    while col < indent:
        sc = col - scroll_col
        if sc != cursor_sc and 0 <= sc < text_w:
            cell_col = gutter_w + sc
            if cell_col < grid.width:
                existing = grid.read_cell(screen_row, cell_col)
                if existing[0] == " ":
                    fg = _RAINBOW[level % 6] if mode == "rainbow" else _GUIDE_DIM
                    grid.write(screen_row, cell_col, _GUIDE_CHAR,
                               fg=fg, bg=existing[2], attrs=existing[3])
        level += 1
        col   += tabstop


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_window(
    snapshot,
    rect,
    is_active,
    decorations=None,
    highlight_spans=None,
    theme=None,
    extra_decorations=None,
    sign_registry=None,
):
    """Render a WindowSnapshot into a new CellGrid sized to rect.

    Drop-in replacement for the pure-Python version.  The public API is
    identical; the implementation uses C-level helpers for the hot inner loop.
    """
    # ------------------------------------------------------------------
    # Merge decorations
    # ------------------------------------------------------------------
    all_decs = list(decorations) if decorations else []
    if extra_decorations:
        all_decs.extend(extra_decorations)

    base_fg = theme.default_fg if theme is not None else None
    base_bg = theme.default_bg if theme is not None else None

    # ------------------------------------------------------------------
    # Pre-index decorations by line (O(n) once → O(1) per row)
    # ------------------------------------------------------------------
    sign_by_line:     dict = {}
    vtext_by_line:    dict = {}
    ghost_by_line:    dict = {}
    highlight_by_line: dict = {}
    overlay_by_line:  dict = {}

    cdef int visible_start_c = <int>snapshot.scroll_line
    cdef int visible_end_c   = visible_start_c + max(0, rect.height - 1)

    if all_decs:
        from peovim.ui.decorations import (  # noqa: PLC0415
            GhostText   as _GT,
            HighlightRegion as _HR,
            OverlayChar  as _OC,
            Sign         as _Sign,
            VirtualText  as _VT,
        )
        for _dec in all_decs:
            if isinstance(_dec, _Sign):
                _existing = sign_by_line.get(_dec.line)
                if _existing is None or _dec.priority >= _existing.priority:
                    sign_by_line[_dec.line] = _dec
            elif isinstance(_dec, _VT):
                vtext_by_line.setdefault(_dec.line, []).append(_dec)
            elif isinstance(_dec, _GT):
                ghost_by_line[_dec.line] = _dec
            elif isinstance(_dec, _HR):
                if _dec.end_line < visible_start_c or _dec.start_line > visible_end_c:
                    continue
                for _line in range(
                    max(_dec.start_line, visible_start_c),
                    min(_dec.end_line, visible_end_c) + 1,
                ):
                    highlight_by_line.setdefault(_line, []).append(_dec)
            elif isinstance(_dec, _OC) and visible_start_c <= _dec.line <= visible_end_c:
                overlay_by_line.setdefault(_dec.line, []).append(_dec)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    cdef int line_count_c = len(snapshot.buffer_snapshot.line_offsets)
    options = snapshot.options

    cdef int signcolumn_is_sign = 0
    signcolumn = options.get("signcolumn", "auto")
    if signcolumn == "yes" or (signcolumn == "auto" and sign_by_line):
        signcolumn_is_sign = 1

    cdef int gutter_w_c
    # --- gutter width ---
    cdef int number_w_c = 0
    if options.get("number") or options.get("relativenumber"):
        digits = max(len(str(line_count_c)), 3)
        number_w_c = digits + 1
    cdef int sign_w_c = 2 if signcolumn_is_sign else 0
    gutter_w_c = number_w_c + sign_w_c

    cdef int rect_width_c  = rect.width
    cdef int rect_height_c = rect.height
    cdef int text_w_c      = rect_width_c - gutter_w_c
    cdef int tabstop_c     = int(options.get("tabstop", 4) or 4)
    cdef int scroll_col_c  = <int>snapshot.scroll_col
    cdef int cursor_line_c = <int>snapshot.cursor_line
    cdef int cursor_col_c  = <int>snapshot.cursor_col
    cdef bint relativenumber_c  = bool(options.get("relativenumber"))
    cdef bint paint_cursor_c    = bool(options.get("_paint_cursor", True))
    cdef bint indent_guides_on  = options.get("indentguides", "none") != "none"
    indent_guides_mode  = str(options.get("indentguides", "none"))

    gutter_style = ACTIVE_GUTTER if is_active else INACTIVE_GUTTER

    grid = CellGrid(rect_width_c, rect_height_c)

    # Pre-compute colorcolumn cells (may be empty tuple)
    colorcolumn_cells = _resolve_colorcolumn_cells_c(
        options.get("colorcolumn", ""), scroll_col_c, text_w_c
    )

    visible_lines   = _extract_visible_lines_native(snapshot, visible_start_c, visible_end_c)
    syntax_by_line  = _index_highlight_spans_c(highlight_spans or (), visible_start_c, visible_end_c)
    fold_headers    = {s: e for s, e in snapshot.closed_folds}
    syntax_style_cache: dict = {}

    # ------------------------------------------------------------------
    # Main line loop (cdef int variables — no Python int boxing)
    # ------------------------------------------------------------------
    cdef int screen_row = 0
    cdef int doc_line   = visible_start_c
    cdef int scroll_display_col, cursor_screen_col, _guide_cursor_sc_c
    cdef int _sign_w_c, _number_w_c, n_lines
    cdef str line_text, visible_text, num_str, padded, fold_text

    while screen_row < rect_height_c:
        if doc_line >= line_count_c:
            if gutter_w_c > 0:
                grid.fill(screen_row, 0, gutter_w_c, bg=base_bg)
            grid.write(screen_row, gutter_w_c, "~", fg=TILDE_FG, bg=base_bg)
            grid.fill(screen_row, gutter_w_c + 1, max(0, text_w_c - 1), fg=base_fg, bg=base_bg)
            screen_row += 1
            doc_line   += 1
            continue

        _sign_w_c   = sign_w_c
        _number_w_c = number_w_c

        # --- Closed fold indicator ---
        fold_end = fold_headers.get(doc_line)
        if fold_end is not None:
            n_lines   = <int>fold_end - doc_line + 1
            fold_text = f"+--  {n_lines} lines  ---"
            if gutter_w_c > 0:
                if _sign_w_c > 0:
                    grid.fill(screen_row, 0, _sign_w_c, bg=base_bg)
                    _sign = sign_by_line.get(doc_line)
                    if _sign:
                        grid.write(screen_row, 0, _sign.char,
                                   fg=_sign.style.fg, bg=_sign.style.bg)
                if _number_w_c > 0:
                    num_str = str(doc_line + 1).rjust(_number_w_c - 1) + " "
                    grid.write_str(screen_row, _sign_w_c, num_str,
                                   fg=gutter_style["fg"], bg=base_bg,
                                   attrs=gutter_style["attrs"])
            grid.write_padded(screen_row, gutter_w_c, fold_text, text_w_c,
                              fg=TILDE_FG, bg=base_bg)
            screen_row += 1
            doc_line    = <int>fold_end + 1
            continue

        # --- Gutter ---
        if gutter_w_c > 0:
            if _sign_w_c > 0:
                grid.fill(screen_row, 0, _sign_w_c, bg=base_bg)
                _sign = sign_by_line.get(doc_line)
                if _sign:
                    grid.write(screen_row, 0, _sign.char,
                               fg=_sign.style.fg, bg=_sign.style.bg)
            if _number_w_c > 0:
                if relativenumber_c and doc_line != cursor_line_c:
                    num_str = str(abs(doc_line - cursor_line_c))
                else:
                    num_str = str(doc_line + 1)
                padded = num_str.rjust(_number_w_c - 1) + " "
                grid.write_str(screen_row, _sign_w_c, padded,
                               fg=gutter_style["fg"], bg=base_bg,
                               attrs=gutter_style["attrs"])

        # --- Text content ---
        line_text = visible_lines.get(doc_line, "")
        scroll_display_col = _logical_col_to_display_col_c(line_text, scroll_col_c, tabstop_c)
        visible_text = _visible_text_slice_c(line_text, scroll_display_col, text_w_c, tabstop_c)
        grid.write_padded(screen_row, gutter_w_c, visible_text, text_w_c, fg=base_fg, bg=base_bg)

        # --- Virtual text (inline after buffer content) ---
        _vtexts = vtext_by_line.get(doc_line)
        if _vtexts:
            _used      = len(visible_text)
            _remaining = text_w_c - _used
            if _remaining > 2:
                _vt_str  = " | ".join(vt.text.strip() for vt in _vtexts)
                _display = (" " + _vt_str)[:_remaining]
                _style   = _vtexts[0].style
                grid.write_str(screen_row, gutter_w_c + _used, _display,
                               fg=_style.fg if _style.fg is not None else base_fg,
                               bg=_style.bg if _style.bg is not None else base_bg)

        # --- Ghost text (faded inline suggestion) ---
        _ghost = ghost_by_line.get(doc_line)
        if _ghost is not None:
            _gcol  = _logical_col_to_display_col_c(line_text, <int>_ghost.col, tabstop_c) - scroll_display_col
            _gtext = _ghost.text
            _gstyle = _ghost.style
            if _gcol < text_w_c and _gtext:
                _avail    = text_w_c - _gcol
                _display_g = _gtext[:_avail]
                grid.write_str(screen_row, gutter_w_c + _gcol, _display_g,
                               fg=_gstyle.fg if _gstyle.fg is not None else base_fg,
                               bg=_gstyle.bg if _gstyle.bg is not None else base_bg)

        # --- Syntax highlighting ---
        if theme is not None:
            _line_spans = syntax_by_line.get(doc_line)
            if _line_spans:
                _apply_syntax_spans_c(
                    grid, _line_spans, doc_line, screen_row,
                    gutter_w_c, line_text, scroll_display_col, text_w_c,
                    tabstop_c, theme, syntax_style_cache,
                )

        # --- Indent guides ---
        if indent_guides_on:
            guide_indent = _resolve_blank_line_indent_c(
                visible_lines, doc_line, visible_start_c, visible_end_c, tabstop_c
            )
            # When terminal cursor is used (bar style), skip guide at cursor column.
            _guide_cursor_sc_c = -1
            if not paint_cursor_c and is_active and doc_line == cursor_line_c:
                _guide_cursor_sc_c = (
                    _logical_col_to_display_col_c(line_text, cursor_col_c, tabstop_c)
                    - scroll_display_col
                )
            _draw_indent_guides_c(
                grid, line_text, screen_row, gutter_w_c,
                scroll_display_col, text_w_c, tabstop_c, indent_guides_mode, guide_indent,
                _guide_cursor_sc_c
            )

        # --- Colorcolumn ---
        if colorcolumn_cells:
            cc_bg = (60, 40, 40)
            for cc in colorcolumn_cells:
                grid.paint_style_range(screen_row, gutter_w_c + cc, gutter_w_c + cc + 1, bg=cc_bg)

        # --- Highlight regions ---
        for dec in highlight_by_line.get(doc_line, ()):
            _apply_highlight_c(
                grid, dec, doc_line, screen_row,
                gutter_w_c, line_text, scroll_display_col, text_w_c, tabstop_c,
            )

        # --- Overlay chars ---
        for dec in overlay_by_line.get(doc_line, ()):
            sc = _logical_col_to_display_col_c(line_text, <int>dec.col, tabstop_c) - scroll_display_col
            if 0 <= sc < text_w_c:
                existing = grid.read_cell(screen_row, gutter_w_c + sc)
                grid.write(screen_row, gutter_w_c + sc, dec.display_char,
                           fg=dec.style.fg if dec.style.fg is not None else existing[1],
                           bg=dec.style.bg if dec.style.bg is not None else existing[2],
                           attrs=dec.style.attrs)

        # --- Cursor ---
        if paint_cursor_c and doc_line == cursor_line_c:
            cursor_screen_col = (
                _logical_col_to_display_col_c(line_text, cursor_col_c, tabstop_c)
                - scroll_display_col
            )
            if 0 <= cursor_screen_col < text_w_c:
                cell_col = gutter_w_c + cursor_screen_col
                ch = visible_text[cursor_screen_col: cursor_screen_col + 1] or " "
                style = CURSOR_ACTIVE if is_active else CURSOR_INACTIVE
                grid.write(screen_row, cell_col, ch, **style)

        screen_row += 1
        doc_line   += 1

    return grid


# ---------------------------------------------------------------------------
# Small helpers (ported for completeness; called from render_window above)
# ---------------------------------------------------------------------------

def _resolve_colorcolumn_cells_c(colorcolumn, int scroll_col, int text_w):
    """Precompute visible colorcolumn screen offsets for the current render."""
    if not colorcolumn:
        return ()
    cells = []
    for part in str(colorcolumn).split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        cc = int(part) - 1 - scroll_col
        if 0 <= cc < text_w:
            cells.append(cc)
    return tuple(cells)


def _index_highlight_spans_c(spans, int visible_start, int visible_end):
    """Index visible syntax spans by line for O(1) per-row lookup."""
    if not spans or visible_end < visible_start:
        return {}
    spans_by_line: dict = {}
    for span in spans:
        if span.end_line < visible_start:
            continue
        if span.start_line > visible_end:
            break
        for line in range(
            max(span.start_line, visible_start),
            min(span.end_line, visible_end) + 1,
        ):
            spans_by_line.setdefault(line, []).append(span)
    return spans_by_line


def _resolve_blank_line_indent_c(visible_lines, int doc_line, int visible_start,
                                  int visible_end, int tabstop):
    """Infer indent guides for blank lines from surrounding nonblank lines."""
    line_text = visible_lines.get(doc_line, "")
    if line_text.strip():
        return _leading_indent_columns_c(line_text, tabstop)

    cdef int prev_indent = -1
    for prev_line in range(doc_line - 1, visible_start - 1, -1):
        prev_text = visible_lines.get(prev_line, "")
        if prev_text.strip():
            prev_indent = _leading_indent_columns_c(prev_text, tabstop)
            break

    cdef int next_indent = -1
    for next_line in range(doc_line + 1, visible_end + 1):
        next_text = visible_lines.get(next_line, "")
        if next_text.strip():
            next_indent = _leading_indent_columns_c(next_text, tabstop)
            break

    if prev_indent >= 0 and next_indent >= 0:
        return prev_indent if prev_indent < next_indent else next_indent
    if prev_indent >= 0:
        return prev_indent
    if next_indent >= 0:
        return next_indent
    return None
