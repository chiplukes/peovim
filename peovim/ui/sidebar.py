"""
ui.sidebar — Persistent left sidebar host and panel adapters.

The sidebar reserves layout space when visible and routes keys only while
focused. Panels are generic so the host can be reused for explorer, outline,
symbols, git status, and similar navigation surfaces.

SidebarHost inherits shared tab-management, focus, and key-routing logic from
PanelHost; this module adds vertical-orientation rendering and width management.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from peovim.core.style import Color, ColorLike, Style, normalize_color
from peovim.ui.backend import ATTR_BOLD
from peovim.ui.cell_grid import CellGrid
from peovim.ui.panel_host import PanelHost

if TYPE_CHECKING:
    from peovim.syntax.themes import Theme
    from peovim.ui.tree_view import TreeView


_UNSET = object()


@dataclass
class SidebarStyle:
    background: Color = None
    header_active_fg: Color = (255, 255, 255)
    header_active_bg: Color = (80, 100, 140)
    header_inactive_fg: Color = (210, 210, 210)
    header_inactive_bg: Color = (40, 44, 52)

    def __post_init__(self) -> None:
        self.background = normalize_color(self.background)
        self.header_active_fg = normalize_color(self.header_active_fg)
        self.header_active_bg = normalize_color(self.header_active_bg)
        self.header_inactive_fg = normalize_color(self.header_inactive_fg)
        self.header_inactive_bg = normalize_color(self.header_inactive_bg)


@runtime_checkable
class SidebarPanel(Protocol):
    width: int

    def render(self, grid: CellGrid) -> None: ...

    def feed_key(self, key: str) -> bool: ...


class TreeSidebarPanel:
    """Adapter that hosts a TreeView inside the persistent sidebar."""

    def __init__(self, tree: TreeView, *, width: int | None = None) -> None:
        self.tree = tree
        self.width = width if width is not None else getattr(tree, "_width", 30)

    def render(self, grid: CellGrid) -> None:
        self.tree.focused = getattr(self, "_sidebar_focused", False)
        self.tree.blink_on = getattr(self, "_sidebar_blink_on", True)
        self.tree._width = grid.width
        self.tree.render(grid)

    def feed_key(self, key: str) -> bool:
        self.tree.feed_key(key)
        return True

    def cursor_row(self, panel_height: int) -> int | None:
        return self.tree.cursor_row(panel_height)

    def on_focus(self) -> None:
        self.tree.focused = True

    def on_blur(self) -> None:
        self.tree.focused = False


class SidebarHost(PanelHost):  # cm:c4f5d1
    """Vertical sidebar host: panel headers + body + footer, width-constrained."""

    _RESIZE_STEP = 5

    def __init__(self, default_width: int = 30) -> None:
        super().__init__()
        self._width = default_width
        self._style = SidebarStyle()
        self.blink_on: bool = True  # toggled by EventLoopRuntimeController._tick_sidebar_blink

        # Sidebar-local key bindings: key → plug_name (consulted while focused).
        # Separate from the global nmap system since e.g. "[" and "q" have other
        # meanings in the editor and must not conflict.
        self._key_to_plug = {
            "[": "SidebarShrink",
            "]": "SidebarGrow",
            "q": "SidebarClose",
            "<Esc>": "SidebarClose",
        }

    # ------------------------------------------------------------------
    # Backward-compat properties (panels = tabs in the base)
    # ------------------------------------------------------------------

    @property
    def active_panel_name(self) -> str | None:
        return self._active_tab

    @property
    def active_panel(self) -> SidebarPanel | None:
        return self.active_tab  # type: ignore[return-value]

    @property
    def style(self) -> SidebarStyle:
        return self._style

    @property
    def visible(self) -> bool:
        # Sidebar is only truly visible when there is an active panel to show
        return self._visible and self.active_tab is not None

    @property
    def focused(self) -> bool:
        return self._focused and self.visible

    # ------------------------------------------------------------------
    # Backward-compat tab accessors
    # ------------------------------------------------------------------

    def get_panel(self, name: str) -> SidebarPanel | None:
        return self.get_tab(name)  # type: ignore[return-value]

    def register_panel(self, name: str, panel: SidebarPanel) -> SidebarPanel:
        return self.register_tab(name, panel, preferred_host="sidebar")  # type: ignore[return-value]

    def list_panels(self) -> list[str]:
        return self.list_tabs()

    def set_style(
        self,
        *,
        background: ColorLike | None | object = _UNSET,
        header_active_fg: ColorLike | None | object = _UNSET,
        header_active_bg: ColorLike | None | object = _UNSET,
        header_inactive_fg: ColorLike | None | object = _UNSET,
        header_inactive_bg: ColorLike | None | object = _UNSET,
    ) -> SidebarStyle:
        if background is not _UNSET:
            self._style.background = normalize_color(cast(ColorLike, background))
        if header_active_fg is not _UNSET:
            self._style.header_active_fg = normalize_color(cast(ColorLike, header_active_fg))
        if header_active_bg is not _UNSET:
            self._style.header_active_bg = normalize_color(cast(ColorLike, header_active_bg))
        if header_inactive_fg is not _UNSET:
            self._style.header_inactive_fg = normalize_color(cast(ColorLike, header_inactive_fg))
        if header_inactive_bg is not _UNSET:
            self._style.header_inactive_bg = normalize_color(cast(ColorLike, header_inactive_bg))
        return self._style

    def click(self, row: int, col: int) -> bool:
        if not self.visible:
            return False
        names = self.list_panels()
        if 0 <= row < len(names):
            name = names[row]
            panel = self.get_tab(name)
            if panel is None:
                return False
            self.show_panel(name, panel, focus=True)
            return True
        self.focus()
        return True

    def show_active_panel(self, *, focus: bool = True) -> SidebarPanel | None:
        return self.show_active_tab(focus=focus)  # type: ignore[return-value]

    def is_visible(self, name: str | None = None) -> bool:
        if not self.visible:
            return False
        if name is None:
            return True
        return self._active_tab == name

    def show_panel(self, name: str, panel: SidebarPanel, *, focus: bool = True) -> SidebarPanel:
        """Show *panel* in the sidebar, updating width from the panel's width attr."""
        self.register_tab(name, panel, preferred_host="sidebar")
        # Update sidebar width from the panel's declared width before showing
        self._width = max(10, int(getattr(panel, "width", self._width)))
        self.show_tab(name, focus=focus)
        return panel

    def toggle_panel(self, name: str, panel: SidebarPanel | None = None, *, focus: bool = True) -> bool:
        if self.is_visible(name):
            self.hide()
            return False
        resolved_panel = panel if panel is not None else self.get_tab(name)
        if resolved_panel is None:
            return False
        self.show_panel(name, resolved_panel, focus=focus)
        return True

    def next_panel(self, *, focus: bool = True) -> SidebarPanel | None:
        self.next_tab()
        return self.active_tab  # type: ignore[return-value]

    def prev_panel(self, *, focus: bool = True) -> SidebarPanel | None:
        self.prev_tab()
        return self.active_tab  # type: ignore[return-value]

    def reserved_width(self, total_cols: int) -> int:
        if not self.visible:
            return 0
        panel = self.active_panel
        if panel is None or total_cols < 20:
            return 0
        panel_width = max(10, int(getattr(panel, "width", self._width)))
        max_sidebar = max(10, total_cols - 20)
        return max(10, min(panel_width, max_sidebar))

    def _builtin_plugs(self) -> dict[str, Any]:
        return {
            "SidebarClose": self.hide,
            "SidebarShrink": lambda: self._adjust_width(-self._RESIZE_STEP),
            "SidebarGrow": lambda: self._adjust_width(self._RESIZE_STEP),
        }

    def _get_key_for_plug(self, plug_name: str, fallback: str) -> str:
        """Return the key mapped to a sidebar-internal plug (from _key_to_plug), formatted for display."""
        for key, name in self._key_to_plug.items():
            if name == plug_name:
                return key[1:-1] if key.startswith("<") and key.endswith(">") else key
        return fallback

    def _get_nav_key(self, plug_name: str, fallback: str) -> str:
        """Return the first key bound to <Plug>plug_name, formatted for display."""
        registry = self._binding_registry
        if registry is None:
            return fallback
        keys = registry.find_keys_for_plug(plug_name, mode="normal")
        if not keys:
            return fallback
        k = keys[0]
        return k[1:-1] if k.startswith("<") and k.endswith(">") else k

    def _get_footer_lines(self) -> list[str]:
        """Return footer lines with current key bindings substituted where known."""
        fl = self._get_nav_key("SidebarFocusLeft", "A-h")
        fr = self._get_nav_key("SidebarFocusRight", "A-l")
        nxt = self._get_nav_key("SidebarNextPanel", "A-j")
        prv = self._get_nav_key("SidebarPrevPanel", "A-k")
        shrink = self._get_key_for_plug("SidebarShrink", "[")
        grow = self._get_key_for_plug("SidebarGrow", "]")
        close = self._get_key_for_plug("SidebarClose", "q")
        return [
            "keys:",
            f"{fl} prev win    {fr} next win",
            f"{nxt} next panel  {prv} prev panel",
            f"{shrink}  shrink    {grow}  grow",
            "j  down      k  up",
            "h  collapse  l  open",
            f"{close}  close",
        ]

    def _adjust_width(self, delta: int) -> None:
        panel = self.active_panel
        new_width = max(10, self._width + delta)
        self._width = new_width
        if panel is not None:
            panel.width = new_width

    def render(
        self,
        grid: CellGrid,
        *,
        theme: Theme | None = None,
        default_fg: tuple[int, int, int] | None = None,
        default_bg: tuple[int, int, int] | None = None,
    ) -> bool:
        if not self.visible:
            return False
        panel = self.active_panel
        if panel is None:
            return False
        body_fg = cast(Color, theme.default_fg if theme is not None else default_fg)
        body_bg = self._resolve_sidebar_background(theme, default_bg)
        header_rows = self._header_row_count(grid.height)
        self._render_panel_headers(grid, theme=theme, default_fg=body_fg, default_bg=body_bg, active_only=False)
        body_top = header_rows
        if grid.height > header_rows + 1:
            self._render_body_separator(
                grid,
                row=header_rows,
                theme=theme,
                default_fg=body_fg,
                default_bg=body_bg,
            )
            body_top += 1

        if grid.height <= body_top:
            grid.apply_default_style(fg=body_fg, bg=body_bg)
            return True

        footer_height = len(self._get_footer_lines())
        available = grid.height - body_top
        panel_height = available - footer_height

        if panel_height > 0:
            body = CellGrid(grid.width, panel_height)
            # Push focus state and blink phase into panel so tree-based panels can blink.
            setattr(panel, "_sidebar_focused", self._focused)
            setattr(panel, "_sidebar_blink_on", self.blink_on if self._focused else True)
            panel.render(body)
            body.apply_default_style(fg=body_fg, bg=body_bg)
            grid.blit(body, 0, body_top)

        footer_start = body_top + max(0, panel_height)
        self._render_footer(grid, start_row=footer_start, default_bg=body_bg)
        grid.apply_default_style(fg=body_fg, bg=body_bg)
        return True

    def cursor_row(self, grid_height: int) -> int | None:
        """Return the sidebar-grid-local row where the terminal cursor should sit, or *None*."""
        panel = self.active_panel
        if panel is None:
            return None
        fn = getattr(panel, "cursor_row", None)
        if not callable(fn):
            return None
        header_rows = self._header_row_count(grid_height)
        body_top = header_rows + (1 if grid_height > header_rows + 1 else 0)
        panel_height = max(0, grid_height - body_top - len(self._get_footer_lines()))
        if panel_height <= 0:
            return None
        panel_row = fn(panel_height)
        if panel_row is None:
            return None
        return body_top + panel_row

    def _header_row_count(self, total_rows: int) -> int:
        return min(len(self.list_panels()), total_rows)

    def _render_panel_headers(
        self,
        grid: CellGrid,
        *,
        theme: Theme | None,
        default_fg: Color,
        default_bg: Color,
        active_only: bool,
    ) -> None:
        for row in range(grid.height):
            grid.fill(row, 0, grid.width, bg=default_bg)

        names = self.list_panels()
        for index, name in enumerate(names):
            if index >= grid.height:
                break
            is_active = name == self._active_tab
            if active_only and not is_active:
                continue
            label = _format_panel_label(name)
            marker = "▼" if is_active else "▶"
            fg, bg, attrs = self._resolve_header_style(
                theme,
                is_active=is_active,
                default_fg=default_fg,
                default_bg=default_bg,
            )
            text = f" {marker} {label}"
            grid.write_padded(index, 0, text, grid.width, fg=fg, bg=bg, attrs=attrs)

    def _resolve_sidebar_background(self, theme: Theme | None, default_bg: Color) -> Color:
        if self._style.background is not None:
            return self._style.background
        if theme is not None:
            sidebar_style = theme.resolve("sidebar")
            if sidebar_style.bg is not None:
                return cast(Color, sidebar_style.bg)
            return cast(Color, theme.default_bg)
        return default_bg

    def _resolve_header_style(
        self,
        theme: Theme | None,
        *,
        is_active: bool,
        default_fg: Color,
        default_bg: Color,
    ) -> tuple[Color, Color, int]:
        fallback_fg = self._style.header_active_fg if is_active else self._style.header_inactive_fg
        fallback_bg = self._style.header_active_bg if is_active else self._style.header_inactive_bg
        theme_style = (
            theme.resolve("sidebar.header.active" if is_active else "sidebar.header.inactive") if theme else Style()
        )
        fg = cast(Color, theme_style.fg if theme_style.fg is not None else fallback_fg if fallback_fg is not None else default_fg)
        bg = cast(Color, theme_style.bg if theme_style.bg is not None else fallback_bg if fallback_bg is not None else default_bg)
        attrs = theme_style.attrs if theme_style.attrs else (ATTR_BOLD if is_active else 0)
        return fg, bg, attrs

    def _render_footer(self, grid: CellGrid, *, start_row: int, default_bg: Color) -> None:
        for i, line in enumerate(self._get_footer_lines()):
            row = start_row + i
            if row >= grid.height:
                break
            fg = (180, 180, 200) if i == 0 else (140, 140, 160)
            text = line[: grid.width].ljust(grid.width)
            grid.write_str(row, 0, text, fg=fg, bg=default_bg)

    def _render_body_separator(
        self,
        grid: CellGrid,
        *,
        row: int,
        theme: Theme | None,
        default_fg: Color,
        default_bg: Color,
    ) -> None:
        if not (0 <= row < grid.height):
            return
        separator_style = theme.resolve("sidebar.header.inactive") if theme else Style()
        fg = separator_style.fg if separator_style.fg is not None else self._style.header_inactive_fg or default_fg
        bg = separator_style.bg if separator_style.bg is not None else default_bg
        grid.write_padded(row, 0, "─" * max(1, grid.width), grid.width, fg=fg, bg=bg)


def _format_panel_label(name: str) -> str:
    return name.replace("-", " ")
