"""
ui.cell_grid — CellGrid: 2D cell buffer with per-cell dirty tracking.

Attempts to import the compiled native extension (peovim._native.cell_grid).
Falls back to the pure-Python implementation transparently when the extension
is not available (no compiler, no --extra native, CI without build).

All callers import only `CellGrid` from this module; the dispatch is invisible.
"""

from __future__ import annotations

try:  # cm:3d7f4a
    from peovim._native.cell_grid import CellGrid  # type: ignore[import]
except ImportError:
    from peovim.ui._cell_grid_pure import CellGrid  # type: ignore[assignment]  # noqa: F401

__all__ = ["CellGrid"]
