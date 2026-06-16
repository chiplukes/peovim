"""
ui.backend — TerminalBackend Protocol + RenderOp union type

The ONLY interface the editor uses to talk to the terminal. Nothing above
this layer imports any terminal library. All terminal access goes through here.

See notes/architecture.md §Terminal Backend for the full specification.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, NamedTuple, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Color
# ---------------------------------------------------------------------------

# Colors are stored as (r, g, b) tuples or special sentinel ints.
# None means "default terminal color".
Color = tuple[int, int, int] | None

# Common color sentinels
DEFAULT_COLOR: Color = None

# Attribute bit flags
ATTR_BOLD = 1 << 0
ATTR_ITALIC = 1 << 1
ATTR_UNDERLINE = 1 << 2
ATTR_BLINK = 1 << 3
ATTR_REVERSE = 1 << 4
ATTR_STRIKETHROUGH = 1 << 5
ATTR_DIM = 1 << 6

# ---------------------------------------------------------------------------
# Input events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyEvent:
    """A keyboard event from the terminal."""

    key: str  # e.g. "a", "A", "<CR>", "<Esc>", "<C-c>", "<F1>", "<Up>"
    text: str = ""
    shift: bool = False
    ctrl: bool = False
    alt: bool = False
    meta: bool = False


@dataclass(frozen=True)
class MouseEvent:
    """A mouse event from the terminal."""

    row: int
    col: int
    button: int  # 0=left, 1=middle, 2=right, 3=scroll_up, 4=scroll_down
    pressed: bool  # True=press, False=release
    dragging: bool = False  # True when button held and moved (motion event)
    shift: bool = False
    ctrl: bool = False
    alt: bool = False


InputEvent = KeyEvent | MouseEvent

# ---------------------------------------------------------------------------
# Render operations
# ---------------------------------------------------------------------------


class MoveCursor(NamedTuple):
    row: int
    col: int


@dataclass(frozen=True)
class PutCell:
    char: str
    fg: Color = None
    bg: Color = None
    attrs: int = 0


class PutCells(NamedTuple):
    """Write a run of same-style cells in one operation."""

    text: str
    fg: Color = None
    bg: Color = None
    attrs: int = 0


@dataclass(frozen=True)
class ClearLine:
    row: int


@dataclass(frozen=True)
class ClearScreen:
    pass


@dataclass(frozen=True)
class ShowCursor:
    pass


CursorShape = Literal["block", "bar"]


@dataclass(frozen=True)
class SetCursorStyle:
    shape: CursorShape = "block"
    blink: bool = False


@dataclass(frozen=True)
class HideCursor:
    pass


@dataclass(frozen=True)
class SetTitle:
    text: str


RenderOp = (
    MoveCursor | PutCell | PutCells | ClearLine | ClearScreen | ShowCursor | SetCursorStyle | HideCursor | SetTitle
)

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class TerminalBackend(Protocol):  # cm:f8e1c2
    """
    Everything the editor needs from the terminal.
    One implementation per backend; the rest of the editor is backend-agnostic.
    """

    def read_events(self) -> AsyncIterator[InputEvent]:
        """Async stream of input events. Never blocks the event loop."""
        ...

    def write(self, ops: list[RenderOp]) -> None:
        """Apply a list of render operations. Backend batches into minimal writes."""
        ...

    def flush(self) -> None:
        """Flush buffered output to the terminal."""
        ...

    def has_pending_output(self) -> bool:
        """Return True if there is buffered output waiting to be flushed."""
        ...

    def get_size(self) -> tuple[int, int]:
        """Returns (cols, rows)."""
        ...

    def enter_raw_mode(self) -> None: ...
    def exit_raw_mode(self) -> None: ...
    def set_mouse_enabled(self, enabled: bool) -> None: ...

    def supports_kitty_keyboard(self) -> bool: ...
    def supports_kitty_mouse(self) -> bool: ...
    def supports_true_color(self) -> bool: ...
    def supports_sixel(self) -> bool: ...
    def supports_kitty_graphics(self) -> bool: ...
