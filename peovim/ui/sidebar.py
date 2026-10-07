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
from peovim.ui.panel_host import PanelHost, _call_hook

if TYPE_CHECKING:
    from peovim.syntax.themes import Theme
    from peovim.ui.tree_view import TreeView


_UNSET = object()


@dataclass
class SidebarStyle:
    background: Color = None
    header_active_fg: Color = (255, 255, 255)
    header_active_bg: Color = (48, 57, 72)
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

    def click(self, row: int, col: int) -> bool:
        return self.tree.click(row, col)

    def cursor_row(self, panel_height: int) -> int | None:
        return self.tree.cursor_row(panel_height)

    def on_focus(self) -> None:
        self.tree.focused = True

    def on_blur(self) -> None:
        self.tree.focused = False


class SidebarHost(PanelHost):  # cm:c4f5d1
    """Narrow panel selector beside a full-height content pane."""

    _RESIZE_STEP = 5
    SELECTOR_WIDTH = 7
    CONTENT_LEFT = SELECTOR_WIDTH + 1

    def __init__(self, default_width: int = 30) -> None:
        super().__init__()
        self._width = default_width
        self._style = SidebarStyle()
        self._selector_focused = False
        self._selector_scroll = 0
        self._selector_height = 0
        self._selector_width = 0
        self.flash_labels: dict[tuple[int, int], str] = {}
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

    @property
    def selector_focused(self) -> bool:
        return self.focused and self._selector_focused

    def focus_selector(self) -> None:
        if not self.focused:
            self.focus()
        if not self._selector_focused:
            _call_hook(self.active_panel, "on_blur")
            self._selector_focused = True

    def flash_matches(self, query: str) -> list[tuple[int, int]]:
        """Find matches in selector labels within the last rendered viewport."""
        if not self.visible or not query:
            return []
        matches = []
        names = self.list_panels()
        end = self._selector_scroll + self._selector_height
        for index in range(self._selector_scroll, min(end, len(names))):
            text = (" " + _format_panel_label(names[index]))[: self._selector_width].lower()
            col = 1
            while (col := text.find(query.lower(), col)) >= 0:
                matches.append((index, col))
                col += 1
        return matches

    def flash_select(self, index: int) -> None:
        """Select a panel and leave focus in the selector."""
        names = self.list_panels()
        if self.visible and 0 <= index < len(names):
            self.focus_selector()
            old = self.active_panel
            self._active_tab = names[index]
            self._fire_tab_change(old, self.active_panel)

    def focus(self) -> None:
        if self._selector_focused:
            self._selector_focused = False
            if self.focused:
                _call_hook(self.active_panel, "on_focus")
        super().focus()

    def _do_focus(self) -> None:
        if self._selector_focused:
            self._focused = True
        else:
            super()._do_focus()

    def _do_blur(self) -> None:
        if self._selector_focused:
            self._focused = False
        else:
            super()._do_blur()

    def hide(self) -> None:
        if self._selector_focused:
            self._focused = False
        super().hide()
        self._selector_focused = False

    def show_tab(self, name: str, content: Any = None, *, focus: bool = True) -> Any:
        if focus and self._selector_focused:
            self.focus()
        return super().show_tab(name, content, focus=focus)

    def _fire_tab_change(self, old: Any, new: Any) -> None:
        focused = self._focused
        if self._selector_focused:
            self._focused = False
        try:
            super()._fire_tab_change(old, new)
        finally:
            self._focused = focused

    def feed_key(self, key: str) -> bool:
        if not self.focused:
            return False
        if key == "h":
            self.focus_selector()
            return True
        if self._selector_focused:
            if key in ("j", "<Down>"):
                self.next_panel()
                return True
            if key in ("k", "<Up>"):
                self.prev_panel()
                return True
            if key in ("l", "<Right>", "<CR>"):
                self.focus()
                return True
            if key not in self._key_to_plug:
                return True
        return super().feed_key(key)

    def click(self, row: int, col: int) -> bool:
        if not self.visible:
            return False
        if col < self.SELECTOR_WIDTH:
            names = self.list_panels()
            index = row + self._selector_scroll
            self.focus_selector()
            if 0 <= index < len(names):
                self.flash_select(index)
            return True
        self.focus()
        panel = self.active_panel
        if col >= self.CONTENT_LEFT and panel is not None and hasattr(panel, "click"):
            return bool(panel.click(row, col - self.CONTENT_LEFT))
        return True

    def show_active_panel(self, *, focus: bool = True) -> SidebarPanel | None:
        return self.show_active_tab(focus=focus)  # type: ignore[return-value]

    def show_active_tab(self, *, focus: bool = True) -> Any:
        if self._active_tab is None and "explorer" in self._tabs:
            return self.show_tab("explorer", focus=focus)
        return super().show_active_tab(focus=focus)

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
        return max(10, min(panel_width + self.CONTENT_LEFT, max_sidebar))

    def _builtin_plugs(self) -> dict[str, Any]:
        return {
            "SidebarClose": self.hide,
            "SidebarShrink": lambda: self._adjust_width(-self._RESIZE_STEP),
            "SidebarGrow": lambda: self._adjust_width(self._RESIZE_STEP),
        }

    def _adjust_width(self, delta: int) -> None:
        panel = self.active_panel
        new_width = max(10, int(getattr(panel, "width", self._width)) + delta)
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
        if not self.visible or self.active_panel is None:
            return False
        panel = self.active_panel
        body_fg = cast(Color, theme.default_fg if theme is not None else default_fg)
        body_bg = self._resolve_sidebar_background(theme, default_bg)
        for row in range(grid.height):
            grid.fill(row, 0, grid.width, fg=body_fg, bg=body_bg)
        names = self.list_panels()
        if names and grid.height > 0:
            selected = names.index(self._active_tab) if self._active_tab in names else 0
            self._selector_scroll = min(self._selector_scroll, max(0, len(names) - grid.height))
            if selected < self._selector_scroll:
                self._selector_scroll = selected
            elif selected >= self._selector_scroll + grid.height:
                self._selector_scroll = selected - grid.height + 1
        selector_width = min(self.SELECTOR_WIDTH, grid.width)
        self._selector_height = grid.height
        self._selector_width = selector_width
        for row, name in enumerate(names[self._selector_scroll : self._selector_scroll + grid.height]):
            active = name == self._active_tab
            fg, bg, attrs = self._resolve_header_style(theme, is_active=active, default_fg=body_fg, default_bg=body_bg)
            if not active:
                bg = body_bg
            if active and self.selector_focused:
                attrs |= ATTR_BOLD
            grid.write_padded(
                row,
                0,
                ("›" if active and self.selector_focused else " ") + _format_panel_label(name),
                selector_width,
                fg=fg,
                bg=bg,
                attrs=attrs,
            )
        for (index, col), label in self.flash_labels.items():
            row = index - self._selector_scroll
            if 0 <= row < grid.height and 0 <= col < selector_width:
                grid.write_str(row, col, label, fg=(255, 215, 0), bg=(30, 25, 0))
        if grid.width > self.SELECTOR_WIDTH:
            for row in range(grid.height):
                grid.write_str(row, self.SELECTOR_WIDTH, "│", fg=self._style.header_inactive_fg, bg=body_bg)
        content_width = grid.width - self.CONTENT_LEFT
        if content_width > 0 and grid.height > 0:
            body = CellGrid(content_width, grid.height)
            panel._sidebar_focused = self.focused and not self._selector_focused  # type: ignore[attr-defined]
            panel._sidebar_blink_on = self.blink_on if panel._sidebar_focused else True  # type: ignore[attr-defined]
            panel.render(body)
            body.apply_default_style(fg=body_fg, bg=body_bg)
            grid.blit(body, self.CONTENT_LEFT, 0)
        grid.apply_default_style(fg=body_fg, bg=body_bg)
        return True

    def cursor_row(self, grid_height: int) -> int | None:
        if self.selector_focused:
            names = self.list_panels()
            if self._active_tab in names:
                row = names.index(self._active_tab) - self._selector_scroll
                return row if 0 <= row < grid_height else None
            return None
        fn = getattr(self.active_panel, "cursor_row", None)
        return fn(grid_height) if callable(fn) else None

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
        fg = cast(
            Color,
            theme_style.fg if theme_style.fg is not None else fallback_fg if fallback_fg is not None else default_fg,
        )
        bg = cast(
            Color,
            theme_style.bg if theme_style.bg is not None else fallback_bg if fallback_bg is not None else default_bg,
        )
        attrs = theme_style.attrs if theme_style.attrs else (ATTR_BOLD if is_active else 0)
        return fg, bg, attrs


_PANEL_LABELS = {
    "explorer": "file",
    "git-status": "git",
    "svn-status": "svn",
    "diagnostics": "diag",
    "outline": "oline",
    "references": "ref",
    "workspace-symbols": "sym",
    "file_history": "hist",
    "local-history": "local",
    "verilog_hierarchy": "vlog",
    "codemap": "cmap",
    "markers": "mark",
}


def _format_panel_label(name: str) -> str:
    return _PANEL_LABELS.get(name, name.replace("-", " ").replace("_", " ")[:5])
