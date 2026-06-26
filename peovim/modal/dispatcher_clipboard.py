"""
modal.dispatcher_clipboard — yank, paste, and block-insert action handlers.

Each function has signature (d, action, doc, cur) → None.
Registered in ActionDispatcher._action_handlers via dispatcher.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from peovim.modal.actions import (
    BeginBlockInsert,
    PasteRegister,
    RepeatBlockInsert,
    YankBlock,
    YankLine,
    YankRange,
)

LINE_END = 0x7FFFFFFF  # sentinel matching dispatcher.LINE_END

if TYPE_CHECKING:
    from peovim.core.cursor import Cursor
    from peovim.core.document import Document
    from peovim.modal.dispatcher import ActionDispatcher


def handle_yank_range(d: ActionDispatcher, action: YankRange, doc: Document, cur: Cursor) -> None:
    sl, sc, el, ec = action.start_line, action.start_col, action.end_line, action.end_col
    if ec == LINE_END:
        ec = len(doc.get_line(el))
    if sl == el:
        text = doc.get_line(sl)[sc:ec]
    else:
        parts = [doc.get_line(sl)[sc:]]
        for ln in range(sl + 1, el):
            parts.append(doc.get_line(ln))
        parts.append(doc.get_line(el)[:ec])
        text = "\n".join(parts)
    if not text:
        return
    d.registers.set(action.register, text, action.yank_type)
    if action.register != '"':
        d.registers.set('"', text, action.yank_type)
    d._maybe_sync_clipboard(text, action.yank_type)
    d._emit_later(
        "yank_done",
        buf_id=d._buf_id,
        start_line=sl,
        start_col=sc,
        end_line=el,
        end_col=ec,
        yank_type=action.yank_type,
    )


def handle_yank_line(d: ActionDispatcher, action: YankLine, doc: Document, cur: Cursor) -> None:
    lines = []
    for i in range(action.count):
        ln = action.line + i
        if ln < doc.line_count():
            lines.append(doc.get_line(ln))
    text = "\n".join(lines)
    d.registers.set(action.register, text, "line")
    d.registers.set('"', text, "line")
    d._maybe_sync_clipboard(text, "line")
    end_ln = action.line + action.count - 1
    d._emit_later(
        "yank_done",
        buf_id=d._buf_id,
        start_line=action.line,
        start_col=0,
        end_line=end_ln,
        end_col=len(doc.get_line(end_ln)) if end_ln < doc.line_count() else 0,
        yank_type="line",
    )


def handle_yank_block(d: ActionDispatcher, action: YankBlock, doc: Document, cur: Cursor) -> None:
    start_line = min(action.start_line, action.end_line)
    end_line = min(max(action.start_line, action.end_line), doc.line_count() - 1)
    start_col = min(action.start_col, action.end_col)
    end_col = max(action.start_col, action.end_col)
    rows: list[str] = []
    for line_no in range(start_line, end_line + 1):
        line_text = doc.get_line(line_no)
        if start_col >= len(line_text):
            rows.append("")
            continue
        rows.append(line_text[start_col : min(end_col, len(line_text))])
    text = "\n".join(rows)
    d.registers.set(action.register, text, "block")
    if action.register != '"':
        d.registers.set('"', text, "block")
    d._maybe_sync_clipboard(text, "block")


def _char_paste_end(start_line: int, start_col: int, text: str) -> tuple[int, int]:
    """Return (line, col) of the last character of text pasted at (start_line, start_col)."""
    if "\n" not in text:
        return start_line, max(0, start_col + len(text) - 1)
    parts = text.split("\n")
    return start_line + len(parts) - 1, max(0, len(parts[-1]) - 1)


def handle_paste_register(d: ActionDispatcher, action: PasteRegister, doc: Document, cur: Cursor) -> None:
    reg = action.register
    if reg == '"' and d._editor_state is not None:
        cb = d._editor_state.options.get("clipboard") or ""
        if "unnamedplus" in cb:
            reg = "+"
        elif "unnamed" in cb:
            reg = "*"
    text, kind = d.registers.get(reg)
    if not text:
        return
    line, col = cur.line, cur.col
    if kind == "line":
        text = text * action.count
        if action.before:
            doc.insert(line, 0, text + "\n")
            cur.move_to(line, 0)
        else:
            if line >= doc.line_count() - 1:
                doc.insert(line, len(doc.get_line(line)), "\n" + text)
            else:
                doc.insert(line + 1, 0, text + "\n")
            cur.move_to(line + 1, 0)
    elif kind == "block":
        d._apply_block_paste(doc, cur, text, action.before, action.count)
    else:
        text = text * action.count
        if action.before:
            doc.insert(line, col, text)
            cur.move_to(*_char_paste_end(line, col, text))
        else:
            insert_col = col + 1
            doc.insert(line, insert_col, text)
            cur.move_to(*_char_paste_end(line, insert_col, text))
    d._dot_repeat = action
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_begin_block_insert(d: ActionDispatcher, action: BeginBlockInsert, doc: Document, cur: Cursor) -> None:
    from peovim.modal.dispatcher import _PendingBlockInsert

    last_line = max(0, doc.line_count() - 1)
    start_line = max(0, min(action.start_line, last_line))
    end_line = max(start_line, min(action.end_line, last_line))
    d._pending_block_insert = _PendingBlockInsert(
        start_line=start_line,
        end_line=end_line,
        col=max(0, action.col),
        source_line=start_line,
    )


def handle_repeat_block_insert(d: ActionDispatcher, action: RepeatBlockInsert, doc: Document, cur: Cursor) -> None:
    insert_line = cur.line
    insert_col = max(0, action.col)
    row_count = max(1, action.row_count)

    with doc.compound_edit():
        for offset in range(row_count):
            line_no = insert_line + offset
            d._ensure_line_exists(doc, line_no)
            d._ensure_line_length(doc, line_no, insert_col)
            doc.insert(line_no, insert_col, action.text)

    cur.move_to(insert_line, insert_col)
    d._clamp_cursor_for_mode(doc)
    d._dot_repeat = action
    d._emit_later("buffer_changed", buf_id=d._buf_id)
