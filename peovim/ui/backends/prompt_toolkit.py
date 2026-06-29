"""
ui.backends.prompt_toolkit — PromptToolkitBackend (default)

Wraps prompt_toolkit.input.create_input() for keyboard reading.
Writes ANSI escape sequences directly to sys.stdout for rendering.
Supports true color (RGB). No Kitty protocol extensions.

Zero extra build steps. Ships as the default backend.
"""

from __future__ import annotations

import asyncio
import contextlib
import locale
import logging
import os
import re
import sys
import threading
from collections.abc import AsyncIterator

from prompt_toolkit.input import create_input

from peovim.ui.backend import (
    ATTR_BLINK,
    ATTR_BOLD,
    ATTR_DIM,
    ATTR_ITALIC,
    ATTR_REVERSE,
    ATTR_STRIKETHROUGH,
    ATTR_UNDERLINE,
    Color,
    HideCursor,
    InputEvent,
    KeyEvent,
    MouseEvent,
    MoveCursor,
    PutCell,
    PutCells,
    RenderOp,
    SetCursorStyle,
    SetTitle,
    ShowCursor,
)

log = logging.getLogger(__name__)

# SGR mouse: \x1b[<button;col;row{M|m}
_SGR_MOUSE_RE = re.compile(r"<(\d+);(\d+);(\d+)([Mm])")
_BRACKETED_PASTE_ENABLE = "\x1b[?2004h"
_BRACKETED_PASTE_DISABLE = "\x1b[?2004l"

# ---------------------------------------------------------------------------
# Mouse event parsing
# ---------------------------------------------------------------------------


def _parse_mouse_event(data: str) -> MouseEvent | None:
    """Parse a prompt_toolkit mouse event data string into our MouseEvent.

    Handles SGR format: '<button;col;row{M|m}'
    button bits: 0-1=button(0=left,1=mid,2=right), bit6=scroll(64=up,65=down)
    """
    m = _SGR_MOUSE_RE.search(data)
    if not m:
        return None
    code = int(m.group(1))
    col = int(m.group(2)) - 1  # SGR is 1-indexed
    row = int(m.group(3)) - 1
    pressed = m.group(4) == "M"

    shift = bool(code & 4)
    alt = bool(code & 8)
    ctrl = bool(code & 16)

    dragging = bool(code & 32) and not bool(code & 64)

    if code & 64:
        # Scroll event: 64=up, 65=down
        button = 3 if (code & 1) == 0 else 4
    else:
        raw = code & 3
        button = raw if raw < 3 else 0  # clamp motion (3) to left

    return MouseEvent(
        row=row, col=col, button=button, pressed=pressed, dragging=dragging, shift=shift, ctrl=ctrl, alt=alt
    )


# ---------------------------------------------------------------------------
# Key translation: prompt_toolkit key name → our key name
# ---------------------------------------------------------------------------

_PT_KEY_MAP: dict[str, str] = {
    "c-a": "<C-a>",
    "c-b": "<C-b>",
    "c-c": "<C-c>",
    "c-d": "<C-d>",
    "c-e": "<C-e>",
    "c-f": "<C-f>",
    "c-g": "<C-g>",
    "c-h": "<BS>",
    "c-i": "<Tab>",
    "c-j": "<CR>",
    "c-k": "<C-k>",
    "c-l": "<C-l>",
    "c-m": "<CR>",
    "c-n": "<C-n>",
    "c-o": "<C-o>",
    "c-p": "<C-p>",
    "c-q": "<C-q>",
    "c-r": "<C-r>",
    "c-s": "<C-s>",
    "c-t": "<C-t>",
    "c-u": "<C-u>",
    "c-v": "<C-v>",
    "c-w": "<C-w>",
    "c-x": "<C-x>",
    "c-y": "<C-y>",
    "c-z": "<C-z>",
    "enter": "<CR>",
    "escape": "<Esc>",
    "backspace": "<BS>",
    "delete": "<Del>",
    "up": "<Up>",
    "down": "<Down>",
    "left": "<Left>",
    "right": "<Right>",
    "home": "<Home>",
    "end": "<End>",
    "pageup": "<PageUp>",
    "pagedown": "<PageDown>",
    "tab": "<Tab>",
    "s-tab": "<S-Tab>",
    "f1": "<F1>",
    "f2": "<F2>",
    "f3": "<F3>",
    "f4": "<F4>",
    "f5": "<F5>",
    "f6": "<F6>",
    "f7": "<F7>",
    "f8": "<F8>",
    "f9": "<F9>",
    "f10": "<F10>",
    "f11": "<F11>",
    "f12": "<F12>",
}
# Note: Alt/Meta keys (m-a..m-z) are NOT in this map because prompt_toolkit never
# generates them. Alt+x in VT100 terminals = ESC + x, which arrives as two separate
# key events. They are combined into <A-x> in _on_readable() directly.


def _translate_key(pt_key: str) -> str | None:
    """Translate a prompt_toolkit key name to our key name. Returns None to ignore."""
    lower = pt_key.lower()
    if lower in _PT_KEY_MAP:
        return _PT_KEY_MAP[lower]
    # Raw control characters (VT100 terminals send these for common keys)
    _RAW = {"\r": "<CR>", "\n": "<CR>", "\x7f": "<BS>", "\x08": "<BS>", "\x1b": "<Esc>", "\t": "<Tab>"}
    if pt_key in _RAW:
        return _RAW[pt_key]
    # Single printable character — pass through as-is
    if len(pt_key) == 1 and pt_key.isprintable():
        return pt_key
    return None


# ---------------------------------------------------------------------------
# ANSI escape helpers
# ---------------------------------------------------------------------------


def _ansi_fg(color: Color) -> str:
    if color is None:
        return "\x1b[39m"
    r, g, b = color
    return f"\x1b[38;2;{r};{g};{b}m"


def _ansi_bg(color: Color) -> str:
    if color is None:
        return "\x1b[49m"
    r, g, b = color
    return f"\x1b[48;2;{r};{g};{b}m"


def _ansi_attrs(attrs: int) -> str:
    codes: list[str] = []
    if attrs & ATTR_BOLD:
        codes.append("1")
    if attrs & ATTR_DIM:
        codes.append("2")
    if attrs & ATTR_ITALIC:
        codes.append("3")
    if attrs & ATTR_UNDERLINE:
        codes.append("4")
    if attrs & ATTR_BLINK:
        codes.append("5")
    if attrs & ATTR_REVERSE:
        codes.append("7")
    if attrs & ATTR_STRIKETHROUGH:
        codes.append("9")
    return f"\x1b[{';'.join(codes)}m" if codes else ""


def _put_style(buf: list[str], fg: Color, bg: Color, attrs: int) -> None:
    buf.append("\x1b[0m")
    if fg is not None:
        buf.append(_ansi_fg(fg))
    if bg is not None:
        buf.append(_ansi_bg(bg))
    if attrs:
        buf.append(_ansi_attrs(attrs))


def _ansi_cursor_style(shape: str, blink: bool) -> str:
    if shape == "bar":
        return "\x1b[5 q" if blink else "\x1b[6 q"
    return "\x1b[1 q" if blink else "\x1b[2 q"


# ---------------------------------------------------------------------------
# PromptToolkitBackend
# ---------------------------------------------------------------------------


class PromptToolkitBackend:
    """
    Terminal backend using prompt_toolkit for input and raw ANSI for output.
    Works on Windows (Win32 input) and Linux/macOS (VT100 input).
    """

    def __init__(self) -> None:
        self._pt_input = create_input()
        self._raw_mode_ctx: object | None = None
        self._buf: list[str] = []
        self._raw_buf: bytearray = bytearray()
        self._stdout_lock = threading.Lock()  # serialise concurrent flush threads
        self._encoding_warning_emitted = False

    # --- Capabilities ---

    def supports_true_color(self) -> bool:
        return True

    def supports_kitty_keyboard(self) -> bool:
        return False

    def supports_kitty_mouse(self) -> bool:
        return False

    def supports_sixel(self) -> bool:
        return False

    def supports_kitty_graphics(self) -> bool:
        return False

    # --- Lifecycle ---

    def enter_raw_mode(self) -> None:
        self._raw_mode_ctx = self._pt_input.raw_mode()
        self._raw_mode_ctx.__enter__()  # type: ignore[union-attr]
        sys.stdout.write("\x1b[2J\x1b[H\x1b[?25l")  # clear screen + hide cursor
        self.set_mouse_enabled(True)
        sys.stdout.write(_BRACKETED_PASTE_ENABLE)
        sys.stdout.flush()

    def exit_raw_mode(self) -> None:
        self.set_mouse_enabled(False)
        sys.stdout.write(_BRACKETED_PASTE_DISABLE)
        sys.stdout.write("\x1b[2 q\x1b[0m\x1b[?25h\x1b[2J\x1b[H")  # reset style + show cursor + clear
        sys.stdout.flush()
        if self._raw_mode_ctx is not None:
            with contextlib.suppress(Exception):
                self._raw_mode_ctx.__exit__(None, None, None)  # type: ignore[attr-defined]
            self._raw_mode_ctx = None

    def set_mouse_enabled(self, enabled: bool) -> None:
        if enabled:
            # Enable basic mouse + button-motion tracking + SGR extended coordinates
            sys.stdout.write("\x1b[?1000h\x1b[?1002h\x1b[?1006h")
        else:
            sys.stdout.write("\x1b[?1006l\x1b[?1002l\x1b[?1000l")

    # --- Size ---

    def get_size(self) -> tuple[int, int]:
        try:
            size = os.get_terminal_size()
            return size.columns, size.lines
        except OSError:
            return 80, 24

    # --- Input ---

    async def read_events(self) -> AsyncIterator[InputEvent]:
        queue: asyncio.Queue[InputEvent] = asyncio.Queue()

        def _drain_input(*, flush_pending: bool = False) -> None:
            with contextlib.suppress(Exception):
                # Read all keys at once so we can detect ESC+letter = Alt combos.
                # In VT100 terminals Alt+x is sent as \x1b followed by x. Both bytes
                # arrive in the same read() call, so they appear together in read_keys().
                # A bare Escape press arrives alone (the parser flushes it after timeout).
                if flush_pending and hasattr(self._pt_input, "flush_keys"):
                    raw = list(self._pt_input.flush_keys())
                else:
                    raw = list(self._pt_input.read_keys())
                i = 0
                while i < len(raw):
                    key_press = raw[i]
                    # Use str() only for mouse detection; str enums give enum name not value
                    if key_press.key == "Keys.BracketedPaste" or key_press.key == "<bracketed-paste>":
                        queue.put_nowait(KeyEvent("<BracketedPaste>", text=str(key_press.data or "")))
                        i += 1
                        continue
                    if "mouse" in str(key_press.key).lower():
                        mouse_ev = _parse_mouse_event(str(key_press.data))
                        if mouse_ev is not None:
                            queue.put_nowait(mouse_ev)
                        i += 1
                        continue
                    # Detect ESC + printable letter in the same batch → Alt key
                    key_val = key_press.key  # str or Keys enum (both support .lower())
                    if key_val.lower() == "escape" and i + 1 < len(raw):
                        next_kp = raw[i + 1]
                        next_val = next_kp.key
                        if len(next_val) == 1 and next_val.isprintable():
                            queue.put_nowait(KeyEvent(f"<A-{next_val}>"))
                            i += 2
                            continue
                    # Pass key directly — .lower() on a str enum returns the value,
                    # but str() returns 'Keys.ControlM' on Python < 3.12 (breaks lookup)
                    translated = _translate_key(key_press.key)
                    if translated is not None:
                        queue.put_nowait(KeyEvent(translated))
                    i += 1

        def _on_readable() -> None:
            _drain_input()

        # Use attach() — works on both Win32 and VT100 backends
        with self._pt_input.attach(_on_readable):
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.01)
                except TimeoutError:
                    # Prompt_toolkit may keep a lone Escape pending until its internal
                    # timeout expires. Polling flush_keys() lets the parser release that
                    # pending key even when no further OS readability event arrives.
                    _drain_input(flush_pending=True)
                    continue
                yield event

    # --- Output ---

    def write_raw(self, data: bytes | bytearray) -> None:
        """Accept pre-encoded ANSI bytes (from CellGrid.flush_ansi) for output."""
        self._raw_buf.extend(data)

    def write(self, ops: list[RenderOp]) -> None:
        buf = self._buf
        for op in ops:
            if isinstance(op, MoveCursor):
                buf.append(f"\x1b[{op.row + 1};{op.col + 1}H")
            elif isinstance(op, PutCells):
                _put_style(buf, op.fg, op.bg, op.attrs)
                buf.append(op.text)
            elif isinstance(op, PutCell):
                _put_style(buf, op.fg, op.bg, op.attrs)
                buf.append(op.char)
            elif isinstance(op, ShowCursor):
                buf.append("\x1b[?25h")
            elif isinstance(op, SetCursorStyle):
                buf.append(_ansi_cursor_style(op.shape, op.blink))
            elif isinstance(op, HideCursor):
                buf.append("\x1b[?25l")
            elif isinstance(op, SetTitle):
                buf.append(f"\x1b]0;{op.text}\x07")

    def has_pending_output(self) -> bool:
        return bool(self._buf) or bool(self._raw_buf)

    def flush(self) -> None:
        # Write raw ANSI bytes first (grid content), then string ops (cursor etc.)
        # to maintain the emission order from the render cycle.
        had_output = bool(self._raw_buf) or bool(self._buf)
        if self._raw_buf:
            raw = bytes(self._raw_buf)
            self._raw_buf.clear()
            stdout_buffer = getattr(sys.stdout, "buffer", None)
            if stdout_buffer is not None:
                try:
                    stdout_buffer.write(raw)
                except Exception:
                    self._write_stdout(raw.decode("utf-8", errors="replace"))
            else:
                self._write_stdout(raw.decode("utf-8", errors="replace"))
        if self._buf:
            rendered = "".join(self._buf)
            self._write_stdout(rendered)
            self._buf.clear()
        if had_output:
            sys.stdout.flush()

    def _write_stdout(self, rendered: str) -> None:
        try:
            sys.stdout.write(rendered)
            return
        except UnicodeEncodeError:
            self._write_with_encoding_fallback(rendered)

    def _write_with_encoding_fallback(self, rendered: str) -> None:
        encoding = getattr(sys.stdout, "encoding", None) or locale.getpreferredencoding(False) or "utf-8"
        safe_bytes = rendered.encode(encoding, errors="replace")
        stdout_buffer = getattr(sys.stdout, "buffer", None)
        if stdout_buffer is not None:
            stdout_buffer.write(safe_bytes)
        else:
            sys.stdout.write(safe_bytes.decode(encoding, errors="replace"))
        if not self._encoding_warning_emitted:
            log.warning(
                "stdout encoding %r could not represent some UI glyphs; falling back to replacement characters",
                encoding,
            )
            self._encoding_warning_emitted = True
