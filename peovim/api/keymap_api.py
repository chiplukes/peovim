"""
KeymapAPI — key binding registration
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.modal.keybindings import BindingRegistry


class KeymapAPI:  # cm:f2b8e7
    """Plugin-facing API for registering key bindings."""

    def __init__(self, registry: BindingRegistry) -> None:
        self._registry = registry

    def nmap(self, keys: str, target: Any, desc: str = "") -> None:
        """Register a normal-mode binding."""
        self._registry.register("normal", keys, target, desc=desc)

    def vmap(self, keys: str, target: Any, desc: str = "") -> None:
        """Register a visual-mode binding."""
        self._registry.register("visual", keys, target, desc=desc)

    def imap(self, keys: str, target: Any, desc: str = "") -> None:
        """Register an insert-mode binding."""
        self._registry.register("insert", keys, target, desc=desc)

    def nunmap(self, keys: str) -> None:
        """Remove a normal-mode binding."""
        self._registry.unregister("normal", keys)

    def vunmap(self, keys: str) -> None:
        """Remove a visual-mode binding."""
        self._registry.unregister("visual", keys)

    def iunmap(self, keys: str) -> None:
        """Remove an insert-mode binding."""
        self._registry.unregister("insert", keys)

    def unmap(self, keys: str) -> None:
        """Remove a normal-mode binding (alias for nunmap)."""
        self._registry.unregister("normal", keys)

    def ngroup(self, keys: str, name: str) -> None:
        """Register a human-readable name for a key prefix group (for which-key display)."""
        self._registry.register_group(keys, name)

    def define_plug(self, name: str, target: Any, desc: str = "") -> None:
        """Register a <Plug> mapping."""
        self._registry.register("normal", f"<Plug>{name}", target, desc=desc)

    def define_vplug(self, name: str, target: Any, desc: str = "") -> None:
        """Register a visual-mode <Plug> mapping."""
        self._registry.register("visual", f"<Plug>{name}", target, desc=desc)

    def invoke_plug(self, name: str) -> bool:
        """Execute a registered <Plug> callback immediately."""
        return self._registry.execute_plug(name)

    def find_keys_for_plug(self, name: str, mode: str = "normal") -> list[str]:
        """Return all key sequences currently bound to <Plug>name in the given mode."""
        return self._registry.find_keys_for_plug(name, mode=mode)

    def get_bindings(self, mode: str | None = None) -> list[Any]:
        """Return registered bindings, optionally filtered by mode."""
        return self._registry.get_bindings(mode)

    def get_group_name(self, prefix: str) -> str:
        """Return the registered group label for a prefix, if any."""
        return self._registry.get_group_name(prefix)

    def feed_keys(self, keys: str, remap: bool = True) -> None:
        """Feed key sequence to the engine (via RunNormalKeys action)."""
        from peovim.modal.actions import RunNormalKeys

        disp = self._registry._dispatcher
        disp.dispatch([RunNormalKeys(keys, remap=remap)])

    @property
    def leader(self) -> str:
        """Return the current leader key."""
        disp = self._registry._dispatcher
        es = getattr(disp, "_editor_state", None)
        if es is not None and hasattr(es, "options"):
            return es.options.get("leader") or "\\"
        return "\\"

    @property
    def local_leader(self) -> str:
        """Return the local leader key."""
        disp = self._registry._dispatcher
        es = getattr(disp, "_editor_state", None)
        if es is not None and hasattr(es, "options"):
            return es.options.get("localleader") or "\\"
        return "\\"
