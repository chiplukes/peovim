"""Review generated edits as current text versus proposed future text."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from peovim.plugins.compare import (
    SIGN_DEFS,
    block_anchor,
    block_is_active,
    compute_blocks,
)

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

_controller: ProposedEditReviewController | None = None
_NAMESPACE = "proposed_review"
_HINT_NAMESPACE = "proposed_review.hints"
_BLOCK_STYLES: dict[tuple[str, str], tuple[int, int, int]] = {
    ("change", "left"): (97, 74, 26),
    ("change", "right"): (70, 84, 34),
    ("insert", "right"): (70, 84, 34),
    ("delete", "left"): (85, 34, 46),
}


@dataclass(frozen=True)
class ProposedEditReview:
    title: str
    current_label: str
    proposed_label: str
    current_text: str
    proposed_text: str
    filetype: str = ""
    file_path: str = ""
    on_confirm: Callable[[], None] | None = None

    def __str__(self) -> str:
        return f"{self.title}: {self.proposed_label}"


@dataclass
class _PreReviewState:
    path: Any
    cursor: tuple[int, int]
    scroll_line: int


@dataclass
class _ReviewSession:
    title: str
    current_label: str
    proposed_label: str
    blocks: tuple[Any, ...]
    current_block_index: int | None
    current_buf_id: int
    proposed_buf_id: int
    current_window_id: int
    proposed_window_id: int
    on_confirm: Callable[[], None] | None


class ProposedEditReviewController:
    def __init__(self, api: EditorAPI) -> None:
        self._api = api
        self._session: _ReviewSession | None = None
        self._pre_review_state: _PreReviewState | None = None
        self._reviews: tuple[ProposedEditReview, ...] = ()

    def open_reviews(
        self,
        reviews: list[ProposedEditReview] | tuple[ProposedEditReview, ...],
        *,
        initial_review: ProposedEditReview | None = None,
    ) -> None:
        self._reviews = tuple(reviews)
        if not self._reviews:
            _set_status(self._api, "No proposed edit files to review")
            return
        if initial_review is not None:
            for review in self._reviews:
                if review == initial_review:
                    self.open_review(review)
                    return
        if len(self._reviews) == 1:
            self.open_review(self._reviews[0])
            return

        def _on_confirm(review: ProposedEditReview | None) -> None:
            if review is not None:
                self.open_review(review)

        self._api.ui.open_picker(
            title=f"Proposed edit files ({len(self._reviews)})",
            source=list(self._reviews),
            on_confirm=_on_confirm,
            preview=_review_picker_preview,
        )
        _set_status(self._api, f"Select one of {len(self._reviews)} proposed edit files to review")

    def open_review(self, review: ProposedEditReview) -> None:
        self._pre_review_state = self._capture_view()
        self._clear_session_decorations()
        self._api.commands.execute("only")

        self._api.open_scratch_buffer(review.current_text, filetype=review.filetype, name=review.current_label)
        current_window = self._api.active_window()
        self._api.commands.execute("vsplit")
        self._api.open_scratch_buffer(review.proposed_text, filetype=review.filetype, name=review.proposed_label)
        proposed_window = self._api.active_window()

        current_buf = current_window.buffer()
        proposed_buf = proposed_window.buffer()
        blocks = compute_blocks(current_buf.get_lines(), proposed_buf.get_lines())
        self._decorate_blocks(current_buf, proposed_buf, blocks)
        self._session = _ReviewSession(
            title=review.title,
            current_label=review.current_label,
            proposed_label=review.proposed_label,
            blocks=blocks,
            current_block_index=0 if blocks else None,
            current_buf_id=current_buf.buf_id,
            proposed_buf_id=proposed_buf.buf_id,
            current_window_id=current_window.win_id,
            proposed_window_id=proposed_window.win_id,
            on_confirm=review.on_confirm,
        )
        self._align_to_first_block(current_window, proposed_window, blocks)
        self._publish_statusline_state()
        _set_status(self._api, f"Review: {review.title} ({len(blocks)} change blocks)")

    def confirm(self) -> None:
        session = self._session
        if session is None:
            _set_status(self._api, "No active proposed edit review")
            return
        on_confirm = session.on_confirm
        title = session.title
        self.cancel(status=f"Accepted proposed edit: {title}")
        if on_confirm is not None:
            on_confirm()

    def cancel(self, *, status: str = "Proposed edit review cancelled") -> None:
        self._clear_session_decorations()
        pre = self._pre_review_state
        self._pre_review_state = None
        self._restore_view(pre)
        _set_status(self._api, status)

    def next_change(self) -> None:
        self._jump_change(direction="next")

    def prev_change(self) -> None:
        self._jump_change(direction="prev")

    def session_summary(self) -> dict[str, Any] | None:
        session = self._session
        if session is None:
            return None
        return {
            "title": session.title,
            "current": session.current_label,
            "proposed": session.proposed_label,
            "blocks": len(session.blocks),
        }

    def pick_file(self) -> None:
        if not self._reviews:
            _set_status(self._api, "No proposed edit file list is active")
            return
        self.open_reviews(self._reviews)

    def _capture_view(self) -> _PreReviewState:
        try:
            buf = self._api.active_buffer()
            path = buf.path
        except Exception:
            path = None
        try:
            win = self._api.active_window()
            return _PreReviewState(path=path, cursor=win.cursor, scroll_line=win.visible_range()[0])
        except Exception:
            return _PreReviewState(path=path, cursor=(0, 0), scroll_line=0)

    def _restore_view(self, state: _PreReviewState | None) -> None:
        with contextlib.suppress(Exception):
            self._api.commands.execute("only")
        if state is None or state.path is None:
            return
        try:
            self._api.open_buffer(state.path, line=state.cursor[0], col=state.cursor[1])
            self._api.active_window().set_scroll_line(state.scroll_line)
        except Exception:
            pass

    def _clear_session_decorations(self) -> None:
        session = self._session
        if session is not None:
            for buf_id in (session.current_buf_id, session.proposed_buf_id):
                buf = self._api.buffer_by_id(buf_id)
                if buf is None:
                    continue
                buf.clear_namespace(_NAMESPACE)
                buf.clear_namespace(_HINT_NAMESPACE)
        self._session = None
        self._api.set_compare_status(None)

    def _session_windows(self) -> tuple[Any | None, Any | None]:
        session = self._session
        if session is None:
            return None, None
        return (
            self._api.window_by_id(session.current_window_id, active_tab_only=True),
            self._api.window_by_id(session.proposed_window_id, active_tab_only=True),
        )

    def _active_side(self) -> str | None:
        session = self._session
        if session is None:
            return None
        active_id = self._api.active_window().win_id
        if active_id == session.current_window_id:
            return "left"
        if active_id == session.proposed_window_id:
            return "right"
        return None

    def _jump_change(self, *, direction: str) -> None:
        session = self._session
        if session is None or not session.blocks:
            _set_status(self._api, "No active proposed edit review")
            return
        side = self._active_side() or "left"
        current_line = self._api.active_window().cursor[0]
        current_index = session.current_block_index
        if current_index is not None:
            next_index = current_index + 1 if direction == "next" else current_index - 1
            if not 0 <= next_index < len(session.blocks):
                _set_status(self._api, f"No {direction} proposed edit block")
                return
        else:
            next_index = 0
            for index, block in enumerate(session.blocks):
                if block_is_active(block, side, current_line):
                    next_index = index
                    break

        current_window, proposed_window = self._session_windows()
        if current_window is None or proposed_window is None:
            _set_status(self._api, "Proposed edit review windows are no longer available")
            self._session = None
            return
        block = session.blocks[next_index]
        self._move_window_to_block(current_window, block, side="left")
        self._move_window_to_block(proposed_window, block, side="right")
        session.current_block_index = next_index
        self._api.activate_window(current_window if side == "left" else proposed_window)
        _set_status(self._api, f"Review {direction}: {session.title}")

    def _decorate_blocks(self, current_buf: Any, proposed_buf: Any, blocks: tuple[Any, ...]) -> None:
        current_buf.clear_namespace(_NAMESPACE)
        current_buf.clear_namespace(_HINT_NAMESPACE)
        proposed_buf.clear_namespace(_NAMESPACE)
        proposed_buf.clear_namespace(_HINT_NAMESPACE)
        for block in blocks:
            if block.kind in {"change", "delete"}:
                _highlight_review_lines(current_buf, block.left_start, block.left_end, block.kind, side="left")
            if block.kind in {"change", "insert"}:
                _highlight_review_lines(proposed_buf, block.right_start, block.right_end, block.kind, side="right")
            if block.kind == "insert":
                current_buf.add_virtual_line(
                    _HINT_NAMESPACE,
                    max(-1, block.left_start - 1),
                    (140, 214, 122),
                    count=block.right_count,
                )
            elif block.kind == "delete":
                proposed_buf.add_virtual_line(
                    _HINT_NAMESPACE,
                    max(-1, block.right_start - 1),
                    (244, 143, 177),
                    count=block.left_count,
                )

    def _align_to_first_block(self, current_window: Any, proposed_window: Any, blocks: tuple[Any, ...]) -> None:
        if not blocks:
            current_window.set_cursor(0, 0)
            proposed_window.set_cursor(0, 0)
            return
        first = blocks[0]
        self._move_window_to_block(current_window, first, side="left")
        self._move_window_to_block(proposed_window, first, side="right")
        self._api.activate_window(current_window)

    def _move_window_to_block(self, window: Any, block: Any, *, side: str) -> None:
        line = block_anchor(block, side)
        target_line = min(line, max(0, window.buffer().line_count() - 1))
        window.set_cursor(target_line, 0)
        window.set_scroll_line(max(0, target_line - 2))
        window.scroll_to_cursor()

    def _publish_statusline_state(self) -> None:
        session = self._session
        if session is None:
            self._api.set_compare_status(None)
            return
        self._api.set_compare_status(
            {
                "left": session.current_label,
                "right": session.proposed_label,
                "left_dirty": False,
                "right_dirty": False,
                "blocks": len(session.blocks),
                "active_side": self._active_side(),
            }
        )


def setup(api: EditorAPI) -> None:
    global _controller
    _controller = ProposedEditReviewController(api)

    from peovim.core.style import Style

    for name, (char, color) in SIGN_DEFS.items():
        api.register_sign_type(name, char, Style(fg=color))

    api.keymap.define_plug("ProposedReviewConfirm", lambda: _controller.confirm(), desc="Review: accept proposed edit")
    api.keymap.define_plug("ProposedReviewCancel", lambda: _controller.cancel(), desc="Review: cancel proposed edit")
    api.keymap.define_plug("ProposedReviewNext", lambda: _controller.next_change(), desc="Review: next change")
    api.keymap.define_plug("ProposedReviewPrev", lambda: _controller.prev_change(), desc="Review: previous change")
    api.keymap.define_plug("ProposedReviewFiles", lambda: _controller.pick_file(), desc="Review: choose file")
    api.keymap.nmap("<leader>ra", "<Plug>ProposedReviewConfirm", desc="Review: accept proposed edit")
    api.keymap.nmap("<leader>rq", "<Plug>ProposedReviewCancel", desc="Review: cancel proposed edit")
    api.keymap.nmap("<leader>rf", "<Plug>ProposedReviewFiles", desc="Review: choose proposed edit file")
    api.keymap.nmap("]r", "<Plug>ProposedReviewNext", desc="Review: next proposed edit block")
    api.keymap.nmap("[r", "<Plug>ProposedReviewPrev", desc="Review: previous proposed edit block")
    api.commands.register("ProposedReviewAccept", lambda cmd, ctx: _controller.confirm(), min_abbrev=16)
    api.commands.register("ProposedReviewCancel", lambda cmd, ctx: _controller.cancel(), min_abbrev=16)
    api.commands.register("ProposedReviewNext", lambda cmd, ctx: _controller.next_change(), min_abbrev=14)
    api.commands.register("ProposedReviewPrev", lambda cmd, ctx: _controller.prev_change(), min_abbrev=14)
    api.commands.register("ProposedReviewFiles", lambda cmd, ctx: _controller.pick_file(), min_abbrev=15)


def open_proposed_edit(api: EditorAPI, review: ProposedEditReview) -> None:
    open_proposed_edits(api, [review])


def open_proposed_edits(api: EditorAPI, reviews: list[ProposedEditReview] | tuple[ProposedEditReview, ...]) -> None:
    global _controller
    if _controller is None:
        setup(api)
    assert _controller is not None
    _controller.open_reviews(reviews)


def open_proposed_edits_with_initial(
    api: EditorAPI,
    reviews: list[ProposedEditReview] | tuple[ProposedEditReview, ...],
    *,
    initial_review: ProposedEditReview,
) -> None:
    global _controller
    if _controller is None:
        setup(api)
    assert _controller is not None
    _controller.open_reviews(reviews, initial_review=initial_review)


def _set_status(api: EditorAPI, message: str) -> None:
    api.set_status(message, notify=False)


def _highlight_review_lines(buf: Any, start: int, end: int, kind: str, *, side: str) -> None:
    from peovim.core.style import Style

    sign_name = f"compare.{kind}"
    style = Style(bg=_BLOCK_STYLES[(kind, side)])
    for line in range(start, end):
        buf.add_sign(_NAMESPACE, line, sign_name)
        buf.add_highlight(_NAMESPACE, line, 0, line, 0x7FFFFFFF, style)


def _review_picker_preview(review: ProposedEditReview) -> list[str]:
    current_lines = review.current_text.splitlines()
    proposed_lines = review.proposed_text.splitlines()
    blocks = compute_blocks(current_lines, proposed_lines)
    return [
        f"Title:    {review.title}",
        f"Current:  {review.current_label}",
        f"Proposed: {review.proposed_label}",
        f"Blocks:   {len(blocks)}",
        f"Lines:    {len(current_lines)} -> {len(proposed_lines)}",
        "",
        "Open this file to review current vs proposed text.",
    ]
