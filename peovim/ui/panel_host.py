"""
ui.panel_host — Shared base class for sidebar and bottom panel hosts.

Both SidebarHost and BottomPanelHost inherit from PanelHost, which owns:
  - Tab registry (dict of named content objects)
  - Active tab tracking and needs_full_redraw flag
  - Visible / focused state
  - key-to-plug routing table
  - show / hide / toggle / focus / blur operations
  - Tab cycling (next / prev)
  - feed_key skeleton
  - Lifecycle hook dispatch (on_show, on_hide, on_focus, on_blur)

Subclasses add orientation-specific rendering and sizing (reserved_width /
reserved_height) and may override feed_key or _builtin_plugs to add special-case
handling.

Content objects need only:
    render(grid: CellGrid) -> None
    feed_key(key: str) -> bool

Optional attributes (queried with getattr + defaults):
    title: str        — shown in bottom-panel tab bar (default: registration name)
    width: int        — used by sidebar to set its reserved width (default: host width)
    on_show()         — called when content becomes the active visible tab
    on_hide()         — called when content is hidden or replaced
    on_focus()        — called when the host receives focus while this tab is active
    on_blur()         — called when the host loses focus while this tab is active
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


def _call_hook(content: Any, hook_name: str) -> None:
    """Call a lifecycle hook on *content* if it exists."""
    fn = getattr(content, hook_name, None)
    if callable(fn):
        fn()


class PanelHost:  # cm:7e9f8c
    """
    Base class for dockable panel hosts (sidebar, bottom panel, etc.).

    Subclasses must implement:
        render(grid, ...) -> bool
        reserved_size(total) -> int   (reserved_width or reserved_height)
    """

    def __init__(self) -> None:
        self._tabs: dict[str, Any] = {}
        self._tab_order: list[str] = []
        self._active_tab: str | None = None
        self._visible: bool = False
        self._focused: bool = False
        self._needs_full_redraw: bool = False
        self._binding_registry: Any = None
        self._key_to_plug: dict[str, str] = {}
        # Preferred host metadata: name → "sidebar" | "bottom"
        self._preferred_host: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def focused(self) -> bool:
        return self._focused and self._visible

    @property
    def active_tab_name(self) -> str | None:
        return self._active_tab

    @property
    def active_tab(self) -> Any | None:
        if self._active_tab is None:
            return None
        return self._tabs.get(self._active_tab)

    # ------------------------------------------------------------------
    # Tab registry
    # ------------------------------------------------------------------

    def register_tab(self, name: str, content: Any, *, preferred_host: str | None = None) -> Any:
        """Register *content* under *name*.  Replaces any existing entry in-place."""
        if name not in self._tabs:
            self._tab_order.append(name)
        self._tabs[name] = content
        if preferred_host is not None:
            self._preferred_host[name] = preferred_host
        return content

    def get_tab(self, name: str) -> Any | None:
        return self._tabs.get(name)

    def unregister_tab(self, name: str) -> Any | None:
        """Remove *name* from this host.  Returns the content object, or None."""
        content = self._tabs.pop(name, None)
        if name in self._tab_order:
            self._tab_order.remove(name)
        self._preferred_host.pop(name, None)
        if self._active_tab == name:
            self._active_tab = next((n for n in self._tab_order if n in self._tabs), None)
        return content

    def list_tabs(self, *, exclude: set[str] | None = None) -> list[str]:
        """Return ordered tab names, optionally skipping names in *exclude*."""
        if exclude:
            return [n for n in self._tab_order if n in self._tabs and n not in exclude]
        return [n for n in self._tab_order if n in self._tabs]

    # ------------------------------------------------------------------
    # Visibility / focus
    # ------------------------------------------------------------------

    def show_tab(self, name: str, content: Any | None = None, *, focus: bool = True) -> Any | None:
        """Make the host visible with *name* as the active tab.

        If *content* is provided it is registered first.  Returns the content
        object, or None if *name* is not registered.
        """
        if content is not None:
            self.register_tab(name, content)
        if name not in self._tabs:
            return None

        prev_name = self._active_tab
        prev_content = self.active_tab
        was_visible = self._visible

        # Notify previous tab if switching
        if was_visible and prev_name != name and prev_content is not None:
            if self._focused:
                _call_hook(prev_content, "on_blur")
            _call_hook(prev_content, "on_hide")

        if prev_name != name:
            self._needs_full_redraw = True
            _log.debug("%s show %r (was %r)", type(self).__name__, name, prev_name)
        elif not was_visible:
            _log.debug("%s show %r", type(self).__name__, name)
        self._active_tab = name
        self._visible = True

        new_content = self._tabs[name]
        if not was_visible or prev_name != name:
            _call_hook(new_content, "on_show")

        if focus:
            self._do_focus()
        else:
            self._do_blur()

        return new_content

    def show_active_tab(self, *, focus: bool = True) -> Any | None:
        """Re-show the last-active (or first available) tab."""
        if not self._tab_order:
            return None
        name = self._active_tab
        if name is None or name not in self._tabs:
            name = next((n for n in self._tab_order if n in self._tabs), None)
        if name is None:
            return None
        return self.show_tab(name, focus=focus)

    def hide(self) -> None:
        content = self.active_tab
        if self._visible and content is not None:
            if self._focused:
                _call_hook(content, "on_blur")
            _call_hook(content, "on_hide")
        if self._visible:
            _log.debug("%s hide (active=%r)", type(self).__name__, self._active_tab)
        self._visible = False
        self._focused = False
        self._needs_full_redraw = True

    def toggle(self, *, focus: bool = True) -> bool:
        """Toggle visibility.  Returns True if now visible."""
        if self._visible:
            self.hide()
            return False
        return self.show_active_tab(focus=focus) is not None

    def focus(self) -> None:
        if self._visible:
            self._do_focus()

    def blur(self) -> None:
        self._do_blur()

    def _do_focus(self) -> None:
        if not self._focused:
            _log.debug("%s focus (active=%r)", type(self).__name__, self._active_tab)
            content = self.active_tab
            if content is not None:
                _call_hook(content, "on_focus")
        self._focused = True

    def _do_blur(self) -> None:
        if self._focused:
            _log.debug("%s blur (active=%r)", type(self).__name__, self._active_tab)
            content = self.active_tab
            if content is not None:
                _call_hook(content, "on_blur")
        self._focused = False

    # ------------------------------------------------------------------
    # Tab cycling
    # ------------------------------------------------------------------

    def next_tab(self, *, exclude: set[str] | None = None) -> None:
        visible = self.list_tabs(exclude=exclude)
        if not visible:
            return
        prev_content = self.active_tab
        if self._active_tab not in visible:
            self._active_tab = visible[0]
        else:
            idx = visible.index(self._active_tab)
            self._active_tab = visible[(idx + 1) % len(visible)]
        self._fire_tab_change(prev_content, self.active_tab)

    def prev_tab(self, *, exclude: set[str] | None = None) -> None:
        visible = self.list_tabs(exclude=exclude)
        if not visible:
            return
        prev_content = self.active_tab
        if self._active_tab not in visible:
            self._active_tab = visible[-1]
        else:
            idx = visible.index(self._active_tab)
            self._active_tab = visible[(idx - 1) % len(visible)]
        self._fire_tab_change(prev_content, self.active_tab)

    def _fire_tab_change(self, old: Any | None, new: Any | None) -> None:
        """Call lifecycle hooks when the active tab changes via cycling."""
        self._needs_full_redraw = True
        _log.debug("%s tab → %r", type(self).__name__, self._active_tab)
        if old is not None and old is not new:
            if self._focused:
                _call_hook(old, "on_blur")
            _call_hook(old, "on_hide")
        if new is not None and new is not old:
            _call_hook(new, "on_show")
            if self._focused:
                _call_hook(new, "on_focus")

    # ------------------------------------------------------------------
    # Key routing
    # ------------------------------------------------------------------

    def feed_key(self, key: str) -> bool:
        """Route *key* to the active tab.  Returns True if consumed."""
        if not self._focused:
            return False
        plug_name = self._key_to_plug.get(key)
        if plug_name is not None:
            self._execute_plug(plug_name)
            return True
        content = self.active_tab
        if content is None:
            return False
        return bool(content.feed_key(key))

    def _execute_plug(self, plug_name: str) -> None:
        if self._binding_registry is not None:
            self._binding_registry.execute_plug(plug_name)
            return
        fn = self._builtin_plugs().get(plug_name)
        if fn is not None:
            fn()

    def _builtin_plugs(self) -> dict[str, Any]:
        """Override in subclasses to provide fallback plug actions for tests."""
        return {}
