"""
ui.layout — compute_layout(): split tree → Rect per window

Pure function with no side effects. Takes the workspace split tree and
total terminal Rect, returns dict[WindowLeaf, Rect]. Parallelisable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.core.workspace import SplitNode, WindowLeaf


@dataclass(frozen=True)
class Rect:
    """A rectangular region of the terminal, in cell coordinates."""

    x: int
    y: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height


def compute_layout(root: SplitNode, total: Rect) -> tuple[dict[WindowLeaf, Rect], list[Rect]]:  # cm:2a8b7e
    """
    Recursively assign a Rect to each WindowLeaf in the split tree.

    HSplitNode → top/bottom split (equal height, each gets full width).
    VSplitNode → left/right split (equal width, each gets full height).

    A 1-cell separator is reserved at each split point.
    Vertical separators have width=1; horizontal separators have height=1.

    Returns ({leaf: Rect}, [separator Rect, ...]) for all leaves and separators.
    """
    from peovim.core.workspace import HSplitNode, VSplitNode, WindowLeaf

    result: dict[WindowLeaf, Rect] = {}
    separators: list[Rect] = []

    def _recurse(node: SplitNode, rect: Rect) -> None:
        if isinstance(node, WindowLeaf):
            result[node] = rect
            return

        if isinstance(node, HSplitNode):
            top_height = _split_extent(rect.height - 1, node.ratio)
            bottom_height = rect.height - top_height - 1
            top_rect = Rect(rect.x, rect.y, rect.width, max(1, top_height))
            sep_rect = Rect(rect.x, rect.y + top_height, rect.width, 1)
            bottom_rect = Rect(rect.x, rect.y + top_height + 1, rect.width, max(1, bottom_height))
            separators.append(sep_rect)
            _recurse(node.top, top_rect)
            _recurse(node.bottom, bottom_rect)

        elif isinstance(node, VSplitNode):
            left_width = _split_extent(rect.width - 1, node.ratio)
            right_width = rect.width - left_width - 1
            left_rect = Rect(rect.x, rect.y, max(1, left_width), rect.height)
            sep_rect = Rect(rect.x + left_width, rect.y, 1, rect.height)
            right_rect = Rect(rect.x + left_width + 1, rect.y, max(1, right_width), rect.height)
            separators.append(sep_rect)
            _recurse(node.left, left_rect)
            _recurse(node.right, right_rect)

    _recurse(root, total)
    return result, separators


def _split_extent(usable: int, ratio: float) -> int:
    if usable <= 1:
        return max(0, usable)
    clamped_ratio = max(0.1, min(0.9, ratio))
    extent = int(round(usable * clamped_ratio))
    return max(1, min(usable - 1, extent))
