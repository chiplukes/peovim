from __future__ import annotations

from peovim.modal.actions import RepeatLastExCommand, RunExCommand, RunNormalKeys


def handle_command_action(dispatcher, action: object) -> bool:
    if isinstance(action, RepeatLastExCommand):
        if dispatcher._last_ex_command:
            dispatcher._run_ex_command(dispatcher._last_ex_command)
        return True

    if isinstance(action, RunExCommand):
        dispatcher._run_ex_command(action.command)
        return True

    if isinstance(action, RunNormalKeys):
        seq = action.keys
        if seq.startswith(":") and ("<CR>" in seq or seq.endswith("\n")):
            ex_cmd = seq[1:].replace("<CR>", "").replace("\n", "").strip()
            if ex_cmd:
                dispatcher._run_ex_command(ex_cmd)
            return True

        i = 0
        while i < len(seq):
            if seq[i] == "<":
                end = seq.find(">", i)
                if end != -1:
                    key = seq[i : end + 1]
                    i = end + 1
                else:
                    key = seq[i]
                    i += 1
            else:
                key = seq[i]
                i += 1
            actions = dispatcher.engine.feed_key(key, remap=action.remap)
            for sub_action in actions:
                dispatcher._apply(sub_action)
        return True

    return False
