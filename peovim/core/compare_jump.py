"""Shared helper: should a goto-style jump open a new split?

Used by both `EditorAPI.goto_location` and `LspUiAdapter.goto_location` (the two
independent code paths that jump to a file/line — see notes/architecture.md) so
that jumping out of a diff/compare pane never clobbers it. Kept dependency-free
(core-level types only) so either layer can call it without a layering cycle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def should_split_for_compare_jump(editor_state: Any, workspace: Any, target: Path) -> bool:
    """True if the active window is half of a diff/compare split and `target` is
    a different file than what it currently shows — i.e. jumping there in place
    would silently swap out one side of the comparison.
    """
    window_ids = getattr(editor_state, "compare_window_ids", None)
    if not window_ids:
        return False
    win = workspace.active_window
    if id(win) not in window_ids:
        return False
    current_path = getattr(win.document, "path", None)
    return current_path is None or Path(current_path).resolve() != target
