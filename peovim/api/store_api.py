"""
StoreAPI — persistent plugin key-value storage factory
"""

from __future__ import annotations


class StoreAPI:
    """Factory that returns per-plugin PluginStore instances."""

    def get_store(self, name: str):
        """Return a PluginStore for the given plugin name."""
        from peovim.core.store_api import PluginStore

        return PluginStore(name)
