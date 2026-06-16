"""Persistent workspace symbols sidebar sourced from LSP workspace symbols."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI
    from peovim.commands.parser import ParsedCommand
    from peovim.ui.tree_view import TreeNode

_PANEL_NAME = "workspace-symbols"
_panel: _WorkspaceSymbolsSidebarPanel | None = None


class _WorkspaceSymbolsSidebarPanel:
    def __init__(self, api: Any, *, width: int = 34) -> None:
        from peovim.ui.tree_view import TreeView

        self._api = api
        self.width = width
        self._query = ""
        self._tree = TreeView(
            [],
            title="Workspace Symbols",
            on_select=self._on_select,
            on_cursor_move=self._on_cursor_move,
            on_key=self._on_key,
            width=width,
        )

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
        if not self._query:
            self._query = _word_under_cursor(self._api)
        self.refresh()

    def set_query(self, query: str) -> None:
        self._query = query.strip()

    def refresh(self) -> None:
        self._tree._title = _title_for_query(self._query)
        if self._api.lsp is None:
            self._tree.set_nodes(_message_nodes("LSP unavailable"))
            return
        if not self._query:
            self._tree.set_nodes(_message_nodes("No workspace symbol query"))
            return

        def _apply(symbols: list[dict]) -> None:
            self._tree._title = _title_for_query(self._query)
            self._tree.set_nodes(_build_symbol_nodes(symbols) or _message_nodes(f"No symbols for '{self._query}'"))

        self._api.lsp.workspace_symbol_search(self._query, _apply)

    def _on_select(self, node: Any) -> None:
        if node.value is None:
            return
        path, line, col, _kind, _name = node.value
        self._api.goto_location(Path(path), line, col)
        self._api.ui.blur_sidebar()

    def _on_cursor_move(self, node: Any) -> None:
        """Preview the symbol under the cursor without leaving the panel."""
        if node.value is None:
            return
        path, line, col, _kind, _name = node.value
        active_path = _active_path(self._api)
        if active_path is not None and Path(path).resolve() == Path(active_path).resolve():
            win = self._api.active_window()
            win.set_cursor(line, col)
            win.scroll_to_cursor()
        else:
            self._api.open_buffer(Path(path), line, col)

    def _on_key(self, key: str, node: Any | None) -> bool:
        if key != "/":
            return False
        self._api.open_cmdline(f"WorkspaceSymbolsPanel {self._query}".rstrip())
        return True


def setup(api: EditorAPI) -> None:
    """Register the persistent workspace symbols sidebar."""
    global _panel
    _panel = _WorkspaceSymbolsSidebarPanel(api)
    api.ui.register_sidebar_panel(_PANEL_NAME, _panel)

    api.keymap.define_plug(
        "WorkspaceSymbolsPanel",
        lambda: _toggle_workspace_symbols(api),
        desc="Workspace symbols: toggle sidebar",
    )
    api.keymap.nmap(
        "<leader>csW",
        "<Plug>WorkspaceSymbolsPanel",
        desc="Workspace symbols: sidebar",
    )
    api.commands.register(
        "WorkspaceSymbolsPanel",
        lambda cmd, ctx: _command_workspace_symbols_panel(api, cmd),
        min_abbrev=18,
    )


def _command_workspace_symbols_panel(api: Any, cmd: ParsedCommand) -> None:
    query = cmd.args.strip() if hasattr(cmd, "args") else ""
    _toggle_workspace_symbols(api, query=(query or None), explicit_query=bool(query))


def _toggle_workspace_symbols(api: Any, *, query: str | None = None, explicit_query: bool = False) -> None:
    panel = api.ui.get_sidebar_panel(_PANEL_NAME)
    if panel is None:
        return
    if not explicit_query and query is None:
        query = _word_under_cursor(api)
    current_query = getattr(panel, "_query", "")
    if api.ui.is_sidebar_visible(_PANEL_NAME) and (not query or query == current_query):
        api.ui.hide_sidebar()
        return
    if query:
        panel.set_query(query)
    if hasattr(panel, "refresh"):
        panel.refresh()
    api.ui.show_sidebar_panel(_PANEL_NAME, panel, focus=True)


def _active_path(api: Any) -> str | None:
    buf = api.active_buffer()
    if buf is None or buf.path is None:
        return None
    return str(buf.path)


def _title_for_query(query: str) -> str:
    return f"Workspace Symbols [{query}]" if query else "Workspace Symbols"


def _message_nodes(message: str) -> list[TreeNode]:
    from peovim.ui.tree_view import TreeNode

    return [TreeNode(label=message)]


def _build_symbol_nodes(symbols: list[dict]) -> list[TreeNode]:
    from peovim.ui.tree_view import TreeNode

    nodes = []
    for symbol in symbols:
        detail = f" — {symbol['detail']}" if symbol.get("detail") else ""
        nodes.append(
            TreeNode(
                label=f"{symbol['kind']:<12} {symbol['name']}{detail}",
                value=_symbol_value(symbol),
            )
        )
    return nodes


def _symbol_value(symbol: dict) -> tuple:
    return (
        str(symbol.get("path", "")),
        int(symbol.get("line", 0)),
        int(symbol.get("col", 0)),
        str(symbol.get("kind", "")),
        str(symbol.get("name", "")),
    )


def _word_under_cursor(api: Any) -> str:
    win = api.active_window()
    buf = api.active_buffer()
    line_no, col = win.cursor
    line = buf.get_line(line_no)
    if not line:
        return ""
    col = min(col, max(0, len(line) - 1))
    if not (line[col].isalnum() or line[col] == "_"):
        if col > 0 and (line[col - 1].isalnum() or line[col - 1] == "_"):
            col -= 1
        else:
            return ""
    start = col
    end = col + 1
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
        start -= 1
    while end < len(line) and (line[end].isalnum() or line[end] == "_"):
        end += 1
    return line[start:end]
