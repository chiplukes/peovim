"""
ui.decorations — Decoration type definitions

Types: HighlightRegion, VirtualText, VirtualLine, Sign, InlayHint,
GhostText, OverlayChar, CodeLens, Conceal.
All decorations are namespace-isolated; buffer.clear_namespace() removes
all decorations for a namespace atomically.

See notes/api.md for the decoration API.
"""

from __future__ import annotations

from dataclasses import dataclass

from peovim.core.style import Color, Style  # noqa: F401 — re-exported for existing importers

# ---------------------------------------------------------------------------
# Decoration types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HighlightRegion:
    """Overlay color range on buffer text (syntax, search match, etc.)."""

    start_line: int
    start_col: int  # character col (inclusive)
    end_line: int
    end_col: int  # character col (exclusive)
    style: Style
    priority: int = 0  # higher wins when overlapping
    kind: str = "highlight"  # stable tag for core-layer sorting


@dataclass(frozen=True)
class VirtualText:
    """Inline virtual text appended after a buffer line."""

    line: int
    text: str
    style: Style
    priority: int = 0


@dataclass(frozen=True)
class VirtualLine:
    """One or more blank virtual lines inserted after a buffer line."""

    after_line: int  # buffer line anchor; -1 means before line 0
    style: Style
    count: int = 1


@dataclass(frozen=True)
class Sign:
    """Single-character marker displayed in the gutter."""

    line: int
    char: str  # exactly 1 character
    style: Style
    priority: int = 0
    kind: str = "sign"  # stable tag for core-layer sorting


@dataclass(frozen=True)
class InlayHint:
    """Inline hint inserted before a column (type annotations, param names)."""

    line: int
    col: int  # character col — hint appears before this column
    text: str
    style: Style


@dataclass(frozen=True)
class GhostText:
    """Faded suggested text overlaid on the buffer (LSP completion ghost)."""

    line: int
    col: int
    text: str
    style: Style


@dataclass(frozen=True)
class OverlayChar:
    """Replace a single cell's visual character without changing the buffer."""

    line: int
    col: int
    display_char: str  # exactly 1 character
    style: Style


@dataclass(frozen=True)
class CodeLens:
    """Actionable annotation line above a buffer line."""

    line: int
    text: str
    style: Style


@dataclass(frozen=True)
class Conceal:
    """Hide or replace a range of text (folding, markdown, etc.)."""

    line: int
    start_col: int
    end_col: int
    replacement: str  # "" to hide, or a single-char substitute


# Union alias
DecorationSet = list
