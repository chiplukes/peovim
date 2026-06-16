"""Shared scrollbar geometry and styling helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from peovim.ui.backend import ATTR_BOLD

SCROLLBAR_WIDTH = 1
SCROLLBAR_TRACK_CHAR = "│"
SCROLLBAR_THUMB_CHAR = "█"
SCROLLBAR_TRACK = {"fg": (68, 68, 88), "bg": None, "attrs": 0}
SCROLLBAR_THUMB_ACTIVE = {"fg": (176, 186, 224), "bg": None, "attrs": ATTR_BOLD}
SCROLLBAR_THUMB_INACTIVE = {"fg": (116, 126, 156), "bg": None, "attrs": 0}


def scrollbar_width(options: Mapping[str, Any]) -> int:
    return SCROLLBAR_WIDTH if bool(options.get("scrollbar", False)) else 0


def scrollbar_thumb_range(line_count: int, viewport_height: int, scroll_line: int) -> tuple[int, int]:
    viewport_height = max(0, int(viewport_height))
    if viewport_height == 0:
        return 0, 0

    max_scroll = max(0, line_count - viewport_height)
    if line_count <= 0 or max_scroll == 0:
        return 0, viewport_height

    thumb_height = max(1, min(viewport_height, int(round((viewport_height * viewport_height) / line_count))))
    travel = max(0, viewport_height - thumb_height)
    clamped_scroll = max(0, min(scroll_line, max_scroll))
    thumb_top = 0 if travel == 0 else int(round((clamped_scroll / max_scroll) * travel))
    return thumb_top, thumb_height


def scrollbar_scroll_line_for_thumb_top(line_count: int, viewport_height: int, thumb_top: int) -> int:
    viewport_height = max(0, int(viewport_height))
    if viewport_height == 0:
        return 0

    max_scroll = max(0, line_count - viewport_height)
    if line_count <= 0 or max_scroll == 0:
        return 0

    _, thumb_height = scrollbar_thumb_range(line_count, viewport_height, 0)
    travel = max(0, viewport_height - thumb_height)
    if travel == 0:
        return 0

    clamped_top = max(0, min(int(thumb_top), travel))
    return int(round((clamped_top / travel) * max_scroll))
