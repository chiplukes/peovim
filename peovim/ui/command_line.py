"""
ui.command_line — Command-line input: ':', '/', '?', '!'

Renders the command line at the bottom, handles history navigation,
and provides completion for ex commands. Replaceable via
ui.set_cmdline_handler() for noice-style plugins.
"""

from __future__ import annotations

from peovim.commands.parser import _parse_addr
from peovim.ui.backend import Color
from peovim.ui.cell_grid import CellGrid
from peovim.ui.layout import Rect
from peovim.ui.text_layout import expand_for_display, logical_col_to_display_col

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

CMDLINE_FG: Color = None  # default terminal foreground
CMDLINE_BG: Color = None  # default terminal background
PROMPT_FG: Color = (200, 200, 0)  # yellow prompt character
CURSOR_FG: Color = (0, 0, 0)
CURSOR_BG: Color = (200, 200, 200)
COMPLETION_BG: Color = (24, 24, 36)
COMPLETION_FG: Color = (190, 190, 210)
COMPLETION_SEL_BG: Color = (70, 70, 115)
COMPLETION_SEL_FG: Color = (255, 255, 255)


# ---------------------------------------------------------------------------
# CommandLine
# ---------------------------------------------------------------------------


class CommandLine:
    """
    Stateful command-line input widget.

    enter(prompt) activates the widget. feed_key(key) processes input and
    returns a committed string on <CR> or "" on <Esc>/<C-c>, or None while
    still accumulating. render(rect, grid) draws the widget into a CellGrid.
    """

    def __init__(self, history_max: int = 100) -> None:
        self._active: bool = False
        self._prompt: str = ""
        self._text: str = ""
        self._cursor_col: int = 0
        self._history: list[str] = []
        self._history_index: int = -1  # -1 = live edit (not in history)
        self._saved_text: str = ""  # live text saved when entering history
        self._history_max = history_max
        self._completion_source: list[str] = []
        self._completion_open: bool = False
        self._completion_items: list[str] = []
        self._completion_selected: int = 0
        self._last_completion_rows: int = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active

    @property
    def text(self) -> str:
        return self._text

    @property
    def cursor_col(self) -> int:
        return self._cursor_col

    @property
    def prompt(self) -> str:
        return self._prompt

    @property
    def completion_open(self) -> bool:
        return self._completion_open and bool(self._completion_items)

    @property
    def last_completion_rows(self) -> int:
        return self._last_completion_rows

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def enter(self, prompt: str, initial: str = "") -> None:
        """Activate the command line with the given prompt character."""
        self._active = True
        self._prompt = prompt
        self._text = initial
        self._cursor_col = len(initial)
        self._history_index = -1
        self._saved_text = ""
        self._completion_open = False
        self._completion_items = []
        self._completion_selected = 0
        self._last_completion_rows = 0
        self._refresh_completion()

    def exit(self) -> None:
        """Deactivate. History is committed by feed_key on <CR>."""
        self._last_completion_rows = self.visible_completion_rows()
        self._completion_open = False
        self._active = False

    def set_completion_source(self, items: list[str]) -> None:
        self._completion_source = sorted(dict.fromkeys(items))
        self._refresh_completion()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def feed_key(self, key: str) -> str | None:
        """
        Process one key. Returns:
          str  — committed text (<CR>) or "" (<Esc>/<C-c>, cancellation)
          None — still accumulating
        """
        if not self._active:
            return None

        if key == "<CR>":
            text = self._text
            self._commit_to_history(text)
            self.exit()
            return text

        if key in ("<Esc>", "<C-c>"):
            self.exit()
            return ""

        if self._handle_completion_key(key):
            return None

        if key == "<BS>":
            if self._cursor_col > 0:
                pos = self._cursor_col
                self._text = self._text[: pos - 1] + self._text[pos:]
                self._cursor_col -= 1
            self._refresh_completion()
            return None

        if key == "<C-w>":
            # Delete word before cursor
            text = self._text[: self._cursor_col]
            stripped = text.rstrip()
            word_start = stripped.rfind(" ") + 1  # 0 if no space
            self._text = stripped[:word_start] + self._text[self._cursor_col :]
            self._cursor_col = word_start
            self._refresh_completion()
            return None

        if key == "<C-u>":
            self._text = self._text[self._cursor_col :]
            self._cursor_col = 0
            self._refresh_completion()
            return None

        if key in ("<Left>",):
            self._cursor_col = max(0, self._cursor_col - 1)
            self._refresh_completion()
            return None

        if key in ("<Right>",):
            self._cursor_col = min(len(self._text), self._cursor_col + 1)
            self._refresh_completion()
            return None

        if key in ("<Home>", "<C-a>"):
            self._cursor_col = 0
            self._refresh_completion()
            return None

        if key in ("<End>", "<C-e>"):
            self._cursor_col = len(self._text)
            self._refresh_completion()
            return None

        if key == "<Up>":
            if self.completion_open:
                self._move_completion(-1)
                return None
            self._history_up()
            return None

        if key == "<Down>":
            if self.completion_open:
                self._move_completion(1)
                return None
            self._history_down()
            return None

        # Printable single character
        if len(key) == 1:
            pos = self._cursor_col
            self._text = self._text[:pos] + key + self._text[pos:]
            self._cursor_col += 1
            self._refresh_completion()

        return None

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _commit_to_history(self, text: str) -> None:
        if not text:
            return
        # Don't duplicate most recent entry
        if self._history and self._history[0] == text:
            return
        self._history.insert(0, text)
        if len(self._history) > self._history_max:
            self._history = self._history[: self._history_max]

    def _history_up(self) -> None:
        if not self._history:
            return
        if self._history_index == -1:
            self._saved_text = self._text
        next_idx = min(self._history_index + 1, len(self._history) - 1)
        if next_idx != self._history_index or self._history_index == -1:
            self._history_index = next_idx
            self._text = self._history[self._history_index]
            self._cursor_col = len(self._text)

    def _history_down(self) -> None:
        if self._history_index == -1:
            return
        if self._history_index == 0:
            self._history_index = -1
            self._text = self._saved_text
        else:
            self._history_index -= 1
            self._text = self._history[self._history_index]
        self._cursor_col = len(self._text)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self, rect: Rect, grid: CellGrid) -> None:
        """Draw the command line into grid at rect.y."""
        row = rect.y
        x = rect.x
        width = rect.width

        if not self._active:
            grid.fill(row, x, width)
            return

        # Fill row with default background
        grid.fill(row, x, width, " ", bg=CMDLINE_BG)

        # Prompt character
        grid.write(row, x, self._prompt, fg=PROMPT_FG)

        # Compute visible text window (scroll right so cursor is visible)
        available = width - 1  # space after prompt
        text = self._text
        cursor = self._cursor_col
        tabstop = 4
        display_text = expand_for_display(text, tabstop)
        cursor_display_col = logical_col_to_display_col(text, cursor, tabstop)

        if len(display_text) > available:
            start = cursor_display_col - available + 1 if cursor_display_col >= available else 0
            visible_text = display_text[start : start + available]
            cursor_screen = cursor_display_col - start
        else:
            visible_text = display_text
            cursor_screen = cursor_display_col

        # Write text
        text_col = x + 1
        grid.write_str(row, text_col, visible_text, fg=CMDLINE_FG, bg=CMDLINE_BG)

        # Cursor indicator
        cursor_cell_col = text_col + cursor_screen
        if cursor_cell_col < x + width:
            ch = visible_text[cursor_screen : cursor_screen + 1] or " "
            grid.write(row, cursor_cell_col, ch, fg=CURSOR_FG, bg=CURSOR_BG)

    def render_completion(self, rect: Rect, grid: CellGrid, *, max_rows: int = 8) -> None:
        rows = min(self.visible_completion_rows(), max_rows)
        self._last_completion_rows = rows
        if rows <= 0:
            return

        scroll = max(0, self._completion_selected - rows + 1)
        visible = self._visible_completion_items(rows)
        start_y = max(0, rect.y - rows)
        for idx, item in enumerate(visible):
            row = start_y + idx
            is_selected = scroll + idx == self._completion_selected
            fg = COMPLETION_SEL_FG if is_selected else COMPLETION_FG
            bg = COMPLETION_SEL_BG if is_selected else COMPLETION_BG
            grid.fill(row, rect.x, rect.width, " ", bg=bg)
            grid.write_str(row, rect.x, item[: rect.width], fg=fg, bg=bg)

    def visible_completion_rows(self) -> int:
        if not self.completion_open:
            return 0
        return len(self._completion_items)

    def _visible_completion_items(self, rows: int) -> list[str]:
        scroll = max(0, self._completion_selected - rows + 1)
        return self._completion_items[scroll : scroll + rows]

    def _move_completion(self, delta: int) -> None:
        if not self._completion_items:
            return
        self._completion_selected = max(0, min(self._completion_selected + delta, len(self._completion_items) - 1))

    def _handle_completion_key(self, key: str) -> bool:
        if self._prompt != ":":
            return False
        if key != "<Tab>":
            return False
        was_open = self.completion_open
        self._refresh_completion(force_open=True)
        if not self._completion_items:
            return True
        if was_open:
            self._accept_completion()
        return True

    def _accept_completion(self) -> None:
        context = self._command_completion_context()
        if context is None or not self._completion_items:
            return
        start, end, suffix = context
        selected = self._completion_items[self._completion_selected]
        replacement = selected + (suffix if suffix.startswith("!") else "")
        replacement = "! " if replacement == "!" else replacement + " "
        self._text = self._text[:start] + replacement + self._text[end + len(suffix) :]
        self._cursor_col = start + len(replacement)
        self._completion_open = False
        self._completion_items = []
        self._completion_selected = 0

    def _refresh_completion(self, *, force_open: bool = False) -> None:
        context = self._command_completion_context()
        if self._prompt != ":" or context is None or not self._completion_source:
            self._completion_open = False
            self._completion_items = []
            self._completion_selected = 0
            return
        start, end, _suffix = context
        query = self._text[start:end]
        if not self._completion_open and not force_open:
            return
        self._completion_open = True
        self._completion_items = _filter_completion_items(query, self._completion_source)
        if not self._completion_items:
            self._completion_open = False
        self._completion_selected = max(0, min(self._completion_selected, len(self._completion_items) - 1))

    def _command_completion_context(self) -> tuple[int, int, str] | None:
        if self._prompt != ":":
            return None
        start, end = _find_ex_command_span(self._text)
        if not (start <= self._cursor_col <= end):
            return None
        suffix = self._text[end:]
        if suffix and not (suffix.startswith("!") and suffix[1:].strip() == ""):
            return None
        return start, end, suffix


def _find_ex_command_span(text: str) -> tuple[int, int]:
    pos = 0
    length = len(text)
    while pos < length and text[pos] == " ":
        pos += 1
    if pos < length and text[pos] == "%":
        pos += 1
    else:
        addr1, next_pos = _parse_addr(text, pos)
        if addr1 is not None:
            pos = next_pos
            if pos < length and text[pos] == ",":
                pos += 1
                addr2, next_pos = _parse_addr(text, pos)
                if addr2 is not None:
                    pos = next_pos
    while pos < length and text[pos] == " ":
        pos += 1
    start = pos
    while pos < length and text[pos].isalnum():
        pos += 1
    if start == pos and pos < length and text[pos] == "!":
        return pos, pos + 1
    return start, pos


def _filter_completion_items(query: str, items: list[str]) -> list[str]:
    if not query:
        return list(items)
    lower_query = query.lower()
    try:
        from rapidfuzz import fuzz as _fuzz

        scored: list[tuple[float, str]] = []
        for item in items:
            score = _fuzz.partial_ratio(lower_query, item.lower())
            if score >= 40:
                scored.append((score, item))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item for _, item in scored]
    except ImportError:
        return [item for item in items if lower_query in item.lower()]
