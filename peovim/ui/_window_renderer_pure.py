"""
ui.window_renderer — render_window(): WindowSnapshot → CellGrid

Pure function; no side effects; parallelisable across windows.
Applies: visible lines, syntax highlight spans, all decoration types
(virtual text, virtual lines, signs, inlay hints, ghost text, conceal,
overlay chars), and soft word wrap (display lines ≠ buffer lines).

Primary performance bottleneck (~2-15ms). Phase 9 candidate for Cython
or parallel execution under free-threaded Python.

See notes/architecture.md §Hot Code Paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from peovim.ui.backend import ATTR_BOLD, Color
from peovim.ui.cell_grid import CellGrid
from peovim.ui.layout import Rect
from peovim.ui.scrollbar import (
    SCROLLBAR_THUMB_ACTIVE,
    SCROLLBAR_THUMB_CHAR,
    SCROLLBAR_THUMB_INACTIVE,
    SCROLLBAR_TRACK,
    SCROLLBAR_TRACK_CHAR,
    scrollbar_thumb_range,
    scrollbar_width,
)
from peovim.ui.text_layout import logical_col_to_display_col as _logical_col_to_display_col
from peovim.ui.text_layout import visible_text_slice as _visible_text_slice

if TYPE_CHECKING:
    from peovim.core.snapshot import BufferSnapshot, WindowSnapshot
    from peovim.syntax.engine import HighlightSpan
    from peovim.syntax.themes import Theme
    from peovim.ui.decorations import DecorationSet, HighlightRegion


# ---------------------------------------------------------------------------
# Color constants (placeholder — replaced by theme system in Phase 4)
# ---------------------------------------------------------------------------

TILDE_FG: Color = (80, 80, 140)
CURSOR_ACTIVE: dict = {"fg": None, "bg": (80, 120, 200), "attrs": ATTR_BOLD}
CURSOR_INACTIVE: dict = {"fg": None, "bg": (60, 60, 60), "attrs": 0}
ACTIVE_GUTTER: dict = {"fg": (140, 140, 180), "bg": None, "attrs": 0}
INACTIVE_GUTTER: dict = {"fg": (80, 80, 80), "bg": None, "attrs": 0}


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def render_window(
    snapshot: WindowSnapshot,
    rect: Rect,
    is_active: bool,
    decorations: DecorationSet | None = None,
    highlight_spans: list[HighlightSpan] | None = None,
    theme: Theme | None = None,
    extra_decorations: list | None = None,
    sign_registry: object | None = None,
    grid: CellGrid | None = None,
) -> CellGrid:
    """
    Render a WindowSnapshot into a CellGrid sized to rect.

    If `grid` is provided and matches rect dimensions, it is cleared and reused
    instead of allocating a new one (reduces GC pressure on the hot render path).
    The caller blits the returned grid into the master grid at (rect.x, rect.y).

    extra_decorations are merged with decorations before rendering.
    sign_registry is accepted for API compatibility (used in later phases).

    Rendering proceeds in five phases:
      Phase 1 — Pre-index all decorations by line (O(n) once → O(1) per row).
      Phase 2 — Setup: grid allocation, gutter width, option extraction,
                visible-line decode, syntax span indexing, fold-header lookup.
      Phase 3 — Line loop: for each visible row, render gutter + text, apply
                text layout (tabs, scroll), paint syntax spans and highlight
                regions, render ghost text, indent guides, colour column.
      Phase 4 — Special rows: virtual-text rows inserted after their anchor line.
      Phase 5 — Cursor: paint the cursor cell(s) on top of everything else.
    """
    # Merge extra_decorations with decorations (avoid copying when not needed)
    if extra_decorations:
        all_decs: list = list(decorations) if decorations else []
        all_decs.extend(extra_decorations)
    else:
        all_decs = decorations or []  # type: ignore[assignment]

    base_fg = theme.default_fg if theme is not None else None
    base_bg = theme.default_bg if theme is not None else None

    line_count = _line_count(snapshot)
    visible_start = snapshot.scroll_line
    visible_end = visible_start + max(0, rect.height - 1)
    options = snapshot.options

    # Pre-build per-line lookups for sign, virtual text, and ghost text decorations.
    # Done once here to keep the per-line loop O(1) per line.
    sign_by_line: dict[int, Any] = {}
    vtext_by_line: dict[int, list[Any]] = {}
    ghost_by_line: dict[int, Any] = {}
    highlight_by_line: dict[int, list[Any]] = {}
    overlay_by_line: dict[int, list[Any]] = {}
    vlines_by_anchor: dict[int, list[Any]] = {}
    if all_decs:
        from peovim.ui.decorations import GhostText as _GT  # noqa: PLC0415
        from peovim.ui.decorations import HighlightRegion as _HR  # noqa: PLC0415
        from peovim.ui.decorations import OverlayChar as _OC  # noqa: PLC0415
        from peovim.ui.decorations import Sign as _Sign  # noqa: PLC0415
        from peovim.ui.decorations import VirtualLine as _VLine  # noqa: PLC0415
        from peovim.ui.decorations import VirtualText as _VT  # noqa: PLC0415

        for _dec in all_decs:
            if isinstance(_dec, _Sign):
                _existing = sign_by_line.get(_dec.line)
                if _existing is None or _dec.priority >= _existing.priority:
                    sign_by_line[_dec.line] = _dec
            elif isinstance(_dec, _VT):
                vtext_by_line.setdefault(_dec.line, []).append(_dec)
            elif isinstance(_dec, _GT):
                # Last ghost text set for a line wins
                ghost_by_line[_dec.line] = _dec
            elif isinstance(_dec, _HR):
                if _dec.end_line < visible_start or _dec.start_line > visible_end:
                    continue
                for _line in range(max(_dec.start_line, visible_start), min(_dec.end_line, visible_end) + 1):
                    highlight_by_line.setdefault(_line, []).append(_dec)
            elif isinstance(_dec, _OC) and visible_start <= _dec.line <= visible_end:
                overlay_by_line.setdefault(_dec.line, []).append(_dec)
            elif isinstance(_dec, _VLine):
                vlines_by_anchor.setdefault(_dec.after_line, []).append(_dec)

    _grid: CellGrid
    if grid is not None and grid.width == rect.width and grid.height == rect.height:
        grid.clear()
        _grid = grid
    else:
        _grid = CellGrid(rect.width, rect.height)
    gutter_w = _gutter_width(snapshot, line_count, has_signs=bool(sign_by_line))
    available_text_w = max(0, rect.width - gutter_w)
    scrollbar_w = 1 if scrollbar_width(options) and available_text_w > 0 else 0
    text_w = max(0, available_text_w - scrollbar_w)
    signcolumn = options.get("signcolumn", "auto")
    render_sign_w = 2 if signcolumn == "yes" or (signcolumn == "auto" and bool(sign_by_line)) else 0
    gutter_style = ACTIVE_GUTTER if is_active else INACTIVE_GUTTER
    relativenumber = bool(options.get("relativenumber"))
    indent_guides_mode = str(options.get("indentguides", "none"))
    tabstop = int(options.get("tabstop", 4) or 4)
    colorcolumn_cells = _resolve_colorcolumn_cells(options.get("colorcolumn", ""), snapshot.scroll_col, text_w)

    # Decode only potentially visible lines for this render pass.
    visible_lines = _extract_visible_lines(snapshot, visible_start, visible_end)
    syntax_style_cache: dict[str, Any] = theme._style_cache if theme is not None else {}
    syntax_by_line = _index_highlight_spans(highlight_spans or (), visible_start, visible_end)

    # Build fold-header lookup: doc_line -> end_line for all closed folds
    fold_headers: dict[int, int] = {s: e for s, e in snapshot.closed_folds}

    screen_row = 0
    doc_line = snapshot.scroll_line

    # Virtual lines anchored before buffer line 0 (after_line == -1).
    # Only visible when the viewport starts at line 0.
    if doc_line == 0 and vlines_by_anchor.get(-1):
        for _vl in vlines_by_anchor[-1]:
            for _ in range(_vl.count):
                if screen_row >= rect.height:
                    break
                _vl_bg = _vl.style.bg if _vl.style.bg is not None else base_bg
                _grid.fill(screen_row, 0, rect.width, bg=_vl_bg)
                screen_row += 1

    while screen_row < rect.height:
        if doc_line >= line_count:
            # Past EOF — tilde
            if gutter_w > 0:
                _grid.fill(screen_row, 0, gutter_w, bg=base_bg)
            if text_w > 0:
                _grid.write(screen_row, gutter_w, "~", fg=TILDE_FG, bg=base_bg)
                _grid.fill(screen_row, gutter_w + 1, max(0, text_w - 1), fg=base_fg, bg=base_bg)
            screen_row += 1
            doc_line += 1
            continue

        # Compute sign/number column widths (mirrors _gutter_width logic)
        _sign_w = render_sign_w
        _number_w = gutter_w - _sign_w

        # --- Closed fold indicator ---
        fold_end = fold_headers.get(doc_line)
        if fold_end is not None:
            n_lines = fold_end - doc_line + 1
            fold_text = f"+--  {n_lines} lines  ---"
            if gutter_w > 0:
                if _sign_w > 0:
                    _grid.fill(screen_row, 0, _sign_w, bg=base_bg)
                    _sign = sign_by_line.get(doc_line)
                    if _sign:
                        _grid.write(
                            screen_row,
                            0,
                            _sign.char,
                            fg=_sign.style.fg,
                            bg=_sign.style.bg,
                        )
                if _number_w > 0:
                    num_str = str(doc_line + 1).rjust(_number_w - 1) + " "
                    _grid.write_str(
                        screen_row, _sign_w, num_str, fg=gutter_style["fg"], bg=base_bg, attrs=gutter_style["attrs"]
                    )
            if text_w > 0:
                _grid.write_padded(screen_row, gutter_w, fold_text, text_w, fg=TILDE_FG, bg=base_bg)
            screen_row += 1
            doc_line = fold_end + 1  # skip past fold body
            continue

        # --- Gutter ---
        if gutter_w > 0:
            # Sign column (leftmost 2 chars)
            if _sign_w > 0:
                _grid.fill(screen_row, 0, _sign_w, bg=base_bg)
                _sign = sign_by_line.get(doc_line)
                if _sign:
                    _grid.write(
                        screen_row,
                        0,
                        _sign.char,
                        fg=_sign.style.fg,
                        bg=_sign.style.bg,
                    )
            # Line number
            if _number_w > 0:
                if relativenumber and doc_line != snapshot.cursor_line:
                    num_str = str(abs(doc_line - snapshot.cursor_line))
                else:
                    num_str = str(doc_line + 1)  # 1-based
                padded = num_str.rjust(_number_w - 1) + " "
                _grid.write_str(
                    screen_row, _sign_w, padded, fg=gutter_style["fg"], bg=base_bg, attrs=gutter_style["attrs"]
                )

        # --- Text content ---
        line_text = visible_lines.get(doc_line, "")
        scroll_display_col = _logical_col_to_display_col(line_text, snapshot.scroll_col, tabstop)
        visible = _visible_text_slice(line_text, scroll_display_col, text_w, tabstop) if text_w > 0 else ""
        if text_w > 0:
            _grid.write_padded(screen_row, gutter_w, visible, text_w, fg=base_fg, bg=base_bg)

        # --- Virtual text (inline after buffer content) ---
        _vtexts = vtext_by_line.get(doc_line)
        if _vtexts and text_w > 0:
            _used = len(visible)
            _remaining = text_w - _used
            if _remaining > 2:  # need at least a separator + one char
                _vt_str = " | ".join(vt.text.strip() for vt in _vtexts)
                _display = (" " + _vt_str)[:_remaining]
                _style = _vtexts[0].style
                _grid.write_str(
                    screen_row,
                    gutter_w + _used,
                    _display,
                    fg=_style.fg if _style.fg is not None else base_fg,
                    bg=_style.bg if _style.bg is not None else base_bg,
                )

        # --- Ghost text (faded inline suggestion) ---
        _ghost = ghost_by_line.get(doc_line)
        if _ghost is not None and text_w > 0:
            _gcol = _logical_col_to_display_col(line_text, _ghost.col, tabstop) - scroll_display_col
            _gtext = _ghost.text
            _gstyle = _ghost.style
            if _gcol < text_w and _gtext:
                # Truncate to available width
                _avail = text_w - _gcol
                _display_g = _gtext[:_avail]
                _grid.write_str(
                    screen_row,
                    gutter_w + _gcol,
                    _display_g,
                    fg=_gstyle.fg if _gstyle.fg is not None else base_fg,
                    bg=_gstyle.bg if _gstyle.bg is not None else base_bg,
                )

        # --- Syntax highlighting ---
        if theme and text_w > 0:
            _line_spans = syntax_by_line.get(doc_line)
            if _line_spans:
                _apply_syntax_spans(
                    _grid,
                    _line_spans,
                    doc_line,
                    screen_row,
                    gutter_w,
                    line_text,
                    scroll_display_col,
                    text_w,
                    tabstop,
                    theme,
                    syntax_style_cache,
                )

        # --- Indent guides ---
        if indent_guides_mode != "none" and text_w > 0:
            guide_indent = _resolve_blank_line_indent(visible_lines, doc_line, visible_start, visible_end, tabstop)
            # When the terminal cursor is used (bar style), skip the guide at
            # the cursor column so the bar cursor is visible against plain text.
            _guide_cursor_sc = -1
            if not snapshot.options.get("_paint_cursor", True) and is_active and doc_line == snapshot.cursor_line:
                _guide_cursor_sc = (
                    _logical_col_to_display_col(line_text, snapshot.cursor_col, tabstop) - scroll_display_col
                )
            _draw_indent_guides(
                _grid,
                line_text,
                screen_row,
                gutter_w,
                scroll_display_col,
                text_w,
                tabstop,
                indent_guides_mode,
                guide_indent,
                _guide_cursor_sc,
            )

        # --- Colorcolumn ---
        if colorcolumn_cells and text_w > 0:
            cc_bg = (60, 40, 40)
            for cc in colorcolumn_cells:
                _grid.paint_style_range(screen_row, gutter_w + cc, gutter_w + cc + 1, bg=cc_bg)

        # --- Decorations ---
        if text_w > 0:
            for dec in highlight_by_line.get(doc_line, ()):
                _apply_highlight(
                    _grid, dec, doc_line, screen_row, gutter_w, line_text, scroll_display_col, text_w, tabstop
                )
            for dec in overlay_by_line.get(doc_line, ()):
                sc = _logical_col_to_display_col(line_text, dec.col, tabstop) - scroll_display_col
                if 0 <= sc < text_w:
                    existing = _grid.read_cell(screen_row, gutter_w + sc)
                    _grid.write(
                        screen_row,
                        gutter_w + sc,
                        dec.display_char,
                        fg=dec.style.fg if dec.style.fg is not None else existing[1],
                        bg=dec.style.bg if dec.style.bg is not None else existing[2],
                        attrs=dec.style.attrs,
                    )

        # --- Cursor ---
        if text_w > 0 and snapshot.options.get("_paint_cursor", True) and doc_line == snapshot.cursor_line:
            cursor_screen_col = (
                _logical_col_to_display_col(line_text, snapshot.cursor_col, tabstop) - scroll_display_col
            )
            if 0 <= cursor_screen_col < text_w:
                cell_col = gutter_w + cursor_screen_col
                ch = visible[cursor_screen_col : cursor_screen_col + 1] or " "
                style = CURSOR_ACTIVE if is_active else CURSOR_INACTIVE
                _grid.write(screen_row, cell_col, ch, **style)

        screen_row += 1

        # --- Virtual lines anchored after this buffer line ---
        for _vl in vlines_by_anchor.get(doc_line, ()):
            for _ in range(_vl.count):
                if screen_row >= rect.height:
                    break
                _vl_bg = _vl.style.bg if _vl.style.bg is not None else base_bg
                _grid.fill(screen_row, 0, rect.width, bg=_vl_bg)
                screen_row += 1

        doc_line += 1

    if scrollbar_w:
        _render_scrollbar(_grid, line_count, rect.height, snapshot.scroll_line, is_active, base_bg)  # type: ignore[arg-type]

    return _grid


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _line_count(snapshot: WindowSnapshot) -> int:
    return len(snapshot.buffer_snapshot.line_offsets)


def _render_scrollbar(
    grid: CellGrid,
    line_count: int,
    viewport_height: int,
    scroll_line: int,
    is_active: bool,
    base_bg: Color | None,
) -> None:
    if grid.width <= 0 or viewport_height <= 0:
        return

    thumb_top, thumb_height = scrollbar_thumb_range(line_count, viewport_height, scroll_line)
    thumb_style = SCROLLBAR_THUMB_ACTIVE if is_active else SCROLLBAR_THUMB_INACTIVE
    col = grid.width - 1
    for row in range(viewport_height):
        if thumb_top <= row < thumb_top + thumb_height:
            style = thumb_style
            ch = SCROLLBAR_THUMB_CHAR
        else:
            style = SCROLLBAR_TRACK
            ch = SCROLLBAR_TRACK_CHAR
        grid.write(
            row,
            col,
            ch,
            fg=style["fg"],
            bg=style["bg"] if style["bg"] is not None else base_bg,
            attrs=style["attrs"],
        )


def _gutter_width(snapshot: WindowSnapshot, line_count: int, has_signs: bool = False) -> int:
    number_w = 0
    if snapshot.options.get("number") or snapshot.options.get("relativenumber"):
        digits = max(len(str(line_count)), 3)
        number_w = digits + 1  # digits + space separator
    signcolumn = snapshot.options.get("signcolumn", "auto")
    if signcolumn == "yes":
        sign_w = 2
    elif signcolumn == "auto":
        sign_w = 2 if has_signs else 0
    else:
        sign_w = 0
    return number_w + sign_w


def _reconstruct_bytes(bs: BufferSnapshot) -> bytes:
    """Walk piece list once to reconstruct full byte content."""
    result = bytearray()
    for piece in bs.pieces:
        buf = bs.original if piece.buf == "original" else bs.add
        result.extend(buf[piece.start : piece.start + piece.length])
    return bytes(result)


def _extract_visible_lines(snapshot: WindowSnapshot, start_line: int, end_line: int) -> dict[int, str]:
    """Decode only the requested inclusive line range from the snapshot."""
    bs = snapshot.buffer_snapshot
    full_bytes = _reconstruct_bytes(bs)
    offsets = bs.line_offsets

    if not offsets:
        return {}

    start_line = max(0, start_line)
    end_line = min(end_line, len(offsets) - 1)
    if start_line > end_line:
        return {}

    lines: dict[int, str] = {}
    for i in range(start_line, end_line + 1):
        start = offsets[i]
        end = offsets[i + 1] if i + 1 < len(offsets) else len(full_bytes)
        raw = full_bytes[start:end]
        text = raw.decode("utf-8", errors="replace")
        lines[i] = text.rstrip("\n").rstrip("\r")
    return lines


def _resolve_colorcolumn_cells(colorcolumn: object, scroll_col: int, text_w: int) -> tuple[int, ...]:
    """Precompute visible colorcolumn screen offsets for the current render."""
    if not colorcolumn:
        return ()

    cells: list[int] = []
    for part in str(colorcolumn).split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        cc = int(part) - 1 - scroll_col
        if 0 <= cc < text_w:
            cells.append(cc)
    return tuple(cells)


def _apply_syntax_spans(
    grid: CellGrid,
    spans: list[HighlightSpan],
    doc_line: int,
    screen_row: int,
    gutter_w: int,
    line_text: str,
    scroll_display_col: int,
    text_w: int,
    tabstop: int,
    theme: Theme,
    style_cache: dict[str, Any],
) -> None:
    """Apply syntax highlight spans for a single doc_line to the cell grid."""
    for span in spans:
        # Spans are sorted by start; skip past-end spans early
        if span.end_line < doc_line:
            continue
        if span.start_line > doc_line:
            break  # all remaining spans are on later lines
        col_start = span.start_col if span.start_line == doc_line else 0
        col_end = span.end_col if span.end_line == doc_line else 0x7FFFFFFF
        display_col_start = _logical_col_to_display_col(line_text, col_start, tabstop)
        display_col_end = (
            _logical_col_to_display_col(line_text, col_end, tabstop) if col_end != 0x7FFFFFFF else 0x7FFFFFFF
        )
        sc_start = max(0, display_col_start - scroll_display_col)
        sc_end = min(text_w, (display_col_end - scroll_display_col) if display_col_end != 0x7FFFFFFF else text_w)
        if sc_start >= sc_end:
            continue
        style = style_cache.get(span.group)
        if style is None:
            style = theme.resolve(span.group)
            style_cache[span.group] = style
        if style.fg is None and style.bg is None and style.attrs == 0:
            continue  # nothing to paint
        paint_start = gutter_w + sc_start
        paint_end = min(gutter_w + sc_end, grid.width)
        grid.paint_style_range(screen_row, paint_start, paint_end, style.fg, style.bg, style.attrs)


def _index_highlight_spans(
    spans: tuple[HighlightSpan, ...] | list[HighlightSpan],
    visible_start: int,
    visible_end: int,
) -> dict[int, list[HighlightSpan]]:
    """Index visible syntax spans by line to avoid rescanning the full span list per row."""
    if not spans or visible_end < visible_start:
        return {}

    spans_by_line: dict[int, list[HighlightSpan]] = {}
    for span in spans:
        if span.end_line < visible_start:
            continue
        if span.start_line > visible_end:
            break
        for line in range(max(span.start_line, visible_start), min(span.end_line, visible_end) + 1):
            spans_by_line.setdefault(line, []).append(span)
    return spans_by_line


_GUIDE_CHAR = "\u2502"  # │
_GUIDE_DIM: Color = (50, 50, 50)
_RAINBOW_COLORS: list[Color] = [
    (255, 100, 100),
    (255, 200, 100),
    (100, 220, 100),
    (100, 200, 255),
    (200, 100, 255),
    (255, 100, 200),
]


def _draw_indent_guides(
    grid: CellGrid,
    line_text: str,
    screen_row: int,
    gutter_w: int,
    scroll_col: int,
    text_w: int,
    tabstop: int,
    mode: str,
    guide_indent: int | None = None,
    cursor_sc: int = -1,
) -> None:
    """Draw vertical indent guide lines on cells that contain spaces.

    cursor_sc: screen-column of the terminal cursor (-1 = unused).  The guide
    at that column is skipped so a bar-style terminal cursor is visible.
    """
    if guide_indent is None:
        if not line_text.strip():
            return  # skip blank lines unless an inferred indent is provided
        indent = _leading_indent_columns(line_text, tabstop)
    else:
        indent = guide_indent

    if indent == 0:
        return

    # Draw a guide at each tabstop column that falls within the indent
    level = 0
    col = tabstop  # first guide column (1-indexed indent level boundary)
    while col < indent:
        sc = col - scroll_col  # screen column within text area
        if sc != cursor_sc and 0 <= sc < text_w:
            cell_col = gutter_w + sc
            if cell_col < grid.width:
                existing = grid.read_cell(screen_row, cell_col)
                if existing[0] == " ":  # only replace space cells
                    fg = _RAINBOW_COLORS[level % len(_RAINBOW_COLORS)] if mode == "rainbow" else _GUIDE_DIM
                    grid.write(
                        screen_row,
                        cell_col,
                        _GUIDE_CHAR,
                        fg=fg,
                        bg=existing[2],
                        attrs=existing[3],
                    )
        level += 1
        col += tabstop


def _leading_indent_columns(line_text: str, tabstop: int) -> int:
    """Return the visual indent width for the leading whitespace of a line."""
    indent = 0
    for ch in line_text:
        if ch == " ":
            indent += 1
        elif ch == "\t":
            indent += tabstop - (indent % tabstop)
        else:
            break
    return indent


def _resolve_blank_line_indent(
    visible_lines: dict[int, str],
    doc_line: int,
    visible_start: int,
    visible_end: int,
    tabstop: int,
) -> int | None:
    """Infer indent guides for blank lines from surrounding visible nonblank lines."""
    line_text = visible_lines.get(doc_line, "")
    if line_text.strip():
        return _leading_indent_columns(line_text, tabstop)

    prev_indent: int | None = None
    for prev_line in range(doc_line - 1, visible_start - 1, -1):
        prev_text = visible_lines.get(prev_line, "")
        if prev_text.strip():
            prev_indent = _leading_indent_columns(prev_text, tabstop)
            break

    next_indent: int | None = None
    for next_line in range(doc_line + 1, visible_end + 1):
        next_text = visible_lines.get(next_line, "")
        if next_text.strip():
            next_indent = _leading_indent_columns(next_text, tabstop)
            break

    if prev_indent is not None and next_indent is not None:
        return min(prev_indent, next_indent)
    return prev_indent if prev_indent is not None else next_indent


def _apply_highlight(
    grid: CellGrid,
    dec: HighlightRegion,
    doc_line: int,
    screen_row: int,
    gutter_w: int,
    line_text: str,
    scroll_display_col: int,
    text_w: int,
    tabstop: int,
) -> None:
    """Apply a HighlightRegion decoration to the appropriate cells."""
    if doc_line < dec.start_line or doc_line > dec.end_line:
        return

    col_start = dec.start_col if doc_line == dec.start_line else 0
    col_end = dec.end_col if doc_line == dec.end_line else 0x7FFFFFFF

    display_col_start = _logical_col_to_display_col(line_text, col_start, tabstop)
    display_col_end = _logical_col_to_display_col(line_text, col_end, tabstop) if col_end < 0x7FFFFFFF else 0x7FFFFFFF
    sc_start = max(0, display_col_start - scroll_display_col)
    sc_end = min(text_w, (display_col_end - scroll_display_col) if display_col_end != 0x7FFFFFFF else text_w)
    if sc_start >= sc_end:
        return

    paint_start = gutter_w + sc_start
    paint_end = min(gutter_w + sc_end, grid.width)
    grid.paint_style_range(screen_row, paint_start, paint_end, dec.style.fg, dec.style.bg, dec.style.attrs)
