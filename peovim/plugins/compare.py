"""Diff selection state and keymaps for the side-by-side diff / merge view."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

_controller: _CompareController | None = None
_NAMESPACE = "compare"
_HINT_NAMESPACE = "compare.hints"
log = logging.getLogger(__name__)

SIGN_DEFS: dict[str, tuple[str, tuple[int, int, int]]] = {
    "compare.change": ("~", (232, 189, 104)),
    "compare.insert": ("+", (166, 227, 161)),
    "compare.delete": ("-", (244, 143, 177)),
}

_BLOCK_STYLES: dict[tuple[str, str], tuple[int, int, int]] = {
    ("change", "left"): (97, 74, 26),
    ("change", "right"): (70, 84, 34),
    ("insert", "right"): (70, 84, 34),
    ("delete", "left"): (85, 34, 46),
}

_HINT_STYLES: dict[str, tuple[int, int, int]] = {
    "insert": (140, 214, 122),
    "delete": (244, 143, 177),
}


@dataclass(frozen=True)
class _CompareSelection:
    slot: int
    path: Path

    def __str__(self) -> str:
        return f"{self.slot}: {self.path}"


@dataclass(frozen=True)
class CompareBlock:
    kind: str
    left_start: int
    left_end: int
    right_start: int
    right_end: int

    @property
    def left_count(self) -> int:
        return self.left_end - self.left_start

    @property
    def right_count(self) -> int:
        return self.right_end - self.right_start


@dataclass
class _PreDiffState:
    """Snapshot of the editor view taken just before a diff session opens."""

    path: Path | None
    cursor: tuple[int, int]
    scroll_line: int


@dataclass
class _CompareSession:
    left_path: Path
    right_path: Path
    blocks: tuple[CompareBlock, ...]
    current_block_index: int | None
    left_buf_id: int
    right_buf_id: int
    left_window_id: int
    right_window_id: int


class _CompareController:
    def __init__(self, api: EditorAPI) -> None:
        self._api = api
        self._slot1: Path | None = None
        self._slot2: Path | None = None
        self._session: _CompareSession | None = None
        self._pre_diff_state: _PreDiffState | None = None

    def select_slot(self, slot: int) -> None:
        path = self._current_file()
        if path is None:
            _set_status(self._api, f"Diff {slot} requires a file-backed buffer")
            return
        if slot == 1:
            self._slot1 = path
        else:
            self._slot2 = path
        self._debug("select_slot", slot=slot, path=str(path))
        _set_status(self._api, f"Diff {slot}: {self._display_path(path)}")

    def compare_selected(self) -> None:
        if self._slot1 is None or self._slot2 is None:
            missing: list[str] = []
            if self._slot1 is None:
                missing.append("c1")
            if self._slot2 is None:
                missing.append("c2")
            _set_status(self._api, f"Select diff targets first ({', '.join(missing)})")
            return
        left, right = self._normalized_selection_order(self._slot1, self._slot2)
        self._slot1 = left
        self._slot2 = right
        self._debug("compare_selected", left=str(left), right=str(right))
        self._api.events.emit("diff_selection_ready", left=str(left), right=str(right))
        _set_status(self._api, f"Diff ready: {self._display_path(left)} ↔ {self._display_path(right)}")

    def slot_summary(self) -> tuple[str | None, str | None]:
        return (
            self._display_path(self._slot1) if self._slot1 is not None else None,
            self._display_path(self._slot2) if self._slot2 is not None else None,
        )

    def session_summary(self) -> dict[str, Any] | None:
        if self._session is None:
            return None
        return {
            "left": self._display_path(self._session.left_path),
            "right": self._display_path(self._session.right_path),
            "blocks": len(self._session.blocks),
        }

    def open_selected_compare(self, *, left: str, right: str) -> None:
        left_path = Path(left).resolve()
        right_path = Path(right).resolve()
        if left_path == right_path:
            _set_status(self._api, "Diff requires two different files")
            return
        if not left_path.exists() or not right_path.exists():
            _set_status(self._api, "Diff target is missing on disk")
            return

        # Snapshot the current view so stop_compare can restore it.
        self._pre_diff_state = self._capture_view()

        self._clear_session_decorations()
        self._api.commands.execute("only")
        self._activate_window(self._api.active_window())
        self._api.open_buffer(left_path)
        self._api.commands.execute("vsplit")
        self._activate_window(self._api.active_window())
        self._api.open_buffer(right_path)

        left_window, right_window = self._resolve_compare_windows(left_path, right_path)
        if left_window is None or right_window is None:
            _set_status(self._api, "Diff windows were not created")
            return

        self._activate_window(left_window)

        left_buf = left_window.buffer()
        right_buf = right_window.buffer()
        blocks = compute_blocks(left_buf.get_lines(), right_buf.get_lines())
        self._debug(
            "open_selected_compare",
            left=str(left_path),
            right=str(right_path),
            blocks=len(blocks),
        )
        self._decorate_blocks(left_buf, right_buf, blocks)
        self._align_to_first_block(left_window, right_window, blocks)

        self._session = _CompareSession(
            left_path=left_path,
            right_path=right_path,
            blocks=blocks,
            current_block_index=0 if blocks else None,
            left_buf_id=left_buf.buf_id,
            right_buf_id=right_buf.buf_id,
            left_window_id=left_window.win_id,
            right_window_id=right_window.win_id,
        )
        self._publish_statusline_state(left_window, right_window)
        _set_status(
            self._api,
            f"Diff view: {self._display_path(left_path)} ↔ {self._display_path(right_path)} ({len(blocks)} blocks)",
        )

    def next_diff(self) -> None:
        self._jump_diff(direction="next")

    def prev_diff(self) -> None:
        self._jump_diff(direction="prev")

    def merge_left_to_right(self) -> None:
        self._merge_block(source_side="left", target_side="right")

    def merge_right_to_left(self) -> None:
        self._merge_block(source_side="right", target_side="left")

    def stop_compare(self) -> None:
        session = self._session
        self._debug("stop_compare", session=self._session_debug_summary())
        self._clear_session_decorations()
        pre = self._pre_diff_state
        self._pre_diff_state = None
        self._restore_view(pre)
        if session is None:
            _set_status(self._api, "Diff session cleared")
            return
        _set_status(
            self._api,
            f"Diff stopped: {self._display_path(session.left_path)} ↔ {self._display_path(session.right_path)}",
        )

    def on_cursor_moved(self, buf_id: int = 0, **_kwargs: Any) -> None:
        """Sync the other diff pane's scroll whenever the active pane moves."""
        session = self._session
        if session is None:
            return
        if buf_id == session.left_buf_id:
            left_window, right_window = self._session_windows()
            active_win, other_win = left_window, right_window
            from_side = "left"
        elif buf_id == session.right_buf_id:
            left_window, right_window = self._session_windows()
            active_win, other_win = right_window, left_window
            from_side = "right"
        else:
            return
        if active_win is None or other_win is None:
            return
        scroll = active_win.visible_range()[0]
        height = active_win.get_height()
        other_scroll = _map_scroll_line(scroll, session.blocks, from_side=from_side)
        other_win.set_scroll_line(other_scroll)
        # Clamp the other pane's cursor into the new visible range.
        # sync_window_render_state calls scroll_to_cursor() before each render;
        # if the cursor is outside the synced viewport it would fight us and reset
        # the scroll back on the very next frame.
        other_cur = other_win.cursor[0]
        other_max = max(0, other_win.buffer().line_count() - 1)
        clamped = max(other_scroll, min(other_cur, other_scroll + height - 1, other_max))
        if clamped != other_cur:
            other_win.set_cursor(clamped, other_win.cursor[1])

    def on_buffer_saved(self, *, path: str | None = None, **_kwargs: Any) -> None:
        session = self._session
        if session is None or path is None:
            return
        saved_path = Path(path).resolve()
        if saved_path not in {session.left_path, session.right_path}:
            return
        self._debug("on_buffer_saved", path=str(saved_path), session=self._session_debug_summary())
        left_window, right_window = self._session_windows()
        if left_window is None or right_window is None:
            self._clear_session_decorations()
            _set_status(self._api, "Diff session ended")
            return

        focus_side = self._active_compare_side() or "left"
        current_line = self._api.active_window().cursor[0] if self._active_compare_side() is not None else 0
        self._refresh_session(
            left_window,
            right_window,
            focus_side=focus_side,
            preferred_index=session.current_block_index or 0,
            fallback_line=current_line,
        )
        _set_status(
            self._api,
            f"Diff refreshed: {self._display_path(session.left_path)} ↔ {self._display_path(session.right_path)}",
        )

    def _capture_view(self) -> _PreDiffState:
        """Snapshot the active buffer path, cursor, and scroll for later restoration."""
        try:
            buf = self._api.active_buffer()
            path = Path(buf.path).resolve() if buf.path is not None else None
        except Exception:
            path = None
        try:
            win = self._api.active_window()
            cursor = win.cursor
            scroll_line = win.visible_range()[0]
        except Exception:
            cursor = (0, 0)
            scroll_line = 0
        return _PreDiffState(path=path, cursor=cursor, scroll_line=scroll_line)

    def _restore_view(self, state: _PreDiffState | None) -> None:
        """Collapse to a single window and restore the pre-diff buffer/cursor/scroll."""
        with contextlib.suppress(Exception):
            self._api.commands.execute("only")
        if state is None or state.path is None:
            return
        try:
            self._api.open_buffer(state.path)
            win = self._api.active_window()
            win.set_cursor(*state.cursor)
            win.set_scroll_line(state.scroll_line)
        except Exception:
            pass

    def _current_file(self) -> Path | None:
        buf = self._api.active_buffer()
        path = getattr(buf, "path", None)
        if path is None:
            return None
        return Path(path).resolve()

    def _display_path(self, path: Path) -> str:
        try:
            root = self._api.find_root()
        except Exception:
            root = None
        if root is not None:
            try:
                return str(path.relative_to(Path(root).resolve()))
            except ValueError:
                pass
        try:
            return str(path.relative_to(Path.cwd()))
        except ValueError:
            return str(path)

    def _clear_session_decorations(self) -> None:
        if self._session is None:
            return
        for buf_id in (self._session.left_buf_id, self._session.right_buf_id):
            buf = self._api.buffer_by_id(buf_id)
            if buf is None:
                continue
            buf.clear_namespace(_NAMESPACE)
            buf.clear_namespace(_HINT_NAMESPACE)
        self._session = None
        self._api.set_compare_status(None)

    def _normalized_selection_order(self, slot1: Path, slot2: Path) -> tuple[Path, Path]:
        windows = self._api.list_tab_windows()
        indexes: dict[Path, int] = {}
        for index, window in enumerate(windows):
            path = getattr(window.buffer(), "path", None)
            if path is None:
                continue
            indexes[Path(path).resolve()] = index

        slot1_index = indexes.get(slot1)
        slot2_index = indexes.get(slot2)
        if slot1_index is None or slot2_index is None or slot1_index <= slot2_index:
            return slot1, slot2

        self._debug(
            "normalize_selection_order",
            slot1=str(slot1),
            slot2=str(slot2),
            slot1_index=slot1_index,
            slot2_index=slot2_index,
        )
        return slot2, slot1

    def _jump_diff(self, *, direction: str) -> None:
        session = self._session
        if session is None or not session.blocks:
            _set_status(self._api, "No active diff")
            return

        side = self._active_compare_side()
        if side is None:
            _set_status(self._api, "Focus a diff window first")
            return

        current_line = self._api.active_window().cursor[0]
        active = self._active_block(side, current_line, preferred_index=session.current_block_index)
        current_index = active[0] if active is not None else session.current_block_index

        target_entry: tuple[int, CompareBlock] | None
        if current_index is not None:
            next_index = current_index + 1 if direction == "next" else current_index - 1
            target_entry = (next_index, session.blocks[next_index]) if 0 <= next_index < len(session.blocks) else None
        elif direction == "next":
            target_entry = next(
                (
                    (index, block)
                    for index, block in enumerate(session.blocks)
                    if block_anchor(block, side) > current_line
                ),
                None,
            )
        else:
            target_entry = next(
                (
                    (index, block)
                    for index, block in reversed(list(enumerate(session.blocks)))
                    if block_anchor(block, side) < current_line
                ),
                None,
            )

        if target_entry is None:
            _set_status(self._api, f"No {direction} diff block")
            return

        target_index, target = target_entry
        self._debug(
            "jump_diff",
            direction=direction,
            side=side,
            current_line=current_line,
            target_index=target_index,
            target=self._block_debug_summary(target),
        )

        left_window, right_window = self._session_windows()
        if left_window is None or right_window is None:
            _set_status(self._api, "Diff windows are no longer available")
            self._session = None
            return

        self._move_window_to_block(left_window, target, side="left")
        self._move_window_to_block(right_window, target, side="right")
        session.current_block_index = target_index
        self._activate_window(left_window if side == "left" else right_window)
        _set_status(
            self._api,
            f"Diff {direction}: {self._display_path(session.left_path)} ↔ {self._display_path(session.right_path)}",
        )

    def _merge_block(self, *, source_side: str, target_side: str) -> None:
        session = self._session
        if session is None or not session.blocks:
            _set_status(self._api, "No active diff")
            return

        active_side = self._active_compare_side()
        if active_side is None:
            _set_status(self._api, "Focus a diff window first")
            return

        current_line = self._api.active_window().cursor[0]
        active = self._active_block(active_side, current_line, preferred_index=session.current_block_index)
        if active is None:
            _set_status(self._api, "Cursor is not on a diff block")
            return
        block_index, block = active

        left_window, right_window = self._session_windows()
        if left_window is None or right_window is None:
            _set_status(self._api, "Diff windows are no longer available")
            self._session = None
            return

        source_window = left_window if source_side == "left" else right_window
        target_window = right_window if target_side == "right" else left_window
        source_buf = source_window.buffer()
        target_buf = target_window.buffer()

        source_lines = source_buf.get_lines()
        target_lines = target_buf.get_lines()
        replacement = _block_lines(block, source_lines, source_side)
        start, end = _block_range(block, target_side)
        merged_lines = target_lines[:start] + replacement + target_lines[end:]
        self._debug(
            "merge_block",
            source_side=source_side,
            target_side=target_side,
            active_side=active_side,
            current_line=current_line,
            block_index=block_index,
            block=self._block_debug_summary(block),
            replacement_count=len(replacement),
            target_range=f"{start}:{end}",
        )
        source_focus_line = _merged_focus_line(block, source_side, source_lines)
        replacement_focus_line = _replacement_focus_line(start, replacement)

        self._replace_buffer_lines(target_window, target_buf, merged_lines)
        self._refresh_session(
            left_window,
            right_window,
            focus_side=active_side,
            preferred_index=block_index,
            fallback_line=block_anchor(block, active_side),
            merged_left_line=(replacement_focus_line if target_side == "left" else source_focus_line),
            merged_right_line=(replacement_focus_line if target_side == "right" else source_focus_line),
        )
        self._debug("merge_block_done", session=self._session_debug_summary())
        arrow = "left → right" if source_side == "left" else "right → left"
        _set_status(self._api, f"Merged {arrow}")

    def debug_session(self) -> None:
        summary = self._session_debug_summary()
        self._debug("compare_debug", summary=summary)
        _set_status(self._api, summary)

    def _resolve_compare_windows(self, left_path: Path, right_path: Path) -> tuple[Any | None, Any | None]:
        left_window = None
        right_window = None
        for window in self._api.list_tab_windows():
            path = getattr(window.buffer(), "path", None)
            if path is None:
                continue
            resolved = Path(path).resolve()
            if resolved == left_path and left_window is None:
                left_window = window
            elif resolved == right_path and right_window is None:
                right_window = window
        return left_window, right_window

    def _session_windows(self) -> tuple[Any | None, Any | None]:
        session = self._session
        if session is None:
            return None, None
        return (
            self._api.window_by_id(session.left_window_id, active_tab_only=True),
            self._api.window_by_id(session.right_window_id, active_tab_only=True),
        )

    def _active_compare_side(self) -> str | None:
        session = self._session
        if session is None:
            return None
        active_id = self._api.active_window().win_id
        if active_id == session.left_window_id:
            return "left"
        if active_id == session.right_window_id:
            return "right"
        return None

    def _active_block(
        self, side: str, current_line: int, *, preferred_index: int | None = None
    ) -> tuple[int, CompareBlock] | None:
        session = self._session
        if session is None:
            return None
        if preferred_index is not None and 0 <= preferred_index < len(session.blocks):
            preferred = session.blocks[preferred_index]
            if block_is_active(preferred, side, current_line):
                return preferred_index, preferred
        for index, block in enumerate(session.blocks):
            if block_is_active(block, side, current_line):
                return index, block
        return None

    def _decorate_blocks(self, left_buf: Any, right_buf: Any, blocks: tuple[CompareBlock, ...]) -> None:
        left_buf.clear_namespace(_NAMESPACE)
        left_buf.clear_namespace(_HINT_NAMESPACE)
        right_buf.clear_namespace(_NAMESPACE)
        right_buf.clear_namespace(_HINT_NAMESPACE)

        for block in blocks:
            if block.kind in {"change", "delete"}:
                _highlight_lines(left_buf, block.left_start, block.left_end, block.kind, side="left")
            if block.kind in {"change", "insert"}:
                _highlight_lines(right_buf, block.right_start, block.right_end, block.kind, side="right")
            if block.kind == "insert":
                # Insert N blank virtual lines on the left (shorter side) to maintain visual alignment.
                left_buf.add_virtual_line(
                    _HINT_NAMESPACE,
                    max(-1, block.left_start - 1),
                    _HINT_STYLES["insert"],
                    count=block.right_count,
                )
            elif block.kind == "delete":
                # Insert N blank virtual lines on the right (shorter side) to maintain visual alignment.
                right_buf.add_virtual_line(
                    _HINT_NAMESPACE,
                    max(-1, block.right_start - 1),
                    _HINT_STYLES["delete"],
                    count=block.left_count,
                )

    def _activate_window(self, window: Any) -> None:
        self._api.activate_window(window)

    def _move_window_to_block(self, window: Any, block: CompareBlock, *, side: str) -> None:
        line = block_anchor(block, side)
        self._move_window_to_line(window, line)

    def _move_window_to_line(self, window: Any, line: int) -> None:
        target_line = min(line, max(0, window.buffer().line_count() - 1))
        window.set_cursor(target_line, 0)
        window.set_scroll_line(max(0, target_line - 2))
        window.scroll_to_cursor()

    def _replace_buffer_lines(self, window: Any, buf: Any, lines: list[str]) -> None:
        self._activate_window(window)
        last_line_index = max(0, buf.line_count() - 1)
        last_line_len = len(buf.get_line(last_line_index))
        buf.replace(0, 0, last_line_index, last_line_len, "\n".join(lines))

    def _refresh_session(
        self,
        left_window: Any,
        right_window: Any,
        *,
        focus_side: str,
        preferred_index: int,
        fallback_line: int,
        merged_left_line: int | None = None,
        merged_right_line: int | None = None,
    ) -> None:
        left_buf = left_window.buffer()
        right_buf = right_window.buffer()
        blocks = compute_blocks(left_buf.get_lines(), right_buf.get_lines())
        self._debug(
            "refresh_session",
            focus_side=focus_side,
            preferred_index=preferred_index,
            fallback_line=fallback_line,
            blocks=len(blocks),
        )
        self._decorate_blocks(left_buf, right_buf, blocks)

        if self._session is not None:
            self._session.blocks = blocks
            self._session.current_block_index = (
                None if merged_left_line is not None else (min(preferred_index, len(blocks) - 1) if blocks else None)
            )
            self._session.left_buf_id = left_buf.buf_id
            self._session.right_buf_id = right_buf.buf_id
            self._session.left_window_id = left_window.win_id
            self._session.right_window_id = right_window.win_id

        if merged_left_line is not None and merged_right_line is not None:
            self._move_window_to_line(
                left_window,
                min(merged_left_line, max(0, left_window.buffer().line_count() - 1)),
            )
            self._move_window_to_line(
                right_window,
                min(merged_right_line, max(0, right_window.buffer().line_count() - 1)),
            )

        elif blocks:
            block = blocks[min(preferred_index, len(blocks) - 1)]
            self._move_window_to_block(left_window, block, side="left")
            self._move_window_to_block(right_window, block, side="right")
        else:
            self._move_window_to_line(
                left_window,
                min(fallback_line, max(0, left_window.buffer().line_count() - 1)),
            )
            self._move_window_to_line(
                right_window,
                min(fallback_line, max(0, right_window.buffer().line_count() - 1)),
            )

        self._activate_window(left_window if focus_side == "left" else right_window)
        self._publish_statusline_state(left_window, right_window)

    def _session_debug_summary(self) -> str:
        session = self._session
        if session is None:
            return "DiffDebug: no active session"
        left_window, right_window = self._session_windows()
        active_side = self._active_compare_side()
        left_cursor = left_window.cursor[0] if left_window is not None else None
        right_cursor = right_window.cursor[0] if right_window is not None else None
        current_index = session.current_block_index
        current_block = (
            self._block_debug_summary(session.blocks[current_index])
            if current_index is not None and 0 <= current_index < len(session.blocks)
            else None
        )
        return (
            "DiffDebug: "
            f"left={self._display_path(session.left_path)} "
            f"right={self._display_path(session.right_path)} "
            f"blocks={len(session.blocks)} "
            f"current_index={current_index} "
            f"active_side={active_side} "
            f"left_cursor={left_cursor} "
            f"right_cursor={right_cursor} "
            f"current_block={current_block}"
        )

    def _block_debug_summary(self, block: CompareBlock) -> str:
        return f"{block.kind}(left={block.left_start}:{block.left_end},right={block.right_start}:{block.right_end})"

    def _debug(self, event: str, **fields: Any) -> None:
        if not log.isEnabledFor(logging.DEBUG):
            return
        detail = " ".join(f"{key}={value!r}" for key, value in fields.items())
        log.debug("compare.%s %s", event, detail)

    def _publish_statusline_state(self, left_window: Any, right_window: Any) -> None:
        session = self._session
        if session is None:
            self._api.set_compare_status(None)
            return
        self._api.set_compare_status(
            {
                "left": self._display_path(session.left_path),
                "right": self._display_path(session.right_path),
                "left_dirty": bool(left_window.buffer().is_modified()),
                "right_dirty": bool(right_window.buffer().is_modified()),
                "blocks": len(session.blocks),
                "active_side": self._active_compare_side(),
            }
        )

    def _align_to_first_block(self, left_window: Any, right_window: Any, blocks: tuple[CompareBlock, ...]) -> None:
        if not blocks:
            left_window.set_cursor(0, 0)
            right_window.set_cursor(0, 0)
            left_window.set_scroll_line(0)
            right_window.set_scroll_line(0)
            left_window.scroll_to_cursor()
            right_window.scroll_to_cursor()
            return

        first = blocks[0]
        left_line = first.left_start if first.left_count else max(0, first.left_start - 1)
        right_line = first.right_start if first.right_count else max(0, first.right_start - 1)
        self._move_window_to_line(left_window, left_line)
        self._move_window_to_line(right_window, right_line)
        self._activate_window(left_window)


def setup(api: EditorAPI) -> None:
    """Register diff selection keymaps and commands."""
    global _controller
    _controller = _CompareController(api)

    from peovim.core.style import Style

    for name, (char, color) in SIGN_DEFS.items():
        api.register_sign_type(name, char, Style(fg=color))

    api.keymap.define_plug("CompareSelect1", lambda: _controller.select_slot(1), desc="Diff: select slot 1")
    api.keymap.define_plug("CompareSelect2", lambda: _controller.select_slot(2), desc="Diff: select slot 2")
    api.keymap.define_plug("CompareSelected", lambda: _controller.compare_selected(), desc="Diff: launch selected")
    api.keymap.define_plug("CompareNextDiff", lambda: _controller.next_diff(), desc="Diff: next diff")
    api.keymap.define_plug("ComparePrevDiff", lambda: _controller.prev_diff(), desc="Diff: previous diff")
    api.keymap.define_plug("CompareStop", lambda: _controller.stop_compare(), desc="Diff: stop")
    api.keymap.define_plug(
        "CompareMerge12", lambda: _controller.merge_left_to_right(), desc="Diff: merge left to right"
    )
    api.keymap.define_plug(
        "CompareMerge21", lambda: _controller.merge_right_to_left(), desc="Diff: merge right to left"
    )
    api.keymap.define_plug("DiffSelect1", lambda: _controller.select_slot(1), desc="Diff: select slot 1")
    api.keymap.define_plug("DiffSelect2", lambda: _controller.select_slot(2), desc="Diff: select slot 2")
    api.keymap.define_plug("DiffSelected", lambda: _controller.compare_selected(), desc="Diff: launch selected")
    api.keymap.define_plug("DiffNext", lambda: _controller.next_diff(), desc="Diff: next diff")
    api.keymap.define_plug("DiffPrev", lambda: _controller.prev_diff(), desc="Diff: previous diff")
    api.keymap.define_plug("DiffStop", lambda: _controller.stop_compare(), desc="Diff: stop")
    api.keymap.define_plug("DiffMerge12", lambda: _controller.merge_left_to_right(), desc="Diff: merge left to right")
    api.keymap.define_plug("DiffMerge21", lambda: _controller.merge_right_to_left(), desc="Diff: merge right to left")

    api.keymap.nmap("<leader>c1", "<Plug>CompareSelect1", desc="Diff: select file 1")
    api.keymap.nmap("<leader>c2", "<Plug>CompareSelect2", desc="Diff: select file 2")
    api.keymap.nmap("<leader>cc", "<Plug>CompareSelected", desc="Diff: launch selected")
    api.keymap.nmap("]c", "<Plug>CompareNextDiff", desc="Diff: next diff block")
    api.keymap.nmap("[c", "<Plug>ComparePrevDiff", desc="Diff: previous diff block")
    api.keymap.nmap("<leader>cj", "<Plug>CompareNextDiff", desc="Diff: next diff block")
    api.keymap.nmap("<leader>ck", "<Plug>ComparePrevDiff", desc="Diff: previous diff block")
    api.keymap.nmap("<leader>cs", "<Plug>CompareStop", desc="Diff: stop")
    api.keymap.nmap("<leader>m12", "<Plug>CompareMerge12", desc="Diff: merge left to right")
    api.keymap.nmap("<leader>m21", "<Plug>CompareMerge21", desc="Diff: merge right to left")

    api.commands.register("CompareSelect1", lambda cmd, ctx: _controller.select_slot(1), min_abbrev=10)
    api.commands.register("CompareSelect2", lambda cmd, ctx: _controller.select_slot(2), min_abbrev=10)
    api.commands.register("Compare", lambda cmd, ctx: _controller.compare_selected(), min_abbrev=3)
    api.commands.register("CompareNext", lambda cmd, ctx: _controller.next_diff(), min_abbrev=11)
    api.commands.register("ComparePrev", lambda cmd, ctx: _controller.prev_diff(), min_abbrev=11)
    api.commands.register("CompareStop", lambda cmd, ctx: _controller.stop_compare(), min_abbrev=11)
    api.commands.register("CompareDebug", lambda cmd, ctx: _controller.debug_session(), min_abbrev=12)
    api.commands.register("CompareMerge12", lambda cmd, ctx: _controller.merge_left_to_right(), min_abbrev=13)
    api.commands.register("CompareMerge21", lambda cmd, ctx: _controller.merge_right_to_left(), min_abbrev=13)
    api.commands.register("DiffSelect1", lambda cmd, ctx: _controller.select_slot(1), min_abbrev=7)
    api.commands.register("DiffSelect2", lambda cmd, ctx: _controller.select_slot(2), min_abbrev=7)
    api.commands.register("Diff", lambda cmd, ctx: _controller.compare_selected(), min_abbrev=2)
    api.commands.register("DiffNext", lambda cmd, ctx: _controller.next_diff(), min_abbrev=8)
    api.commands.register("DiffPrev", lambda cmd, ctx: _controller.prev_diff(), min_abbrev=8)
    api.commands.register("DiffStop", lambda cmd, ctx: _controller.stop_compare(), min_abbrev=8)
    api.commands.register("DiffDebug", lambda cmd, ctx: _controller.debug_session(), min_abbrev=9)
    api.commands.register("DiffMerge12", lambda cmd, ctx: _controller.merge_left_to_right(), min_abbrev=10)
    api.commands.register("DiffMerge21", lambda cmd, ctx: _controller.merge_right_to_left(), min_abbrev=10)
    api.events.on("compare_selection_ready", lambda **kwargs: _controller.open_selected_compare(**kwargs))
    api.events.on("diff_selection_ready", lambda **kwargs: _controller.open_selected_compare(**kwargs))
    api.events.on("buffer_saved", lambda **kwargs: _controller.on_buffer_saved(**kwargs))
    api.events.on("cursor_moved", lambda **kwargs: _controller.on_cursor_moved(**kwargs))


def _block_visual_rows(block: CompareBlock) -> int:
    """Number of visual rows a diff block occupies on BOTH sides (equal due to virtual lines)."""
    if block.kind == "insert":
        return block.right_count
    return block.left_count  # "change" (left==right) and "delete"


def _buffer_line_to_visual_row(line: int, blocks: tuple[CompareBlock, ...], side: str) -> int:
    """Convert a buffer line index on `side` to its visual row position."""
    visual_pos = 0
    buf_pos = 0
    for block in blocks:
        buf_start = block.left_start if side == "left" else block.right_start
        buf_end = block.left_end if side == "left" else block.right_end

        if line < buf_start:
            return visual_pos + (line - buf_pos)
        visual_pos += buf_start - buf_pos

        block_buf_count = buf_end - buf_start
        if block_buf_count > 0 and line < buf_end:
            return visual_pos + (line - buf_start)

        visual_pos += _block_visual_rows(block)
        buf_pos = buf_end

    return visual_pos + (line - buf_pos)


def _visual_row_to_buffer_line(visual_row: int, blocks: tuple[CompareBlock, ...], side: str) -> int:
    """Convert a visual row index to the buffer line that should be at the top of the viewport.

    For visual rows occupied by virtual lines (no buffer content on `side`), returns the
    anchor buffer line so the viewport scroll makes those virtual rows visible.
    """
    vis_pos = 0
    buf_pos = 0
    for block in blocks:
        buf_start = block.left_start if side == "left" else block.right_start
        buf_end = block.left_end if side == "left" else block.right_end

        equal_vis = buf_start - buf_pos
        if visual_row < vis_pos + equal_vis:
            return buf_pos + (visual_row - vis_pos)
        vis_pos += equal_vis

        block_vis = _block_visual_rows(block)
        if visual_row < vis_pos + block_vis:
            block_buf_count = buf_end - buf_start
            if block_buf_count > 0:
                return buf_start + min(visual_row - vis_pos, block_buf_count - 1)
            # Virtual lines on this side — scroll to anchor so they are visible.
            return max(0, buf_start - 1)

        vis_pos += block_vis
        buf_pos = buf_end

    return buf_pos + (visual_row - vis_pos)


def _map_scroll_line(line: int, blocks: tuple[CompareBlock, ...], *, from_side: str) -> int:
    """Map a viewport-top buffer line on one diff side to the equivalent buffer line on the other.

    Uses visual-row accounting so equal content stays aligned even when the two sides
    have different line counts due to insert/delete blocks.  Virtual lines on the
    shorter side are counted as visual rows, maintaining 1:1 visual alignment.
    """
    to_side = "right" if from_side == "left" else "left"
    vis = _buffer_line_to_visual_row(line, blocks, from_side)
    return _visual_row_to_buffer_line(vis, blocks, to_side)


def compute_blocks(left_lines: list[str], right_lines: list[str]) -> tuple[CompareBlock, ...]:
    matcher = SequenceMatcher(a=left_lines, b=right_lines, autojunk=False)
    blocks: list[CompareBlock] = []
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            left_count = left_end - left_start
            right_count = right_end - right_start
            shared_count = min(left_count, right_count)
            if shared_count:
                blocks.append(
                    CompareBlock(
                        kind="change",
                        left_start=left_start,
                        left_end=left_start + shared_count,
                        right_start=right_start,
                        right_end=right_start + shared_count,
                    )
                )
            if right_count > shared_count:
                blocks.append(
                    CompareBlock(
                        kind="insert",
                        left_start=left_start + shared_count,
                        left_end=left_start + shared_count,
                        right_start=right_start + shared_count,
                        right_end=right_end,
                    )
                )
            elif left_count > shared_count:
                blocks.append(
                    CompareBlock(
                        kind="delete",
                        left_start=left_start + shared_count,
                        left_end=left_end,
                        right_start=right_start + shared_count,
                        right_end=right_start + shared_count,
                    )
                )
            continue

        blocks.append(
            CompareBlock(
                kind=tag,
                left_start=left_start,
                left_end=left_end,
                right_start=right_start,
                right_end=right_end,
            )
        )
    return tuple(blocks)


def _highlight_lines(buf: Any, start: int, end: int, kind: str, *, side: str) -> None:
    from peovim.core.style import Style

    sign_name = f"compare.{kind}"
    style = Style(bg=_BLOCK_STYLES[(kind, side)])
    for line in range(start, end):
        buf.add_sign(_NAMESPACE, line, sign_name)
        buf.add_highlight(_NAMESPACE, line, 0, line, 0x7FFFFFFF, style)


def _anchor_line(buf: Any, preferred: int) -> int:
    line_count = max(1, buf.line_count())
    return max(0, min(preferred, line_count - 1))


def _line_label(count: int) -> str:
    suffix = "line" if count == 1 else "lines"
    return f"{count} {suffix}"


def block_anchor(block: CompareBlock, side: str) -> int:
    start = block.left_start if side == "left" else block.right_start
    count = block.left_count if side == "left" else block.right_count
    return start if count else max(0, start - 1)


def _block_range(block: CompareBlock, side: str) -> tuple[int, int]:
    if side == "left":
        return block.left_start, block.left_end
    return block.right_start, block.right_end


def _block_lines(block: CompareBlock, lines: list[str], side: str) -> list[str]:
    start, end = _block_range(block, side)
    return lines[start:end]


def block_is_active(block: CompareBlock, side: str, current_line: int) -> bool:
    start, end = _block_range(block, side)
    if start < end:
        return start <= current_line < end
    return block_anchor(block, side) == current_line


def _merged_focus_line(block: CompareBlock, side: str, lines: list[str]) -> int:
    start, end = _block_range(block, side)
    if start >= end:
        return max(0, start - 1)
    for offset, line in enumerate(lines[start:end]):
        if line.strip():
            return start + offset
    return start


def _replacement_focus_line(start: int, replacement: list[str]) -> int:
    if not replacement:
        return max(0, start - 1)
    for offset, line in enumerate(replacement):
        if line.strip():
            return start + offset
    return start


def _set_status(api: Any, message: str) -> None:
    api.set_status(message)
