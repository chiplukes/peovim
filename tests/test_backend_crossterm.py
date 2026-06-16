from __future__ import annotations

import pytest

from peovim.ui.backend import HideCursor, KeyEvent, MouseEvent, MoveCursor, PutCells, SetTitle
from peovim.ui.backends.crossterm import CrosstermBackend


class FakeProvider:
    def __init__(self) -> None:
        self.capabilities = {
            "kitty_keyboard": True,
            "kitty_mouse": True,
            "true_color": True,
            "sixel": False,
            "kitty_graphics": False,
        }
        self.events: list[object] = [
            {"type": "key", "key": "<C-m>", "ctrl": True},
            {"type": "mouse", "row": 3, "col": 4, "button": 1, "pressed": False, "alt": True},
        ]
        self.written_batches: list[list[dict[str, object]]] = []
        self.flush_calls = 0
        self.raw_mode_entered = False
        self.raw_mode_exited = False
        self.mouse_enabled: list[bool] = []

    def read_event(self):
        if not self.events:
            return None
        return self.events.pop(0)

    def write_ops(self, ops) -> None:
        self.written_batches.append(list(ops))

    def flush(self) -> None:
        self.flush_calls += 1

    def get_size(self) -> tuple[int, int]:
        return (132, 43)

    def enter_raw_mode(self) -> None:
        self.raw_mode_entered = True

    def exit_raw_mode(self) -> None:
        self.raw_mode_exited = True

    def set_mouse_enabled(self, enabled: bool) -> None:
        self.mouse_enabled.append(enabled)


@pytest.mark.asyncio
async def test_crossterm_backend_reads_provider_events() -> None:
    backend = CrosstermBackend(provider=FakeProvider())
    events = backend.read_events()

    assert await anext(events) == KeyEvent("<C-m>", ctrl=True)
    assert await anext(events) == MouseEvent(row=3, col=4, button=1, pressed=False, alt=True)


def test_crossterm_backend_buffers_flushes_and_reports_capabilities() -> None:
    provider = FakeProvider()
    backend = CrosstermBackend(provider=provider)

    backend.write([MoveCursor(1, 2), PutCells("hi"), HideCursor(), SetTitle("peovim")])

    assert backend.has_pending_output() is True

    backend.flush()

    assert backend.has_pending_output() is False
    assert provider.flush_calls == 1
    assert provider.written_batches == [
        [
            {"type": "move_cursor", "row": 1, "col": 2},
            {"type": "put_cells", "text": "hi", "fg": None, "bg": None, "attrs": 0},
            {"type": "hide_cursor"},
            {"type": "set_title", "text": "peovim"},
        ]
    ]
    assert backend.get_size() == (132, 43)
    assert backend.supports_kitty_keyboard() is True
    assert backend.supports_kitty_mouse() is True
    assert backend.supports_true_color() is True
    assert backend.supports_sixel() is False
    assert backend.supports_kitty_graphics() is False


def test_crossterm_backend_delegates_lifecycle() -> None:
    provider = FakeProvider()
    backend = CrosstermBackend(provider=provider)

    backend.enter_raw_mode()
    backend.set_mouse_enabled(True)
    backend.exit_raw_mode()

    assert provider.raw_mode_entered is True
    assert provider.mouse_enabled == [True]
    assert provider.raw_mode_exited is True
