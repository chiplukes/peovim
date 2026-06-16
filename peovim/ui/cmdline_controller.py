"""Command-line flow helpers extracted from `EventLoop`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from peovim.ui.backend import KeyEvent

if TYPE_CHECKING:
    from peovim.ui.event_loop import EventLoop

log = logging.getLogger(__name__)


class CommandLineController:
    """Owns command-line key processing and immediate repaint helpers for `EventLoop`."""

    def __init__(self, host: EventLoop) -> None:
        self._host = host

    def normalize_key_after_cmdline_dismiss(self, event: KeyEvent) -> KeyEvent:
        host = self._host
        if host._cmdline_just_dismissed:
            host._cmdline_just_dismissed = False
            if len(event.key) == 5 and event.key.startswith("<A-") and event.key.endswith(">"):
                log.debug("DEALT: stripping spurious Alt from %r → %r after cmdline dismiss", event.key, event.key[3])
                return KeyEvent(event.key[3])
        return event

    def resolve_normal_key(self, key: str, normal_key_after: str | None) -> str | None:
        if normal_key_after:
            return normal_key_after
        if not self._host._cmdline.active:
            return key
        return None

    def process_active_cmdline_key(
        self,
        key: str,
        run_ex_command_type: type,
        set_search_pattern_type: type,
    ) -> tuple[bool, str | None]:
        host = self._host
        cmdline_key = key
        normal_key_after: str | None = None
        if len(cmdline_key) == 5 and cmdline_key.startswith("<A-") and cmdline_key.endswith(">"):
            normal_key_after = cmdline_key[3]
            cmdline_key = "<Esc>"
        result = host._cmdline.feed_key(cmdline_key)
        host._invalidate_cmdline()
        log.debug("CMDLINE: sent=%r result=%r active_after=%s", cmdline_key, result, host._cmdline.active)
        if result is not None:
            self.handle_cmdline_result(
                result,
                run_ex_command_type=run_ex_command_type,
                set_search_pattern_type=set_search_pattern_type,
            )
            if host._dispatcher.quit_requested:
                host._running = False
                return True, None
        if not (normal_key_after and not host._cmdline.active):
            normal_key_after = None
        return False, normal_key_after

    def handle_cmdline_result(
        self,
        result: str,
        run_ex_command_type: type,
        set_search_pattern_type: type,
    ) -> None:
        host = self._host
        if result == "":
            host._cmdline_just_dismissed = True
            self.render_cmdline_immediately()
            return
        if not result:
            return

        prompt = host._cmdline.prompt
        if prompt == ":":
            host._dispatcher.dispatch([run_ex_command_type(result)])
            if host._editor_state is not None:
                host._editor_state.shada.push_command_history(result)
            return
        if prompt == "/":
            host._dispatcher.dispatch([set_search_pattern_type(result, "forward")])
            if host._editor_state is not None:
                host._editor_state.shada.push_search_history(result)
            return
        if prompt == "?":
            host._dispatcher.dispatch([set_search_pattern_type(result, "backward")])
            if host._editor_state is not None:
                host._editor_state.shada.push_search_history(result)
            return
        if prompt == "!":
            from peovim.modal.actions import FilterRange

            filter_range = host._engine.consume_filter_range()
            if filter_range is None:
                filter_range = (
                    host._dispatcher.window.cursor.line,
                    host._dispatcher.window.cursor.line,
                )
            host._dispatcher.dispatch([FilterRange(filter_range[0], filter_range[1], result)])

    def clear_transient_message_on_keypress(self) -> None:
        host = self._host
        if host._editor_state is not None and host._editor_state.message:
            # Don't clear the message while confirm-substitute is running — it's the prompt
            if host._editor_state.confirm_sub is not None:
                return
            host._editor_state.message = ""
            host._invalidate_message()

    def render_cmdline_immediately(self) -> None:
        host = self._host
        if host._grid is None:
            return
        cols, rows = host._backend.get_size()
        overlay_rows = max(1, host._cmdline.last_completion_rows + 1)
        host._render_body(host._grid, cols, rows)
        host._grid.invalidate_prev_rows(max(0, rows - overlay_rows), rows)
        self._flush_grid(host)

    def render_picker_immediately(self) -> None:
        host = self._host
        if host._grid is None:
            return
        cols, rows = host._backend.get_size()
        overlay_rows = min(rows, max(10, rows * 2 // 5))
        host._render_body(host._grid, cols, rows)
        host._grid.invalidate_prev_rows(max(0, rows - overlay_rows), rows)
        self._flush_grid(host)

    def _flush_grid(self, host) -> None:
        """Flush grid → backend using the fast (flush_ansi) or fallback path."""
        if hasattr(host._backend, "write_raw"):
            host._grid.flush_ansi(host._ansi_buf)
            if not host._ansi_buf:
                return
            host._backend.write_raw(host._ansi_buf)
        else:
            ops = host._grid.flush()
            if not ops:
                return
            host._backend.write(ops)
        if host._backend.has_pending_output():
            host._backend.flush()

    def list_available_commands(self) -> list[str]:
        from peovim.commands.builtin import register_builtins
        from peovim.commands.registry import CommandRegistry

        host = self._host
        registry = host._dispatcher._command_registry
        if registry is None:
            registry = CommandRegistry()
            register_builtins(registry)
            host._dispatcher._command_registry = registry
        return registry.list_commands()
