"""
core.event_bus — EventBus: synchronous pub/sub event system

Plugins subscribe to named events. The editor emits them after state changes.
All calls happen on the main thread. Thread safety is NOT a concern here.

Events used in Phase 6:
  buffer_opened   — kwargs: buf_id (int), path (str | None), filetype (str)
    buffer_pre_save — kwargs: buf_id (int), path (str | None)
  buffer_changed  — kwargs: buf_id (int)
    buffer_text_changed — kwargs: buf_id (int), path (str | None), start_line (int), start_col (int), end_line (int), end_col (int), new_text (str)
  buffer_saved    — kwargs: buf_id (int), path (str)
  filetype_detected — kwargs: buf_id (int), filetype (str)
  cursor_moved    — kwargs: buf_id (int), line (int), col (int)
  insert_entered  — kwargs: buf_id (int)
  insert_left     — kwargs: buf_id (int)
  mode_changed    — kwargs: mode (str)
  option_changed  — kwargs: name (str), value (Any), scope (str)
  editor_ready    — no kwargs
  editor_shutdown — no kwargs
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

_log = logging.getLogger("peovim.event_bus")


class EventBus:  # cm:4c8a1d
    """Synchronous publish/subscribe event bus."""

    def __init__(self) -> None:
        # event -> list of (token, handler, once)
        self._handlers: dict[str, list[tuple[int, Callable, bool]]] = {}
        self._next_token: int = 0
        # token -> event name (for off() lookups)
        self._token_event: dict[int, str] = {}

    def on(self, event: str, handler: Callable, *, once: bool = False) -> int:
        """Subscribe to an event. Returns a token for later unsubscription."""
        token = self._next_token
        self._next_token += 1
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append((token, handler, once))
        self._token_event[token] = event
        return token

    def off(self, token: int) -> None:
        """Unsubscribe by token. No-op if token is unknown."""
        event = self._token_event.pop(token, None)
        if event is None:
            return
        handlers = self._handlers.get(event, [])
        self._handlers[event] = [(t, h, o) for t, h, o in handlers if t != token]

    def once(self, event: str, handler: Callable) -> int:
        """Subscribe to an event; auto-unsubscribes after first call."""
        return self.on(event, handler, once=True)

    def emit(self, event: str, **kwargs: Any) -> None:
        """Call all handlers registered for event, passing kwargs."""
        handlers = self._handlers.get(event)
        if not handlers:
            return
        if _log.isEnabledFor(logging.DEBUG):
            _log.debug(
                "emit %r  handlers=%d  %s", event, len(handlers), "  ".join(f"{k}={v!r}" for k, v in kwargs.items())
            )
        # Iterate directly; only allocate to_remove list when once-handlers exist.
        # self.off() replaces self._handlers[event] rather than mutating it, so
        # our local `handlers` reference remains valid for the duration of the loop.
        to_remove: list[int] | None = None
        for token, handler, is_once in handlers:
            if is_once:
                to_remove = [token] if to_remove is None else (to_remove + [token])
            try:
                handler(**kwargs)
            except Exception:
                _log.exception("handler for %r raised", event)
        if to_remove is not None:
            for token in to_remove:
                self.off(token)

    def handler_count(self, event: str) -> int:
        """Return the number of handlers registered for event."""
        return len(self._handlers.get(event, []))
