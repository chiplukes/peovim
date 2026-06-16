"""
syntax.themes — Theme: highlight group name → Style mapping

Backend-agnostic. Imports Style/Color from peovim.core.style (not peovim/ui/).
Ships three built-in themes: Catppuccin Mocha, Gruvbox Dark, One Dark.

Plugins register additional themes via register_theme().
The active theme name is stored in EditorState.active_theme and resolved
by get_theme() during each render frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from peovim.core.style import ColorLike, Style, normalize_color

# ---------------------------------------------------------------------------
# Theme dataclass
# ---------------------------------------------------------------------------


@dataclass
class Theme:  # cm:3e2a4c
    name: str
    groups: dict[str, Style]
    default_fg: ColorLike = (212, 212, 212)
    default_bg: ColorLike = None
    _style_cache: dict = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.default_fg = normalize_color(self.default_fg)
        self.default_bg = normalize_color(self.default_bg)

    def resolve(self, group: str) -> Style:
        """
        Return Style for the given capture group name.

        Falls back to the parent group if the specific group is not defined
        (e.g. 'keyword.return' → 'keyword'), then returns an empty Style
        (terminal defaults) if nothing matches.
        """
        if group in self.groups:
            return self.groups[group]
        if "." in group:
            return self.resolve(group.rsplit(".", 1)[0])
        return Style()


# ---------------------------------------------------------------------------
# Built-in theme definitions
# ---------------------------------------------------------------------------


def _make_catppuccin() -> Theme:
    """Catppuccin Mocha palette."""
    # Core palette
    rosewater = (245, 224, 220)
    flamingo = (242, 205, 205)
    pink = (245, 189, 230)
    mauve = (203, 166, 247)
    red = (243, 139, 168)
    maroon = (235, 160, 172)
    peach = (250, 179, 135)
    yellow = (249, 226, 175)
    green = (166, 227, 161)
    sky = (137, 220, 235)
    sapphire = (116, 199, 236)
    blue = (137, 180, 250)
    lavender = (180, 190, 254)
    text = (205, 214, 244)
    subtext1 = (186, 194, 222)
    overlay2 = (147, 153, 178)

    g = {}
    g["keyword"] = Style(fg=mauve)
    g["keyword.control"] = Style(fg=red)
    g["keyword.return"] = Style(fg=mauve)
    g["keyword.function"] = Style(fg=mauve)
    g["keyword.operator"] = Style(fg=sky)
    g["keyword.import"] = Style(fg=mauve)
    g["keyword.repeat"] = Style(fg=mauve)
    g["keyword.exception"] = Style(fg=mauve)
    g["keyword.conditional"] = Style(fg=peach)

    g["string"] = Style(fg=green)
    g["string.escape"] = Style(fg=pink)
    g["string.special"] = Style(fg=peach)

    g["comment"] = Style(fg=overlay2)

    g["function"] = Style(fg=blue)
    g["function.call"] = Style(fg=blue)
    g["function.builtin"] = Style(fg=peach)
    g["function.macro"] = Style(fg=sky)

    g["type"] = Style(fg=yellow)
    g["type.builtin"] = Style(fg=yellow)
    g["type.definition"] = Style(fg=yellow)

    g["variable"] = Style(fg=text)
    g["variable.builtin"] = Style(fg=red)
    g["variable.parameter"] = Style(fg=maroon)

    g["constant"] = Style(fg=peach)
    g["constant.builtin"] = Style(fg=peach)
    g["constant.macro"] = Style(fg=peach)

    g["operator"] = Style(fg=sky)

    g["number"] = Style(fg=peach)
    g["float"] = Style(fg=peach)

    g["boolean"] = Style(fg=peach)

    g["punctuation"] = Style(fg=overlay2)
    g["punctuation.bracket"] = Style(fg=overlay2)
    g["punctuation.delimiter"] = Style(fg=overlay2)
    g["punctuation.special"] = Style(fg=sky)

    g["label"] = Style(fg=sapphire)
    g["namespace"] = Style(fg=lavender)
    g["attribute"] = Style(fg=yellow)

    g["tag"] = Style(fg=mauve)
    g["tag.attribute"] = Style(fg=yellow)

    g["error"] = Style(fg=red)

    g["text"] = Style(fg=text)
    g["text.title"] = Style(fg=blue)
    g["text.strong"] = Style(fg=rosewater)
    g["text.emphasis"] = Style(fg=flamingo)
    g["text.literal"] = Style(fg=green)
    g["text.uri"] = Style(fg=sky)

    g["embedded"] = Style(fg=subtext1)
    g["property"] = Style(fg=sapphire)
    g["field"] = Style(fg=sapphire)
    g["constructor"] = Style(fg=sapphire)
    g["module"] = Style(fg=lavender)
    g["parameter"] = Style(fg=maroon)
    g["decorator"] = Style(fg=peach)
    g["annotation"] = Style(fg=peach)

    return Theme(name="catppuccin", groups=g, default_fg=text, default_bg=(30, 30, 46))


def _make_gruvbox() -> Theme:
    """Gruvbox Dark (Medium contrast) palette."""
    # Gruvbox colors
    bright_red = (251, 73, 52)
    bright_green = (184, 187, 38)
    bright_yellow = (250, 189, 47)
    bright_blue = (131, 165, 152)
    bright_purple = (211, 134, 155)
    bright_aqua = (142, 192, 124)
    bright_orange = (254, 128, 25)
    neutral_red = (204, 36, 29)
    neutral_yellow = (215, 153, 33)
    neutral_aqua = (104, 157, 106)
    fg1 = (235, 219, 178)
    fg4 = (168, 153, 132)
    gray = (146, 131, 116)

    g = {}
    g["keyword"] = Style(fg=bright_red)
    g["keyword.control"] = Style(fg=bright_red)
    g["keyword.return"] = Style(fg=bright_red)
    g["keyword.function"] = Style(fg=bright_red)
    g["keyword.operator"] = Style(fg=neutral_red)
    g["keyword.import"] = Style(fg=neutral_aqua)
    g["keyword.conditional"] = Style(fg=bright_orange)

    g["string"] = Style(fg=bright_green)
    g["string.escape"] = Style(fg=bright_orange)

    g["comment"] = Style(fg=gray)

    g["function"] = Style(fg=bright_blue)
    g["function.call"] = Style(fg=bright_blue)
    g["function.builtin"] = Style(fg=bright_orange)
    g["function.macro"] = Style(fg=bright_aqua)

    g["type"] = Style(fg=bright_yellow)
    g["type.builtin"] = Style(fg=bright_yellow)

    g["variable"] = Style(fg=fg1)
    g["variable.builtin"] = Style(fg=bright_orange)
    g["variable.parameter"] = Style(fg=fg4)
    g["parameter"] = Style(fg=fg4)

    g["constant"] = Style(fg=bright_purple)
    g["constant.builtin"] = Style(fg=bright_purple)

    g["operator"] = Style(fg=neutral_aqua)

    g["number"] = Style(fg=bright_purple)
    g["float"] = Style(fg=bright_purple)
    g["boolean"] = Style(fg=bright_purple)

    g["punctuation"] = Style(fg=fg4)
    g["punctuation.bracket"] = Style(fg=neutral_yellow)
    g["punctuation.delimiter"] = Style(fg=fg4)

    g["label"] = Style(fg=bright_aqua)
    g["namespace"] = Style(fg=bright_aqua)
    g["attribute"] = Style(fg=bright_yellow)
    g["tag"] = Style(fg=bright_aqua)
    g["error"] = Style(fg=bright_red)

    g["text"] = Style(fg=fg1)
    g["text.title"] = Style(fg=bright_blue)
    g["text.strong"] = Style(fg=bright_orange)
    g["text.emphasis"] = Style(fg=bright_green)
    g["text.literal"] = Style(fg=bright_green)

    g["property"] = Style(fg=bright_aqua)
    g["field"] = Style(fg=bright_aqua)
    g["module"] = Style(fg=bright_blue)
    g["decorator"] = Style(fg=bright_orange)

    return Theme(name="gruvbox", groups=g, default_fg=fg1, default_bg=(29, 32, 33))


def _make_onedark() -> Theme:
    """Atom One Dark palette."""
    red = (224, 108, 117)
    green = (152, 195, 121)
    yellow = (229, 192, 123)
    blue = (97, 175, 239)
    purple = (198, 120, 221)
    cyan = (86, 182, 194)
    fg = (171, 178, 191)
    fg_dark = (92, 99, 112)
    orange = (209, 154, 102)
    fg_light = (200, 204, 212)

    g = {}
    g["keyword"] = Style(fg=purple)
    g["keyword.control"] = Style(fg=red)
    g["keyword.return"] = Style(fg=purple)
    g["keyword.function"] = Style(fg=purple)
    g["keyword.operator"] = Style(fg=cyan)
    g["keyword.import"] = Style(fg=purple)
    g["keyword.conditional"] = Style(fg=orange)

    g["string"] = Style(fg=green)
    g["string.escape"] = Style(fg=cyan)

    g["comment"] = Style(fg=fg_dark)

    g["function"] = Style(fg=blue)
    g["function.call"] = Style(fg=blue)
    g["function.builtin"] = Style(fg=cyan)
    g["function.macro"] = Style(fg=cyan)

    g["type"] = Style(fg=yellow)
    g["type.builtin"] = Style(fg=yellow)

    g["variable"] = Style(fg=fg)
    g["variable.builtin"] = Style(fg=orange)
    g["variable.parameter"] = Style(fg=red)
    g["parameter"] = Style(fg=red)

    g["constant"] = Style(fg=orange)
    g["constant.builtin"] = Style(fg=orange)

    g["operator"] = Style(fg=cyan)

    g["number"] = Style(fg=orange)
    g["float"] = Style(fg=orange)
    g["boolean"] = Style(fg=orange)

    g["punctuation"] = Style(fg=fg_dark)
    g["punctuation.bracket"] = Style(fg=yellow)
    g["punctuation.delimiter"] = Style(fg=fg_dark)
    g["punctuation.special"] = Style(fg=cyan)

    g["label"] = Style(fg=cyan)
    g["namespace"] = Style(fg=blue)
    g["attribute"] = Style(fg=yellow)
    g["tag"] = Style(fg=red)
    g["error"] = Style(fg=red)

    g["text"] = Style(fg=fg)
    g["text.title"] = Style(fg=blue)
    g["text.strong"] = Style(fg=fg_light)
    g["text.emphasis"] = Style(fg=green)
    g["text.literal"] = Style(fg=green)

    g["property"] = Style(fg=red)
    g["field"] = Style(fg=red)
    g["module"] = Style(fg=yellow)
    g["decorator"] = Style(fg=blue)
    g["annotation"] = Style(fg=blue)

    return Theme(name="onedark", groups=g, default_fg=fg, default_bg=(40, 44, 52))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_THEMES: dict[str, Theme] = {}


def register_theme(name: str, theme: Theme) -> None:
    """Register a theme by name. Overwrites any existing theme with the same name."""
    _THEMES[name] = theme


def get_theme(name: str) -> Theme | None:
    """Return the named theme, or None if not registered."""
    return _THEMES.get(name)


def theme_names() -> list[str]:
    """Return sorted list of registered theme names."""
    return sorted(_THEMES.keys())


# Register built-ins at module load
register_theme("catppuccin", _make_catppuccin())
register_theme("gruvbox", _make_gruvbox())
register_theme("onedark", _make_onedark())
