"""
modal.dispatcher — ActionDispatcher: routes Actions to editor state

Receives completed Actions from ModalEngine and applies them to the editor.
Owns: dot-repeat state, re-entrancy guard (ReentrancyError), mode sync,
register reads for paste, visual anchor tracking, and undo stack coordination.

See notes/plan_actions.md Part 4 for the dispatcher contract.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from peovim.modal.actions import (
    Action,
    BeginBlockInsert,
    ChangeCase,
    ChangeCaseBlock,
    CompoundAction,
    DeleteBlock,
    DeleteRange,
    EnterCommandMode,
    EnterInsertMode,
    EnterNormalMode,
    EnterReplaceMode,
    EnterVisualMode,
    FilterRange,
    IncrementNumber,
    IndentRange,
    InsertNewline,
    InsertTab,
    InsertText,
    JoinLines,
    MoveCursor,
    PasteRegister,
    Redo,
    RepeatBlockInsert,
    ReplaceBlock,
    ReplaceRange,
    ScrollToCursor,
    ScrollView,
    Undo,
    YankBlock,
    YankLine,
    YankRange,
)
from peovim.modal.dispatcher_buffers import handle_buffer_action
from peovim.modal.dispatcher_clipboard import (
    handle_begin_block_insert,
    handle_paste_register,
    handle_repeat_block_insert,
    handle_yank_block,
    handle_yank_line,
    handle_yank_range,
)
from peovim.modal.dispatcher_commands import handle_command_action
from peovim.modal.dispatcher_ex_commands import run_ex_command
from peovim.modal.dispatcher_folds import handle_fold_action
from peovim.modal.dispatcher_modes import (
    handle_compound_action,
    handle_enter_command_mode,
    handle_enter_insert_mode,
    handle_enter_normal_mode,
    handle_enter_replace_mode,
    handle_enter_visual_mode,
    handle_move_cursor,
    handle_redo,
    handle_scroll_to_cursor,
    handle_scroll_view,
    handle_undo,
)
from peovim.modal.dispatcher_navigation import handle_navigation_action
from peovim.modal.dispatcher_plugins import handle_plugin_action
from peovim.modal.dispatcher_repeat import handle_repeat_action
from peovim.modal.dispatcher_search import handle_search_action
from peovim.modal.dispatcher_text import (
    handle_change_case,
    handle_change_case_block,
    handle_delete_block,
    handle_delete_range,
    handle_filter_range,
    handle_increment_number,
    handle_indent_range,
    handle_insert_newline,
    handle_insert_tab,
    handle_insert_text,
    handle_join_lines,
    handle_replace_block,
    handle_replace_range,
)
from peovim.modal.dispatcher_workspace import handle_workspace_action
from peovim.modal.engine import ModalEngine, Mode

if TYPE_CHECKING:
    from peovim.commands.registry import CommandRegistry
    from peovim.core.document import Document
    from peovim.core.editor_state import EditorState
    from peovim.core.jumplist import JumpList
    from peovim.core.marks import MarkStore
    from peovim.core.registers import RegisterStore
    from peovim.core.window import Window
    from peovim.core.workspace import Workspace


# Sentinel for "line end" column (expanded to actual EOL by dispatcher)
LINE_END = 0x7FFFFFFF


@dataclass
class _PendingBlockInsert:
    start_line: int
    end_line: int
    col: int
    source_line: int
    baseline_text: str | None = None


@dataclass
class _InsertSession:
    """Tracks a single insert-mode session so dot-repeat can replay the full session."""

    start_line: int
    start_col: int  # actual insertion column (after entry-mode adjustment)
    text: str = ""  # accumulated typed text
    simple: bool = True  # False if newlines/non-sequential edits make rebasing unreliable


class ActionDispatcher:  # cm:7a5d8b
    """
    Applies a list of Actions to the editor state (Document, Window, registers).

    Each ActionDispatcher is associated with one ModalEngine (for mode sync)
    and has access to the active Window and RegisterStore.
    """

    def __init__(
        self,
        engine: ModalEngine,
        window: Window,
        registers: RegisterStore,
        marks: MarkStore | None = None,
        jumplist: JumpList | None = None,
        editor_state: EditorState | None = None,
        workspace: Workspace | None = None,
    ) -> None:
        self.engine = engine
        self.window = window
        self.registers = registers
        self.marks: MarkStore | None = marks
        self.jumplist: JumpList | None = jumplist
        self._editor_state: EditorState | None = editor_state
        self._workspace: Workspace | None = workspace
        self._dot_repeat: Action | None = None  # last text-mutating action
        self._in_dispatch: bool = False  # re-entrancy guard
        self._insert_compound_open: bool = False  # True while insert-mode compound edit is open
        self._pending_block_insert: _PendingBlockInsert | None = None
        self._insert_session: _InsertSession | None = None  # tracks current insert session
        self.quit_requested: bool = False
        self._last_ex_command: str = ""
        self._pending_events: list[tuple[str, dict]] = []
        self._plugin_callbacks: dict[int, Callable] = {}
        self._pending_callbacks: list[Callable] = []
        self._command_registry: CommandRegistry | None = None  # initialized lazily by _run_ex_command or set externally
        self._public_mutation_guard_stack: list[str] = []
        self._public_mutation_allowance_stack: list[str] = []
        self._current_plugin_callback: str | None = None
        self._action_handlers: dict[type, Any] = {
            InsertTab: handle_insert_tab,
            InsertText: handle_insert_text,
            DeleteRange: handle_delete_range,
            DeleteBlock: handle_delete_block,
            ReplaceRange: handle_replace_range,
            ReplaceBlock: handle_replace_block,
            ChangeCase: handle_change_case,
            ChangeCaseBlock: handle_change_case_block,
            InsertNewline: handle_insert_newline,
            IndentRange: handle_indent_range,
            JoinLines: handle_join_lines,
            IncrementNumber: handle_increment_number,
            FilterRange: handle_filter_range,
            YankRange: handle_yank_range,
            YankLine: handle_yank_line,
            YankBlock: handle_yank_block,
            PasteRegister: handle_paste_register,
            BeginBlockInsert: handle_begin_block_insert,
            RepeatBlockInsert: handle_repeat_block_insert,
            MoveCursor: handle_move_cursor,
            ScrollView: handle_scroll_view,
            ScrollToCursor: handle_scroll_to_cursor,
            EnterInsertMode: handle_enter_insert_mode,
            EnterNormalMode: handle_enter_normal_mode,
            EnterVisualMode: handle_enter_visual_mode,
            EnterCommandMode: handle_enter_command_mode,
            EnterReplaceMode: handle_enter_replace_mode,
            Undo: handle_undo,
            Redo: handle_redo,
            CompoundAction: handle_compound_action,
        }

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def dispatch(self, actions: list[Action]) -> None:  # cm:1e3f2c
        """Apply a list of actions produced by the ModalEngine."""
        if self._in_dispatch:
            raise RuntimeError("ReentrancyError: dispatch called while already dispatching")
        prev_window = self.window
        prev_cursor = (prev_window.cursor.line, prev_window.cursor.col)
        self._in_dispatch = True
        self._pending_events = []
        self._pending_callbacks = []
        try:
            for action in actions:
                self._apply(action)
        finally:
            self._in_dispatch = False
        self._sync_post_dispatch_state()
        if self.window is prev_window and (self.window.cursor.line, self.window.cursor.col) != prev_cursor:
            self.window.follow_cursor = True
        self._flush_pending_events()
        self._flush_pending_callbacks()

    def set_command_registry(self, registry: Any) -> None:
        """Wire the command registry after construction."""
        self._command_registry = registry

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------

    @property
    def _buf_id(self) -> int:
        return id(self.window.document)

    def _emit(self, event: str, **kwargs) -> None:
        """Emit an event on the editor event bus if available."""
        if self._editor_state is not None:
            self._editor_state.event_bus.emit(event, **kwargs)

    def _maybe_sync_clipboard(self, text: str, kind: str) -> None:
        """Mirror yanked text to the system clipboard if the clipboard option says so."""
        if self._editor_state is None:
            return
        cb = self._editor_state.options.get("clipboard") or ""
        if "unnamedplus" in cb:
            self.registers.set("+", text, kind)  # type: ignore[arg-type]
        elif "unnamed" in cb:
            self.registers.set("*", text, kind)  # type: ignore[arg-type]

    def _emit_later(self, event: str, **kwargs) -> None:
        """Queue an event to be fired after dispatch() completes."""
        self._pending_events.append((event, kwargs))

    def _run_public_event_callbacks(self, event: str, **kwargs) -> None:
        """Run event handlers immediately in a public-callback phase.

        This is used for hooks like buffer_pre_save that need to allow public
        API mutations before the current action completes.
        """
        if self._editor_state is None:
            return

        bus = self._editor_state.event_bus
        handlers = list(bus._handlers.get(event, []))
        if not handlers:
            return

        for token, _handler, is_once in handlers:
            if is_once:
                bus.off(token)

        was_dispatching = self._in_dispatch
        self._in_dispatch = False
        try:
            for _token, handler, _is_once in handlers:
                try:
                    self._current_plugin_callback = self._describe_callback(handler)
                    with self._allow_public_mutation(f"event handler '{event}'"):
                        handler(**kwargs)
                except Exception as exc:
                    import logging

                    logging.getLogger("peovim.dispatcher").warning("Event callback error (%s): %s", event, exc)
                    self._set_message(f"Event error ({event}): {exc}")
                finally:
                    self._current_plugin_callback = None
        finally:
            self._in_dispatch = was_dispatching
            self._sync_post_dispatch_state()

    def _apply(self, action: Action) -> None:
        doc = self.window.document
        cur = self.window.cursor
        handler = self._action_handlers.get(type(action))
        if handler is not None:
            handler(self, action, doc, cur)
            return
        if (
            handle_repeat_action(self, action)
            or handle_buffer_action(self, action, doc, cur)
            or handle_command_action(self, action)
            or handle_navigation_action(self, action, doc, cur)
            or handle_plugin_action(self, action)
            or handle_search_action(self, action, doc, cur)
            or handle_fold_action(self, action)
            or handle_workspace_action(self, action)
        ):
            pass
        # All others silently ignored until implemented in later phases

    def _run_ex_command(self, command_text: str) -> None:
        run_ex_command(self, command_text)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_message(self, msg: str) -> None:
        if self._editor_state is not None:
            self._editor_state.message = msg

    def _capture_deleted_range_text(self, doc: Document, sl: int, sc: int, el: int, ec: int) -> str:
        if ec == LINE_END:
            ec = len(doc.get_line(el))
        if sl == el:
            return doc.get_line(sl)[sc:ec]
        parts = [doc.get_line(sl)[sc:]]
        for ln in range(sl + 1, el):
            parts.append(doc.get_line(ln))
        parts.append(doc.get_line(el)[:ec])
        return "\n".join(parts)

    def _capture_deleted_block_text(
        self, doc: Document, start_line: int, end_line: int, start_col: int, end_col: int
    ) -> str:
        rows: list[str] = []
        for line_no in range(start_line, end_line + 1):
            line_text = doc.get_line(line_no)
            if start_col >= len(line_text):
                rows.append("")
                continue
            rows.append(line_text[start_col : min(end_col, len(line_text))])
        return "\n".join(rows)

    def _store_deleted_text(self, register: str, text: str, kind: str) -> None:
        if register == "_":
            return
        self.registers.set(register, text, kind)  # type: ignore[arg-type]
        if register != '"':
            self.registers.set('"', text, kind)  # type: ignore[arg-type]
        self.registers.shift_numbered(text, kind)  # type: ignore[arg-type]
        self._maybe_sync_clipboard(text, kind)

    def _transform_case(self, text: str, mode: str) -> str:
        if mode == "upper":
            return text.upper()
        if mode == "lower":
            return text.lower()
        return "".join(ch.lower() if ch.isupper() else ch.upper() if ch.islower() else ch for ch in text)

    def _ensure_line_length(self, doc: Document, line_no: int, col: int) -> None:
        line_text = doc.get_line(line_no)
        if len(line_text) < col:
            doc.insert(line_no, len(line_text), " " * (col - len(line_text)))

    def _ensure_line_exists(self, doc: Document, line_no: int) -> None:
        while doc.line_count() <= line_no:
            last_line = doc.line_count() - 1
            doc.insert(last_line, len(doc.get_line(last_line)), "\n")

    def _apply_block_paste(self, doc: Document, cur, text: str, before: bool, count: int) -> None:
        rows = text.split("\n")
        if count > 1:
            rows = [row * count for row in rows]

        insert_line = cur.line
        insert_col = cur.col if before else cur.col + 1

        with doc.compound_edit():
            for offset, row_text in enumerate(rows):
                line_no = insert_line + offset
                self._ensure_line_exists(doc, line_no)
                self._ensure_line_length(doc, line_no, insert_col)
                if row_text:
                    doc.insert(line_no, insert_col, row_text)

        cur.move_to(insert_line, insert_col)
        self._clamp_cursor_for_mode(doc)

    def _prepare_block_insert_source(self, doc: Document, cur) -> None:
        pending = self._pending_block_insert
        if pending is None:
            return
        self._ensure_line_length(doc, pending.source_line, pending.col)
        pending.baseline_text = doc.get_line(pending.source_line)
        cur.move_to(pending.source_line, pending.col)

    def _replay_pending_block_insert(self, doc: Document) -> None:
        pending = self._pending_block_insert
        if pending is None or pending.baseline_text is None:
            return

        source_line = min(pending.source_line, doc.line_count() - 1)
        current_text = doc.get_line(source_line)
        suffix = pending.baseline_text[pending.col :]
        if "\n" in current_text or not current_text.endswith(suffix):
            return

        inserted_end = len(current_text) - len(suffix)
        if inserted_end < pending.col:
            return
        inserted_text = current_text[pending.col : inserted_end]
        if not inserted_text or "\n" in inserted_text:
            return

        self._dot_repeat = RepeatBlockInsert(
            row_count=(pending.end_line - pending.start_line + 1),
            col=pending.col,
            text=inserted_text,
        )

        for line_no in range(pending.start_line, pending.end_line + 1):
            if line_no == source_line or line_no >= doc.line_count():
                continue
            self._ensure_line_length(doc, line_no, pending.col)
            doc.insert(line_no, pending.col, inserted_text)

    def _clamp_cursor_for_mode(self, doc: Document) -> None:
        normal_mode = self.engine.mode not in {Mode.INSERT, Mode.REPLACE}
        self.window.cursor.clamp(doc._table, normal_mode=normal_mode)

    def _sync_post_dispatch_state(self) -> None:
        if self._workspace is not None:
            self.window = self._workspace.active_window
        cur = self.window.cursor
        self.engine.set_cursor(cur.line, cur.col)
        self.engine.set_line_count(self.window.document.line_count())
        self.engine.set_document(self.window.document)
        self.engine.set_scroll(self.window.scroll_line)

    def _flush_pending_events(self) -> None:
        events = self._pending_events
        self._pending_events = []
        for event, kwargs in events:
            self._push_public_mutation_guard(f"event handler '{event}'")
            try:
                if event == "buffer_pre_save":
                    with self._allow_public_mutation(f"event handler '{event}'"):
                        self._emit(event, **kwargs)
                else:
                    self._emit(event, **kwargs)
            finally:
                self._pop_public_mutation_guard()

    def _flush_pending_callbacks(self) -> None:
        callbacks = self._pending_callbacks
        self._pending_callbacks = []
        for fn in callbacks:
            try:
                self._current_plugin_callback = self._describe_callback(fn)
                with self._allow_public_mutation(f"plugin callback '{self._current_plugin_callback}'"):
                    fn()
            except Exception as exc:
                import logging

                logging.getLogger("peovim.dispatcher").warning("Plugin callback error: %s", exc)
                self._set_message(f"Plugin error: {exc}")
            finally:
                self._current_plugin_callback = None

    def ensure_public_mutation_allowed(self, operation: str) -> None:
        if self._public_mutation_allowance_stack:
            return
        if not self._public_mutation_guard_stack:
            return
        context = self._public_mutation_guard_stack[-1]
        raise ReentrancyError(f"Buffer mutation '{operation}' is not allowed during {context}; use editor.defer().")

    def allows_public_mutation(self) -> bool:
        return bool(self._public_mutation_allowance_stack) or not self._public_mutation_guard_stack

    def _push_public_mutation_guard(self, context: str) -> None:
        self._public_mutation_guard_stack.append(context)

    def _pop_public_mutation_guard(self) -> None:
        if self._public_mutation_guard_stack:
            self._public_mutation_guard_stack.pop()

    @contextlib.contextmanager
    def _allow_public_mutation(self, context: str):
        self._public_mutation_allowance_stack.append(context)
        try:
            yield
        finally:
            self._public_mutation_allowance_stack.pop()

    def _describe_callback(self, fn: Callable) -> str:
        return getattr(fn, "__name__", fn.__class__.__name__)

    def _get_option(self, name: str, default: Any) -> Any:
        """Return option value: window-local dict first, then global OptionsStore, then default."""
        if name in self.window.options:
            return self.window.options[name]
        if self._editor_state is not None:
            val = self._editor_state.options.get(name)
            if val is not None:
                return val
        return default

    def _resolve_col(self, line: int, col: int, doc: Document) -> int:
        """Clamp col to valid range for the line."""
        if col == LINE_END:
            return len(doc.get_line(line))
        max_col = len(doc.get_line(line))
        return max(0, min(col, max_col))


class ReentrancyError(RuntimeError):
    pass
