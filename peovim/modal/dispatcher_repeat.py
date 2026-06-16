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
