from __future__ import annotations

from peovim.modal.actions import (
    ChangeCase,
    ChangeRepeat,
    DeleteRange,
    InsertText,
    PluginContext,
    RepeatLastChange,
    ReplaceRange,
    RunPlugin,
)

LINE_END = 0x7FFFFFFF


def handle_repeat_action(dispatcher, action: object) -> bool:
    if not isinstance(action, RepeatLastChange):
        return False

    if isinstance(dispatcher._dot_repeat, RunPlugin) and dispatcher._dot_repeat.ctx is not None:
        original_context = dispatcher._dot_repeat.ctx
        cursor = dispatcher.window.cursor
        repeat_context = PluginContext(
            mode="normal",
            visual_range=None,
            count=original_context.count,
            register=original_context.register,
            cursor=(cursor.line, cursor.col),
            is_repeat=True,
            visual_line_count=original_context.visual_line_count,
        )
        dispatcher._apply(RunPlugin(dispatcher._dot_repeat.callback_id, repeat_context))
        return True

    if isinstance(dispatcher._dot_repeat, ChangeRepeat):
        _apply_change_repeat(dispatcher, dispatcher._dot_repeat)
        return True

    if dispatcher._dot_repeat is not None:
        dispatcher._apply(_rebase_repeat_action(dispatcher, dispatcher._dot_repeat))
        return True

    return True


def _apply_change_repeat(dispatcher, action: ChangeRepeat) -> None:
    """Replay a `c`-operator change: re-resolve the range at the cursor, delete
    it, and insert the originally-typed text — as one compound (single-undo) edit.
    """
    cursor = dispatcher.window.cursor
    document = dispatcher.window.document
    # A change never deletes through the trailing newline (see _rebase_range's
    # content_only doc) — it always leaves one line/range to type into, and
    # a linewise-sourced change still registers as a linewise yank.
    is_linewise = action.linewise_count is not None or (
        action.motion_fn is not None and action.motion_range_type == "line"
    )

    rebased = _rebase_range(action, cursor, document, content_only=True)
    if rebased is not None:
        sl, sc, el, ec = rebased
    elif action.start_line != action.end_line or action.end_col == LINE_END:
        # No provenance and not a simple single-line width (e.g. a Visual-mode
        # change, which carries none of these) — replay at the exact original spot.
        sl, sc, el, ec = action.start_line, action.start_col, action.end_line, action.end_col
    else:
        # Fixed-width fallback, mirroring DeleteRange's own fallback.
        width = max(0, action.end_col - action.start_col)
        line = cursor.line
        col = min(cursor.col, len(document.get_line(line)))
        sl, sc, el, ec = line, col, line, min(col + width, len(document.get_line(line)))

    with document.compound_edit():
        dispatcher._apply(
            DeleteRange(
                sl,
                sc,
                el,
                ec,
                register=action.register,
                save_deleted=True,
                yank_type="line" if is_linewise else None,
            )
        )
        # Insert exactly where the deleted range started — reading the cursor
        # back here instead would get clamped to Normal-mode bounds (no
        # one-past-the-end column), unlike the live "c{motion}" path where this
        # runs while still in Insert mode.
        dispatcher._apply(InsertText(sl, sc, action.insert_text))

    # The sub-dispatches above each overwrite _dot_repeat with themselves (a
    # plain DeleteRange, then a plain InsertText) — restore it to a fresh
    # ChangeRepeat so a further "." still redoes delete+insert together.
    dispatcher._dot_repeat = ChangeRepeat(
        sl,
        sc,
        el,
        ec,
        action.register,
        action.insert_text,
        motion_fn=action.motion_fn,
        motion_count=action.motion_count,
        motion_range_type=action.motion_range_type,
        motion_end_exclusive=action.motion_end_exclusive,
        motion_end_inclusive=action.motion_end_inclusive,
        text_object_key=action.text_object_key,
        text_object_mode=action.text_object_mode,
        linewise_count=action.linewise_count,
    )


def _rebase_repeat_action(dispatcher, action: object) -> object:
    cursor = dispatcher.window.cursor
    document = dispatcher.window.document

    if isinstance(action, ReplaceRange):
        if action.start_line != action.end_line or action.end_col == LINE_END:
            return action
        width = max(0, action.end_col - action.start_col)
        line = cursor.line
        col = min(cursor.col, len(document.get_line(line)))
        end_col = min(col + width, len(document.get_line(line)))
        return ReplaceRange(line, col, line, end_col, action.new_text)

    if isinstance(action, DeleteRange):
        rebased = _rebase_range(action, cursor, document)
        if rebased is not None:
            (sl, sc, el, ec) = rebased
            return DeleteRange(
                sl,
                sc,
                el,
                ec,
                register=action.register,
                save_deleted=action.save_deleted,
                motion_fn=action.motion_fn,
                motion_count=action.motion_count,
                motion_range_type=action.motion_range_type,
                motion_end_exclusive=action.motion_end_exclusive,
                motion_end_inclusive=action.motion_end_inclusive,
                text_object_key=action.text_object_key,
                text_object_mode=action.text_object_mode,
                linewise_count=action.linewise_count,
            )
        # Fixed-width fallback for non-motion, non-text-object deletes (x, dl, etc.)
        if action.start_line != action.end_line or action.end_col == LINE_END:
            return action
        width = max(0, action.end_col - action.start_col)
        line = cursor.line
        col = min(cursor.col, len(document.get_line(line)))
        end_col = min(col + width, len(document.get_line(line)))
        return DeleteRange(
            line,
            col,
            line,
            end_col,
            register=action.register,
            save_deleted=action.save_deleted,
        )

    if isinstance(action, ChangeCase):
        rebased = _rebase_range(action, cursor, document)
        if rebased is not None:
            (sl, sc, el, ec) = rebased
            return ChangeCase(
                sl,
                sc,
                el,
                ec,
                action.mode,
                motion_fn=action.motion_fn,
                motion_count=action.motion_count,
                motion_range_type=action.motion_range_type,
                motion_end_exclusive=action.motion_end_exclusive,
                motion_end_inclusive=action.motion_end_inclusive,
                text_object_key=action.text_object_key,
                text_object_mode=action.text_object_mode,
                linewise_count=action.linewise_count,
            )
        return action

    if isinstance(action, InsertText):
        # Rebase insert to current cursor — the session accumulator ensures `action`
        # already holds the full typed text from the insert session.
        line = cursor.line
        col = cursor.col
        return InsertText(line, col, action.text)

    return action


def _rebase_range(action, cursor, document, *, content_only: bool = False) -> tuple[int, int, int, int] | None:
    """Re-derive (start_line, start_col, end_line, end_col) for `action` at the
    current cursor, using whichever provenance it carries (text object, doubled
    linewise operator, or motion). Returns None if `action` carries none of
    these, so the caller can fall back to its own default behavior.

    `content_only` swaps the LINE_END sentinel (used by "d"-style linewise
    deletes, which remove through the trailing newline) for a concrete end
    column (the target line's length) — for a "c"-style change, which must
    leave the newline in place so typing doesn't merge onto the next line.
    """
    line = cursor.line
    col = min(cursor.col, len(document.get_line(line)))

    def _line_end(target_line: int) -> int:
        return len(document.get_line(target_line)) if content_only else LINE_END

    if action.text_object_key is not None:
        from peovim.modal.engine import _resolve_text_object

        rng = _resolve_text_object(document, line, col, action.text_object_key, action.text_object_mode)
        if rng is None:
            return None
        return rng

    if action.linewise_count is not None:
        end_line = min(line + action.linewise_count - 1, document.line_count() - 1)
        return (line, 0, end_line, _line_end(end_line))

    if action.motion_fn is not None:
        new_line, new_col = action.motion_fn(document, line, col, action.motion_count)
        # Mirror the range normalization from engine._resolve_operator_motion
        if action.motion_range_type == "line":
            start = (min(line, new_line), 0)
            end = (max(line, new_line), _line_end(max(line, new_line)))
        else:
            start = min((line, col), (new_line, new_col))
            end = max((line, col), (new_line, new_col))
            if action.motion_end_exclusive and (line, col) <= (new_line, new_col):
                end = (new_line, new_col)
                line_text = document.get_line(new_line)
                if new_col >= max(0, len(line_text) - 1):
                    end = (new_line, len(line_text))
            elif action.motion_end_inclusive:
                line_text = document.get_line(end[0])
                end = (end[0], min(end[1] + 1, len(line_text)))
        return (start[0], start[1], end[0], end[1])

    return None
