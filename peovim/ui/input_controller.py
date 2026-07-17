"""Input-loop and key-routing helpers extracted from `EventLoop`."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from peovim.ui.backend import KeyEvent, MouseEvent

if TYPE_CHECKING:
    from peovim.ui.event_loop import EventLoop


log = logging.getLogger(__name__)
_WINDOWS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\).+")
_UNIX_PATH_RE = re.compile(r"^/.+")


class InputController:
    """Owns input-loop handling, key echo/logging, and normal key routing for `EventLoop`."""

    def __init__(self, host: EventLoop) -> None:
        self._host = host

    def start_key_echo(self) -> None:
        import time

        host = self._host
        host._key_echo_active = True
        host._key_echo_keys = []
        host._key_echo_idle_since = time.monotonic()
        host._invalidate_message()

    def toggle_key_log(self) -> None:
        import pathlib

        host = self._host
        if host._key_log_path is not None:
            if host._editor_state is not None:
                host._editor_state.message = f"KeyLog stopped: {host._key_log_path}"
            host._key_log_path = None
        else:
            log_path = str(pathlib.Path.home() / ".config" / "peovim" / "keylog.txt")
            host._key_log_path = log_path
            try:
                pathlib.Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                pathlib.Path(log_path).write_text("", encoding="utf-8")
            except Exception:
                pass
            if host._editor_state is not None:
                host._editor_state.message = f"KeyLog started → {log_path}"
        host._invalidate_message()

    async def input_loop(self) -> None:
        host = self._host
        async for event in host._backend.read_events():
            if not host._running:
                break

            try:
                if isinstance(event, KeyEvent):
                    if event.key == "<BracketedPaste>":
                        if self.handle_paste_event(event.text):
                            return
                    elif self.handle_key_event(event):
                        return
                elif isinstance(event, MouseEvent):
                    host._mouse_dispatcher.handle(event)
            except Exception as exc:
                host._report_runtime_error("input handling", exc)

            host._invalidate("full")

    def handle_key_event(self, event: KeyEvent) -> bool:
        from peovim.modal.actions import EnterCommandMode, QuitEditor, RunExCommand, SetSearchPattern
        from peovim.modal.engine import Mode

        host = self._host
        self.write_key_log(event.key)
        log.debug(
            "KEY=%r  cmdline=%s  mode=%s  flash=%s",
            event.key,
            "active" if host._cmdline.active else "off",
            host._engine.mode.value,
            "active" if host._flash is not None and getattr(host._flash, "is_active", False) else "off",
        )

        if host._key_echo_active and self.handle_key_echo_key(event.key):
            return False

        normalized_event = host._normalize_key_after_cmdline_dismiss(event)
        host._clear_transient_message_on_keypress()

        # Confirm-substitute mode intercepts all keys
        if host._editor_state is not None and host._editor_state.confirm_sub is not None:
            self._handle_confirm_sub_key(normalized_event.key)
            return False

        normal_key_after: str | None = None
        if host._cmdline.active:
            should_exit, normal_key_after = host._process_active_cmdline_key(
                normalized_event.key,
                run_ex_command_type=RunExCommand,
                set_search_pattern_type=SetSearchPattern,
            )
            if should_exit:
                return True
            # Cmdline commit may have activated confirm-sub mode
            if not host._cmdline.active and host._editor_state is not None:
                cs = host._editor_state.confirm_sub
                if cs is not None and not cs.initialized:
                    self._init_confirm_sub(cs)
                    return False

        if not host._cmdline.active and host._handle_overlay_key(normalized_event.key):
            return False

        normal_key = host._resolve_normal_key(normalized_event.key, normal_key_after)
        log.debug(
            "ROUTE: normal_key=%r normal_key_after=%r cmdline=%s",
            normal_key,
            normal_key_after,
            host._cmdline.active,
        )
        return normal_key is not None and self.dispatch_normal_key(
            normal_key,
            enter_command_mode_type=EnterCommandMode,
            quit_action_type=QuitEditor,
            normal_mode=Mode.NORMAL,
        )

    def handle_paste_event(self, text: str) -> bool:
        from peovim.modal.actions import OpenBuffer
        from peovim.modal.engine import Mode

        host = self._host
        self.write_key_log("<BracketedPaste>")
        if (
            not host._cmdline.active
            and host._engine.mode == Mode.NORMAL
            and self._maybe_open_dropped_path(text, open_buffer_type=OpenBuffer)
        ):
            return False

        return any(self.handle_key_event(KeyEvent(key)) for key in self._pasted_text_to_keys(text))

    def write_key_log(self, key: str) -> None:
        host = self._host
        if host._key_log_path is None:
            return
        try:
            with open(host._key_log_path, "a", encoding="utf-8") as key_log_file:
                key_log_file.write(repr(key) + "\n")
        except Exception:
            pass

    def _maybe_open_dropped_path(self, text: str, *, open_buffer_type: type) -> bool:
        host = self._host
        paths = self._extract_existing_paths(text)
        if not paths:
            return False
        self.sync_active_window_to_engine()
        host._dispatcher.dispatch([open_buffer_type(paths[0])])
        if host._editor_state is not None and len(paths) > 1:
            host._editor_state.message = f"Opened dropped file: {Path(paths[0]).name} (+{len(paths) - 1} more ignored)"
            host._invalidate_message()
        return True

    def _extract_existing_paths(self, text: str) -> list[str]:
        stripped = text.strip()
        if not stripped:
            return []

        candidates: list[str] = []
        if "\n" in stripped or "\r" in stripped:
            candidates.extend(part.strip().strip('"') for part in stripped.splitlines())
        elif stripped.startswith('"') and stripped.endswith('"') and len(stripped) >= 2:
            candidates.append(stripped[1:-1])
        elif _WINDOWS_PATH_RE.match(stripped) or _UNIX_PATH_RE.match(stripped):
            candidates.append(stripped)
        else:
            quoted = re.findall(r'"([^"]+)"', stripped)
            if quoted:
                candidates.extend(quoted)

        existing: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or (not _WINDOWS_PATH_RE.match(candidate) and not _UNIX_PATH_RE.match(candidate)):
                continue
            try:
                resolved = str(Path(candidate).resolve())
            except OSError:
                continue
            if resolved in seen or not Path(resolved).exists():
                continue
            seen.add(resolved)
            existing.append(resolved)
        return existing

    def _pasted_text_to_keys(self, text: str) -> list[str]:
        keys: list[str] = []
        for ch in text:
            if ch in ("\r", "\n"):
                keys.append("<CR>")
            elif ch == "\t":
                keys.append("<Tab>")
            elif ch == "\x08" or ch == "\x7f":
                keys.append("<BS>")
            elif ch == "\x1b":
                keys.append("<Esc>")
            else:
                keys.append(ch)
        return keys

    def handle_key_echo_key(self, key: str) -> bool:
        import time

        host = self._host
        if key in ("<Esc>", "q"):
            host._key_echo_active = False
            host._key_echo_keys.clear()
            if host._editor_state is not None:
                host._editor_state.message = ""
            host._invalidate_message()
            return True
        host._key_echo_idle_since = time.monotonic()
        host._key_echo_keys.append(key)
        self.update_key_echo_display()
        host._invalidate_message()
        return True

    def dispatch_normal_key(
        self, normal_key: str, enter_command_mode_type: type, quit_action_type: type, normal_mode
    ) -> bool:
        host = self._host
        self.sync_active_window_to_engine()
        actions = host._engine.feed_key(normal_key)
        log.debug("ENGINE: mode=%s actions=%s", host._engine.mode.value, [type(a).__name__ for a in actions])
        if any(isinstance(action, quit_action_type) for action in actions):
            host._running = False
            return True
        host._dispatcher.dispatch(actions)
        if host._dispatcher.quit_requested:
            host._running = False
            return True
        if normal_key == "<Esc>" and host._engine.mode == normal_mode:
            from peovim.modal.actions import ClearSearchHighlight

            host._dispatcher.dispatch([ClearSearchHighlight()])
        for action in actions:
            if isinstance(action, enter_command_mode_type):
                prompt = getattr(action, "prompt", ":")
                initial = getattr(action, "initial", "")
                host._cmdline.enter(prompt, initial)
                if prompt == ":":
                    host._cmdline.set_completion_source(host._list_available_commands())
                host._engine.set_mode(normal_mode)
                host._invalidate_cmdline()
                break
        self.check_key_prefix_events()
        return False

    def sync_active_window_to_engine(self) -> None:
        host = self._host
        active_window = host._workspace.active_window
        host._dispatcher.window = active_window

    def update_key_echo_display(self) -> None:
        import time

        host = self._host
        if host._editor_state is None:
            return
        remaining = max(0.0, host._key_echo_idle_secs - (time.monotonic() - host._key_echo_idle_since))
        recent = host._key_echo_keys[-8:]
        keys_str = "  ".join(recent)
        host._editor_state.message = f"KEY ECHO ({remaining:.0f}s): {keys_str}"

    def check_key_prefix_events(self) -> None:
        from peovim.modal.engine import Mode

        host = self._host
        if host._editor_state is None:
            return
        state = host._engine._state
        buf_len = len(state.key_buffer)
        mode = host._engine.mode
        prefix_mode: str | None = None
        if mode == Mode.NORMAL:
            prefix_mode = "normal"
        elif mode in (Mode.VISUAL_CHAR, Mode.VISUAL_LINE, Mode.VISUAL_BLOCK):
            prefix_mode = "visual"
        if prefix_mode is not None and buf_len > 0:
            if buf_len != host._last_key_buf_len:
                prefix = "".join(state.key_buffer)
                host._editor_state.event_bus.emit("key_prefix_pending", prefix=prefix, mode=prefix_mode)
        elif host._last_key_buf_len > 0:
            host._editor_state.event_bus.emit("key_prefix_done")
        host._last_key_buf_len = buf_len

    # ------------------------------------------------------------------
    # Confirm-substitute mode
    # ------------------------------------------------------------------

    def _init_confirm_sub(self, cs: object) -> None:
        """Move cursor to the first match and show the initial prompt."""
        from peovim.core.editor_state import ConfirmSubState

        if not isinstance(cs, ConfirmSubState):
            return
        cs.initialized = True
        host = self._host
        cur = cs.current
        if cur is not None:
            line, col_start, _col_end, _rep = cur
            win = host._dispatcher.window
            win.cursor.move_to(line, col_start)
            win.cursor.clamp(win.document._table)
            host._dispatcher.engine.set_cursor(line, col_start)
        self._set_confirm_prompt(cs)
        host._invalidate("full")

    def _handle_confirm_sub_key(self, key: str) -> bool:
        from peovim.core.editor_state import ConfirmSubState

        host = self._host
        if host._editor_state is None:
            return False
        cs = host._editor_state.confirm_sub
        if not isinstance(cs, ConfirmSubState):
            return False

        if not cs.initialized:
            self._init_confirm_sub(cs)

        k = key.lower()
        try:
            if k == "y":
                self._apply_confirm_match(cs)
                cs.current_idx += 1
            elif k == "n":
                cs.current_idx += 1
            elif k == "a":
                while not cs.done:
                    self._apply_confirm_match(cs)
                    cs.current_idx += 1
            elif k in ("q", "<esc>", "<c-c>"):
                self._finish_confirm_sub(cs)
                return True
            elif k == "l":
                self._apply_confirm_match(cs)
                cs.current_idx += 1
                self._finish_confirm_sub(cs)
                return True
        except Exception:
            import logging

            logging.getLogger(__name__).exception("confirm_sub error")
            self._finish_confirm_sub(cs)
            return True

        if cs.done:
            self._finish_confirm_sub(cs)
        else:
            cur = cs.current
            if cur is not None:
                line, col_start, _col_end, _rep = cur
                win = host._dispatcher.window
                win.cursor.move_to(line, col_start)
                win.cursor.clamp(win.document._table)
                host._dispatcher.engine.set_cursor(line, col_start)
            self._set_confirm_prompt(cs)
            host._invalidate("full")
        return True

    def _apply_confirm_match(self, cs: object) -> None:
        from peovim.core.editor_state import ConfirmSubState

        if not isinstance(cs, ConfirmSubState):
            return
        cur = cs.current
        if cur is None:
            return
        line, col_start, col_end, replacement = cur
        host = self._host
        doc = host._dispatcher.window.document
        doc.delete(line, col_start, line, col_end)
        if replacement:
            doc.insert(line, col_start, replacement)
        cs.applied += 1
        # Adjust column offsets for subsequent matches on the same line
        delta = len(replacement) - (col_end - col_start)
        if delta != 0:
            for i in range(cs.current_idx + 1, len(cs.matches)):
                m_line, m_start, m_end, m_rep = cs.matches[i]
                if m_line == line:
                    cs.matches[i] = (m_line, m_start + delta, m_end + delta, m_rep)

    def _finish_confirm_sub(self, cs: object) -> None:
        from peovim.core.editor_state import ConfirmSubState

        host = self._host
        if host._editor_state is None:
            return
        if isinstance(cs, ConfirmSubState):
            n = cs.applied
            host._editor_state.message = f"{n} substitution{'s' if n != 1 else ''}"
        host._editor_state.confirm_sub = None
        host._invalidate("full")
        host._invalidate_message()

    def _set_confirm_prompt(self, cs: object) -> None:
        from peovim.core.editor_state import ConfirmSubState

        host = self._host
        if host._editor_state is None or not isinstance(cs, ConfirmSubState):
            return
        cur = cs.current
        rep = cur[3] if cur else ""
        total = len(cs.matches)
        idx = cs.current_idx + 1
        host._editor_state.message = f"replace with '{rep}'? ({idx}/{total})  [y]es [n]o [a]ll [q]uit [l]last"
        host._invalidate_message()
