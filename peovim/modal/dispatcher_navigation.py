from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from peovim.modal.actions import JumpBack, JumpForward, JumpToMark, SetMark
from peovim.modal.dispatcher_buffers import open_path_in_window

if TYPE_CHECKING:
    from peovim.core.cursor import Cursor
    from peovim.core.document import Document
    from peovim.modal.dispatcher import ActionDispatcher


def _move_to_jump_target(
    dispatcher: ActionDispatcher,
    doc: Document,
    cur: Cursor,
    path: str,
    line: int,
    col: int,
    scroll_line: int = 0,
) -> None:
    current_path = str(doc.path) if doc.path else ""
    if path and path != current_path:
        target_doc = open_path_in_window(dispatcher, Path(path), doc, cur, push_jumplist=False)
        if target_doc is None:
            return
        doc = target_doc
    cur.move_to(line, col)
    cur.clamp(doc._table)
    # Restore the saved viewport, then ensure cursor is actually visible
    # (scroll_to_cursor is a no-op if cursor already falls within the window).
    dispatcher.window.scroll_line = max(0, scroll_line)
    dispatcher.window.scroll_to_cursor(center=True)


def handle_navigation_action(dispatcher: ActionDispatcher, action: object, doc: Document, cur: Cursor) -> bool:
    if isinstance(action, SetMark):
        if dispatcher.marks is not None:
            dispatcher.marks.set(action.name, cur.line, cur.col)
        return True

    if isinstance(action, JumpToMark):
        if dispatcher.marks is not None:
            mark_pos = dispatcher.marks.get(action.name)
            if mark_pos is not None:
                target_line, target_col = mark_pos
                if action.line_only:
                    text = doc.get_line(target_line)
                    stripped = text.lstrip()
                    target_col = len(text) - len(stripped)
                cur.move_to(target_line, target_col)
                cur.clamp(doc._table)
        return True

    if isinstance(action, JumpBack):
        if dispatcher.jumplist is not None:
            pos: tuple[str, int, int, int] | None = None
            for _ in range(action.count):
                pos = dispatcher.jumplist.back()
                if pos is None:
                    break
            if pos is not None:
                _move_to_jump_target(dispatcher, doc, cur, *pos)
        return True

    if isinstance(action, JumpForward):
        if dispatcher.jumplist is not None:
            fwd_pos: tuple[str, int, int, int] | None = None
            for _ in range(action.count):
                fwd_pos = dispatcher.jumplist.forward()
                if fwd_pos is None:
                    break
            if fwd_pos is not None:
                _move_to_jump_target(dispatcher, doc, cur, *fwd_pos)
        return True

    return False
