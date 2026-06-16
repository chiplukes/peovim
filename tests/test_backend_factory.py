import pytest

from peovim.ui.backend import HideCursor, KeyEvent, MouseEvent, ShowCursor, TerminalBackend
from peovim.ui.backend_factory import create_backend
from peovim.ui.backends.crossterm import CrosstermBackend
from peovim.ui.backends.headless import HeadlessBackend


class StubBackend:
    def __init__(self, name: str) -> None:
        self.name = name
        self._events: list[object] = []

    async def read_events(self):
        for event in self._events:
            yield event

    def write(self, ops):
        return None

    def flush(self) -> None:
        return None

    def has_pending_output(self) -> bool:
        return False

    def get_size(self) -> tuple[int, int]:
        return (80, 24)

    def enter_raw_mode(self) -> None:
        return None

    def exit_raw_mode(self) -> None:
        return None

    def set_mouse_enabled(self, enabled: bool) -> None:
        return None

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


def test_create_backend_uses_headless_backend() -> None:
    backend = create_backend("headless")

    assert isinstance(backend, HeadlessBackend)
    assert isinstance(backend, TerminalBackend)


@pytest.mark.parametrize("name", ["pt", "prompt-toolkit", "prompt_toolkit"])
def test_create_backend_accepts_prompt_toolkit_aliases(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    import peovim.ui.backend_factory as backend_factory

    prompt_backend = StubBackend("prompt")
    monkeypatch.setattr(backend_factory, "_create_prompt_toolkit_backend", lambda: prompt_backend)

    backend = create_backend(name)

    assert backend is prompt_backend


def test_create_backend_uses_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ED_BACKEND", "headless")

    backend = create_backend()

    assert isinstance(backend, HeadlessBackend)


def test_create_backend_requested_value_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import peovim.ui.backend_factory as backend_factory

    prompt_backend = StubBackend("prompt")
    monkeypatch.setenv("ED_BACKEND", "headless")
    monkeypatch.setattr(backend_factory, "_create_prompt_toolkit_backend", lambda: prompt_backend)

    backend = create_backend("prompt_toolkit")

    assert backend is prompt_backend


def test_create_backend_falls_back_for_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import peovim.ui.backend_factory as backend_factory

    prompt_backend = StubBackend("prompt")
    monkeypatch.setattr(backend_factory, "_create_prompt_toolkit_backend", lambda: prompt_backend)

    backend = create_backend("unknown")

    assert backend is prompt_backend


def test_create_backend_falls_back_when_crossterm_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import peovim.ui.backend_factory as backend_factory

    prompt_backend = StubBackend("prompt")
    monkeypatch.setattr(backend_factory, "_create_prompt_toolkit_backend", lambda: prompt_backend)
    monkeypatch.setattr(backend_factory, "_create_crossterm_backend", lambda: None)

    backend = create_backend("crossterm")

    assert backend is prompt_backend


def test_create_backend_uses_crossterm_backend_when_provider_available(monkeypatch: pytest.MonkeyPatch) -> None:
    import peovim.ui.backends.crossterm as crossterm_module

    class Provider:
        capabilities = {"kitty_keyboard": True, "kitty_mouse": True}

        def read_event(self):
            return None

        def write_ops(self, ops):
            return None

        def flush(self) -> None:
            return None

        def get_size(self) -> tuple[int, int]:
            return (120, 40)

        def enter_raw_mode(self) -> None:
            return None

        def exit_raw_mode(self) -> None:
            return None

        def set_mouse_enabled(self, enabled: bool) -> None:
            return None

    monkeypatch.setattr(
        crossterm_module.importlib,
        "import_module",
        lambda name: type("Module", (), {"create_backend": staticmethod(lambda: Provider())})(),
    )

    backend = create_backend("crossterm")

    assert isinstance(backend, CrosstermBackend)
    assert isinstance(backend, TerminalBackend)


def test_create_backend_falls_back_when_crossterm_provider_import_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import peovim.ui.backend_factory as backend_factory
    import peovim.ui.backends.crossterm as crossterm_module

    prompt_backend = StubBackend("prompt")

    def raise_import_error(_name: str):
        raise ImportError("missing provider")

    monkeypatch.setattr(backend_factory, "_create_prompt_toolkit_backend", lambda: prompt_backend)
    monkeypatch.setattr(crossterm_module.importlib, "import_module", raise_import_error)

    backend = create_backend("crossterm")

    assert backend is prompt_backend


@pytest.mark.asyncio
async def test_headless_backend_reads_key_and_mouse_events() -> None:
    backend = HeadlessBackend()
    backend.feed_key("x")
    backend.feed_mouse(3, 4, button=1, pressed=False)

    events = backend.read_events()

    assert await anext(events) == KeyEvent("x")
    assert await anext(events) == MouseEvent(row=3, col=4, button=1, pressed=False)


def test_headless_backend_tracks_write_and_size_state() -> None:
    backend = HeadlessBackend(cols=120, rows=40)

    backend.write([HideCursor(), ShowCursor()])

    assert backend.get_size() == (120, 40)
    assert backend.cursor_visible() is True
    assert backend.last_ops(2) == [HideCursor(), ShowCursor()]


def test_headless_backend_lifecycle_and_capabilities() -> None:
    backend = HeadlessBackend()

    backend.enter_raw_mode()
    backend.set_mouse_enabled(True)

    assert backend._in_raw_mode is True
    assert backend._mouse_enabled is True
    assert backend.has_pending_output() is False
    assert backend.supports_true_color() is True
    assert backend.supports_kitty_keyboard() is False
    assert backend.supports_kitty_mouse() is False
    assert backend.supports_sixel() is False
    assert backend.supports_kitty_graphics() is False

    backend.exit_raw_mode()

    assert backend._in_raw_mode is False
