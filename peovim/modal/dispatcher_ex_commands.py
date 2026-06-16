from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.modal.dispatcher import ActionDispatcher


class ExCommandContext:
    """Thin context wrapper passed to ex command handlers."""

    def __init__(self, dispatcher: ActionDispatcher) -> None:
        self._dispatcher = dispatcher
        self.window = dispatcher.window
        self.engine = dispatcher.engine
        self.registers = dispatcher.registers
        self.marks = getattr(dispatcher, "marks", None)
        self.editor_state = dispatcher._editor_state
        self.workspace = getattr(dispatcher, "_workspace", None)

    @property
    def dispatcher(self) -> ExCommandContext:
        return self

    def dispatch(self, actions: list) -> None:
        """Apply actions directly via _apply, bypassing the reentrancy guard."""
        for action in actions:
            self._dispatcher._apply(action)


def run_ex_command(dispatcher: ActionDispatcher, command_text: str) -> None:
    from peovim.commands.builtin import register_builtins
    from peovim.commands.parser import parse_ex_command
    from peovim.commands.registry import CommandRegistry

    if dispatcher._command_registry is None:
        dispatcher._command_registry = CommandRegistry()
        register_builtins(dispatcher._command_registry)
    registry = dispatcher._command_registry

    try:
        parsed = parse_ex_command(command_text)
    except Exception:
        return

    handler = registry.get(parsed.cmd)
    if handler is None:
        return

    dispatcher._last_ex_command = command_text
    dispatcher.engine.set_last_ex_command(command_text)

    context = ExCommandContext(dispatcher)
    try:
        handler(parsed, context)
    except Exception as exc:
        dispatcher._set_message(f"E: {exc}")
