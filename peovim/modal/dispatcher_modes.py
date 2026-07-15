"""
modal.dispatcher_modes — mode-transition, cursor-scroll, and history action handlers.

Each function has signature (d, action, doc, cur) → None.
Registered in ActionDispatcher._action_handlers via dispatcher.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from peovim.modal.actions import (
    CompoundAction,
    EnterCommandMode,
    EnterInsertMode,
    EnterNormalMode,
    EnterReplaceMode,
    EnterVisualMode,
    InsertText,
    MoveCursor,
    Redo,
    ScrollToCursor,
    ScrollView,
    Undo,
)
from peovim.modal.engine import Mode

if TYPE_CHECKING:
    from peovim.core.cursor import Cursor
    from peovim.core.document import Document
    from peovim.modal.dispatcher import ActionDispatcher


def handle_enter_insert_mode(d: ActionDispatcher, action: EnterInsertMode, doc: Document, cur: Cursor) -> None:
    from peovim.modal.dispatcher import _InsertSession

    d.engine.set_mode(Mode.INSERT)
    if not d._insert_compound_open:
        doc.begin_compound()
        d._insert_compound_open = True
    if d._pending_block_insert is not None:
        d._prepare_block_insert_source(doc, cur)
        d.engine.set_block_insert_col(d._pending_block_insert.col)
        d._insert_session = None
    elif action.position == "after_cursor":
        line_len = len(doc.get_line(cur.line))
        cur.move_to(cur.line, min(cur.col + 1, line_len))
    elif action.position == "line_start":
        line_text = doc.get_line(cur.line)
        indent = len(line_text) - len(line_text.lstrip())
        cur.move_to(cur.line, indent)
    elif action.position == "line_end":
        cur.move_to(cur.line, len(doc.get_line(cur.line)))
    elif action.position == "new_line_below":
        line = cur.line
        doc.insert(line, len(doc.get_line(line)), "\n")
        cur.move_to(line + 1, 0)
    elif action.position == "new_line_above":
        line = cur.line
        doc.insert(line, 0, "\n")
        cur.move_to(line, 0)
    elif action.position == "col_1":
        cur.move_to(cur.line, 0)
    if d._insert_session is None and d._pending_block_insert is None:
        d._insert_session = _InsertSession(start_line=cur.line, start_col=cur.col)
    d._emit_later("insert_entered", buf_id=d._buf_id)


def handle_enter_normal_mode(d: ActionDispatcher, action: EnterNormalMode, doc: Document, cur: Cursor) -> None:
    d.engine.set_mode(Mode.NORMAL)
    d._clamp_cursor_for_mode(doc)
    if d._insert_compound_open:
        d._replay_pending_block_insert(doc)
        doc.end_compound()
        d._insert_compound_open = False
        d._emit_later("insert_left", buf_id=d._buf_id)
    d._pending_block_insert = None
    d.engine.set_block_insert_col(None)
    if d._insert_session is not None:
        sess = d._insert_session
        if sess.simple and sess.text:
            d._dot_repeat = InsertText(sess.start_line, sess.start_col, sess.text)
        d._insert_session = None
    d._emit_later("mode_changed", mode="normal")


def handle_enter_visual_mode(d: ActionDispatcher, action: EnterVisualMode, doc: Document, cur: Cursor) -> None:
    mode_map = {"char": Mode.VISUAL_CHAR, "line": Mode.VISUAL_LINE, "block": Mode.VISUAL_BLOCK}
    d.engine.set_mode(mode_map[action.mode])
    cur = d.window.cursor
    d.engine.set_visual_anchor(cur.line, cur.col)


def handle_enter_command_mode(d: ActionDispatcher, action: EnterCommandMode, doc: Document, cur: Cursor) -> None:
    d.engine.set_mode(Mode.COMMAND)


def handle_enter_replace_mode(d: ActionDispatcher, action: EnterReplaceMode, doc: Document, cur: Cursor) -> None:
    d.engine.set_mode(Mode.REPLACE)


def handle_undo(d: ActionDispatcher, action: Undo, doc: Document, cur: Cursor) -> None:
    last_pos: tuple[int, int] | None = None
    for _ in range(action.count):
        pos = doc.undo()
        if pos is not None:
            last_pos = pos
    if last_pos is not None:
        cur.move_to(last_pos[0], last_pos[1])
    d._clamp_cursor_for_mode(doc)
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_redo(d: ActionDispatcher, action: Redo, doc: Document, cur: Cursor) -> None:
    last_pos2: tuple[int, int] | None = None
    for _ in range(action.count):
        pos2 = doc.redo()
        if pos2 is not None:
            last_pos2 = pos2
    if last_pos2 is not None:
        cur.move_to(last_pos2[0], last_pos2[1])
    d._clamp_cursor_for_mode(doc)
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_compound_action(d: ActionDispatcher, action: CompoundAction, doc: Document, cur: Cursor) -> None:
    # Save cursor before compound so plugin batch ops (commentary, etc.)
    # don't strand the cursor at an arbitrary intermediate position.
    saved_line, saved_col = cur.line, cur.col
    with doc.compound_edit():
        for sub in action.actions:
            d._apply(sub)
    saved_line = min(saved_line, max(0, doc.line_count() - 1))
    line_len = len(doc.get_line(saved_line))
    saved_col = min(saved_col, max(0, line_len - 1)) if line_len > 0 else 0
    cur.move_to(saved_line, saved_col)


def handle_move_cursor(d: ActionDispatcher, action: MoveCursor, doc: Document, cur: Cursor) -> None:
    prev_line, prev_col = cur.line, cur.col
    line = max(0, min(action.line, doc.line_count() - 1))
    if action.add_to_jumplist and d.jumplist is not None and (prev_line != line or prev_col != action.col):
        d.jumplist.push(
            prev_line,
            prev_col,
            str(doc.path) if doc.path else "",
            d.window.scroll_line,
        )
    cur.move_to(line, action.col)
    d._clamp_cursor_for_mode(doc)
    if cur.line != prev_line or cur.col != prev_col:
        d._emit_later("cursor_moved", buf_id=d._buf_id, line=cur.line, col=cur.col)


def handle_scroll_view(d: ActionDispatcher, action: ScrollView, doc: Document, cur: Cursor) -> None:
    lc = doc.line_count()
    d.window.follow_cursor = True
    d.window.scroll_line = max(0, min(d.window.scroll_line + action.lines, lc - 1))
    vis_start = d.window.scroll_line
    vis_end = vis_start + d.window.height - 1
    cur.move_to(max(vis_start, min(cur.line, vis_end)), cur.col)
    d._clamp_cursor_for_mode(doc)
    d._emit_later("cursor_moved", buf_id=d._buf_id, line=cur.line, col=cur.col)


def handle_scroll_to_cursor(d: ActionDispatcher, action: ScrollToCursor, doc: Document, cur: Cursor) -> None:
    d.window.follow_cursor = True
    line = cur.line
    if action.position == "top":
        d.window.scroll_line = line
    elif action.position == "bottom":
        d.window.scroll_line = max(0, line - d.window.height + 1)
    else:
        d.window.scroll_line = max(0, line - d.window.height // 2)
