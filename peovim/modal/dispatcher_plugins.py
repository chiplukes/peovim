from __future__ import annotations

import inspect

from peovim.modal.actions import RunPlugin


def handle_plugin_action(dispatcher, action: object) -> bool:
    if not isinstance(action, RunPlugin):
        return False

    callback = dispatcher._plugin_callbacks.get(action.callback_id)
    if callback is None:
        return True

    dispatcher._dot_repeat = action
    context = action.ctx
    if context is None:
        dispatcher._pending_callbacks.append(callback)
        return True

    try:
        signature = inspect.signature(callback)
        positional_kinds = {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        }
        takes_context = any(
            parameter.kind in positional_kinds and parameter.default is inspect.Parameter.empty
            for parameter in signature.parameters.values()
        )
    except (ValueError, TypeError):
        takes_context = False

    if takes_context:
        dispatcher._pending_callbacks.append(lambda fn=callback, ctx=context: fn(ctx))
    else:
        dispatcher._pending_callbacks.append(callback)
    return True
