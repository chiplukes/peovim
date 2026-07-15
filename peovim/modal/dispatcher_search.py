from __future__ import annotations

import re as _re
from typing import TYPE_CHECKING

from peovim.modal.actions import (
    ClearSearchHighlight,
    SearchNext,
    SearchWordUnderCursor,
    SetSearchPattern,
)

if TYPE_CHECKING:
    from peovim.core.cursor import Cursor
    from peovim.core.document import Document
    from peovim.modal.dispatcher import ActionDispatcher


def handle_search_action(dispatcher: ActionDispatcher, action: object, doc: Document, cur: Cursor) -> bool:
    if isinstance(action, SetSearchPattern):
        if dispatcher._editor_state is not None:
            opts = dispatcher.window.options
            dispatcher._editor_state.search.set_pattern(
                action.pattern,
                action.direction,
                ignorecase=opts.get("ignorecase", False),
                smartcase=opts.get("smartcase", False),
            )
            if dispatcher._editor_state.search.compiled:
                from peovim.core.search import search_next

                result = search_next(
                    dispatcher.window.document,
                    dispatcher._editor_state.search.compiled,
                    cur.line,
                    cur.col,
                    action.direction,
                    wrapscan=dispatcher.window.options.get("wrapscan", True),
                )
                if result:
                    cur.move_to(result[0], result[1])
                    dispatcher.window.scroll_to_cursor(center=True)
        return True

    if isinstance(action, SearchNext):
        if dispatcher._editor_state is not None and dispatcher._editor_state.search.compiled:
            from peovim.core.search import search_next

            direction = dispatcher._editor_state.search.direction
            if action.reverse:
                direction = "backward" if direction == "forward" else "forward"
            for _ in range(action.count):
                result = search_next(
                    doc,
                    dispatcher._editor_state.search.compiled,
                    cur.line,
                    cur.col,
                    direction,
                    wrapscan=dispatcher.window.options.get("wrapscan", True),
                )
                if result:
                    cur.move_to(result[0], result[1])
                else:
                    break
            if result:
                dispatcher.window.scroll_to_cursor(center=True)
        return True

    if isinstance(action, SearchWordUnderCursor):
        line_text = doc.get_line(cur.line)
        match = None
        for word_match in _re.finditer(r"\w+", line_text):
            if word_match.start() <= cur.col < word_match.end():
                match = word_match
                break
        if match is None:
            match = _re.search(r"\w+", line_text[cur.col :])
        if match is None:
            match = _re.search(r"\w+\Z", line_text[: cur.col + 1])
        if match is not None:
            from peovim.core.search import build_word_pattern, search_next

            pattern = build_word_pattern(match.group(), whole_word=action.whole_word)
            direction = "backward" if action.reverse else "forward"
            if dispatcher._editor_state is not None:
                opts = dispatcher.window.options
                dispatcher._editor_state.search.set_pattern(
                    pattern,
                    direction,
                    ignorecase=opts.get("ignorecase", False),
                    smartcase=opts.get("smartcase", False),
                )
                if dispatcher._editor_state.search.compiled:
                    result = search_next(
                        doc,
                        dispatcher._editor_state.search.compiled,
                        cur.line,
                        cur.col,
                        direction,
                        wrapscan=dispatcher.window.options.get("wrapscan", True),
                    )
                if result:
                    cur.move_to(result[0], result[1])
                    dispatcher.window.scroll_to_cursor(center=True)
        return True

    if isinstance(action, ClearSearchHighlight):
        if dispatcher._editor_state is not None:
            dispatcher._editor_state.search.hlsearch_active = False
        return True

    return False
