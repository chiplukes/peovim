"""Persistent diagnostics sidebar sourced from editor diagnostics across open buffers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI
    from peovim.ui.tree_view import TreeNode

_PANEL_NAME = "diagnostics"
_panel: _DiagnosticsSidebarPanel | None = None


class _DiagnosticsSidebarPanel:  # cm:b3e5d8
    def __init__(self, api: Any, *, width: int = 36) -> None:
        from peovim.ui.tree_view import TreeView

        self._api = api
        self.width = width
        self._tree = TreeView(
            [], title="Diagnostics", on_select=self._on_select, on_cursor_move=self._on_cursor_move, width=width
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
        self.refresh()

    def refresh(self) -> None:
        diagnostics = _sorted_diagnostics(self._api.list_diagnostics())
        self._tree._title = _title_for_diagnostics(diagnostics)
        selected_value = _selected_value(self._tree)
        expanded_values = _expanded_values(self._tree)
        current_path = _active_path(self._api)
        nodes = _build_diagnostic_nodes(
            diagnostics,
            expanded_values=expanded_values,
            current_path=current_path,
        )
        self._tree.set_nodes(nodes or _message_nodes("No diagnostics"))
        if selected_value is not None:
            self._tree.select_value(selected_value)

    def _on_select(self, node: Any) -> None:
        if node.value is None or not isinstance(node.value, tuple) or len(node.value) != 3:
            return
        path, line, col = node.value
        self._api.goto_location(Path(path), line, col)
        self._api.ui.blur_sidebar()

    def _on_cursor_move(self, node: Any) -> None:
        """Preview the diagnostic under the cursor without leaving the diagnostics panel."""
        if node.value is None or not isinstance(node.value, tuple) or len(node.value) != 3:
            return
        path, line, col = node.value
        active_path = _active_path(self._api)
        if active_path is not None and Path(path).resolve() == Path(active_path).resolve():
            win = self._api.active_window()
            win.set_cursor(line, col)
            win.scroll_to_cursor()
        else:
            self._api.open_buffer(Path(path), line, col)


def setup(api: EditorAPI) -> None:
    """Register the persistent diagnostics sidebar."""
    global _panel
    _panel = _DiagnosticsSidebarPanel(api)

    api.ui.register_sidebar_panel(_PANEL_NAME, _panel)
    api.keymap.define_plug("DiagnosticsPanel", lambda: _toggle_diagnostics(api), desc="Diagnostics: sidebar")
    api.keymap.nmap("<leader>cD", "<Plug>DiagnosticsPanel", desc="Diagnostics: sidebar")
    api.commands.register("DiagnosticsPanel", lambda cmd, ctx: _toggle_diagnostics(api), min_abbrev=11)

    api.events.on("buffer_opened", lambda **kwargs: _refresh_if_visible(api))
    api.events.on("diagnostics_updated", lambda **kwargs: _refresh_if_visible(api))


def _toggle_diagnostics(api: Any) -> None:
    panel = api.ui.get_sidebar_panel(_PANEL_NAME)
    if panel is None:
        return
    if api.ui.is_sidebar_visible(_PANEL_NAME):
        api.ui.hide_sidebar()
        return
    panel.refresh()
    api.ui.show_sidebar_panel(_PANEL_NAME, panel, focus=True)


def _refresh_if_visible(api: Any) -> None:
    if not api.ui.is_sidebar_visible(_PANEL_NAME):
        return
    panel = api.ui.get_sidebar_panel(_PANEL_NAME)
    if panel is None or not hasattr(panel, "refresh"):
        return
    panel.refresh()


def _title_for_diagnostics(diagnostics: list[dict[str, Any]]) -> str:
    count = len(diagnostics)
    return f"Diagnostics [{count}]" if count else "Diagnostics"


def _sorted_diagnostics(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    severity_order = {"E": 0, "W": 1, "I": 2, "H": 3}
    return sorted(
        diagnostics,
        key=lambda item: (
            severity_order.get(str(item.get("severity", "")).upper(), 9),
            str(item.get("path", "")),
            int(item.get("line", 0)),
            int(item.get("col", 0)),
        ),
    )


def _active_path(api: Any) -> str | None:
    buf = api.active_buffer()
    if buf is None or buf.path is None:
        return None
    return str(buf.path)


def _selected_value(tree: Any) -> tuple[str, int, int] | None:
    node = tree.selected_node
    if node is None or not isinstance(node.value, tuple) or len(node.value) != 3:
        return None
    path, line, col = node.value
    return str(path), int(line), int(col)


def _expanded_values(tree: Any) -> set[tuple[str, str]]:
    expanded: set[tuple[str, str]] = set()

    def _walk(nodes: list[Any]) -> None:
        for node in nodes:
            if node.expanded and isinstance(node.value, tuple) and len(node.value) == 2:
                expanded.add((str(node.value[0]), str(node.value[1])))
                _walk(node.get_children())

    _walk(tree._roots)
    return expanded


def _message_nodes(message: str) -> list[TreeNode]:
    from peovim.ui.tree_view import TreeNode

    return [TreeNode(label=message)]


def _build_diagnostic_nodes(
    diagnostics: list[dict[str, Any]],
    *,
    expanded_values: set[tuple[str, str]] | None = None,
    current_path: str | None = None,
) -> list[TreeNode]:
    from peovim.ui.tree_view import TreeNode

    expanded_values = expanded_values or set()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for diagnostic in diagnostics:
        grouped.setdefault(str(diagnostic.get("path", "")), []).append(diagnostic)

    roots: list[TreeNode] = []
    for index, path in enumerate(grouped):
        items = grouped[path]
        root_value = ("file", path)
        children = [
            TreeNode(
                label=_diagnostic_label(item),
                icon=_severity_icon(str(item.get("severity", ""))),
                value=(path, int(item.get("line", 0)), int(item.get("col", 0))),
            )
            for item in items
        ]
        root = TreeNode(
            label=f"{Path(path).name} ({len(items)})",
            value=root_value,
            children_fn=(lambda entries=children: entries),
        )
        root._cached_children = children
        root.expanded = root_value in expanded_values or path == current_path or (index == 0 and len(grouped) == 1)
        roots.append(root)
    return roots


def _diagnostic_label(item: dict[str, Any]) -> str:
    line = int(item.get("line", 0)) + 1
    col = int(item.get("col", 0)) + 1
    message = str(item.get("message") or "").strip() or "(no message)"
    return f"{line}:{col} {message}"


def _severity_icon(severity: str) -> str:
    icons = {
        "E": "err",
        "W": "warn",
        "I": "info",
        "H": "hint",
    }
    return icons.get(severity.upper(), "diag")
