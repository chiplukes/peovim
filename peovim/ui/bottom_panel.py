"""
ui.bottom_panel — VS Code-style bottom panel with tab bar.

BottomPanelHost manages a set of named tabs, each providing a title and
render surface.  Inherits shared tab-management, focus, and key-routing
logic from PanelHost; this module adds horizontal-orientation rendering
and height management.

Tab rendering layout (within the reserved rect):
    row 0        │ tab bar  (one labelled entry per registered tab)
    row 1        │ ─────────── separator
    rows 2..h-1  │ active tab body
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from peovim.ui.cell_grid import CellGrid
from peovim.ui.panel_host import PanelHost

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
_TAB_ACTIVE_FG: tuple = (255, 255, 255)
_TAB_ACTIVE_BG: tuple = (50, 80, 130)
_TAB_INACTIVE_FG: tuple = (160, 160, 160)
_TAB_INACTIVE_BG: tuple = (35, 35, 50)
_SEP_FG: tuple = (80, 80, 100)
_BODY_FG: tuple = (200, 200, 200)
_BODY_BG: tuple = (28, 28, 40)
_FOCUSED_TAB_BORDER: tuple = (100, 150, 220)

# ---------------------------------------------------------------------------
# Tab protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class BottomPanelTab(Protocol):
    """Protocol that all bottom-panel tabs must satisfy."""

    @property
    def title(self) -> str: ...

    def render(self, grid: CellGrid) -> None: ...

    def feed_key(self, key: str) -> bool: ...


# ---------------------------------------------------------------------------
# BottomPanelHost
# ---------------------------------------------------------------------------


class BottomPanelHost(PanelHost):  # cm:4b3d2a
    """Horizontal bottom panel: tab bar + body, height-constrained."""

    _RESIZE_STEP = 3
    _MIN_HEIGHT = 4
    _MAX_HEIGHT = 50
    _TAB_OVERHEAD = 2  # tab bar row + separator row

    def __init__(self, default_height: int = 12) -> None:
        super().__init__()
        self._height: int = default_height
        self._pre_wk_tab: str | None = None  # saved tab name during which-key

        # Bottom-panel-local key → plug bindings (consulted only while focused)
        self._key_to_plug = {
            "[": "BottomPanelShrink",
            "]": "BottomPanelGrow",
            "q": "BottomPanelClose",
            "<Esc>": "BottomPanelBlur",
            "<A-k>": "BottomPanelBlur",  # Alt+k: go "up" to the editor
            "<": "BottomPanelPrevTab",
            ">": "BottomPanelNextTab",
        }

    # ------------------------------------------------------------------
    # Tab cycling — exclude the hidden "keys" (which-key) tab
    # ------------------------------------------------------------------

    def next_tab(self, **_: Any) -> None:  # type: ignore[override]
        super().next_tab(exclude={"keys"})

    def prev_tab(self, **_: Any) -> None:  # type: ignore[override]
        super().prev_tab(exclude={"keys"})

    # ------------------------------------------------------------------
    # Key routing — which-key and visual-mode special cases
    # ------------------------------------------------------------------

    def feed_key(self, key: str) -> bool:
        if not self._focused:
            return False
        # Give the active tab first chance at <Esc> when it is in a captured
        # state (e.g. visual mode) so it can exit before the panel-level blur.
        tab = self.active_tab
        if key == "<Esc>" and tab is not None and getattr(tab, "_visual", False) and tab.feed_key("<Esc>"):
            return True
        # While which-key is active ("keys" tab), don't consume panel-level
        # bindings — the keys belong to which-key navigation / the engine.
        if self._active_tab != "keys":
            plug_name = self._key_to_plug.get(key)
            if plug_name is not None:
                self._execute_plug(plug_name)
                return True
        if tab is None:
            return False
        return bool(tab.feed_key(key))

    def _builtin_plugs(self) -> dict[str, Any]:
        return {
            "BottomPanelClose": self.hide,
            "BottomPanelBlur": self.blur,
            "BottomPanelShrink": lambda: self._adjust_height(-self._RESIZE_STEP),
            "BottomPanelGrow": lambda: self._adjust_height(self._RESIZE_STEP),
            "BottomPanelNextTab": self.next_tab,
            "BottomPanelPrevTab": self.prev_tab,
        }

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def reserved_height(self, total_rows: int) -> int:
        if not self._visible:
            return 0
        max_h = max(self._MIN_HEIGHT, total_rows // 2)
        return max(self._MIN_HEIGHT, min(self._height, max_h))

    def _adjust_height(self, delta: int) -> None:
        self._height = max(self._MIN_HEIGHT, min(self._height + delta, self._MAX_HEIGHT))

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def click(self, col: int, row: int) -> bool:
        """Handle a click at panel-local (col, row).  Returns True if consumed."""
        if row == 0:
            return self._click_tab_bar(col)
        self.focus()
        tab = self.active_tab
        if tab is not None and hasattr(tab, "click"):
            return bool(tab.click(col, row - self._TAB_OVERHEAD))
        return True

    def _click_tab_bar(self, col: int) -> bool:
        x = 0
        for name in self._tab_order:
            if name == "keys":
                continue
            tab = self._tabs.get(name)
            title = getattr(tab, "title", name)
            label = f" {title} "
            w = len(label)
            if x <= col < x + w:
                self._active_tab = name
                self._focused = True
                self._needs_full_redraw = True
                return True
            x += w
            if x < 200:  # separator char
                x += 1
        self.focus()
        return True

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render(self, grid: CellGrid, theme: Any = None) -> bool:
        if not self._visible or grid.width == 0 or grid.height == 0:
            return False

        # ── Tab bar (row 0) ──────────────────────────────────────────
        grid.fill(0, 0, grid.width, " ", _TAB_INACTIVE_FG, _TAB_INACTIVE_BG)
        x = 0
        for name in self._tab_order:
            if name == "keys":
                continue  # keys tab is hidden from the visible bar
            tab = self._tabs.get(name)
            title = getattr(tab, "title", name)
            is_active = name == self._active_tab
            focused_and_active = is_active and self._focused

            fg = _TAB_ACTIVE_FG if is_active else _TAB_INACTIVE_FG
            bg = _TAB_ACTIVE_BG if is_active else _TAB_INACTIVE_BG
            label = f" {title} "
            if x + len(label) > grid.width:
                break
            grid.write_str(0, x, label, fg, bg)
            if focused_and_active and x + len(label) < grid.width:
                # underline indicator: overwrite the first char with a mark
                grid.write(0, x + len(label) - 1, "▁", _FOCUSED_TAB_BORDER, bg)
            x += len(label)
            if x < grid.width:
                grid.write(0, x, "│", _SEP_FG, _TAB_INACTIVE_BG)
                x += 1

        if grid.height < 2:
            return True

        # ── Separator (row 1) ────────────────────────────────────────
        sep_char = "─"
        for col in range(grid.width):
            grid.write(1, col, sep_char, _SEP_FG, _BODY_BG)

        if grid.height < self._TAB_OVERHEAD + 1:
            return True

        # ── Body (rows 2+) ───────────────────────────────────────────
        body_h = grid.height - self._TAB_OVERHEAD
        body_grid = CellGrid(grid.width, body_h)
        body_grid.apply_default_style(fg=_BODY_FG, bg=_BODY_BG)

        import contextlib

        tab = self.active_tab
        if tab is not None:
            with contextlib.suppress(Exception):
                tab.render(body_grid)

        grid.blit(body_grid, dest_x=0, dest_y=self._TAB_OVERHEAD)
        return True


# ---------------------------------------------------------------------------
# Built-in: Output / log tab
# ---------------------------------------------------------------------------

_LOG_LEVEL_COLORS: dict[int, tuple] = {
    logging.DEBUG: (120, 120, 140),
    logging.INFO: (200, 200, 200),
    logging.WARNING: (220, 180, 60),
    logging.ERROR: (220, 80, 80),
    logging.CRITICAL: (255, 60, 60),
}
_LOG_BG = _BODY_BG


class _NotPeovimFilter(logging.Filter):
    """Rejects records from the 'peovim' logger hierarchy (those are handled separately)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name != "peovim" and not record.name.startswith("peovim.")


class LogOutputTab:
    """
    Bottom panel tab that captures live Python logging output.

    Call `attach()` once to install a log handler.  All records from
    the root logger (and any logger that propagates) are captured.
    """

    title = "output"
    _MAX_LINES = 4000

    def __init__(self) -> None:
        self._lines: deque[tuple[str, int]] = deque(maxlen=self._MAX_LINES)
        self._cursor: int = 0  # index of focused line
        self._scroll: int = 0  # index of top-visible line
        self._auto_scroll: bool = True  # follow new output
        self._visual: bool = False  # visual-line selection active
        self._visual_anchor: int = 0  # line where V was pressed
        self._handler: logging.Handler | None = None
        # Set by the editor layer (e.g. main.py) to write yanked text to registers.
        self.yank_fn: Any = None

    # ------------------------------------------------------------------
    # Log capture
    # ------------------------------------------------------------------

    def attach(self) -> None:
        """Install logging handlers to capture records into this tab.

        Attaches to both:
          - "peovim" logger: captures all peovim-internal logs
          - root logger: captures user config / plugin logs (logging.info() etc.)

        Lowers logger levels to INFO so INFO records actually reach the handler.
        """
        if self._handler is not None:
            return
        fmt = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")

        # Primary handler on the "peovim" logger
        handler = _LogCaptureHandler(self)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(fmt)
        peovim_logger = logging.getLogger("peovim")
        # Lower peovim logger to INFO so INFO records are not silently dropped
        if peovim_logger.level > logging.INFO or peovim_logger.level == logging.NOTSET:
            peovim_logger.setLevel(logging.INFO)
        peovim_logger.addHandler(handler)
        self._handler = handler

        # Secondary handler on root logger so bare logging.info() / logging.getLogger(name).info()
        # calls from user config and plugins are also captured.
        # A filter skips "peovim.*" records to avoid duplicates (they propagate=False, but be safe).
        root_handler = _LogCaptureHandler(self)
        root_handler.setLevel(logging.INFO)
        root_handler.setFormatter(fmt)
        root_handler.addFilter(_NotPeovimFilter())
        root_logger = logging.root
        if root_logger.level > logging.INFO or root_logger.level == logging.NOTSET:
            root_logger.setLevel(logging.INFO)
        root_logger.addHandler(root_handler)
        self._root_handler = root_handler

    def detach(self) -> None:
        """Remove the log handlers."""
        if self._handler is not None:
            logging.getLogger("peovim").removeHandler(self._handler)
            self._handler = None
        root_handler = getattr(self, "_root_handler", None)
        if root_handler is not None:
            logging.root.removeHandler(root_handler)
            self._root_handler = None

    def add_line(self, text: str, level: int) -> None:
        was_full = len(self._lines) >= self._MAX_LINES
        self._lines.append((text, level))
        if self._auto_scroll:
            self._cursor = len(self._lines) - 1
            # _scroll is computed at render time once we know the viewport height
        elif was_full:
            # An item was dropped from the front of the deque; shift indices so
            # cursor/anchor/scroll continue to point at the same items.
            self._cursor = max(0, self._cursor - 1)
            self._visual_anchor = max(0, self._visual_anchor - 1)
            self._scroll = max(0, self._scroll - 1)

    def clear(self) -> None:
        self._lines.clear()
        self._cursor = 0
        self._scroll = 0
        self._visual = False

    # ------------------------------------------------------------------
    # Tab interface
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _move_cursor(self, delta: int) -> None:
        n = len(self._lines)
        self._cursor = max(0, min(self._cursor + delta, max(0, n - 1)))
        self._auto_scroll = False

    def _ensure_cursor_visible(self, height: int) -> None:
        """Adjust scroll so the cursor line is visible."""
        if self._cursor < self._scroll:
            self._scroll = self._cursor
        elif height > 0 and self._cursor >= self._scroll + height:
            self._scroll = self._cursor - height + 1

    def _selection_range(self) -> tuple[int, int]:
        """Return (lo, hi) inclusive line indices of the current selection."""
        if self._visual:
            lo = min(self._visual_anchor, self._cursor)
            hi = max(self._visual_anchor, self._cursor)
        else:
            lo = hi = self._cursor
        return lo, hi

    def _do_yank(self) -> None:
        if not self._lines:
            return
        lo, hi = self._selection_range()
        lines = list(self._lines)
        selected = [text for text, _ in lines[lo : hi + 1]]
        text = "\n".join(selected)
        if self.yank_fn is not None:
            self.yank_fn(text)
        self._visual = False

    # ------------------------------------------------------------------
    # Tab interface
    # ------------------------------------------------------------------

    def feed_key(self, key: str) -> bool:
        n = len(self._lines)

        # Visual mode: <Esc> exits without blurring the panel
        if key == "<Esc>":
            if self._visual:
                self._visual = False
                return True
            return False  # let BottomPanelHost handle blur

        if key in ("V", "v"):
            self._auto_scroll = False
            self._visual = not self._visual
            if self._visual:
                self._visual_anchor = self._cursor
            return True

        if key == "y":
            self._do_yank()
            return True

        if key == "Y":
            # Yank all lines regardless of selection
            text = "\n".join(line for line, _ in self._lines)
            if self.yank_fn is not None:
                self.yank_fn(text)
            return True

        if key in ("j", "<Down>"):
            self._move_cursor(1)
            return True
        if key in ("k", "<Up>"):
            self._move_cursor(-1)
            return True
        if key in ("<C-d>",):
            self._move_cursor(10)
            return True
        if key in ("<C-u>",):
            self._move_cursor(-10)
            return True
        if key == "G":
            self._auto_scroll = True
            self._cursor = max(0, n - 1)
            return True
        if key == "g":
            self._auto_scroll = False
            self._cursor = 0
            return True
        if key == "c":
            self.clear()
            return True
        return False

    def render(self, grid: CellGrid) -> None:
        if grid.height == 0 or grid.width == 0:
            return

        n = len(self._lines)
        if n == 0:
            grid.write_str(0, 0, "  (no output yet)", (90, 90, 110), _LOG_BG)
            return

        if self._auto_scroll:
            # Keep last line pinned to the bottom of the view
            self._cursor = n - 1
            self._scroll = max(0, n - grid.height)
        else:
            self._ensure_cursor_visible(grid.height)
        lo, hi = self._selection_range()

        lines = list(self._lines)
        start = max(0, min(self._scroll, n - 1))
        for row in range(grid.height):
            idx = start + row
            if idx >= n:
                break
            text, level = lines[idx]
            fg = _LOG_LEVEL_COLORS.get(level, _BODY_FG)
            display = text[: grid.width]

            is_cursor = idx == self._cursor
            in_visual = self._visual and lo <= idx <= hi

            if in_visual and is_cursor:
                bg: tuple = (80, 105, 150)  # active end of visual selection
                fg = (255, 255, 255)
            elif in_visual:
                bg = (60, 80, 120)  # selection highlight
                fg = (230, 230, 230)
            elif is_cursor:
                bg = (45, 50, 65)  # cursor line, subtle
            else:
                bg = _LOG_BG

            # Fill the row first so the whole line gets the background
            grid.fill(row, 0, grid.width, " ", fg, bg)
            grid.write_str(row, 0, display, fg, bg)

        # Mode indicator + scroll % in bottom-right corner
        if grid.height > 0:
            if self._visual:
                sel_lo, sel_hi = self._selection_range()
                mode_str = f" VISUAL {sel_hi - sel_lo + 1}L "
            else:
                mode_str = ""
            pct_str = f" {int(100 * start / max(1, n - 1))}% " if n > grid.height else ""
            indicator = mode_str + pct_str
            if indicator:
                col = max(0, grid.width - len(indicator))
                ind_bg: tuple = (70, 50, 100) if self._visual else (20, 20, 30)
                grid.write_str(grid.height - 1, col, indicator, (200, 200, 220), ind_bg)


# ---------------------------------------------------------------------------
# Built-in: Which-key tab (hidden from tab bar, shown during key sequences)
# ---------------------------------------------------------------------------


class WhichKeyTab:
    """
    Wrapper tab that renders a WhichKeyPanel inside the bottom panel.

    This tab is registered under the name "keys" and is never shown in the
    tab bar.  It is activated programmatically by show_which_key() when the
    bottom panel is visible.
    """

    title = "keys"

    def __init__(self, which_key_panel: Any) -> None:
        self._wk = which_key_panel

    def render(self, grid: CellGrid) -> None:
        if self._wk is not None and getattr(self._wk, "is_open", False):
            # Render starting at row 0 within the grid (body grid already excludes tab bar)
            self._wk.render(grid, 0, grid.width)

    def feed_key(self, key: str) -> bool:
        return False


class _LogCaptureHandler(logging.Handler):
    """Logging handler that feeds records into a LogOutputTab."""

    def __init__(self, tab: LogOutputTab) -> None:
        super().__init__()
        self._tab = tab

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            # Split multi-line messages
            for line in msg.splitlines():
                self._tab.add_line(line, record.levelno)
        except Exception:
            self.handleError(record)
