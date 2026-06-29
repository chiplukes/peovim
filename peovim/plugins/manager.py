"""
plugins.manager — PluginManager: load/unload plugins by module path.

A plugin is a Python module with a setup(api) function.
Optional teardown() is called on unload.

Phase 7d adds lazy/deferred loading via on_filetype, on_event, on_command triggers.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

_log = logging.getLogger("peovim.plugins")


@dataclass
class _PendingPlugin:
    """A plugin that has been registered but not yet loaded."""

    module_path: str
    on_filetype: list[str] | None = None
    on_event: str | None = None
    on_command: list[str] | None = None
    _loaded: bool = field(default=False, init=False)


class PluginManager:  # cm:a5c8f4
    """Manages plugin lifecycle: load, unload, list."""

    def __init__(self, api: EditorAPI) -> None:
        self._api = api
        self._loaded: dict[str, Any] = {}  # module_path → module
        self._load_errors: dict[str, str] = {}  # module_path → error message
        self._pending: dict[str, _PendingPlugin] = {}  # module_path → pending info

    def load(
        self,
        module_path: str,
        *,
        on_filetype: list[str] | None = None,
        on_event: str | None = None,
        on_command: list[str] | None = None,
    ) -> None:
        """
        Import a plugin module and call its setup(api) function.

        If trigger kwargs are provided, defer loading until the trigger fires:
          on_filetype: list[str]  — load when filetype_detected fires for matching type
          on_event: str           — load on first fire of this event
          on_command: list[str]   — register stub commands; load when any is invoked
        """
        if module_path in self._loaded:
            return

        has_trigger = on_filetype is not None or on_event is not None or on_command is not None
        if not has_trigger:
            self._load_now(module_path)
            return

        # Deferred loading
        _log.debug(
            "deferred %s  (on_filetype=%s  on_event=%s  on_command=%s)", module_path, on_filetype, on_event, on_command
        )
        pending = _PendingPlugin(
            module_path=module_path,
            on_filetype=on_filetype,
            on_event=on_event,
            on_command=on_command,
        )
        self._pending[module_path] = pending

        if on_filetype is not None:
            self._wire_filetype(pending)

        if on_event is not None:
            self._wire_event(pending)

        if on_command is not None:
            self._wire_commands(pending)

    def _load_now(self, module_path: str) -> None:
        """Import a module immediately and call its setup(api)."""
        if module_path in self._loaded:
            return
        self._pending.pop(module_path, None)
        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            _log.warning("Failed to import plugin %s: %s", module_path, e)
            self._load_errors[module_path] = f"ImportError: {e}"
            return
        if hasattr(module, "setup"):
            try:
                module.setup(self._api)
            except Exception as e:
                _log.warning("Plugin %s setup() failed: %s", module_path, e)
                self._load_errors[module_path] = f"{type(e).__name__}: {e}"
                return
        self._loaded[module_path] = module
        _log.info("loaded %s", module_path)

    def _wire_filetype(self, pending: _PendingPlugin) -> None:
        """Subscribe to filetype_detected; load on matching filetype (once)."""
        module_path = pending.module_path
        on_filetype = pending.on_filetype

        def _handler(**kwargs: Any) -> None:
            if pending._loaded:
                return
            ft = kwargs.get("filetype", "")
            if ft in (on_filetype or []):
                pending._loaded = True
                self._load_now(module_path)

        with contextlib.suppress(Exception):
            self._api.events.on("filetype_detected", _handler)

    def _wire_event(self, pending: _PendingPlugin) -> None:
        """Subscribe to a named event; load on first fire (once)."""
        module_path = pending.module_path
        event_name = pending.on_event
        if event_name is None:
            return

        def _handler(**kwargs: Any) -> None:
            if pending._loaded:
                return
            pending._loaded = True
            self._load_now(module_path)

        with contextlib.suppress(Exception):
            self._api.events.on(event_name, _handler)

    def _wire_commands(self, pending: _PendingPlugin) -> None:
        """Register stub commands that load the plugin on first invocation."""
        module_path = pending.module_path
        commands = pending.on_command or []

        for cmd_name in commands:
            # Capture cmd_name in closure
            def _make_stub(name: str):
                def _stub(cmd, ctx) -> Any:
                    if not pending._loaded:
                        pending._loaded = True
                        self._load_now(module_path)
                        # Unregister the stub (real plugin may have registered its own)
                        with contextlib.suppress(Exception):
                            self._api.commands.unregister(name)
                    # Re-execute the command with the real handler
                    with contextlib.suppress(Exception):
                        return self._api.commands.execute(name, ctx)

                return _stub

            with contextlib.suppress(Exception):
                self._api.commands.register(cmd_name, _make_stub(cmd_name))

    def unload(self, module_path: str) -> None:
        """Call teardown() if present and remove from loaded set."""
        module = self._loaded.pop(module_path, None)
        if module is None:
            return
        if hasattr(module, "teardown"):
            with contextlib.suppress(Exception):
                module.teardown()
        _log.info("unloaded %s", module_path)

    def list_loaded(self) -> list[str]:
        """Return sorted list of loaded plugin module paths."""
        return sorted(self._loaded)

    def list_pending(self) -> list[str]:
        """Return sorted list of deferred-but-not-yet-loaded plugin module paths."""
        return sorted(self._pending)

    def get(self, module_path: str) -> Any:
        """Return the loaded module, or None."""
        return self._loaded.get(module_path)
