"""
EventsAPI — event subscription and emission
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.core.event_bus import EventBus


class EventsAPI:  # cm:1a5b6e
    """Wraps EventBus for plugin event subscription."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def on(self, event: str, handler) -> int:
        """Subscribe to an event. Returns a token for unsubscribing."""
        return self._bus.on(event, handler)

    def off(self, token: int) -> None:
        """Unsubscribe using token returned by on()."""
        self._bus.off(token)

    def once(self, event: str, handler) -> int:
        """Subscribe to an event for one invocation only."""
        return self._bus.once(event, handler)

    def emit(self, event: str, **kwargs) -> None:
        """Emit an event (for plugin→plugin communication)."""
        self._bus.emit(event, **kwargs)

    def handler_count(self, event: str) -> int:
        """Return the number of handlers registered for *event*."""
        return self._bus.handler_count(event)
