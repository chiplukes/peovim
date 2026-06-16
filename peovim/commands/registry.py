"""
commands.registry — CommandRegistry: ex command name → handler mapping

Supports abbreviation matching (:w = :write, :q = :quit, etc.).
Plugins register commands via api.commands.register().
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from peovim.commands.parser import ParsedCommand

_log = logging.getLogger(__name__)

# Handler signature: (cmd: ParsedCommand, context: Any) -> Any
CommandHandler = Callable[[ParsedCommand, Any], Any]


class CommandRegistry:  # cm:f1d5b8
    """
    Maps ex command names to handler functions. Supports prefix abbreviations.

    Registration: register(full_name, handler, min_abbrev=1)
    Lookup: get(name) returns handler or None.
    Abbreviation matching: ':w' matches ':write' if no shorter registered command starts with 'w'.
    """

    def __init__(self) -> None:
        # name -> (handler, min_abbrev_length)
        self._commands: dict[str, tuple[CommandHandler, int]] = {}

    def register(
        self,
        name: str,
        handler: CommandHandler,
        min_abbrev: int = 1,
    ) -> None:
        """Register a command. min_abbrev is the shortest valid abbreviation."""
        self._commands[name] = (handler, min_abbrev)

    def get(self, name: str) -> CommandHandler | None:
        """
        Look up a command by name or abbreviation.
        Returns handler or None if not found.
        """
        # Exact match first
        if name in self._commands:
            return self._commands[name][0]

        # Abbreviation match: find all full names that start with name
        matches: list[str] = []
        for full_name, (_handler, min_abbrev) in self._commands.items():
            if full_name.startswith(name) and len(name) >= min_abbrev:
                matches.append(full_name)

        if len(matches) == 1:
            return self._commands[matches[0]][0]

        # Ambiguous or not found
        return None

    def execute(self, cmd: ParsedCommand, context: Any = None) -> Any:
        """Find and execute a command. Returns None if not found."""
        handler = self.get(cmd.cmd)
        if handler is None:
            _log.debug("unknown command :%s", cmd.cmd)
            return None
        _log.debug("execute :%s  args=%r", cmd.cmd, getattr(cmd, "args", ""))
        return handler(cmd, context)

    def unregister(self, name: str) -> None:
        """Remove a registered command. No-op if not found."""
        self._commands.pop(name, None)

    def list_commands(self) -> list[str]:
        """Return sorted list of all registered command names."""
        return sorted(self._commands.keys())
