"""
WorkspaceAPI — tab pages and layout queries

See notes/api.md for the plugin-facing API surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI
    from peovim.core.workspace import Workspace
    from peovim.modal.dispatcher import ActionDispatcher


class WorkspaceAPI:
    """Plugin-facing API for workspace tab and window management."""

    def __init__(self, workspace: Workspace, dispatcher: ActionDispatcher, editor: EditorAPI) -> None:
        self._workspace = workspace
        self._dispatcher = dispatcher
        self._editor = editor

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    @property
    def active_tab(self) -> Any:
        """Return the active TabPage object."""
        return self._workspace.active_tab

    def list_tabs(self) -> list[Any]:
        """Return the list of all TabPage objects."""
        return list(self._workspace.tabs)

    def new_tab(self, buffer: Any = None) -> Any:
        """Open a new tab. Optionally display `buffer` (BufferAPI) in it.

        Returns a WindowAPI for the new tab's initial window.
        """
        from peovim.core.document import Document
        from peovim.core.window import Window

        doc = getattr(buffer, "_doc", None) if buffer is not None else None

        if doc is None:
            doc = Document()

        win = Window(doc)
        self._workspace.new_tab(win)
        self._dispatcher.window = win

        return self._editor._make_window_api(win)

    def close_tab(self, tab: Any) -> None:
        """Close the given TabPage. Raises ValueError if it is the last tab."""
        tabs = self._workspace.tabs
        try:
            index = tabs.index(tab)
        except ValueError:
            raise ValueError("Tab not found in workspace") from None
        self._workspace.close_tab(index)

    def list_windows(self) -> list[Any]:
        """Return WindowAPI for every window in the active tab."""
        return [self._editor._make_window_api(win) for win in self._workspace.active_tab.all_windows()]

    def find_window(self, buffer: Any) -> Any | None:
        """Return a WindowAPI for the first window showing `buffer`, or None."""
        doc = getattr(buffer, "_doc", None)
        if doc is None:
            return None
        for win in self._workspace.active_tab.all_windows():
            if win.document is doc:
                return self._editor._make_window_api(win)
        return None
