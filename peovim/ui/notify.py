"""
ui.notify — NotifyManager: toast notifications

Shows timed notification toasts in the top-right corner of the terminal.
Multiple notifications stack vertically. Each expires after its timeout.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from peovim.ui.cell_grid import CellGrid
from peovim.ui.float_manager import draw_border

# ---------------------------------------------------------------------------
# Level colours
# ---------------------------------------------------------------------------

_LEVEL_FG: dict[str, tuple] = {
    "info": (180, 180, 220),
    "warn": (255, 200, 60),
    "error": (255, 80, 80),
    "debug": (80, 200, 200),
}
_NOTIFY_BG: tuple = (35, 35, 50)
_BORDER_FG: tuple = (90, 90, 130)
_NOTIFY_WIDTH = 42  # chars (including border)
_NOTIFY_MAX_STACK = 5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Notification:
    message: str
    level: str = "info"
    title: str = ""
    timeout: float = 3.0  # seconds; 0 = persistent
    created_at: float = field(default_factory=time.monotonic)
    _cached_wrap_width: int | None = field(default=None, init=False, repr=False)
    _cached_message_lines: tuple[str, ...] | None = field(default=None, init=False, repr=False)
    _cached_height: int | None = field(default=None, init=False, repr=False)

    def message_lines(self, width: int) -> tuple[str, ...]:
        if width == self._cached_wrap_width and self._cached_message_lines is not None:
            return self._cached_message_lines
        lines = tuple(NotifyManager._message_lines(self.message, width))
        self._cached_wrap_width = width
        self._cached_message_lines = lines
        self._cached_height = 2 + (1 if self.title else 0) + len(lines)
        return lines

    def height(self, width: int) -> int:
        if width == self._cached_wrap_width and self._cached_height is not None:
            return self._cached_height
        self.message_lines(width)
        return self._cached_height or 2 + (1 if self.title else 0)


class NotifyHandle:
    """Returned by notify(); lets caller dismiss early."""

    def __init__(self, notification: Notification, manager: NotifyManager) -> None:
        self._notif = notification
        self._manager = manager

    def dismiss(self) -> None:
        self._manager._dismiss(self._notif)


# ---------------------------------------------------------------------------
# NotifyManager
# ---------------------------------------------------------------------------


class NotifyManager:
    """Queues and renders toast notifications in the top-right corner."""

    def __init__(self) -> None:
        self._queue: list[Notification] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify(self, message: str, level: str = "info", title: str = "", timeout: float = 3.0) -> NotifyHandle:
        n = Notification(message=message, level=level, title=title, timeout=timeout)
        self._queue.append(n)
        if len(self._queue) > _NOTIFY_MAX_STACK:
            self._queue.pop(0)
        return NotifyHandle(n, self)

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render(self, grid: CellGrid) -> None:
        """Composite active notifications on the grid. Expires timed-out ones."""
        now = time.monotonic()
        # Prune expired
        self._queue = [n for n in self._queue if n.timeout == 0 or (now - n.created_at) < n.timeout]
        # Render from top, newest last (bottom of stack)
        y = 0
        for notif in self._queue[:_NOTIFY_MAX_STACK]:
            h = self._notif_height(notif)
            w = _NOTIFY_WIDTH
            x = max(0, grid.width - w)
            if y + h > grid.height:
                break
            self._render_notif(grid, notif, x, y, w, h)
            y += h

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _dismiss(self, notif: Notification) -> None:
        if notif in self._queue:
            self._queue.remove(notif)

    @staticmethod
    def _notif_height(notif: Notification) -> int:
        inner_w = _NOTIFY_WIDTH - 2
        return notif.height(inner_w)

    @staticmethod
    def _message_lines(message: str, width: int) -> list[str]:
        if width <= 0:
            return [""]
        raw_lines = message.splitlines() or [""]
        lines: list[str] = []
        for raw_line in raw_lines:
            if raw_line == "":
                lines.append("")
                continue
            remaining = raw_line
            while remaining:
                lines.append(remaining[:width])
                remaining = remaining[width:]
        return lines or [""]

    def _render_notif(self, grid: CellGrid, notif: Notification, x: int, y: int, w: int, h: int) -> None:
        fg = _LEVEL_FG.get(notif.level, _LEVEL_FG["info"])
        # Draw border with level as title
        border_title = notif.title or notif.level.upper()
        draw_border(grid, x, y, w, h, border_title, fg=_BORDER_FG, bg=_NOTIFY_BG)

        inner_x = x + 1
        inner_w = w - 2

        row = y + 1
        if notif.title:
            # Level badge on first inner row
            badge = f" {notif.level.upper()} "[:inner_w].ljust(inner_w)
            grid.write_str(row, inner_x, badge, fg=fg, bg=_NOTIFY_BG)
            row += 1

        for message_line in notif.message_lines(inner_w):
            if row >= y + h - 1:
                break
            msg = message_line[:inner_w].ljust(inner_w)
            grid.write_str(row, inner_x, msg, fg=fg, bg=_NOTIFY_BG)
            row += 1
