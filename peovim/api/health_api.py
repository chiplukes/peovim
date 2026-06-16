"""
api.health_api — HealthAPI: plugin-facing health check registration

Wraps HealthRegistry. Plugins register named checkers; :checkhealth runs them all.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI


class HealthAPI:
    """Plugin-facing API for registering custom health checkers."""

    def __init__(self) -> None:
        from peovim.core.health import build_registry

        self._registry = build_registry()
        self._plugin_manager: Any = None
        self._config_loader: Any = None

    def set_context(self, plugin_manager: Any = None, config_loader: Any = None) -> None:
        """Wire in the PluginManager and ConfigLoader after construction."""
        if plugin_manager is not None:
            self._plugin_manager = plugin_manager
        if config_loader is not None:
            self._config_loader = config_loader

    def register(self, name: str, fn: Callable, label: str = "") -> None:
        """Register a custom health checker function.

        fn(api, plugin_manager, config_loader) -> list[HealthItem]
        """
        self._registry.register(name, fn, label=label)

    def run(self, api: EditorAPI) -> str:
        """Run all health checks and return formatted report text."""
        from peovim.core.health import format_report

        results = self._registry.run_all(api, self._plugin_manager, self._config_loader)
        return format_report(results)
