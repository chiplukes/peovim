"""Optional adapter for an external crossterm-backed provider."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator, Mapping
from typing import Any

from peovim.ui.backend import (
    ClearLine,
    ClearScreen,
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

_PROVIDER_MODULE = "ed_crossterm"

_DEFAULT_CAPABILITIES = {
    "kitty_keyboard": False,
    "kitty_mouse": False,
    "true_color": True,
    "sixel": False,
    "kitty_graphics": False,
}


def _load_provider_module() -> Any:
    return importlib.import_module(_PROVIDER_MODULE)


def _create_provider(module: Any) -> Any:
    create_backend = getattr(module, "create_backend", None)
    if callable(create_backend):
        return create_backend()

    for name in ("Backend", "CrosstermProvider", "CrosstermBackend"):
        provider_type = getattr(module, name, None)
        if provider_type is not None:
            return provider_type()

    raise ImportError(f"{_PROVIDER_MODULE} does not expose a backend provider")


def _normalise_capabilities(raw: object) -> dict[str, bool]:
    capabilities = dict(_DEFAULT_CAPABILITIES)
    if isinstance(raw, Mapping):
        for name in capabilities:
            if name in raw:
                capabilities[name] = bool(raw[name])
    return capabilities


def _mapping_value(payload: object, name: str, default: object = None) -> object:
    if isinstance(payload, Mapping):
        return payload.get(name, default)
    return getattr(payload, name, default)


def _coerce_input_event(payload: object) -> InputEvent:
    if isinstance(payload, KeyEvent | MouseEvent):
        return payload

    event_type = _mapping_value(payload, "type") or _mapping_value(payload, "kind")
    if event_type == "key":
        key = _mapping_value(payload, "key")
        if not isinstance(key, str):
            raise ValueError("crossterm key payload must provide a string key")
        return KeyEvent(
            key=key,
            shift=bool(_mapping_value(payload, "shift", False)),
            ctrl=bool(_mapping_value(payload, "ctrl", False)),
            alt=bool(_mapping_value(payload, "alt", False)),
            meta=bool(_mapping_value(payload, "meta", False)),
        )

    if event_type == "mouse":
        row = _mapping_value(payload, "row")
        col = _mapping_value(payload, "col")
        button = _mapping_value(payload, "button")
        pressed = _mapping_value(payload, "pressed")
        if not all(isinstance(value, int) for value in (row, col, button)) or not isinstance(pressed, bool):
            raise ValueError("crossterm mouse payload must provide row, col, button, and pressed values")
        return MouseEvent(
            row=row,
            col=col,
            button=button,
            pressed=pressed,
            dragging=bool(_mapping_value(payload, "dragging", False)),
            shift=bool(_mapping_value(payload, "shift", False)),
            ctrl=bool(_mapping_value(payload, "ctrl", False)),
            alt=bool(_mapping_value(payload, "alt", False)),
        )

    raise ValueError(f"unsupported crossterm input payload: {payload!r}")


def _encode_render_op(op: RenderOp) -> dict[str, object]:
    if isinstance(op, MoveCursor):
        return {"type": "move_cursor", "row": op.row, "col": op.col}
    if isinstance(op, PutCells):
        return {"type": "put_cells", "text": op.text, "fg": op.fg, "bg": op.bg, "attrs": op.attrs}
    if isinstance(op, PutCell):
        return {"type": "put_cell", "char": op.char, "fg": op.fg, "bg": op.bg, "attrs": op.attrs}
    if isinstance(op, ClearLine):
        return {"type": "clear_line", "row": op.row}
    if isinstance(op, ClearScreen):
        return {"type": "clear_screen"}
    if isinstance(op, ShowCursor):
        return {"type": "show_cursor"}
    if isinstance(op, SetCursorStyle):
        return {"type": "set_cursor_style", "shape": op.shape, "blink": op.blink}
    if isinstance(op, HideCursor):
        return {"type": "hide_cursor"}
    if isinstance(op, SetTitle):
        return {"type": "set_title", "text": op.text}
    raise TypeError(f"unsupported render op: {type(op).__name__}")


class CrosstermBackend:
    """Adapter that keeps the editor decoupled from the optional provider module."""

    def __init__(self, provider: object | None = None) -> None:
        provider_module = None
        if provider is None:
            provider_module = _load_provider_module()
            provider = _create_provider(provider_module)
        self._provider = provider
        self._buf: list[dict[str, object]] = []
        self._capabilities = _normalise_capabilities(self._read_capabilities(provider_module))

    def _read_capabilities(self, provider_module: object | None) -> object:
        provider_caps = getattr(self._provider, "capabilities", None)
        if callable(provider_caps):
            return provider_caps()
        if provider_caps is not None:
            return provider_caps

        if provider_module is not None:
            module_caps = getattr(provider_module, "capabilities", None)
            if callable(module_caps):
                return module_caps()
            if module_caps is not None:
                return module_caps

        return None

    async def read_events(self) -> AsyncIterator[InputEvent]:
        while True:
            payload = await asyncio.to_thread(self._provider.read_event)
            if payload is None:
                await asyncio.sleep(0)
                continue
            yield _coerce_input_event(payload)

    def write(self, ops: list[RenderOp]) -> None:
        self._buf.extend(_encode_render_op(op) for op in ops)

    def flush(self) -> None:
        if not self._buf:
            return
        self._provider.write_ops(self._buf)
        self._buf = []
        self._provider.flush()

    def has_pending_output(self) -> bool:
        return bool(self._buf)

    def get_size(self) -> tuple[int, int]:
        cols, rows = self._provider.get_size()
        return int(cols), int(rows)

    def enter_raw_mode(self) -> None:
        self._provider.enter_raw_mode()

    def exit_raw_mode(self) -> None:
        self._provider.exit_raw_mode()

    def set_mouse_enabled(self, enabled: bool) -> None:
        self._provider.set_mouse_enabled(enabled)

    def supports_kitty_keyboard(self) -> bool:
        return self._capabilities["kitty_keyboard"]

    def supports_kitty_mouse(self) -> bool:
        return self._capabilities["kitty_mouse"]

    def supports_true_color(self) -> bool:
        return self._capabilities["true_color"]

    def supports_sixel(self) -> bool:
        return self._capabilities["sixel"]

    def supports_kitty_graphics(self) -> bool:
        return self._capabilities["kitty_graphics"]
