from __future__ import annotations

from peovim.modal.actions import (
    CloseAllFolds,
    CloseFold,
    CreateFold,
    DeleteFold,
    OpenAllFolds,
    OpenFold,
    ToggleFold,
)


def handle_fold_action(dispatcher, action: object) -> bool:
    folds = dispatcher.window.folds

    if isinstance(action, CreateFold):
        folds.create(action.start_line, action.end_line)
        return True

    if isinstance(action, OpenFold):
        folds.open(action.line)
        return True

    if isinstance(action, CloseFold):
        folds.close(action.line)
        return True

    if isinstance(action, ToggleFold):
        folds.toggle(action.line)
        return True

    if isinstance(action, OpenAllFolds):
        folds.open_all()
        return True

    if isinstance(action, CloseAllFolds):
        folds.close_all()
        return True

    if isinstance(action, DeleteFold):
        folds.delete(action.line)
        return True

    return False
