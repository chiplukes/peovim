"""
compute_layout() with various split trees and sizes.
"""

from peovim.core.document import Document
from peovim.core.window import Window
from peovim.core.workspace import HSplitNode, VSplitNode, WindowLeaf
from peovim.ui.layout import Rect, compute_layout


def make_window() -> Window:
    doc = Document()
    doc.load_string("")
    return Window(doc)


def make_leaf() -> WindowLeaf:
    return WindowLeaf(make_window())


class TestRect:
    def test_rect_fields(self):
        r = Rect(x=0, y=0, width=80, height=24)
        assert r.x == 0
        assert r.y == 0
        assert r.width == 80
        assert r.height == 24

    def test_rect_area(self):
        r = Rect(0, 0, 10, 5)
        assert r.area == 50


class TestComputeLayout:
    def test_single_leaf(self):
        leaf = make_leaf()
        rect = Rect(0, 0, 80, 24)
        result, seps = compute_layout(leaf, rect)
        assert len(result) == 1
        assert result[leaf] == rect
        assert seps == []

    def test_horizontal_split(self):
        top = make_leaf()
        bottom = make_leaf()
        node = HSplitNode(top, bottom)
        rect = Rect(0, 0, 80, 24)
        result, seps = compute_layout(node, rect)
        assert len(result) == 2
        tr = result[top]
        br = result[bottom]
        # Both same width
        assert tr.width == 80
        assert br.width == 80
        # Heights split with 1 separator row between them
        assert tr.height + br.height == 23
        # Top starts at y=0; bottom starts at y = top.height + 1 (separator)
        assert tr.y == 0
        assert br.y == tr.height + 1
        # One horizontal separator
        assert len(seps) == 1
        assert seps[0].height == 1
        assert seps[0].width == 80

    def test_vertical_split(self):
        left = make_leaf()
        right = make_leaf()
        node = VSplitNode(left, right)
        rect = Rect(0, 0, 80, 24)
        result, seps = compute_layout(node, rect)
        assert len(result) == 2
        lr = result[left]
        rr = result[right]
        # Both same height
        assert lr.height == 24
        assert rr.height == 24
        # Widths split with 1 separator column between them
        assert lr.width + rr.width == 79
        # Left at x=0; right at x = left.width + 1 (separator)
        assert lr.x == 0
        assert rr.x == lr.width + 1
        # One vertical separator
        assert len(seps) == 1
        assert seps[0].width == 1
        assert seps[0].height == 24

    def test_vertical_split_respects_ratio(self):
        left = make_leaf()
        right = make_leaf()
        node = VSplitNode(left, right, ratio=0.75)
        rect = Rect(0, 0, 80, 24)

        result, _seps = compute_layout(node, rect)

        assert result[left].width == 59
        assert result[right].width == 20

    def test_horizontal_split_respects_ratio(self):
        top = make_leaf()
        bottom = make_leaf()
        node = HSplitNode(top, bottom, ratio=0.75)
        rect = Rect(0, 0, 80, 24)

        result, _seps = compute_layout(node, rect)

        assert result[top].height == 17
        assert result[bottom].height == 6

    def test_nested_splits(self):
        a = make_leaf()
        b = make_leaf()
        c = make_leaf()
        # VSplit of (a, HSplit(b, c))
        inner = HSplitNode(b, c)
        root = VSplitNode(a, inner)
        rect = Rect(0, 0, 80, 24)
        result, seps = compute_layout(root, rect)
        assert len(result) == 3
        # All widths/heights are positive
        for _leaf, r in result.items():
            assert r.width > 0
            assert r.height > 0
        # Two splits → two separators
        assert len(seps) == 2

    def test_total_area_preserved(self):
        """Sum of leaf areas plus separator areas equals total rect area."""
        a = make_leaf()
        b = make_leaf()
        c = make_leaf()
        d = make_leaf()
        root = HSplitNode(VSplitNode(a, b), VSplitNode(c, d))
        rect = Rect(0, 0, 80, 24)
        result, seps = compute_layout(root, rect)
        leaf_area = sum(r.area for r in result.values())
        sep_area = sum(r.area for r in seps)
        assert leaf_area + sep_area == rect.area

    def test_offset_rect(self):
        """Non-zero origin rect propagates correctly."""
        leaf = make_leaf()
        rect = Rect(10, 5, 60, 20)
        result, seps = compute_layout(leaf, rect)
        assert result[leaf] == rect
