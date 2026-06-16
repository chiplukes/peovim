"""
core.style — Color type alias and Style dataclass

Defined here (not in peovim/ui/) so that peovim/syntax/ can import Style without
creating an upward dependency on peovim/ui/. peovim/ui/decorations.py re-exports
both names so all existing importers continue to work unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

# RGB triple or None (terminal default colour).
# Kept identical to the Color alias in peovim/ui/backend.py — no coupling needed
# since it's just a type alias.
Color = tuple[int, int, int] | None
ColorLike = Color | str


def normalize_color(value: ColorLike) -> Color:
    """Normalize `#RRGGBB` strings into RGB tuples."""
    if value is None:
        return None
    if isinstance(value, tuple):
        if len(value) != 3 or any(not isinstance(channel, int) for channel in value):
            raise ValueError(f"Invalid RGB color tuple: {value!r}")
        if any(channel < 0 or channel > 255 for channel in value):
            raise ValueError(f"RGB channels must be between 0 and 255: {value!r}")
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("#"):
            text = text[1:]
        if len(text) != 6:
            raise ValueError(f"Hex colors must use #RRGGBB format: {value!r}")
        try:
            return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
        except ValueError as exc:
            raise ValueError(f"Invalid hex color: {value!r}") from exc
    raise TypeError(f"Unsupported color value: {value!r}")


@dataclass(frozen=True)
class Style:
    """Bundle of visual attributes applied to one or more cells."""

    fg: ColorLike = None
    bg: ColorLike = None
    attrs: int = 0  # bitmask — use ATTR_* constants from peovim/ui/backend.py

    def __post_init__(self) -> None:
        object.__setattr__(self, "fg", normalize_color(self.fg))
        object.__setattr__(self, "bg", normalize_color(self.bg))
