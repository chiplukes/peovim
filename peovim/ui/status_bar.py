"""
ui.status_bar — Status bar renderer

Renders: mode indicator, filename, dirty flag, cursor position, git branch,
and registered plugin components (ui.register_statusline_component()).
Replaceable via ui.set_message_handler() (noice pattern).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from peovim.ui.backend import ATTR_BOLD, Color
from peovim.ui.cell_grid import CellGrid
from peovim.ui.layout import Rect

if TYPE_CHECKING:
    from peovim.core.window import Window
    from peovim.modal.engine import Mode


# ---------------------------------------------------------------------------
# Color constants
# ---------------------------------------------------------------------------

STATUS_BG: Color = (40, 40, 70)
STATUS_FG: Color = (200, 200, 230)


# Lazy import to avoid circular dep at module level
def _mode_config():
    from peovim.modal.engine import Mode

    _COLORS = {
        Mode.INSERT: {"fg": (0, 220, 0), "bg": STATUS_BG, "attrs": ATTR_BOLD},
        Mode.REPLACE: {"fg": (220, 100, 0), "bg": STATUS_BG, "attrs": ATTR_BOLD},
        Mode.VISUAL_CHAR: {"fg": (220, 160, 0), "bg": STATUS_BG, "attrs": ATTR_BOLD},
        Mode.VISUAL_LINE: {"fg": (220, 160, 0), "bg": STATUS_BG, "attrs": ATTR_BOLD},
        Mode.VISUAL_BLOCK: {"fg": (220, 160, 0), "bg": STATUS_BG, "attrs": ATTR_BOLD},
        Mode.COMMAND: {"fg": (0, 220, 220), "bg": STATUS_BG, "attrs": ATTR_BOLD},
    }
    _LABELS = {
        Mode.INSERT: "-- INSERT --",
        Mode.REPLACE: "-- REPLACE --",
        Mode.VISUAL_CHAR: "-- VISUAL --",
        Mode.VISUAL_LINE: "-- VISUAL LINE --",
        Mode.VISUAL_BLOCK: "-- VISUAL BLOCK --",
        Mode.COMMAND: "-- COMMAND --",
    }
    return _COLORS, _LABELS


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def render_status_bar(
    window: Window,
    mode: Mode,
    rect: Rect,
    grid: CellGrid,
    workspace: Any | None = None,
    editor_state: Any | None = None,
) -> None:
    """
    Write status bar content into `grid` at rect.y. rect.height must be 1.
    The grid row is fully overwritten — no transparency.
    """
    row = rect.y
    x = rect.x
    width = rect.width

    # Fill entire row with status background
    grid.fill(row, x, width, " ", fg=STATUS_FG, bg=STATUS_BG)

    compare_status = getattr(editor_state, "compare_status", None) if editor_state is not None else None
    if compare_status:
        _render_compare_status_bar(compare_status, row, x, width, grid)
        return

    mode_colors, mode_labels = _mode_config()

    # --- Left: mode label ---
    left = mode_labels.get(mode, "")
    if left:
        style = mode_colors.get(mode, {"fg": STATUS_FG, "bg": STATUS_BG, "attrs": 0})
        grid.write_str(row, x, left, **style)

    # --- Right: line:col (1-based) ---
    right = f"{window.cursor.line + 1}:{window.cursor.col + 1}"
    right_col = x + width - len(right)
    if right_col >= x:
        grid.write_str(row, right_col, right, fg=STATUS_FG, bg=STATUS_BG)

    # --- Center: filename + dirty flag ---
    doc = window.document
    filepath_mode = "filename"
    if editor_state is not None:
        with contextlib.suppress(Exception):
            filepath_mode = str(editor_state.options.get("statusline_filepath", "filename"))
    if doc.path is None:
        fname = doc.name or "[No Name]"
    elif filepath_mode == "full":
        fname = str(doc.path)
    elif filepath_mode == "relative":
        import pathlib

        try:
            fname = str(doc.path.relative_to(pathlib.Path.cwd()))
        except ValueError:
            fname = str(doc.path)
    else:
        fname = doc.path.name
    if doc.dirty:
        fname += " [+]"

    # Right-align boundary: don't overlap right segment
    available_end = right_col
    center_col = x + max(0, (width - len(fname)) // 2)
    if center_col + len(fname) <= available_end:
        grid.write_str(row, center_col, fname, fg=STATUS_FG, bg=STATUS_BG)
    elif len(fname) <= available_end - x - len(left) - 1:
        # Fallback: left-justify after mode label
        start = x + len(left) + 1
        grid.write_str(row, start, fname, fg=STATUS_FG, bg=STATUS_BG)

    # --- Tab bar: show tabs inline at end of right segment when >1 tab ---
    if workspace is not None:
        tabs = getattr(workspace, "tabs", [])
        active_idx = getattr(workspace, "active_tab_index", 0)
        if len(tabs) > 1:
            tab_parts = []
            for i, _tab in enumerate(tabs):
                wins = _tab.all_windows()
                tab_fname = "[No Name]"
                if wins and wins[0].document.path is not None:
                    tab_fname = wins[0].document.path.name
                marker = "*" if i == active_idx else " "
                tab_parts.append(f"{marker}{i + 1}:{tab_fname}")
            tab_str = "  |  ".join(tab_parts)
            tab_col = x + width - len(tab_str)
            if tab_col > x + len(left):
                grid.fill(row, tab_col, len(tab_str), " ", fg=STATUS_FG, bg=STATUS_BG)
                grid.write_str(row, tab_col, tab_str, fg=STATUS_FG, bg=STATUS_BG)


def _render_compare_status_bar(compare_status: dict, row: int, x: int, width: int, grid: CellGrid) -> None:
    compare_bg: Color = (32, 52, 68)
    compare_fg: Color = (214, 224, 235)
    accent_fg: Color = (140, 214, 122)

    grid.fill(row, x, width, " ", fg=compare_fg, bg=compare_bg)

    left = _with_dirty(compare_status.get("left", "[left]"), compare_status.get("left_dirty", False))
    right = _with_dirty(compare_status.get("right", "[right]"), compare_status.get("right_dirty", False))
    center = " DIFF "

    center_col = x + max(0, (width - len(center)) // 2)
    grid.write_str(row, center_col, center, fg=accent_fg, bg=compare_bg, attrs=ATTR_BOLD)

    left_max = max(0, center_col - x - 1)
    if left_max > 0:
        left_text = _fit_left(left, left_max)
        grid.write_str(row, x, left_text, fg=compare_fg, bg=compare_bg)

    right_start = center_col + len(center) + 1
    right_max = max(0, x + width - right_start)
    if right_max > 0:
        right_text = _fit_right(right, right_max)
        grid.write_str(row, x + width - len(right_text), right_text, fg=compare_fg, bg=compare_bg)


def _with_dirty(label: str, dirty: bool) -> str:
    return f"{label} [+]" if dirty else label


def _fit_left(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def _fit_right(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 1:
        return text[-width:]
    return "…" + text[-(width - 1) :]
