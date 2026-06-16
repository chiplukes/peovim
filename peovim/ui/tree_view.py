"""
ui.tree_view — TreeView: generic hierarchical tree widget

Used by the file explorer, symbol outline, and any directed graph that
can be viewed as a tree. Supports lazy node expansion via children_fn.

See notes/api.md for the tree-view API.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from peovim.ui.backend import Color

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TreeNode:
    """A node in a tree view."""

    label: str
    icon: str = ""
    value: Any = None
    fg: Color = None
    bg: Color = None
    children_fn: Callable[[], list[TreeNode]] | None = None
    expanded: bool = False
    _cached_children: list[TreeNode] = field(default_factory=list)
    # Optional multi-color label: list of (text, fg_color) tuples.
    # When set, overrides label+fg for non-selected rendering.
    label_segments: list[tuple[str, Any]] | None = None

    def has_children(self) -> bool:
        """Return True if this node can have children."""
        return self.children_fn is not None

    def get_children(self) -> list[TreeNode]:
        """Return children, loading from children_fn if not cached."""
        if not self._cached_children and self.children_fn is not None:
            self._cached_children = self.children_fn()
        return self._cached_children


# ---------------------------------------------------------------------------
# TreeView widget
# ---------------------------------------------------------------------------


class TreeView:  # cm:e3f1b4
    """
    A scrollable tree view widget.

    Renders as a left-anchored panel. Keyboard navigation via feed_key().
    """

    # Fold indicator glyphs
    _EXPANDED = "▼ "
    _COLLAPSED = "▶ "
    _LEAF = "  "

    def __init__(
        self,
        nodes: list[TreeNode],
        *,
        title: str = "",
        on_select: Callable[[TreeNode], None] | None = None,
        on_cursor_move: Callable[[TreeNode], None] | None = None,
        on_close: Callable[[], None] | None = None,
        on_key: Callable[[str, TreeNode | None], bool] | None = None,
        width: int = 30,
    ) -> None:
        self._roots = nodes
        self._title = title
        self._on_select = on_select
        self._on_cursor_move = on_cursor_move
        self._on_close = on_close
        self._on_key = on_key
        self._width = width
        self._selected_idx: int = 0  # index into _visible_nodes()
        self._scroll_top: int = 0  # first visible row offset
        self._is_open: bool = True
        self.focused: bool = False
        self.blink_on: bool = True  # controlled externally by sidebar blink tick

    # ------------------------------------------------------------------
    # Public actions
    # ------------------------------------------------------------------

    def expand(self, node: TreeNode) -> None:
        """Expand a node, loading its children if not cached."""
        if node.has_children():
            node.get_children()  # trigger lazy load
            node.expanded = True

    def collapse(self, node: TreeNode) -> None:
        """Collapse a node."""
        node.expanded = False

    def toggle(self, node: TreeNode) -> None:
        """Toggle expanded/collapsed state."""
        if node.expanded:
            self.collapse(node)
        else:
            self.expand(node)

    def refresh(self) -> None:
        """Clear all cached children and re-render."""
        self._clear_cache(self._roots)

    def set_nodes(self, nodes: list[TreeNode]) -> None:
        """Replace the tree roots and clamp selection to the new visible set."""
        self._roots = nodes
        visible = self._visible_nodes()
        if not visible:
            self._selected_idx = 0
            self._scroll_top = 0
        else:
            self._selected_idx = max(0, min(self._selected_idx, len(visible) - 1))

    @property
    def selected_node(self) -> TreeNode | None:
        """Return the currently selected node, if any."""
        visible = self._visible_nodes()
        if not visible:
            return None
        return visible[self._selected_idx][0]

    def select_value(self, value: Any) -> bool:
        """Select the first visible node whose value matches *value*."""
        visible = self._visible_nodes()
        for index, (node, _depth) in enumerate(visible):
            if node.value == value:
                self._selected_idx = index
                return True
        return False

    def _clear_cache(self, nodes: list[TreeNode]) -> None:
        for node in nodes:
            node._cached_children = []
            node.expanded = False

    def close(self) -> None:
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def feed_key(self, key: str) -> None:
        """Handle a key event."""
        # Handle close keys regardless of visible node count
        if key in ("q", "<Esc>"):
            if self._on_close is not None:
                self._on_close()
            self.close()
            return

        if key == "R":
            self.refresh()
            return

        visible = self._visible_nodes()
        if not visible:
            if self._on_key is not None:
                self._on_key(key, None)
            return

        if key in ("j", "<Down>"):
            self._selected_idx = min(self._selected_idx + 1, len(visible) - 1)
            if self._on_cursor_move is not None:
                node, _ = visible[self._selected_idx]
                self._on_cursor_move(node)

        elif key in ("k", "<Up>"):
            self._selected_idx = max(self._selected_idx - 1, 0)
            if self._on_cursor_move is not None:
                node, _ = visible[self._selected_idx]
                self._on_cursor_move(node)

        elif key in ("<CR>", "l", "o"):
            node, depth = visible[self._selected_idx]
            if node.has_children():
                self.toggle(node)
            elif self._on_select is not None:
                self._on_select(node)

        elif key == "h":
            # Collapse current node, or jump to parent
            node, depth = visible[self._selected_idx]
            if node.expanded:
                self.collapse(node)
            else:
                # Jump to parent
                parent, parent_idx = self._find_parent(visible, self._selected_idx)
                if parent is not None and parent_idx is not None:
                    self._selected_idx = parent_idx
                    self.collapse(parent)

        elif self._on_key is not None:
            node, _depth = visible[self._selected_idx]
            if self._on_key(key, node):
                visible = self._visible_nodes()
                if not visible:
                    self._selected_idx = 0
                    return

        # Clamp selection
        self._selected_idx = max(0, min(self._selected_idx, len(visible) - 1))

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self, grid) -> None:
        """Render the tree into the grid as a left panel."""
        from peovim.ui.backend import ATTR_BOLD

        height = grid.height
        visible = self._visible_nodes()
        blink_on = self.blink_on if self.focused else True

        # Title bar
        if self._title:
            title_str = f" {self._title}"[: self._width].ljust(self._width)
            grid.write_str(0, 0, title_str, fg=(200, 200, 255), attrs=ATTR_BOLD)
            start_row = 1
        else:
            start_row = 0

        content_rows = height - start_row

        # Keep selected item visible by adjusting scroll offset
        if self._selected_idx < self._scroll_top:
            self._scroll_top = self._selected_idx
        elif content_rows > 0 and self._selected_idx >= self._scroll_top + content_rows:
            self._scroll_top = self._selected_idx - content_rows + 1

        for row_idx in range(start_row, height):
            vis_idx = row_idx - start_row + self._scroll_top
            # Fill background
            grid.fill(row_idx, 0, self._width)
            if vis_idx >= len(visible):
                continue

            node, depth = visible[vis_idx]
            indent = "  " * depth
            fold = self._EXPANDED if node.expanded else (self._COLLAPSED if node.has_children() else self._LEAF)
            icon = (node.icon + " ") if node.icon else ""
            text = (indent + fold + icon + node.label)[: self._width].ljust(self._width)

            is_selected = vis_idx == self._selected_idx
            if is_selected and self.focused:
                fg = (0, 0, 0)
                bg = (80, 140, 200)
                if blink_on:
                    grid.write_str(row_idx, 0, text, fg=fg, bg=bg)
                else:
                    # Blink: blank the first character, keep rest highlighted.
                    grid.write_str(row_idx, 0, " ", fg=fg, bg=bg)
                    grid.write_str(row_idx, 1, text[1:], fg=fg, bg=bg)
            elif is_selected:
                fg = (0, 0, 0)
                bg = (80, 140, 200)
                grid.write_str(row_idx, 0, text, fg=fg, bg=bg)
            elif node.label_segments:
                # Multi-color rendering: write prefix then each segment.
                prefix = (indent + fold + icon)[: self._width]
                col = 0
                remaining = self._width
                if prefix:
                    grid.write_str(row_idx, col, prefix, fg=node.fg, bg=node.bg)
                    col += len(prefix)
                    remaining -= len(prefix)
                for seg_text, seg_fg in node.label_segments:
                    if remaining <= 0:
                        break
                    clipped = seg_text[:remaining]
                    grid.write_str(row_idx, col, clipped, fg=seg_fg, bg=node.bg)
                    col += len(clipped)
                    remaining -= len(clipped)
                if remaining > 0:
                    grid.write_str(row_idx, col, " " * remaining, fg=node.fg, bg=node.bg)
            else:
                grid.write_str(row_idx, 0, text, fg=node.fg, bg=node.bg)

    def cursor_row(self, available_height: int) -> int | None:
        """Return the row within the tree grid where the terminal cursor should sit.

        Returns *None* if the selection is scrolled out of view or the tree is empty.
        """
        visible = self._visible_nodes()
        if not visible:
            return None
        start_row = 1 if self._title else 0
        item_row = start_row + (self._selected_idx - self._scroll_top)
        if item_row < start_row or item_row >= available_height:
            return None
        return item_row

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _visible_nodes(self) -> list[tuple[TreeNode, int]]:
        """Return (node, depth) pairs for all visible nodes in DFS order."""
        result: list[tuple[TreeNode, int]] = []
        self._collect(self._roots, 0, result)
        return result

    def _collect(self, nodes: list[TreeNode], depth: int, result: list) -> None:
        for node in nodes:
            result.append((node, depth))
            if node.expanded:
                children = node.get_children()
                self._collect(children, depth + 1, result)

    def _find_parent(self, visible: list[tuple[TreeNode, int]], idx: int) -> tuple[TreeNode | None, int | None]:
        """Find the parent node of visible[idx], scanning backwards for a shallower depth."""
        if idx <= 0:
            return None, None
        _, target_depth = visible[idx]
        for i in range(idx - 1, -1, -1):
            node, depth = visible[i]
            if depth < target_depth:
                return node, i
        return None, None


# ---------------------------------------------------------------------------
# TreeViewHandle — returned by UIAPI.open_tree()
# ---------------------------------------------------------------------------


class TreeViewHandle:
    """Public handle to a TreeView, returned by UIAPI.open_tree()."""

    def __init__(self, tree: TreeView, on_remove: Callable | None = None) -> None:
        self._tree = tree
        self._on_remove = on_remove

    def close(self) -> None:
        """Close and remove the tree view."""
        self._tree.close()
        if self._on_remove is not None:
            with contextlib.suppress(Exception):
                self._on_remove(self)

    def refresh(self) -> None:
        self._tree.refresh()

    @property
    def is_open(self) -> bool:
        return self._tree.is_open

    @property
    def tree(self) -> TreeView:
        return self._tree
