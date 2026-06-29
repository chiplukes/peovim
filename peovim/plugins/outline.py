"""Persistent document outline sidebar sourced from LSP document symbols."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI
    from peovim.ui.tree_view import TreeNode

_PANEL_NAME = "outline"
_REFRESH_DELAY_MS = 200
_panel: _OutlineSidebarPanel | None = None


class _OutlineSidebarPanel:
    def __init__(self, api: Any, *, width: int = 32) -> None:
        from peovim.ui.tree_view import TreeView

        self._api = api
        self.width = width
        self._tree = TreeView(
            [], title="Outline", on_select=self._on_select, on_cursor_move=self._on_cursor_move, width=width
        )
        self._refresh_handle: Any = None

    def render(self, grid: Any) -> None:
        self._tree.focused = getattr(self, "_sidebar_focused", False)
        self._tree.blink_on = getattr(self, "_sidebar_blink_on", True)
        self._tree._width = grid.width
        self._tree.render(grid)

    def feed_key(self, key: str) -> bool:
        if key == "R":
            self.refresh()
            return True
        self._tree.feed_key(key)
        return True

    def on_focus(self) -> None:
        self._tree.focused = True

    def on_blur(self) -> None:
        self._tree.focused = False

    def on_show(self) -> None:
        self.refresh()

    def on_hide(self) -> None:
        self._cancel_scheduled_refresh()

    def schedule_refresh(self, delay_ms: int = _REFRESH_DELAY_MS) -> None:
        self._cancel_scheduled_refresh()
        try:
            loop = asyncio.get_event_loop()
            self._refresh_handle = loop.call_later(delay_ms / 1000.0, self.refresh)
        except RuntimeError:
            self.refresh()

    def refresh(self) -> None:
        self._refresh_handle = None
        title = _outline_title(self._api)
        path = _active_path(self._api)
        if path is None:
            self._tree._title = title
            self._tree.set_nodes(_message_nodes("No file path"))
            return
        if self._api.lsp is None:
            self._tree._title = title
            self._tree.set_nodes(_message_nodes("LSP unavailable"))
            return

        expanded_values = _expanded_values(self._tree)
        cursor_line = self._api.active_window().cursor[0]

        def _apply(symbols: list[dict]) -> None:
            active_path = _active_path(self._api)
            if active_path != path:
                return
            selected_path = _best_symbol_path(symbols, cursor_line)
            selected_value = None
            if selected_path:
                expanded_values.update(_symbol_value(symbol) for symbol in selected_path[:-1])
                selected_value = _symbol_value(selected_path[-1])
            nodes = _build_outline_nodes(symbols, expanded_values=expanded_values)
            self._tree._title = title
            self._tree.set_nodes(nodes or _message_nodes("No document symbols"))
            if selected_value is not None:
                self._tree.select_value(selected_value)

        self._api.lsp.document_symbol_tree(_apply)

    def _on_select(self, node: Any) -> None:
        if node.value is None:
            return
        path, line, col, _kind, _name = node.value
        self._api.goto_location(Path(path), line, col)
        self._api.ui.blur_sidebar()

    def _on_cursor_move(self, node: Any) -> None:
        """Preview the symbol under the cursor without leaving the outline panel."""
        if node.value is None:
            return
        path, line, col, _kind, _name = node.value
        active_path = _active_path(self._api)
        if active_path is None or Path(path).resolve() != Path(active_path).resolve():
            return
        win = self._api.active_window()
        win.set_cursor(line, col)
        win.scroll_to_cursor()

    def _cancel_scheduled_refresh(self) -> None:
        if self._refresh_handle is None:
            return
        with contextlib.suppress(Exception):
            self._refresh_handle.cancel()
        self._refresh_handle = None


def setup(api: EditorAPI) -> None:
    """Register the persistent outline sidebar."""
    global _panel
    _panel = _OutlineSidebarPanel(api)

    api.ui.register_sidebar_panel(_PANEL_NAME, _panel)
    api.keymap.define_plug("OutlineToggle", lambda: _toggle_outline(api), desc="Outline: toggle sidebar")
    api.keymap.nmap("<leader>o", "<Plug>OutlineToggle", desc="Outline: toggle sidebar")
    api.commands.register("Outline", lambda cmd, ctx: _toggle_outline(api), min_abbrev=3)

    api.events.on("buffer_opened", lambda **kwargs: _refresh_if_visible(api, immediate=True))
    api.events.on("buffer_saved", lambda **kwargs: _refresh_if_visible(api, immediate=True))
    api.events.on("buffer_changed", lambda **kwargs: _refresh_if_visible(api, immediate=False))
    api.events.on("cursor_moved", lambda **kwargs: _refresh_if_visible(api, immediate=False))


def _toggle_outline(api: Any) -> None:
    panel = api.ui.get_sidebar_panel(_PANEL_NAME)
    if panel is None:
        return
    if api.ui.is_sidebar_visible(_PANEL_NAME):
        api.ui.hide_sidebar()
        return
    if hasattr(panel, "refresh"):
        panel.refresh()
    api.ui.show_sidebar_panel(_PANEL_NAME, panel, focus=True)


def _refresh_if_visible(api: Any, *, immediate: bool) -> None:
    if not api.ui.is_sidebar_visible(_PANEL_NAME):
        return
    panel = api.ui.get_sidebar_panel(_PANEL_NAME)
    if panel is None:
        return
    if immediate or not hasattr(panel, "schedule_refresh"):
        panel.refresh()
        return
    panel.schedule_refresh()


def _outline_title(api: Any) -> str:
    path = _active_path(api)
    if path is None:
        return "Outline"
    return f"Outline [{Path(path).name}]"


def _active_path(api: Any) -> str | None:
    buf = api.active_buffer()
    if buf is None or buf.path is None:
        return None
    return str(buf.path)


def _message_nodes(message: str) -> list[TreeNode]:
    from peovim.ui.tree_view import TreeNode

    return [TreeNode(label=message)]


def _build_outline_nodes(symbols: list[dict], *, expanded_values: set[tuple] | None = None) -> list[TreeNode]:
    from peovim.ui.tree_view import TreeNode

    expanded_values = expanded_values or set()
    nodes = []
    for index, symbol in enumerate(symbols):
        children = _build_outline_nodes(symbol.get("children", []), expanded_values=expanded_values)
        value = _symbol_value(symbol)
        node = TreeNode(
            label=str(symbol.get("name", "")),
            icon=_symbol_icon(str(symbol.get("kind", ""))),
            value=value,
            children_fn=(lambda items=children: items) if children else None,  # type: ignore[misc]
        )
        if children:
            node._cached_children = children
            node.expanded = value in expanded_values or index == 0
        nodes.append(node)
    return nodes


def _expanded_values(tree: Any) -> set[tuple]:
    expanded: set[tuple] = set()

    def _walk(nodes: list[Any]) -> None:
        for node in nodes:
            if node.expanded and node.value is not None:
                expanded.add(node.value)
                _walk(node.get_children())

    _walk(tree._roots)
    return expanded


def _best_symbol_path(symbols: list[dict], cursor_line: int) -> list[dict]:
    entries: list[list[dict]] = []
    _collect_symbol_paths(symbols, [], entries)
    containing = [path for path in entries if _path_contains_line(path, cursor_line)]
    if containing:
        return max(containing, key=len)
    previous = [path for path in entries if int(path[-1].get("line", 0)) <= cursor_line]
    if previous:
        return max(previous, key=lambda path: (int(path[-1].get("line", 0)), len(path)))
    return entries[0] if entries else []


def _collect_symbol_paths(symbols: list[dict], prefix: list[dict], result: list[list[dict]]) -> None:
    for symbol in symbols:
        path = [*prefix, symbol]
        result.append(path)
        children = symbol.get("children", [])
        if isinstance(children, list):
            _collect_symbol_paths(children, path, result)


def _path_contains_line(path: list[dict], cursor_line: int) -> bool:
    symbol = path[-1]
    start = int(symbol.get("line", 0))
    end = int(symbol.get("end_line", start))
    return start <= cursor_line <= end


def _symbol_value(symbol: dict) -> tuple:
    return (
        str(symbol.get("path", "")),
        int(symbol.get("line", 0)),
        int(symbol.get("col", 0)),
        str(symbol.get("kind", "")),
        str(symbol.get("name", "")),
    )


def _symbol_icon(kind: str) -> str:
    icons = {
        "class": "cls",
        "function": "fn",
        "method": "meth",
        "module": "mod",
        "interface": "if",
        "struct": "struct",
        "property": "prop",
        "field": "fld",
        "variable": "var",
        "const": "const",
        "enum": "enum",
    }
    return icons.get(kind, kind[:3])
