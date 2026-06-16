"""
ui.window_renderer — public shim.

Tries to import render_window from the Cython native extension; falls back
to the pure-Python implementation in _window_renderer_pure.py when the
extension has not been compiled.

All non-render_window symbols (_gutter_width, _GUIDE_CHAR, CURSOR_ACTIVE, etc.)
are always re-exported from the pure module so other callers are unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.ui.cell_grid import CellGrid

from peovim.ui._window_renderer_pure import render_window as _pure_render_window

try:
    from peovim._native.window_renderer import render_window as _native_render_window  # type: ignore[import]

    def render_window(*args, grid: CellGrid | None = None, **kwargs):  # type: ignore[misc]
        """Prefer native rendering, but fall back to pure rendering for unsupported features."""
        snapshot = args[0] if args else kwargs.get("snapshot")
        if getattr(snapshot, "options", {}).get("scrollbar"):
            return _pure_render_window(*args, grid=grid, **kwargs)
        return _native_render_window(*args, **kwargs)

except ImportError:
    render_window = _pure_render_window  # type: ignore[assignment]

# Re-export everything else unchanged from the pure module so that callsites
# importing helpers (_gutter_width, _draw_indent_guides, CURSOR_ACTIVE, …)
# continue to work without modification.
from peovim.ui._window_renderer_pure import (  # noqa: E402, F401
    _GUIDE_CHAR,
    _GUIDE_DIM,
    _RAINBOW_COLORS,
    ACTIVE_GUTTER,
    CURSOR_ACTIVE,
    CURSOR_INACTIVE,
    INACTIVE_GUTTER,
    TILDE_FG,
    _apply_highlight,
    _apply_syntax_spans,
    _draw_indent_guides,
    _extract_visible_lines,
    _gutter_width,
    _index_highlight_spans,
    _leading_indent_columns,
    _resolve_blank_line_indent,
    _resolve_colorcolumn_cells,
)

__all__ = [
    "render_window",
    "ACTIVE_GUTTER",
    "CURSOR_ACTIVE",
    "CURSOR_INACTIVE",
    "INACTIVE_GUTTER",
    "TILDE_FG",
    "_apply_highlight",
    "_apply_syntax_spans",
    "_draw_indent_guides",
    "_extract_visible_lines",
    "_gutter_width",
    "_GUIDE_CHAR",
    "_GUIDE_DIM",
    "_index_highlight_spans",
    "_leading_indent_columns",
    "_RAINBOW_COLORS",
    "_resolve_blank_line_indent",
    "_resolve_colorcolumn_cells",
]
