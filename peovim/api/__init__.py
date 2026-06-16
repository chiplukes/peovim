"""peovim.api — public plugin API"""

from peovim.api._metadata import (
    API_NAMESPACE_STATUS,
    VERSION,
    VERSION_STR,
    NamespaceStatus,
    PluginVersionError,
    namespace_status,
    requires_version,
)
from peovim.api.editor import EditorAPI

__all__ = [
    "API_NAMESPACE_STATUS",
    "EditorAPI",
    "NamespaceStatus",
    "PluginVersionError",
    "VERSION",
    "VERSION_STR",
    "namespace_status",
    "requires_version",
]
