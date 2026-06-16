"""
ui.terminal_buffer — TerminalBuffer: embedded terminal emulator pane

Wraps a pyte Screen + subprocess. Provides read()/write()/render() for
REPL and terminal workflows. pyte is a required dependency; the class
guards against ImportError and degrades gracefully.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from typing import TYPE_CHECKING

try:
    import pyte

    _PYTE_AVAILABLE = True
except ImportError:
    _PYTE_AVAILABLE = False

if TYPE_CHECKING:
    from peovim.ui.cell_grid import CellGrid

_log = logging.getLogger("peovim.terminal")


def _pyte_color_to_rgb(color_name: str) -> tuple[int, int, int] | None:
    """Convert a pyte color name/number to an RGB tuple."""
    _ANSI_COLORS = {
        "black": (0, 0, 0),
        "red": (170, 0, 0),
        "green": (0, 170, 0),
        "brown": (170, 85, 0),
        "blue": (0, 0, 170),
        "magenta": (170, 0, 170),
        "cyan": (0, 170, 170),
        "white": (170, 170, 170),
        "brightblack": (85, 85, 85),
        "brightred": (255, 85, 85),
        "brightgreen": (85, 255, 85),
        "brightyellow": (255, 255, 85),
        "brightblue": (85, 85, 255),
        "brightmagenta": (255, 85, 255),
        "brightcyan": (85, 255, 255),
        "brightwhite": (255, 255, 255),
    }
    if not color_name or color_name == "default":
        return None
    lower = color_name.lower()
    if lower in _ANSI_COLORS:
        return _ANSI_COLORS[lower]
    # Try hex
    try:
        if lower.startswith("#"):
            h = lower[1:]
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        pass
    return None


class TerminalBuffer:
    """
    An embedded terminal emulator backed by pyte.

    Lifecycle:
      tb = TerminalBuffer("repl", rows=24, cols=80)
      await tb.open(["python", "-i"])
      tb.write("print('hello')\\n")
      text = tb.read()
      grid = tb.render()
      tb.close()
    """

    def __init__(self, name: str, rows: int = 24, cols: int = 80) -> None:
        self._name = name
        self._rows = rows
        self._cols = cols
        self._is_open = False
        self._process: asyncio.subprocess.Process | None = None
        self._output_buf: str = ""  # fallback when pyte not available

        if _PYTE_AVAILABLE:
            self._screen: pyte.Screen = pyte.Screen(cols, rows)
            self._stream: pyte.ByteStream = pyte.ByteStream(self._screen)
        else:
            self._screen = None
            self._stream = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self, cmd: list[str] | None = None) -> None:
        """Launch a subprocess and set is_open=True."""
        if cmd is None:
            cmd = [sys.executable, "-i"]
        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._is_open = True
            # Start reading output in background
            asyncio.get_event_loop().create_task(self._read_output())
        except OSError as exc:
            _log.warning("Failed to open terminal %s: %s", self._name, exc)
            raise

    async def _read_output(self) -> None:
        """Background task: feed subprocess stdout into pyte screen."""
        if self._process is None or self._process.stdout is None:
            return
        try:
            while True:
                data = await self._process.stdout.read(1024)
                if not data:
                    break
                self.feed(data)
        except Exception:
            pass
        self._is_open = False

    def write(self, text: str) -> None:
        """Send text to the subprocess stdin."""
        if not self._is_open or self._process is None or self._process.stdin is None:
            return
        encoded = text.encode("utf-8", errors="replace")
        self._process.stdin.write(encoded)
        # Also echo to pyte so the input appears on screen
        if _PYTE_AVAILABLE and self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.feed(encoded)
        else:
            self._output_buf += text

    def feed(self, data: bytes) -> None:
        """Feed raw bytes to the pyte screen (or fallback buffer)."""
        if _PYTE_AVAILABLE and self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.feed(data)
        else:
            self._output_buf += data.decode("utf-8", errors="replace")

    def read(self) -> str:
        """Return current screen content as newline-joined rows."""
        if _PYTE_AVAILABLE and self._screen is not None:
            rows = []
            for y in range(self._rows):
                row = self._screen.buffer[y]
                line = "".join(row[x].data for x in range(self._cols))
                rows.append(line.rstrip())
            return "\n".join(rows)
        return self._output_buf

    def resize(self, rows: int, cols: int) -> None:
        """Update terminal dimensions."""
        self._rows = rows
        self._cols = cols
        if _PYTE_AVAILABLE and self._screen is not None:
            self._screen.resize(rows, cols)
            self._stream = pyte.ByteStream(self._screen)

    def close(self) -> None:
        """Terminate subprocess and mark closed."""
        self._is_open = False
        if self._process is not None:
            with contextlib.suppress(Exception):
                self._process.terminate()
            self._process = None

    def render(self) -> CellGrid:
        """Convert the pyte screen to a CellGrid."""
        from peovim.ui.backend import ATTR_BOLD
        from peovim.ui.cell_grid import CellGrid

        grid = CellGrid(self._cols, self._rows)

        if _PYTE_AVAILABLE and self._screen is not None:
            for y in range(self._rows):
                row = self._screen.buffer[y]
                for x in range(self._cols):
                    char = row[x]
                    ch = char.data if char.data else " "
                    fg = _pyte_color_to_rgb(char.fg) if char.fg != "default" else None
                    bg = _pyte_color_to_rgb(char.bg) if char.bg != "default" else None
                    attrs = ATTR_BOLD if char.bold else 0
                    grid.write(y, x, ch, fg=fg, bg=bg, attrs=attrs)
        else:
            # Fallback: write buffered text
            lines = self._output_buf.split("\n")
            for row_idx in range(self._rows):
                if row_idx < len(lines):
                    line = lines[row_idx][: self._cols].ljust(self._cols)
                    grid.write_str(row_idx, 0, line)

        return grid

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def name(self) -> str:
        return self._name
