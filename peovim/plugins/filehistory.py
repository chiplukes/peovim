"""File history sidebar plugin.

Shows per-file local history snapshots with a diff preview.  Each entry
displays the save timestamp and age; navigating entries shows a live
unified-diff float comparing that snapshot against the current buffer
contents.  Pressing <CR> restores the snapshot into the active buffer.

Requires the local_history plugin to be capturing snapshots (``plugins.load
("peovim.plugins.local_history")``), but can be loaded independently — it
reads the same on-disk store directly.
"""

from __future__ import annotations

import contextlib
import difflib
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

from peovim.plugins.local_history import (
    _display_age,
    _HistoryItem,
    _LocalHistoryStore,
)

_EventToken = Any

_PANEL_NAME = "file_history"
_PREVIEW_WIDTH = 82
_PREVIEW_HEIGHT = 22


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item_label(item: _HistoryItem) -> str:
    stamp = item.entry.timestamp[:19].replace("T", " ")
    age = _display_age(item.entry.timestamp)
    reason = f"  [{item.entry.reason}]" if item.entry.reason != "save" else ""
    return f"{item.index:>2}. {stamp}  ({age}){reason}"


def _message_nodes(msg: str) -> list:
    from peovim.ui.tree_view import TreeNode

    return [TreeNode(label=msg, fg=(140, 140, 140))]


def _build_diff_lines(snapshot_text: str, current_text: str, item: _HistoryItem) -> list:
    """Return a list of FloatLines (colored segments) for a unified diff."""
    from peovim.core.style import Style

    a_lines = snapshot_text.splitlines(keepends=True)
    b_lines = current_text.splitlines(keepends=True)
    from_label = f"snapshot  {item.entry.timestamp[:19].replace('T', ' ')} UTC"
    to_label = "current"
    raw_diff = list(difflib.unified_diff(a_lines, b_lines, fromfile=from_label, tofile=to_label, n=3))
    if not raw_diff:
        return [[("  (no changes — snapshot matches current buffer)", Style(fg=(120, 180, 120)))]]

    result: list = []
    for raw in raw_diff:
        text = raw.rstrip("\n")
        if text.startswith("--- ") or text.startswith("+++ "):
            seg = [(text, Style(fg=(180, 160, 80)))]
        elif text.startswith("@@"):
            seg = [(text, Style(fg=(100, 150, 220)))]
        elif text.startswith("+"):
            seg = [(text, Style(fg=(80, 200, 100)))]
        elif text.startswith("-"):
            seg = [(text, Style(fg=(210, 80, 80)))]
        else:
            seg = [(text, Style(fg=(190, 190, 190)))]
        result.append(seg)
    return result


def _replace_buffer_text(buf: Any, text: str) -> None:
    line_count = buf.line_count()
    end_line = max(0, line_count - 1)
    end_col = len(buf.get_line(end_line)) if line_count > 0 else 0
    buf.replace(0, 0, end_line, end_col, text)


# ---------------------------------------------------------------------------
# Sidebar panel
# ---------------------------------------------------------------------------


class _FileHistoryPanel:
    width = 40

    def __init__(self, api: Any, store: _LocalHistoryStore) -> None:
        from peovim.ui.tree_view import TreeView

        self._api = api
        self._store = store
        self._items: list[_HistoryItem] = []
        self._current_path: str | None = None
        self._tree = TreeView(
            [],
            title="File History",
            on_select=self._on_select,
            on_cursor_move=self._on_cursor_move,
            width=self.width,
        )
        self._preview_float: Any = None

    # ------------------------------------------------------------------
    # Sidebar protocol
    # ------------------------------------------------------------------

    def render(self, grid: Any) -> None:
        self._tree.focused = getattr(self, "_sidebar_focused", False)
        self._tree.blink_on = getattr(self, "_sidebar_blink_on", True)
        self._tree._width = grid.width
        self._tree.render(grid)

    def feed_key(self, key: str) -> bool:
        if key in ("r", "R"):
            self._reload(path_changed=False)
            return True
        if key == "q":
            self._close_preview()
            self._api.ui.hide_sidebar()
            return True
        self._tree.feed_key(key)
        return True

    def on_focus(self) -> None:
        self._tree.focused = True
        self._preview_current()

    def on_blur(self) -> None:
        self._tree.focused = False
        self._close_preview()

    def on_show(self) -> None:
        self.refresh()

    def on_hide(self) -> None:
        self._close_preview()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Refresh panel for the currently active buffer."""
        buf = self._api.active_buffer()
        path = getattr(buf, "path", None)
        if path is None:
            self._current_path = None
            self._items = []
            self._tree._title = "File History"
            self._tree.set_nodes(_message_nodes("No file-backed buffer"))
            self._close_preview()
            return
        new_path = str(Path(path).resolve())
        path_changed = new_path != self._current_path
        self._current_path = new_path
        self._reload(path_changed=path_changed)

    def _reload(self, *, path_changed: bool = True) -> None:
        """Re-read history entries from disk for the current path."""
        if self._current_path is None:
            return

        # Remember which snapshot was selected so we can re-anchor after rebuild.
        selected_snapshot: str | None = None
        if not path_changed:
            visible = self._tree._visible_nodes()
            if visible:
                idx = max(0, min(self._tree._selected_idx, len(visible) - 1))
                prev_node, _ = visible[idx]
                prev_item: _HistoryItem | None = prev_node.value
                if prev_item is not None:
                    selected_snapshot = prev_item.entry.snapshot

        self._items = self._store.items(self._current_path)
        fname = Path(self._current_path).name
        count = len(self._items)
        self._tree._title = f"History [{count}]"
        from peovim.ui.tree_view import TreeNode

        nodes = [TreeNode(label=_item_label(item), value=item) for item in self._items]
        self._tree.set_nodes(nodes or _message_nodes(f"No history — {fname}"))

        # Restore selection by snapshot name so a new save doesn't shift the cursor.
        if selected_snapshot is not None and nodes:
            for i, item in enumerate(self._items):
                if item.entry.snapshot == selected_snapshot:
                    self._tree._selected_idx = i
                    break

        # Keep the preview in sync after reload.
        if path_changed:
            self._close_preview()
        if getattr(self, "_sidebar_focused", False):
            self._preview_current()

    # ------------------------------------------------------------------
    # Selection / preview
    # ------------------------------------------------------------------

    def _on_select(self, node: Any) -> None:
        item: _HistoryItem | None = node.value
        if item is None:
            return
        buf = self._api.active_buffer()
        if buf is None:
            return
        # Guard: verify the active buffer still matches the panel's tracked file.
        buf_path = str(Path(buf.path).resolve()) if getattr(buf, "path", None) else None
        if buf_path != self._current_path:
            self.refresh()
            return
        self._close_preview()
        try:
            text = self._store.read_snapshot(item)
        except Exception:
            with contextlib.suppress(Exception):
                self._api.set_status("File History: could not read snapshot", level="warn")
            return
        _replace_buffer_text(buf, text)
        with contextlib.suppress(Exception):
            self._api.set_status(f"Restored: {item.entry.timestamp[:19].replace('T', ' ')} UTC")
        self._api.ui.blur_sidebar()

    def _on_cursor_move(self, node: Any) -> None:
        item: _HistoryItem | None = node.value
        if item is None:
            self._close_preview()
            return
        buf = self._api.active_buffer()
        if buf is None:
            self._close_preview()
            return
        # Guard: if active buffer drifted away, refresh instead of diffing.
        buf_path = str(Path(buf.path).resolve()) if getattr(buf, "path", None) else None
        if buf_path != self._current_path:
            self.refresh()
            return
        try:
            snapshot_text = self._store.read_snapshot(item)
            current_text = buf.get_text()
        except Exception:
            self._close_preview()
            return
        diff_lines = _build_diff_lines(snapshot_text, current_text, item)
        title = f"Diff  {item.entry.timestamp[:19].replace('T', ' ')} UTC"
        if self._preview_float is not None and self._preview_float.is_open:
            self._preview_float.set_content(diff_lines)
            self._preview_float.set_title(title)
        else:
            self._preview_float = self._api.ui.open_float(
                diff_lines,
                title=title,
                width=_PREVIEW_WIDTH,
                height=_PREVIEW_HEIGHT,
                border=True,
                focusable=False,
            )

    def _preview_current(self) -> None:
        visible = self._tree._visible_nodes()
        if not visible:
            return
        idx = max(0, min(self._tree._selected_idx, len(visible) - 1))
        node, _ = visible[idx]
        self._on_cursor_move(node)

    def _close_preview(self) -> None:
        if self._preview_float is not None:
            with contextlib.suppress(Exception):
                if self._preview_float.is_open:
                    self._preview_float.close()
            self._preview_float = None


# ---------------------------------------------------------------------------
# Plugin-level helpers
# ---------------------------------------------------------------------------

_panel: _FileHistoryPanel | None = None
_event_tokens: list[_EventToken] = []


def _refresh_if_visible(api: Any) -> None:
    if _panel is not None and api.ui.is_sidebar_visible(_PANEL_NAME):
        _panel.refresh()


def _toggle_panel(api: Any) -> None:
    if _panel is not None:
        api.ui.toggle_sidebar_panel(_PANEL_NAME, _panel, focus=True)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def setup(api: EditorAPI) -> None:
    """Register the file history sidebar plugin."""
    global _panel, _event_tokens

    store = _LocalHistoryStore(api)
    _panel = _FileHistoryPanel(api, store)
    api.ui.register_sidebar_panel(_PANEL_NAME, _panel)

    api.keymap.define_plug(
        "FileHistoryToggle",
        lambda: _toggle_panel(api),
        desc="File History: toggle panel",
    )
    api.keymap.nmap("<leader>fh", "<Plug>FileHistoryToggle", desc="File History: toggle panel")

    # Refresh when a different file becomes active or a new snapshot is saved.
    tok1 = api.events.on("buffer_opened", lambda **_kw: _refresh_if_visible(api))
    tok2 = api.events.on("buffer_saved", lambda **_kw: _refresh_if_visible(api))

    # Detect silent buffer switches (e.g. f/j navigation between open buffers).
    def _on_cursor_moved(**_kw: Any) -> None:
        if _panel is None or not api.ui.is_sidebar_visible(_PANEL_NAME):
            return
        buf = api.active_buffer()
        current = str(Path(buf.path).resolve()) if getattr(buf, "path", None) else None
        if current != _panel._current_path:
            _panel.refresh()

    tok3 = api.events.on("cursor_moved", _on_cursor_moved)
    _event_tokens = [tok1, tok2, tok3]


def teardown() -> None:
    global _panel, _event_tokens
    for tok in _event_tokens:
        with contextlib.suppress(Exception):
            tok.unregister()
    _event_tokens = []
    if _panel is not None:
        _panel._close_preview()
    _panel = None
