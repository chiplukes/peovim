"""
UIAPI — floats, picker, notifications

Wraps FloatManager, PickerWidget, and NotifyManager for plugin use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from peovim.core.style import ColorLike
from peovim.ui.bottom_panel import BottomPanelHost, LogOutputTab, WhichKeyTab
from peovim.ui.sidebar import SidebarHost

_UNSET = object()

if TYPE_CHECKING:
    from peovim.ui.float_manager import FloatManager
    from peovim.ui.notify import NotifyManager
    from peovim.ui.picker import PickerWidget
    from peovim.ui.which_key_panel import WhichKeyPanel


class UIAPI:  # cm:3c9d4f
    """UI widget API exposed to plugins."""

    def __init__(
        self,
        float_manager: FloatManager | None = None,
        notify_manager: NotifyManager | None = None,
        picker: PickerWidget | None = None,
        which_key_panel: WhichKeyPanel | None = None,
    ) -> None:
        self._float_manager = float_manager
        self._notify_manager = notify_manager
        self._picker = picker
        self._which_key_panel = which_key_panel
        self._terminals: dict[str, Any] = {}
        self._tree_views: list[Any] = []
        self._sidebar = SidebarHost()
        self._bottom_panel = BottomPanelHost()
        # Register the built-in output tab and start capturing log output
        self._log_output_tab = LogOutputTab()
        self._bottom_panel.register_tab("output", self._log_output_tab)
        self._log_output_tab.attach()
        # Register the hidden which-key tab (activated during key sequences)
        if which_key_panel is not None:
            self._bottom_panel.register_tab("keys", WhichKeyTab(which_key_panel))

    def set_yank_callback(self, fn: Any) -> None:
        """Wire the yank callback to the log output tab."""
        self._log_output_tab.yank_fn = fn

    def open_float(
        self,
        content: str | list[str],
        *,
        anchor: Any = None,
        width: int = 60,
        height: int = 10,
        title: str = "",
        border: bool = True,
        focusable: bool = False,
        z_order: int = 0,
        on_close: Any = None,
    ) -> Any:
        """Open a floating window. Returns a FloatHandle."""
        if self._float_manager is None:
            return None
        return self._float_manager.open_float(
            content,
            anchor=anchor,
            width=width,
            height=height,
            border=border,
            title=title,
            focusable=focusable,
            z_order=z_order,
            on_close=on_close,
        )

    def open_picker(
        self,
        title: str,
        source: Any,
        *,
        on_confirm: Any = None,
        on_close: Any = None,
        multi_select: bool = False,
        preview: Any = None,
        keymap: dict | None = None,
        item_style: Any = None,
        debounce_ms: int = 0,
    ) -> None:
        """Open the fuzzy picker."""
        if self._picker is None:
            return
        self._picker.open(
            title,
            source,
            on_confirm=on_confirm,
            on_close=on_close,
            multi_select=multi_select,
            preview=preview,
            keymap=keymap,
            item_style=item_style,
            debounce_ms=debounce_ms,
        )

    def close_picker(self) -> None:
        """Close the active picker."""
        if self._picker is not None:
            self._picker.close()

    def notify(self, message: str, level: str = "info", title: str = "", timeout: float = 3.0) -> Any:
        """Show a notification toast. Returns a NotifyHandle."""
        if self._notify_manager is None:
            return None
        return self._notify_manager.notify(message, level=level, title=title, timeout=timeout)

    # ------------------------------------------------------------------
    # Terminal buffers
    # ------------------------------------------------------------------

    def open_terminal(
        self,
        name: str,
        cmd: list[str] | None = None,
        rows: int = 24,
        cols: int = 80,
    ) -> Any:
        """Create and open a named terminal buffer. Returns TerminalBuffer."""
        from peovim.ui.terminal_buffer import TerminalBuffer

        tb = TerminalBuffer(name, rows=rows, cols=cols)
        self._terminals[name] = tb
        # Schedule open() as an async task
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(tb.open(cmd))
        except Exception:
            pass
        return tb

    def get_terminal(self, name: str) -> Any:
        """Return the named TerminalBuffer, or None."""
        return self._terminals.get(name)

    def toggle_terminal(self, name: str) -> None:
        """If terminal is open, close it; otherwise open it."""
        tb = self._terminals.get(name)
        if tb is not None and tb.is_open:
            tb.close()
            self._terminals.pop(name, None)
        else:
            self.open_terminal(name)

    # ------------------------------------------------------------------
    # Tree views
    # ------------------------------------------------------------------

    def open_tree(
        self,
        nodes: list,
        *,
        title: str = "",
        width: int = 30,
        on_select=None,
        on_close=None,
        on_key=None,
    ) -> Any:
        """Open a tree view panel. Returns a TreeViewHandle."""
        from peovim.ui.tree_view import TreeView, TreeViewHandle

        tree = TreeView(nodes, title=title, on_select=on_select, on_close=on_close, on_key=on_key, width=width)
        handle = TreeViewHandle(tree, on_remove=lambda h: self._tree_views.remove(h))
        self._tree_views.append(handle)
        return handle

    # ------------------------------------------------------------------
    # Persistent sidebar
    # ------------------------------------------------------------------

    def get_sidebar_panel(self, name: str) -> Any:
        return self._sidebar.get_panel(name)

    def active_sidebar_panel_name(self) -> str | None:
        return self._sidebar.active_panel_name

    def register_sidebar_panel(self, name: str, panel: Any) -> Any:
        return self._sidebar.register_panel(name, panel)

    def set_sidebar_style(
        self,
        *,
        background: ColorLike | None | object = _UNSET,
        header_active_fg: ColorLike | None | object = _UNSET,
        header_active_bg: ColorLike | None | object = _UNSET,
        header_inactive_fg: ColorLike | None | object = _UNSET,
        header_inactive_bg: ColorLike | None | object = _UNSET,
    ) -> Any:
        kwargs: dict[str, Any] = {}
        if background is not _UNSET:
            kwargs["background"] = background
        if header_active_fg is not _UNSET:
            kwargs["header_active_fg"] = header_active_fg
        if header_active_bg is not _UNSET:
            kwargs["header_active_bg"] = header_active_bg
        if header_inactive_fg is not _UNSET:
            kwargs["header_inactive_fg"] = header_inactive_fg
        if header_inactive_bg is not _UNSET:
            kwargs["header_inactive_bg"] = header_inactive_bg
        return self._sidebar.set_style(**kwargs)

    def list_sidebar_panels(self) -> list[str]:
        return self._sidebar.list_panels()

    def is_sidebar_visible(self, name: str | None = None) -> bool:
        return self._sidebar.is_visible(name)

    def show_sidebar_panel(self, name: str, panel: Any, *, focus: bool = True) -> Any:
        return self._sidebar.show_panel(name, panel, focus=focus)

    def show_active_sidebar_panel(self, *, focus: bool = True) -> Any:
        return self._sidebar.show_active_panel(focus=focus)

    def toggle_sidebar_panel(self, name: str, panel: Any | None = None, *, focus: bool = True) -> bool:
        return self._sidebar.toggle_panel(name, panel, focus=focus)

    def hide_sidebar(self) -> None:
        self._sidebar.hide()

    def focus_sidebar(self) -> None:
        self._sidebar.focus()

    def blur_sidebar(self) -> None:
        self._sidebar.blur()

    def sidebar_nmap(self, key: str, plug_name: str) -> None:
        """Bind a sidebar-internal key to a <Plug> name (e.g. 'SidebarShrink')."""
        self._sidebar._key_to_plug[key] = plug_name

    def next_sidebar_panel(self, *, focus: bool = True) -> Any:
        return self._sidebar.next_panel(focus=focus)

    def prev_sidebar_panel(self, *, focus: bool = True) -> Any:
        return self._sidebar.prev_panel(focus=focus)

    def show_tree_sidebar(
        self,
        name: str,
        nodes: list,
        *,
        title: str = "",
        width: int = 30,
        on_select=None,
        on_key=None,
        focus: bool = True,
    ) -> Any:
        from peovim.ui.sidebar import TreeSidebarPanel
        from peovim.ui.tree_view import TreeView

        panel = self._sidebar.get_panel(name)
        if panel is None:
            tree = TreeView(nodes, title=title, on_select=on_select, on_key=on_key, width=width)
            panel = TreeSidebarPanel(tree, width=width)
        else:
            # Update existing tree with fresh nodes and title
            tree = getattr(panel, "tree", None)
            if tree is not None:
                tree.set_nodes(nodes)
                if title:
                    tree._title = title  # noqa: SLF001
        return self._sidebar.show_panel(name, panel, focus=focus)

    # ------------------------------------------------------------------
    # Unified panel API
    # ------------------------------------------------------------------

    def register_panel(self, name: str, content: Any, *, host: str = "sidebar") -> Any:
        """Register *content* in the named host ('sidebar' or 'bottom').

        Stores the preferred_host metadata so move_panel() can find it later.
        Does NOT show the panel — call show_panel() to make it visible.
        """
        if host == "bottom":
            return self._bottom_panel.register_tab(name, content)
        return self._sidebar.register_tab(name, content, preferred_host="sidebar")

    def move_panel(self, name: str, to: str) -> bool:
        """Move a registered panel from its current host to another.

        *to* must be 'sidebar' or 'bottom'.  Returns True if the panel was
        found and moved.  The panel is hidden in its current host and
        registered (but not shown) in the destination.
        """
        if to not in ("sidebar", "bottom"):
            return False
        # Find which host currently owns this panel
        src_host = self._sidebar if self._sidebar.get_tab(name) is not None else None
        if src_host is None and self._bottom_panel.get_tab(name) is not None:
            src_host = self._bottom_panel  # type: ignore[assignment]
        if src_host is None:
            return False
        content = src_host.get_tab(name)
        src_host.unregister_tab(name)
        dst_host = self._sidebar if to == "sidebar" else self._bottom_panel
        dst_host.register_tab(name, content, preferred_host=to)
        return True

    def show_panel(self, name: str, *, focus: bool = True) -> Any:
        """Show a panel by name regardless of which host it is registered in.

        Checks sidebar first, then bottom panel.  Returns the content object,
        or None if *name* is not registered in either host.
        """
        content = self._sidebar.get_tab(name)
        if content is not None:
            return self._sidebar.show_panel(name, content, focus=focus)
        content = self._bottom_panel.get_tab(name)
        if content is not None:
            return self._bottom_panel.show_tab(name, focus=focus)
        return None

    # ------------------------------------------------------------------
    # Bottom panel
    # ------------------------------------------------------------------

    def register_bottom_tab(self, name: str, tab: Any) -> None:
        """Register a tab in the bottom panel without showing it."""
        self._bottom_panel.register_tab(name, tab)

    def show_bottom_tab(self, name: str, tab: Any | None = None, *, focus: bool = True) -> Any:
        """Show the bottom panel with *name* as the active tab."""
        return self._bottom_panel.show_tab(name, tab, focus=focus)

    def toggle_bottom_panel(self, *, focus: bool = True) -> bool:
        """Toggle bottom panel visibility.  Returns True if now visible."""
        return self._bottom_panel.toggle(focus=focus)

    def hide_bottom_panel(self) -> None:
        self._bottom_panel.hide()

    def focus_bottom_panel(self) -> None:
        self._bottom_panel.focus()

    def blur_bottom_panel(self) -> None:
        self._bottom_panel.blur()

    def is_bottom_panel_visible(self, tab_name: str | None = None) -> bool:
        if not self._bottom_panel.visible:
            return False
        if tab_name is not None:
            return self._bottom_panel.active_tab_name == tab_name
        return True

    def get_bottom_tab(self, name: str) -> Any:
        return self._bottom_panel.get_tab(name)

    def list_bottom_tabs(self) -> list[str]:
        return self._bottom_panel.list_tabs()

    def bottom_nmap(self, key: str, plug_name: str) -> None:
        """Bind a bottom-panel-internal key to a <Plug> name."""
        self._bottom_panel._key_to_plug[key] = plug_name

    @property
    def log_output(self) -> Any:
        """Return the built-in LogOutputTab instance."""
        return self._log_output_tab

    def show_which_key(self, pairs: list[tuple[str, str]], *, title: str = "Which Key") -> None:
        """Show the which-key panel if it is available."""
        if self._which_key_panel is not None:
            self._which_key_panel.show(pairs, title=title)
        # If bottom panel is open, switch to the keys tab.
        # Only save _pre_wk_tab when the current tab is not already "keys" so
        # that repeated show_which_key calls (one per prefix step, e.g. <leader>
        # then <leader>c) don't overwrite the original tab with "keys".
        if self._bottom_panel.visible:
            keys_tab = self._bottom_panel.get_tab("keys")
            if keys_tab is not None:
                current = self._bottom_panel.active_tab_name
                if current != "keys":
                    self._bottom_panel._pre_wk_tab = current
                self._bottom_panel._active_tab = "keys"
                self._bottom_panel._needs_full_redraw = True

    def hide_which_key(self) -> None:
        """Hide the which-key panel if it is available."""
        if self._which_key_panel is not None:
            self._which_key_panel.hide()
        # Restore previous tab if we switched
        prev = getattr(self._bottom_panel, "_pre_wk_tab", None)
        if prev is not None and self._bottom_panel.visible:
            self._bottom_panel._active_tab = prev
            self._bottom_panel._pre_wk_tab = None
            self._bottom_panel._needs_full_redraw = True
