"""
CommandsAPI — ex command registration
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.commands.registry import CommandRegistry


class CommandsAPI:
    """Wraps CommandRegistry for plugin command registration."""

    def __init__(self, registry: CommandRegistry, dispatcher: Any = None) -> None:
        self._registry = registry
        self._dispatcher = dispatcher

    def register(self, name: str, handler, *, min_abbrev: int = 0, desc: str = "") -> None:
        """Register an ex command handler."""
        self._registry.register(name, handler, min_abbrev=min_abbrev)

    def unregister(self, name: str) -> None:
        """Remove a registered command."""
        self._registry.unregister(name)

    def execute(self, command_text: str, ctx: Any = None) -> Any:
        """Parse and execute an ex command string."""
        from peovim.commands.parser import parse_ex_command
        from peovim.modal.dispatcher_ex_commands import ExCommandContext

        parsed = parse_ex_command(command_text)
        if ctx is None and self._dispatcher is not None:
            ctx = ExCommandContext(self._dispatcher)
        return self._registry.execute(parsed, ctx)

    def list_commands(self) -> list[str]:
        """Return the registered ex command names."""
        return self._registry.list_commands()
