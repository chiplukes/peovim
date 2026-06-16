"""
SessionAPI — session save and restore

Sessions are JSON files stored at platformdirs.user_data_dir("peovim") / "sessions" / "<name>.json".
Each session records open files, cursor positions, scroll state, and tab/window layout.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import TYPE_CHECKING, Any

import platformdirs

from peovim.core.persistence import atomic_write_text

if TYPE_CHECKING:
    from peovim.core.workspace import SplitNode, Workspace
    from peovim.modal.dispatcher import ActionDispatcher
    from peovim.modal.engine import ModalEngine


class SessionNotFoundError(Exception):
    """Raised when a requested session does not exist."""


class SessionAPI:
    """Save and restore named editor sessions."""

    _sessions_dir: pathlib.Path = pathlib.Path(platformdirs.user_data_dir("peovim")) / "sessions"

    def __init__(
        self,
        workspace: Workspace,
        engine: ModalEngine,
        dispatcher: ActionDispatcher,
    ) -> None:
        self._workspace = workspace
        self._engine = engine
        self._dispatcher = dispatcher

    def save(self, name: str = "default") -> None:
        """Serialize current editor state to <sessions_dir>/<name>.json."""
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        data = self._serialize()
        path = self._sessions_dir / f"{name}.json"
        # save policy: single-writer (user-triggered, one named session file per session name)
        atomic_write_text(path, json.dumps(data, indent=2), encoding="utf-8")

    def restore(self, name: str = "default") -> None:
        """Load session from <sessions_dir>/<name>.json."""
        path = self._sessions_dir / f"{name}.json"
        if not path.exists():
            raise SessionNotFoundError(f"Session '{name}' not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        self._restore(data)

    def list_sessions(self) -> list[str]:
        """Return sorted list of session names (filenames without .json)."""
        if not self._sessions_dir.exists():
            return []
        return sorted(p.stem for p in self._sessions_dir.iterdir() if p.suffix == ".json" and p.is_file())

    def delete(self, name: str) -> None:
        """Remove a session file. Raises SessionNotFoundError if missing."""
        path = self._sessions_dir / f"{name}.json"
        if not path.exists():
            raise SessionNotFoundError(f"Session '{name}' not found")
        path.unlink()

    def _serialize(self) -> dict[str, Any]:
        """Build the session JSON dict from the current workspace state."""
        cwd = str(pathlib.Path.cwd())
        return {
            "version": 3,
            "cwd": cwd,
            "active_tab": self._workspace.active_tab_index,
            "tabs": [self._serialize_tab(tab) for tab in self._workspace.tabs],
        }

    def _restore(self, data: dict[str, Any]) -> None:
        """Apply a session dict to the current workspace."""
        import contextlib

        cwd = data.get("cwd", "")
        if cwd:
            with contextlib.suppress(OSError):
                os.chdir(cwd)

        tabs = data.get("tabs")
        if tabs:
            self._restore_tabs(tabs, int(data.get("active_tab", 0)))
            return

        windows = data.get("windows", [])
        if not windows:
            return

        layout = data.get("layout")
        if layout is not None:
            self._restore_layout(layout, windows, int(data.get("active_window", 0)))
            return

        self._restore_legacy_windows(windows)

    def _serialize_tab(self, tab: Any) -> dict[str, Any]:
        leaves = tab.all_leaves()
        leaf_indexes = {id(leaf): index for index, leaf in enumerate(leaves)}
        active_window = next(
            (index for index, leaf in enumerate(leaves) if leaf is tab._active_leaf),
            0,
        )
        return {
            "active_window": active_window,
            "windows": [self._serialize_window(leaf.window) for leaf in leaves],
            "layout": self._serialize_node(tab.root, leaf_indexes),
        }

    def _serialize_window(self, win: Any) -> dict[str, Any]:
        path_str = str(win.document.path) if win.document.path else ""
        return {
            "path": path_str,
            "cursor": [win.cursor.line, win.cursor.col],
            "scroll": win.scroll_line,
            "scroll_col": win.scroll_col,
        }

    def _serialize_node(self, node: SplitNode, leaf_indexes: dict[int, int]) -> dict[str, Any]:
        from peovim.core.workspace import HSplitNode, VSplitNode, WindowLeaf

        if isinstance(node, WindowLeaf):
            return {"type": "leaf", "window": leaf_indexes[id(node)]}
        if isinstance(node, HSplitNode):
            return {
                "type": "hsplit",
                "ratio": node.ratio,
                "top": self._serialize_node(node.top, leaf_indexes),
                "bottom": self._serialize_node(node.bottom, leaf_indexes),
            }
        if isinstance(node, VSplitNode):
            return {
                "type": "vsplit",
                "ratio": node.ratio,
                "left": self._serialize_node(node.left, leaf_indexes),
                "right": self._serialize_node(node.right, leaf_indexes),
            }
        raise TypeError(f"Unsupported split node: {type(node)!r}")

    def _restore_layout(self, layout: dict[str, Any], windows: list[dict[str, Any]], active_window: int) -> None:
        tab = self._workspace.active_tab
        document_cache: dict[pathlib.Path, Any] = {}
        loaded_documents: list[Any] = []
        root = self._restore_node(layout, windows, document_cache, loaded_documents)
        tab.root = root
        leaves = tab.all_leaves()
        if not leaves:
            return
        active_index = max(0, min(active_window, len(leaves) - 1))
        tab._active_leaf = leaves[active_index]
        self._workspace._documents = []
        for doc in loaded_documents:
            self._workspace.add_document(doc)
        self._dispatcher.window = tab.active_window
        self._sync_active_window()
        self._emit_buffer_opened_events(loaded_documents)

    def _restore_tabs(self, tabs: list[dict[str, Any]], active_tab: int) -> None:
        from peovim.core.workspace import TabPage

        restored_tabs: list[TabPage] = []
        document_cache: dict[pathlib.Path, Any] = {}
        loaded_documents: list[Any] = []

        for tab_data in tabs:
            windows = tab_data.get("windows", [])
            layout = tab_data.get("layout")
            if not windows or layout is None:
                continue
            root = self._restore_node(layout, windows, document_cache, loaded_documents)
            page = TabPage(root)
            leaves = page.all_leaves()
            if leaves:
                active_window = max(0, min(int(tab_data.get("active_window", 0)), len(leaves) - 1))
                page._active_leaf = leaves[active_window]
            restored_tabs.append(page)

        if not restored_tabs:
            return

        self._workspace.tabs = restored_tabs
        self._workspace.active_tab_index = max(0, min(active_tab, len(restored_tabs) - 1))
        self._workspace._documents = []
        for doc in loaded_documents:
            self._workspace.add_document(doc)
        self._dispatcher.window = self._workspace.active_window
        self._sync_active_window()
        self._emit_buffer_opened_events(loaded_documents)

    def _restore_node(
        self,
        layout: dict[str, Any],
        windows: list[dict[str, Any]],
        document_cache: dict[pathlib.Path, Any],
        loaded_documents: list[Any],
    ) -> SplitNode:
        from peovim.core.window import Window
        from peovim.core.workspace import HSplitNode, VSplitNode, WindowLeaf

        node_type = layout.get("type")
        if node_type == "leaf":
            index = int(layout.get("window", 0))
            state = windows[max(0, min(index, len(windows) - 1))]
            doc = self._load_session_document(state, document_cache)
            loaded_documents.append(doc)
            win = Window(doc)
            cursor = state.get("cursor", [0, 0])
            win.cursor.line = int(cursor[0])
            win.cursor.col = int(cursor[1])
            win.scroll_line = int(state.get("scroll", 0))
            win.scroll_col = int(state.get("scroll_col", 0))
            win.options["fileformat"] = doc.fileformat
            return WindowLeaf(win)
        if node_type == "hsplit":
            return HSplitNode(
                self._restore_node(layout["top"], windows, document_cache, loaded_documents),
                self._restore_node(layout["bottom"], windows, document_cache, loaded_documents),
                ratio=float(layout.get("ratio", 0.5)),
            )
        if node_type == "vsplit":
            return VSplitNode(
                self._restore_node(layout["left"], windows, document_cache, loaded_documents),
                self._restore_node(layout["right"], windows, document_cache, loaded_documents),
                ratio=float(layout.get("ratio", 0.5)),
            )
        raise ValueError(f"Unsupported session layout node: {node_type!r}")

    def _restore_legacy_windows(self, windows: list[dict[str, Any]]) -> None:
        """Apply the old flat session format to the current active window only."""
        first = windows[0]
        active_win = self._workspace.active_tab.active_window
        doc = self._load_session_document(first, {})
        active_win.document = doc
        active_win.options["fileformat"] = doc.fileformat
        self._workspace.add_document(doc)

        cursor = first.get("cursor", [0, 0])
        active_win.cursor.line = int(cursor[0])
        active_win.cursor.col = int(cursor[1])
        active_win.scroll_line = int(first.get("scroll", 0))
        active_win.scroll_col = int(first.get("scroll_col", 0))
        self._dispatcher.window = active_win
        self._sync_active_window()

    def _load_session_document(self, state: dict[str, Any], document_cache: dict[pathlib.Path, Any]) -> Any:
        from peovim.core.document import Document

        path_str = state.get("path", "")
        if path_str:
            resolved = pathlib.Path(path_str).resolve()
            cached = document_cache.get(resolved)
            if cached is not None:
                return cached
            doc = Document(path=resolved)
            try:
                doc.load(resolved)
            except (OSError, FileNotFoundError):
                doc.load_string("")
            document_cache[resolved] = doc
            return doc

        doc = Document()
        doc.load_string("")
        return doc

    def _sync_active_window(self) -> None:
        pass  # provider pulls live state from workspace.active_window at feed_key time

    def _emit_buffer_opened_events(self, documents: list[Any]) -> None:
        """Emit buffer_opened for each file-backed restored document so LSP and plugins attach."""
        from peovim.core.filetype import detect_filetype

        event_bus = getattr(getattr(self._dispatcher, "_editor_state", None), "event_bus", None)
        if event_bus is None:
            return
        seen: set[int] = set()
        for doc in documents:
            doc_id = id(doc)
            if doc_id in seen or doc.path is None:
                continue
            seen.add(doc_id)
            ft = detect_filetype(doc.path)
            event_bus.emit("buffer_opened", buf_id=doc_id, path=str(doc.path), filetype=ft)
