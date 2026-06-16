from __future__ import annotations

import contextlib

from peovim.modal.actions import (
    CloseWindow,
    CycleWindow,
    EqualizeWindows,
    FocusTab,
    FocusWindow,
    NewTab,
    NextTab,
    OnlyWindow,
    PrevTab,
    ResizeWindow,
    SmartFocusWindow,
    SplitWindow,
    TabClose,
    ToggleWindowExpand,
)


def _focus_cycle_group(tab: object) -> list:
    """Return the stable leaf order for focus cycling within a tab."""
    return list(tab.all_windows())


def handle_workspace_action(dispatcher, action: object) -> bool:
    workspace = dispatcher._workspace
    if workspace is None:
        return False

    if isinstance(action, SplitWindow):
        tab = workspace.active_tab
        new_win = tab.split_horizontal() if action.direction == "h" else tab.split_vertical()
        if action.buffer_path:
            from pathlib import Path

            path = Path(action.buffer_path).resolve()
            if path.exists():
                new_win.document.load(path)
                new_win.document.path = path
        return True

    if isinstance(action, CloseWindow):
        tab = workspace.active_tab
        try:
            tab.close_active()
        except ValueError:
            if action.force:
                dispatcher.quit_requested = True
            else:
                dispatcher._set_message("E444: Cannot close last window")
        return True

    if isinstance(action, FocusWindow):
        for _ in range(max(1, action.count)):
            workspace.active_tab.focus_direction(action.direction)
        return True

    if isinstance(action, CycleWindow):
        flat: list[tuple[int, object]] = []
        for tab_index, tab in enumerate(workspace.tabs):
            for win in tab.all_windows():
                flat.append((tab_index, win))
        if len(flat) <= 1:
            return True
        cur_tab_i = workspace.active_tab_index
        cur_win = workspace.active_window
        cur_idx = next(
            (i for i, (tab_index, win) in enumerate(flat) if tab_index == cur_tab_i and win is cur_win),
            0,
        )
        new_idx = (cur_idx + 1) % len(flat) if action.direction == "next" else (cur_idx - 1) % len(flat)
        new_tab_i, new_win = flat[new_idx]
        if new_tab_i != cur_tab_i:
            workspace.goto_tab(new_tab_i)
        workspace.active_tab.focus_window(new_win)
        return True

    if isinstance(action, SmartFocusWindow):
        tab = workspace.active_tab
        before = workspace.active_window
        tab.focus_direction(action.direction)
        if workspace.active_window is before:
            cycle_group = _focus_cycle_group(tab)
            if len(cycle_group) > 1:
                cur_idx = next((i for i, win in enumerate(cycle_group) if win is before), 0)
                new_idx = (
                    (cur_idx - 1) % len(cycle_group)
                    if action.direction in ("k", "h")
                    else (cur_idx + 1) % len(cycle_group)
                )
                with contextlib.suppress(ValueError):
                    tab.focus_window(cycle_group[new_idx])
        return True

    if isinstance(action, OnlyWindow):
        workspace.active_tab.only_window()
        return True

    if isinstance(action, EqualizeWindows):
        workspace.active_tab.equalize_window_sizes()
        return True

    if isinstance(action, ResizeWindow):
        resized = workspace.active_tab.resize_active(action.direction, action.delta)
        if not resized:
            axis = "horizontal" if action.direction == "h" else "vertical"
            dispatcher._set_message(f"No local {axis} split to resize")
        return True

    if isinstance(action, ToggleWindowExpand):
        from peovim.core.workspace import VSplitNode

        path = workspace.active_tab._path_to_active_leaf()
        can_expand = bool(path) and isinstance(path[-1][0], VSplitNode)
        workspace.active_tab.toggle_expand_active_width(action.width_fraction)
        if not can_expand:
            dispatcher._set_message("No local vertical split to expand")
        return True

    if isinstance(action, NewTab):
        from peovim.core.document import Document
        from peovim.core.window import Window as EditorWindow

        doc = Document()
        doc.load_string("")
        workspace.new_tab(EditorWindow(doc))
        return True

    if isinstance(action, TabClose):
        try:
            workspace.close_tab(workspace.active_tab_index)
        except ValueError:
            dispatcher._set_message("E784: Cannot close last tab")
        return True

    if isinstance(action, NextTab):
        for _ in range(max(1, action.count)):
            workspace.next_tab()
        return True

    if isinstance(action, PrevTab):
        for _ in range(max(1, action.count)):
            workspace.prev_tab()
        return True

    if isinstance(action, FocusTab):
        if action.index == "next":
            workspace.next_tab()
        elif action.index == "prev":
            workspace.prev_tab()
        elif isinstance(action.index, int):
            idx = action.index - 1
            if 0 <= idx < len(workspace.tabs):
                workspace.goto_tab(idx)
        return True

    return False
