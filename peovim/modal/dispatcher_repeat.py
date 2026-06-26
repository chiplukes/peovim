from __future__ import annotations

from peovim.modal.actions import DeleteRange, InsertText, PluginContext, RepeatLastChange, ReplaceRange, RunPlugin

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

    if dispatcher._dot_repeat is not None:
        dispatcher._apply(_rebase_repeat_action(dispatcher, dispatcher._dot_repeat))
        return True

    return True


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
        # Motion-aware repeat: re-evaluate the motion from the current cursor position
        if action.motion_fn is not None:
            line = cursor.line
            col = min(cursor.col, len(document.get_line(line)))
            new_line, new_col = action.motion_fn(document, line, col, action.motion_count)
            # Mirror the range normalization from engine._resolve_operator_motion
            if action.motion_range_type == "line":
                start = (min(line, new_line), 0)
                end = (max(line, new_line), LINE_END)
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
            return DeleteRange(
                start[0], start[1], end[0], end[1],
                register=action.register,
                save_deleted=action.save_deleted,
                motion_fn=action.motion_fn,
                motion_count=action.motion_count,
                motion_range_type=action.motion_range_type,
                motion_end_exclusive=action.motion_end_exclusive,
                motion_end_inclusive=action.motion_end_inclusive,
            )
        # Fixed-width fallback for non-motion deletes (x, dl, etc.)
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

    if isinstance(action, InsertText):
        # Rebase insert to current cursor — the session accumulator ensures `action`
        # already holds the full typed text from the insert session.
        line = cursor.line
        col = cursor.col
        return InsertText(line, col, action.text)

    return action
