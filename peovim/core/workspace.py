"""
core.workspace — Split tree and tab pages

Workspace manages the binary split tree (HSplitNode / VSplitNode / WindowLeaf)
and the list of TabPages. Operations: split, close, focus, resize, tab management.

See notes/architecture.md for the Buffer/Window/Tab Model.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.core.window import Window


# ---------------------------------------------------------------------------
# Split tree nodes
# ---------------------------------------------------------------------------


class SplitNode:
    """Base for all split tree nodes."""

    @property
    def is_leaf(self) -> bool:
        return False

    def all_leaves(self) -> list[WindowLeaf]:
        raise NotImplementedError


class WindowLeaf(SplitNode):
    """A leaf node in the split tree — contains one Window."""

    def __init__(self, window: Window) -> None:
        self.window = window

    @property
    def is_leaf(self) -> bool:
        return True

    def all_leaves(self) -> list[WindowLeaf]:
        return [self]


class HSplitNode(SplitNode):
    """Horizontal split: top / bottom."""

    def __init__(self, top: SplitNode, bottom: SplitNode, ratio: float = 0.5) -> None:
        self.top = top
        self.bottom = bottom
        self.ratio = ratio

    def all_leaves(self) -> list[WindowLeaf]:
        return self.top.all_leaves() + self.bottom.all_leaves()


class VSplitNode(SplitNode):
    """Vertical split: left / right."""

    def __init__(self, left: SplitNode, right: SplitNode, ratio: float = 0.5) -> None:
        self.left = left
        self.right = right
        self.ratio = ratio

    def all_leaves(self) -> list[WindowLeaf]:
        return self.left.all_leaves() + self.right.all_leaves()


# ---------------------------------------------------------------------------
# TabPage
# ---------------------------------------------------------------------------


class TabPage:
    """
    One tab page. Owns the split tree for that tab and tracks the active window.
    """

    def __init__(self, root: SplitNode) -> None:
        self.root: SplitNode = root
        # Track active leaf
        leaves = root.all_leaves()
        self._active_leaf: WindowLeaf = leaves[0] if leaves else WindowLeaf(None)  # type: ignore[arg-type]

    @property
    def active_window(self) -> Window:
        return self._active_leaf.window

    def all_windows(self) -> list[Window]:
        return [leaf.window for leaf in self.root.all_leaves()]

    def all_leaves(self) -> list[WindowLeaf]:
        return self.root.all_leaves()

    # ------------------------------------------------------------------
    # Splits
    # ------------------------------------------------------------------

    def split_horizontal(self) -> Window:
        """Split the active window horizontally (top/bottom). Returns new window sharing same document."""
        from peovim.core.window import Window

        active_win = self._active_leaf.window
        new_win = Window(active_win.document, width=active_win.width, height=active_win.height // 2)
        new_win.cursor.line = active_win.cursor.line
        new_win.cursor.col = active_win.cursor.col
        new_win.scroll_line = active_win.scroll_line
        new_win.scroll_col = active_win.scroll_col
        new_leaf = WindowLeaf(new_win)
        old_leaf = self._active_leaf
        new_node = HSplitNode(old_leaf, new_leaf)
        if self.root is old_leaf:
            self.root = new_node
        else:
            self._replace_leaf(self.root, old_leaf, new_node)
        self._active_leaf = new_leaf
        return new_win

    def split_vertical(self) -> Window:
        """Split the active window vertically (left/right). Returns new window sharing same document."""
        from peovim.core.window import Window

        active_win = self._active_leaf.window
        new_win = Window(active_win.document, width=active_win.width // 2, height=active_win.height)
        new_win.cursor.line = active_win.cursor.line
        new_win.cursor.col = active_win.cursor.col
        new_win.scroll_line = active_win.scroll_line
        new_win.scroll_col = active_win.scroll_col
        new_leaf = WindowLeaf(new_win)
        old_leaf = self._active_leaf
        new_node = VSplitNode(old_leaf, new_leaf)
        if self.root is old_leaf:
            self.root = new_node
        else:
            self._replace_leaf(self.root, old_leaf, new_node)
        self._active_leaf = new_leaf
        return new_win

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def close_active(self) -> None:
        """Close the active window. Raises ValueError if it is the last one."""
        leaves = self.root.all_leaves()
        if len(leaves) <= 1:
            raise ValueError("Cannot close the last window in a tab")

        target = self._active_leaf
        replacement, parent_ref = self._find_replacement(self.root, target)
        if replacement is None:
            raise ValueError("Could not find replacement window")

        if parent_ref is None:
            self.root = replacement
        else:
            parent_split, side = parent_ref
            if side == "top":
                parent_split.top = replacement  # type: ignore[union-attr]
            elif side == "bottom":
                parent_split.bottom = replacement  # type: ignore[union-attr]
            elif side == "left":
                parent_split.left = replacement  # type: ignore[union-attr]
            elif side == "right":
                parent_split.right = replacement  # type: ignore[union-attr]

        remaining = replacement.all_leaves()
        self._active_leaf = remaining[0]

    # ------------------------------------------------------------------
    # Focus cycling
    # ------------------------------------------------------------------

    def focus_next(self) -> None:
        leaves = self.root.all_leaves()
        idx = self._active_index(leaves)
        self._active_leaf = leaves[(idx + 1) % len(leaves)]

    def focus_prev(self) -> None:
        leaves = self.root.all_leaves()
        idx = self._active_index(leaves)
        self._active_leaf = leaves[(idx - 1) % len(leaves)]

    def focus_window(self, window: Window) -> None:
        for leaf in self.root.all_leaves():
            if leaf.window is window:
                self._active_leaf = leaf
                return
        raise ValueError("Window not found in this tab")

    def focus_direction(self, direction: str) -> None:
        """Focus the window adjacent in the given direction (h/j/k/l).

        Walks up the split tree from the active leaf to find the nearest
        ancestor split whose structure allows movement in that direction.
        """
        # Build parent map: node_id -> (parent_split, side_key)
        parents: dict[int, tuple] = {}

        def _build(node: SplitNode, parent: SplitNode | None = None, side: str = "") -> None:
            if parent is not None:
                parents[id(node)] = (parent, side)
            if isinstance(node, HSplitNode):
                _build(node.top, node, "top")
                _build(node.bottom, node, "bottom")
            elif isinstance(node, VSplitNode):
                _build(node.left, node, "left")
                _build(node.right, node, "right")

        _build(self.root)

        # Determine which split type and which side to look for
        split_cls: type[SplitNode]
        if direction == "h":
            split_cls, need_side, target_attr, pick = VSplitNode, "right", "left", -1
        elif direction == "l":
            split_cls, need_side, target_attr, pick = VSplitNode, "left", "right", 0
        elif direction == "k":
            split_cls, need_side, target_attr, pick = HSplitNode, "bottom", "top", -1
        elif direction == "j":
            split_cls, need_side, target_attr, pick = HSplitNode, "top", "bottom", 0
        else:
            return

        node: SplitNode = self._active_leaf
        while id(node) in parents:
            parent, side = parents[id(node)]
            if isinstance(parent, split_cls) and side == need_side:
                sibling = getattr(parent, target_attr)
                leaves = sibling.all_leaves()
                self._active_leaf = leaves[pick]
                return
            node = parent

    def only_window(self) -> None:
        """Close all windows except the active one."""
        self.root = self._active_leaf

    def resize_active(self, direction: str, delta: int) -> bool:
        """Resize the active window only within its directly enclosing split."""
        if delta == 0:
            return False
        split_cls: type[SplitNode]
        if direction == "h":
            split_cls = VSplitNode
            positive_side = "left"
        elif direction == "v":
            split_cls = HSplitNode
            positive_side = "top"
        else:
            return False

        path = self._path_to_active_leaf()
        if not path:
            return False

        parent, side = path[-1]
        if not isinstance(parent, split_cls):
            return False

        sign = 1 if side == positive_side else -1
        step = 0.05 * delta * sign
        parent.ratio = max(0.1, min(0.9, parent.ratio + step))
        return True

    def toggle_expand_active_width(self, width_fraction: float = 0.75) -> bool:
        """Toggle the active window within its directly enclosing vertical split."""
        path = self._path_to_active_leaf()
        if not path:
            return False

        parent, side = path[-1]
        if not isinstance(parent, VSplitNode):
            return False

        current_share = parent.ratio if side == "left" else (1.0 - parent.ratio)

        if current_share >= width_fraction - 0.05:
            parent.ratio = 0.5
            return False

        target = max(0.55, min(0.9, width_fraction))
        parent.ratio = target if side == "left" else (1.0 - target)
        return True

    def equalize_window_sizes(self) -> None:
        """Reset all split ratios to an even 50/50 layout."""

        def _recurse(node: SplitNode) -> None:
            if isinstance(node, HSplitNode):
                node.ratio = 0.5
                _recurse(node.top)
                _recurse(node.bottom)
            elif isinstance(node, VSplitNode):
                node.ratio = 0.5
                _recurse(node.left)
                _recurse(node.right)

        _recurse(self.root)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _active_index(self, leaves: list[WindowLeaf]) -> int:
        for i, leaf in enumerate(leaves):
            if leaf is self._active_leaf:
                return i
        return 0

    def _replace_leaf(self, node: SplitNode, target: WindowLeaf, replacement: SplitNode) -> bool:
        """Replace target leaf with replacement in-place. Returns True if found."""
        if isinstance(node, HSplitNode):
            if node.top is target:
                node.top = replacement
                return True
            if node.bottom is target:
                node.bottom = replacement
                return True
            return self._replace_leaf(node.top, target, replacement) or self._replace_leaf(
                node.bottom, target, replacement
            )
        if isinstance(node, VSplitNode):
            if node.left is target:
                node.left = replacement
                return True
            if node.right is target:
                node.right = replacement
                return True
            return self._replace_leaf(node.left, target, replacement) or self._replace_leaf(
                node.right, target, replacement
            )
        return False

    def _find_replacement(
        self,
        node: SplitNode,
        target: WindowLeaf,
        parent: HSplitNode | VSplitNode | None = None,
        side_in_parent: str = "",
    ) -> tuple[SplitNode | None, tuple[HSplitNode | VSplitNode, str] | None]:
        """Find the replacement subtree and the grandparent slot to splice it into."""
        if isinstance(node, HSplitNode):
            if node.top is target:
                return node.bottom, (parent, side_in_parent) if parent is not None else None
            if node.bottom is target:
                return node.top, (parent, side_in_parent) if parent is not None else None
            replacement, ref = self._find_replacement(node.top, target, node, "top")
            if replacement is not None:
                return replacement, ref
            return self._find_replacement(node.bottom, target, node, "bottom")
        if isinstance(node, VSplitNode):
            if node.left is target:
                return node.right, (parent, side_in_parent) if parent is not None else None
            if node.right is target:
                return node.left, (parent, side_in_parent) if parent is not None else None
            replacement, ref = self._find_replacement(node.left, target, node, "left")
            if replacement is not None:
                return replacement, ref
            return self._find_replacement(node.right, target, node, "right")
        return None, None

    def _path_to_active_leaf(self) -> list[tuple[HSplitNode | VSplitNode, str]]:
        path: list[tuple[HSplitNode | VSplitNode, str]] = []

        def _walk(node: SplitNode) -> bool:
            if node is self._active_leaf:
                return True
            if isinstance(node, HSplitNode):
                if _walk(node.top):
                    path.append((node, "top"))
                    return True
                if _walk(node.bottom):
                    path.append((node, "bottom"))
                    return True
            elif isinstance(node, VSplitNode):
                if _walk(node.left):
                    path.append((node, "left"))
                    return True
                if _walk(node.right):
                    path.append((node, "right"))
                    return True
            return False

        _walk(self.root)
        path.reverse()
        return path


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class Workspace:  # cm:d4f6b3
    """
    The top-level editor state. Owns a list of TabPages and a buffer list.
    """

    def __init__(self, initial_window: Window) -> None:
        from peovim.core.document import Document

        initial_tab = TabPage(root=WindowLeaf(initial_window))
        self.tabs: list[TabPage] = [initial_tab]
        self.active_tab_index: int = 0
        self._documents: list[Document] = [initial_window.document]

    @property
    def active_tab(self) -> TabPage:
        return self.tabs[self.active_tab_index]

    @property
    def active_window(self) -> Window:
        return self.active_tab.active_window

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------

    def new_tab(self, window: Window) -> None:
        """Open a new tab with the given window as the initial pane."""
        page = TabPage(root=WindowLeaf(window))
        self.tabs.append(page)
        self.active_tab_index = len(self.tabs) - 1
        if window.document not in self._documents:
            self._documents.append(window.document)

    def close_tab(self, index: int) -> None:
        if len(self.tabs) <= 1:
            raise ValueError("Cannot close the last tab")
        self.tabs.pop(index)
        self.active_tab_index = max(0, min(self.active_tab_index, len(self.tabs) - 1))

    def goto_tab(self, index: int) -> None:
        assert 0 <= index < len(self.tabs)
        self.active_tab_index = index

    def next_tab(self) -> None:
        self.active_tab_index = (self.active_tab_index + 1) % len(self.tabs)

    def prev_tab(self) -> None:
        self.active_tab_index = (self.active_tab_index - 1) % len(self.tabs)

    # ------------------------------------------------------------------
    # Buffer list
    # ------------------------------------------------------------------

    @property
    def documents(self) -> list:
        return list(self._documents)

    def add_document(self, doc) -> None:
        if doc not in self._documents:
            self._documents.append(doc)

    def find_document_by_path(self, path: str | Path):
        """Return an already-loaded document for path, or None."""
        target = Path(path).resolve()
        for doc in self._documents:
            if doc.path is None:
                continue
            if doc.path.resolve() == target:
                return doc
        return None
