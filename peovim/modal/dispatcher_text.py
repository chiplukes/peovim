"""
modal.dispatcher_text — text-mutation action handlers for ActionDispatcher.

Each function has signature (d, action, doc, cur) → None.
Registered in ActionDispatcher._action_handlers via dispatcher.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from peovim.modal.actions import (
    ChangeCase,
    ChangeCaseBlock,
    DeleteBlock,
    DeleteRange,
    FilterRange,
    IncrementNumber,
    IndentRange,
    InsertNewline,
    InsertTab,
    InsertText,
    JoinLines,
    ReplaceBlock,
    ReplaceRange,
)

LINE_END = 0x7FFFFFFF  # sentinel matching dispatcher.LINE_END

if TYPE_CHECKING:
    from peovim.core.cursor import Cursor
    from peovim.core.document import Document
    from peovim.modal.dispatcher import ActionDispatcher


def handle_insert_tab(d: ActionDispatcher, action: InsertTab, doc: Document, cur: Cursor) -> None:
    tab_width = d._get_option("tabstop", 4)
    use_spaces = d._get_option("expandtab", True)
    col = d._resolve_col(action.line, action.col, doc)
    if use_spaces:
        spaces = tab_width - (col % tab_width)
        text = " " * spaces
    else:
        text = "\t"
    d._apply(InsertText(action.line, action.col, text))


def handle_insert_text(d: ActionDispatcher, action: InsertText, doc: Document, cur: Cursor) -> None:
    from peovim.modal.engine import Mode

    col = d._resolve_col(action.line, action.col, doc)
    text = action.text.replace("\r\n", "\n").replace("\r", "\n")
    doc.insert(action.line, col, action.text)
    cur.move_to(action.line, action.col + len(action.text))
    d._dot_repeat = action
    if d._insert_session is not None and d.engine.mode == Mode.INSERT:
        sess = d._insert_session
        if sess.simple and "\n" not in action.text:
            expected = sess.start_col + len(sess.text)
            if action.line == sess.start_line and col == expected:
                sess.text += action.text
            else:
                sess.simple = False
    d._emit_later(
        "buffer_text_changed",
        buf_id=d._buf_id,
        path=str(doc.path) if doc.path is not None else None,
        start_line=action.line,
        start_col=col,
        end_line=action.line,
        end_col=col,
        new_text=text,
    )
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_delete_range(d: ActionDispatcher, action: DeleteRange, doc: Document, cur: Cursor) -> None:
    from peovim.modal.engine import Mode

    sl, sc, el, ec = action.start_line, action.start_col, action.end_line, action.end_col
    el = min(el, doc.line_count() - 1)
    deleted_text = d._capture_deleted_range_text(doc, sl, sc, el, ec) if action.save_deleted else ""
    event_start_line = sl
    event_start_col = sc
    event_end_line = el
    event_end_col = ec

    if sc == 0 and action.end_col == LINE_END:
        if el < doc.line_count() - 1:
            event_end_line = el + 1
            event_end_col = 0
            doc.delete(sl, 0, el + 1, 0)
        else:
            if sl > 0:
                event_start_line = sl - 1
                event_start_col = len(doc.get_line(sl - 1))
                event_end_line = el
                event_end_col = len(doc.get_line(el))
                doc.delete(sl - 1, len(doc.get_line(sl - 1)), el, len(doc.get_line(el)))
                cur.move_to(max(0, sl - 1), 0)
            else:
                line_len = len(doc.get_line(el))
                event_end_col = line_len
                if line_len > 0:
                    doc.delete(sl, 0, el, line_len)
            cur.move_to(min(sl, doc.line_count() - 1), 0)
    else:
        if ec == LINE_END:
            ec = len(doc.get_line(el))
        event_end_col = ec
        sc = d._resolve_col(sl, sc, doc)
        ec = d._resolve_col(el, ec, doc)
        event_start_col = sc
        event_end_col = ec
        doc.delete(sl, sc, el, ec)
        cur.move_to(sl, sc)

    d._clamp_cursor_for_mode(doc)
    if action.save_deleted:
        yank_type = "line" if action.start_col == 0 and action.end_col == LINE_END else "char"
        d._store_deleted_text(action.register, deleted_text, yank_type)
    d._dot_repeat = action
    if d._insert_session is not None and d.engine.mode == Mode.INSERT:
        sess = d._insert_session
        if sess.simple:
            sl2, sc2, el2, ec2 = action.start_line, action.start_col, action.end_line, action.end_col
            if sl2 == el2 == sess.start_line and sc2 >= sess.start_col:
                a = sc2 - sess.start_col
                b = ec2 - sess.start_col
                sess.text = sess.text[:a] + sess.text[b:]
            else:
                sess.simple = False
    d._emit_later(
        "buffer_text_changed",
        buf_id=d._buf_id,
        path=str(doc.path) if doc.path is not None else None,
        start_line=event_start_line,
        start_col=event_start_col,
        end_line=event_end_line,
        end_col=event_end_col,
        new_text="",
    )
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_delete_block(d: ActionDispatcher, action: DeleteBlock, doc: Document, cur: Cursor) -> None:
    start_line = min(action.start_line, action.end_line)
    end_line = min(max(action.start_line, action.end_line), doc.line_count() - 1)
    start_col = min(action.start_col, action.end_col)
    end_col = max(action.start_col, action.end_col)
    deleted_text = (
        d._capture_deleted_block_text(doc, start_line, end_line, start_col, end_col) if action.save_deleted else ""
    )

    with doc.compound_edit():
        for line_no in range(start_line, end_line + 1):
            line_text = doc.get_line(line_no)
            if start_col >= len(line_text):
                continue
            line_end = min(end_col, len(line_text))
            if start_col >= line_end:
                continue
            doc.delete(line_no, start_col, line_no, line_end)

    cur.move_to(start_line, start_col)
    d._clamp_cursor_for_mode(doc)
    if action.save_deleted:
        d._store_deleted_text(action.register, deleted_text, "block")
    d._dot_repeat = action
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_replace_range(d: ActionDispatcher, action: ReplaceRange, doc: Document, cur: Cursor) -> None:
    sl, sc, el, ec = action.start_line, action.start_col, action.end_line, action.end_col
    if ec == LINE_END:
        ec = len(doc.get_line(el))
    if sl == el:
        width = max(0, ec - sc)
        new_text = action.new_text * width if len(action.new_text) == 1 and width > 1 else action.new_text
        doc.replace(sl, sc, el, ec, new_text)
    else:
        parts = [doc.get_line(sl)[sc:]]
        for ln in range(sl + 1, el):
            parts.append(doc.get_line(ln))
        parts.append(doc.get_line(el)[:ec])
        selected = "\n".join(parts)
        if len(action.new_text) == 1:
            transformed = "".join(action.new_text if ch != "\n" else "\n" for ch in selected)
        else:
            transformed = action.new_text
        doc.replace(sl, sc, el, ec, transformed)
        new_text = transformed
    cur.move_to(sl, sc)
    d._dot_repeat = action
    d._emit_later(
        "buffer_text_changed",
        buf_id=d._buf_id,
        path=str(doc.path) if doc.path is not None else None,
        start_line=sl,
        start_col=sc,
        end_line=el,
        end_col=ec,
        new_text=new_text.replace("\r\n", "\n").replace("\r", "\n"),
    )
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_replace_block(d: ActionDispatcher, action: ReplaceBlock, doc: Document, cur: Cursor) -> None:
    start_line = min(action.start_line, action.end_line)
    end_line = min(max(action.start_line, action.end_line), doc.line_count() - 1)
    start_col = min(action.start_col, action.end_col)
    end_col = max(action.start_col, action.end_col)

    with doc.compound_edit():
        for line_no in range(start_line, end_line + 1):
            line_text = doc.get_line(line_no)
            if start_col >= len(line_text):
                continue
            line_end = min(end_col, len(line_text))
            if start_col >= line_end:
                continue
            doc.replace(line_no, start_col, line_no, line_end, action.char * (line_end - start_col))

    cur.move_to(start_line, start_col)
    d._clamp_cursor_for_mode(doc)
    d._dot_repeat = action
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_change_case(d: ActionDispatcher, action: ChangeCase, doc: Document, cur: Cursor) -> None:
    sl, sc, el, ec = action.start_line, action.start_col, action.end_line, action.end_col
    if ec == LINE_END:
        ec = len(doc.get_line(el))
    if sl == el:
        selected = doc.get_line(sl)[sc:ec]
    else:
        parts = [doc.get_line(sl)[sc:]]
        for ln in range(sl + 1, el):
            parts.append(doc.get_line(ln))
        parts.append(doc.get_line(el)[:ec])
        selected = "\n".join(parts)
    transformed = d._transform_case(selected, action.mode)
    doc.replace(sl, sc, el, ec, transformed)
    cur.move_to(sl, sc)
    d._clamp_cursor_for_mode(doc)
    d._dot_repeat = action
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_change_case_block(d: ActionDispatcher, action: ChangeCaseBlock, doc: Document, cur: Cursor) -> None:
    start_line = min(action.start_line, action.end_line)
    end_line = min(max(action.start_line, action.end_line), doc.line_count() - 1)
    start_col = min(action.start_col, action.end_col)
    end_col = max(action.start_col, action.end_col)

    with doc.compound_edit():
        for line_no in range(start_line, end_line + 1):
            line_text = doc.get_line(line_no)
            if start_col >= len(line_text):
                continue
            line_end = min(end_col, len(line_text))
            if start_col >= line_end:
                continue
            segment = line_text[start_col:line_end]
            doc.replace(line_no, start_col, line_no, line_end, d._transform_case(segment, action.mode))

    cur.move_to(start_line, start_col)
    d._clamp_cursor_for_mode(doc)
    d._dot_repeat = action
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_insert_newline(d: ActionDispatcher, action: InsertNewline, doc: Document, cur: Cursor) -> None:
    line = action.line
    col = d._resolve_col(line, action.col, doc)
    indent = action.indent
    if not indent and d._get_option("autoindent", False):
        current = doc.get_line(line)
        indent = current[: len(current) - len(current.lstrip())]
    doc.insert(line, col, "\n" + indent)
    cur.move_to(line + 1, len(indent))
    d._dot_repeat = action
    if d._insert_session is not None:
        d._insert_session.simple = False
    d._emit_later(
        "buffer_text_changed",
        buf_id=d._buf_id,
        path=str(doc.path) if doc.path is not None else None,
        start_line=line,
        start_col=col,
        end_line=line,
        end_col=col,
        new_text="\n" + indent,
    )
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_indent_range(d: ActionDispatcher, action: IndentRange, doc: Document, cur: Cursor) -> None:
    tab_width = d._get_option("shiftwidth", 4)
    use_spaces = d._get_option("expandtab", True)
    indent_str = " " * tab_width if use_spaces else "\t"
    for line in range(action.start_line, action.end_line + 1):
        if line >= doc.line_count():
            break
        if action.direction == "in":
            doc.insert(line, 0, indent_str)
        elif action.direction == "out":
            current = doc.get_line(line)
            if current.startswith(indent_str):
                doc.delete(line, 0, line, len(indent_str))
            elif current.startswith("\t"):
                doc.delete(line, 0, line, 1)
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_join_lines(d: ActionDispatcher, action: JoinLines, doc: Document, cur: Cursor) -> None:
    for _ in range(action.count):
        line = cur.line
        if line >= doc.line_count() - 1:
            break
        current = doc.get_line(line)
        next_line = doc.get_line(line + 1).lstrip()
        new_text = current.rstrip() + " " + next_line
        doc.delete(line, 0, line + 1, len(doc.get_line(line + 1)))
        doc.insert(line, 0, new_text)
    d._emit_later("buffer_changed", buf_id=d._buf_id)


def handle_increment_number(d: ActionDispatcher, action: IncrementNumber, doc: Document, cur: Cursor) -> None:
    import re as _re

    text = doc.get_line(cur.line)
    m = _re.search(r"-?(?:0x[\da-fA-F]+|\d+)", text[cur.col :])
    if m is not None:
        col_start = cur.col + m.start()
    else:
        m = _re.search(r"-?(?:0x[\da-fA-F]+|\d+)", text)
        if m is None:
            return
        col_start = m.start()
    num_str = m.group()
    col_end = col_start + len(num_str)
    if num_str.lower().startswith("0x"):
        val = int(num_str, 16) + action.delta
        new_str = ("0x" if num_str.startswith("0x") else "0X") + format(abs(val), "x")
    else:
        val = int(num_str) + action.delta
        new_str = str(val)
    doc.delete(cur.line, col_start, cur.line, col_end)
    doc.insert(cur.line, col_start, new_str)
    cur.move_to(cur.line, col_start + len(new_str) - 1)
    d._dot_repeat = action


def handle_filter_range(d: ActionDispatcher, action: FilterRange, doc: Document, cur: Cursor) -> None:
    import subprocess

    lines = []
    for ln in range(action.start_line, min(action.end_line + 1, doc.line_count())):
        lines.append(doc.get_line(ln))
    input_text = "\n".join(lines) + "\n"
    try:
        result = subprocess.run(
            action.cmd,
            shell=True,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout
    except Exception:
        return
    if action.end_line < doc.line_count() - 1:
        doc.delete(action.start_line, 0, action.end_line + 1, 0)
    else:
        if action.start_line > 0:
            prev_line_text = doc.get_line(action.start_line - 1)
            doc.delete(
                action.start_line - 1,
                len(prev_line_text),
                action.end_line,
                len(doc.get_line(action.end_line)),
            )
            doc.insert(
                action.start_line - 1,
                len(doc.get_line(action.start_line - 1)),
                "\n" + output.rstrip("\n"),
            )
            cur.move_to(action.start_line, 0)
            d._clamp_cursor_for_mode(doc)
            d._emit_later("buffer_changed", buf_id=d._buf_id)
            return
        else:
            doc.delete(0, 0, action.end_line, len(doc.get_line(action.end_line)))
    doc.insert(action.start_line, 0, output)
    cur.move_to(action.start_line, 0)
    d._clamp_cursor_for_mode(doc)
    d._emit_later("buffer_changed", buf_id=d._buf_id)
