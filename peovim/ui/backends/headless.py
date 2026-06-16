"""
ui.backends.headless — HeadlessBackend (testing)

Accepts a queue of pre-programmed KeyEvent/MouseEvent as input.
Captures list[RenderOp] for assertions. Configurable terminal size.
Used by ALL tests — no real terminal required anywhere in the test suite.

See notes/architecture.md §HeadlessBackend for usage example.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator

from peovim.ui.backend import (
    HideCursor,
    InputEvent,
    KeyEvent,
    MouseEvent,
    MoveCursor,
    RenderOp,
    SetCursorStyle,
    ShowCursor,
)


class HeadlessBackend:
    """
    In-memory backend for tests.

    Feed events with feed_key() / feed_mouse(). Inspect render output with
    render_ops() or last_ops(). The editor drives the same code paths as
    with a real terminal.
    """

    def __init__(self, cols: int = 80, rows: int = 24) -> None:
        self._cols = cols
        self._rows = rows
        self._input_queue: deque[InputEvent] = deque()
        self._ops: list[RenderOp] = []
        self._raw_bytes: bytearray = bytearray()
        self._exit_event: asyncio.Event | None = None
        self._cursor_visible: bool = True
        self._cursor_style: tuple[str, bool] = ("block", False)
        self._cursor_pos: tuple[int, int] = (0, 0)
        self._in_raw_mode: bool = False
        self._mouse_enabled: bool = False

    # ------------------------------------------------------------------
    # Test helpers — feed input
    # ------------------------------------------------------------------

    def feed_key(self, key: str, *, ctrl: bool = False, shift: bool = False, alt: bool = False) -> None:
        """Queue a single KeyEvent."""
        self._input_queue.append(KeyEvent(key=key, ctrl=ctrl, shift=shift, alt=alt))

    def feed_keys(self, sequence: str) -> None:
        """
        Queue a sequence of characters as individual KeyEvents.
        Special keys in angle brackets: <Esc>, <CR>, <Tab>, <BS>, <Up>, etc.
        Example: 'iHello<Esc>' → insert mode, type Hello, press Esc.
        """
        i = 0
        while i < len(sequence):
            if sequence[i] == "<":
                end = sequence.find(">", i)
                if end != -1:
                    key_name = sequence[i : end + 1]
                    self._input_queue.append(KeyEvent(key=key_name))
                    i = end + 1
                    continue
            self._input_queue.append(KeyEvent(key=sequence[i]))
            i += 1

    def feed_mouse(self, row: int, col: int, button: int = 0, pressed: bool = True) -> None:
        self._input_queue.append(MouseEvent(row=row, col=col, button=button, pressed=pressed))

    # ------------------------------------------------------------------
    # Test helpers — inspect output
    # ------------------------------------------------------------------

    def render_ops(self) -> list[RenderOp]:
        """All render ops written since creation (or last clear_ops)."""
        return list(self._ops)

    def last_ops(self, n: int = 1) -> list[RenderOp]:
        """The last n render ops."""
        return self._ops[-n:]

    def clear_ops(self) -> None:
        self._ops.clear()
        self._raw_bytes.clear()

    def raw_bytes(self) -> bytes:
        """All raw ANSI bytes written via write_raw since creation (or last clear_ops)."""
        return bytes(self._raw_bytes)

    def cursor_visible(self) -> bool:
        return self._cursor_visible

    def cursor_style(self) -> tuple[str, bool]:
        return self._cursor_style

    def cursor_pos(self) -> tuple[int, int]:
        return self._cursor_pos

    # ------------------------------------------------------------------
    # TerminalBackend Protocol
    # ------------------------------------------------------------------

    async def read_events(self) -> AsyncIterator[InputEvent]:
        while True:
            if self._input_queue:
                yield self._input_queue.popleft()
            else:
                # Yield control without blocking so tests can drive the loop
                await asyncio.sleep(0)

    def write_raw(self, data: bytes | bytearray) -> None:
        """Accept pre-encoded ANSI bytes (from CellGrid.flush_ansi)."""
        self._raw_bytes.extend(data)

    def write(self, ops: list[RenderOp]) -> None:
        for op in ops:
            if isinstance(op, MoveCursor):
                self._cursor_pos = (op.row, op.col)
            elif isinstance(op, ShowCursor):
                self._cursor_visible = True
            elif isinstance(op, SetCursorStyle):
                self._cursor_style = (op.shape, op.blink)
            elif isinstance(op, HideCursor):
                self._cursor_visible = False
        self._ops.extend(ops)

    def has_pending_output(self) -> bool:
        return False

    def flush(self) -> None:
        pass  # no-op for headless

    def get_size(self) -> tuple[int, int]:
        return (self._cols, self._rows)

    def enter_raw_mode(self) -> None:
        self._in_raw_mode = True

    def exit_raw_mode(self) -> None:
        self._in_raw_mode = False

    def set_mouse_enabled(self, enabled: bool) -> None:
        self._mouse_enabled = enabled

    def supports_kitty_keyboard(self) -> bool:
        return False

    def supports_kitty_mouse(self) -> bool:
        return False

    def supports_true_color(self) -> bool:
        return True

    def supports_sixel(self) -> bool:
        return False

    def supports_kitty_graphics(self) -> bool:
        return False
