"""
Phase 7f — TreeView widget tests
"""

from __future__ import annotations

from peovim.ui.cell_grid import CellGrid
from peovim.ui.tree_view import TreeNode, TreeView, TreeViewHandle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _leaf(label: str, value=None) -> TreeNode:
    return TreeNode(label=label, value=value)


def _dir_node(label: str, children: list[TreeNode]) -> TreeNode:
    return TreeNode(label=label, children_fn=lambda: list(children))


def _render_to_text(tree: TreeView, width: int = 30, height: int = 20) -> list[str]:
    """Render tree to a CellGrid and extract text lines."""
    grid = CellGrid(width, height)
    tree.render(grid)
    lines = []
    for row in range(height):
        line = "".join(grid._current[row][col][0] for col in range(width))
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTreeView:
    def test_render_shows_root_nodes(self):
        nodes = [_leaf("alpha"), _leaf("beta"), _leaf("gamma")]
        tree = TreeView(nodes, width=30)
        lines = _render_to_text(tree)
        joined = "\n".join(lines)
        assert "alpha" in joined
        assert "beta" in joined
        assert "gamma" in joined

    def test_leaf_shows_no_fold_indicator(self):
        nodes = [_leaf("leaf_node")]
        tree = TreeView(nodes, width=30)
        lines = _render_to_text(tree)
        # Leaf should show "  " (double space) not "▶ " or "▼ "
        assert "▶" not in lines[0]
        assert "▼" not in lines[0]

    def test_collapsed_dir_shows_collapsed_indicator(self):
        node = _dir_node("mydir", [_leaf("child")])
        tree = TreeView([node], width=30)
        lines = _render_to_text(tree)
        assert "▶" in lines[0]

    def test_expand_reveals_children(self):
        child = _leaf("child_file")
        node = _dir_node("mydir", [child])
        tree = TreeView([node], width=30)
        tree.expand(node)
        lines = _render_to_text(tree)
        joined = "\n".join(lines)
        assert "child_file" in joined
        assert "▼" in lines[0]

    def test_collapse_hides_children(self):
        child = _leaf("hidden_child")
        node = _dir_node("mydir", [child])
        tree = TreeView([node], width=30)
        tree.expand(node)
        tree.collapse(node)
        lines = _render_to_text(tree)
        joined = "\n".join(lines)
        assert "hidden_child" not in joined

    def test_toggle_switches_state(self):
        child = _leaf("toggle_child")
        node = _dir_node("mydir", [child])
        tree = TreeView([node], width=30)
        tree.toggle(node)  # expand
        assert node.expanded
        tree.toggle(node)  # collapse
        assert not node.expanded

    def test_refresh_clears_cache(self):
        call_count = [0]

        def _children():
            call_count[0] += 1
            return [_leaf("child")]

        node = TreeNode(label="dir", children_fn=_children)
        tree = TreeView([node], width=30)
        tree.expand(node)
        assert call_count[0] == 1
        tree.refresh()
        # After refresh, cache is cleared
        assert not node.expanded
        assert node._cached_children == []

    def test_depth2_indents(self):
        grandchild = _leaf("grandchild")
        child = _dir_node("child_dir", [grandchild])
        root = _dir_node("root_dir", [child])
        tree = TreeView([root], width=40)
        tree.expand(root)
        tree.expand(child)
        lines = _render_to_text(tree, width=40)
        # Find grandchild line
        gc_line = next((line_text for line_text in lines if "grandchild" in line_text), None)
        assert gc_line is not None
        # Should have 4 leading spaces (2 levels × 2 spaces)
        assert gc_line.startswith("    ")

    def test_j_k_move_selection(self):
        nodes = [_leaf("a"), _leaf("b"), _leaf("c")]
        tree = TreeView(nodes, width=30)
        assert tree._selected_idx == 0
        tree.feed_key("j")
        assert tree._selected_idx == 1
        tree.feed_key("j")
        assert tree._selected_idx == 2
        tree.feed_key("k")
        assert tree._selected_idx == 1

    def test_cr_on_leaf_calls_on_select(self):
        selected = []
        node = _leaf("file.py", value="file.py")
        tree = TreeView([node], on_select=lambda n: selected.append(n), width=30)
        tree.feed_key("<CR>")
        assert selected == [node]

    def test_q_calls_on_close(self):
        closed = []
        tree = TreeView([], on_close=lambda: closed.append(True), width=30)
        tree.feed_key("q")
        assert closed == [True]
        assert not tree.is_open

    def test_esc_calls_on_close(self):
        closed = []
        tree = TreeView([], on_close=lambda: closed.append(True), width=30)
        tree.feed_key("<Esc>")
        assert closed == [True]

    def test_tree_view_handle(self):
        tree = TreeView([_leaf("x")], width=30)
        removed = []
        handle = TreeViewHandle(tree, on_remove=lambda h: removed.append(h))
        assert handle.is_open
        assert handle.tree is tree
        handle.close()
        assert not handle.is_open
        assert removed == [handle]

    def test_visible_nodes_respects_expansion(self):
        child = _leaf("c")
        node = _dir_node("d", [child])
        tree = TreeView([node], width=30)
        visible = tree._visible_nodes()
        assert len(visible) == 1  # only root, child hidden
        tree.expand(node)
        visible = tree._visible_nodes()
        assert len(visible) == 2  # root + child

    def test_selected_node_returns_current_node(self):
        nodes = [_leaf("a"), _leaf("b")]
        tree = TreeView(nodes, width=30)
        assert tree.selected_node is nodes[0]
        tree.feed_key("j")
        assert tree.selected_node is nodes[1]

    def test_select_value_moves_selection(self):
        nodes = [_leaf("a", value="one"), _leaf("b", value="two")]
        tree = TreeView(nodes, width=30)
        assert tree.select_value("two")
        assert tree.selected_node is nodes[1]

    def test_custom_key_handler_receives_selected_node(self):
        node = _leaf("file.py", value="file.py")
        seen = []
        tree = TreeView([node], on_key=lambda key, selected: seen.append((key, selected)) or True, width=30)
        tree.feed_key("a")
        assert seen == [("a", node)]

    def test_on_cursor_move_fires_on_j_and_k(self):
        nodes = [_leaf("a"), _leaf("b"), _leaf("c")]
        moved: list[TreeNode] = []
        tree = TreeView(nodes, on_cursor_move=lambda n: moved.append(n), width=30)

        tree.feed_key("j")
        tree.feed_key("j")
        tree.feed_key("k")

        assert moved == [nodes[1], nodes[2], nodes[1]]

    def test_on_cursor_move_not_fired_on_select(self):
        nodes = [_leaf("a"), _leaf("b")]
        moved: list[TreeNode] = []
        selected: list[TreeNode] = []
        tree = TreeView(
            nodes, on_select=lambda n: selected.append(n), on_cursor_move=lambda n: moved.append(n), width=30
        )

        tree.feed_key("<CR>")

        assert selected == [nodes[0]]
        assert moved == []

    def test_render_uses_node_foreground_for_unselected_rows(self):
        tree = TreeView([TreeNode(label="alpha", fg=(1, 2, 3)), TreeNode(label="beta")], width=20)
        tree.feed_key("j")
        grid = CellGrid(20, 4)

        tree.render(grid)

        assert grid._current[0][0][1] == (1, 2, 3)
