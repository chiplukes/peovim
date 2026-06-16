from __future__ import annotations

from dataclasses import dataclass

ApiVersion = tuple[int, int, int]
VERSION: ApiVersion = (0, 1, 0)
VERSION_STR = ".".join(str(part) for part in VERSION)


class PluginVersionError(RuntimeError):
    """Raised when a plugin requires an unsupported API version."""


@dataclass(frozen=True)
class NamespaceStatus:
    status: str
    note: str = ""


API_NAMESPACE_STATUS: dict[str, NamespaceStatus] = {
    "editor": NamespaceStatus("implemented", "Core editor facade used by built-in plugins."),
    "buffer": NamespaceStatus("implemented", "Read/write buffer operations and decorations are active."),
    "window": NamespaceStatus("implemented", "Window cursor and option access is active."),
    "workspace": NamespaceStatus("implemented", "Workspace and tab queries are active."),
    "keymap": NamespaceStatus("implemented", "Mapping registration and <Plug> interop are active."),
    "commands": NamespaceStatus("implemented", "Ex command registration and execution are active."),
    "events": NamespaceStatus("implemented", "Event subscription and emission are active."),
    "options": NamespaceStatus("implemented", "Typed option registration and mutation are active."),
    "registers": NamespaceStatus("implemented", "Register read/write helpers are active."),
    "store": NamespaceStatus("implemented", "Persistent plugin key-value storage is active."),
    "ui": NamespaceStatus("implemented", "Float, picker, tree, terminal, and notify helpers are active."),
    "health": NamespaceStatus("implemented", "Health registration and context wiring are active."),
    "lsp": NamespaceStatus("experimental", "Usable today, but the public surface is still evolving."),
    "git": NamespaceStatus("experimental", "Root/status/branch/hunks exist, but the namespace is still partial."),
    "session": NamespaceStatus("experimental", "Save/restore works, but the session surface is still narrow."),
    "completion": NamespaceStatus("planned", "Placeholder module only; pipeline API not implemented yet."),
    "diagnostics": NamespaceStatus("planned", "Placeholder module only; unified diagnostics API not implemented yet."),
    "snippets": NamespaceStatus("planned", "Placeholder module only; snippet API not implemented yet."),
    "syntax": NamespaceStatus("planned", "Placeholder module only; tree-sitter plugin API not implemented yet."),
    "debug": NamespaceStatus("planned", "Placeholder module only; DAP API not implemented yet."),
    "testing": NamespaceStatus("planned", "Placeholder module only; test adapter API not implemented yet."),
    "quickfix": NamespaceStatus("planned", "Placeholder module only; quickfix/location list API not implemented yet."),
    "jumplist": NamespaceStatus("planned", "Placeholder module only; public jumplist API not implemented yet."),
    "diff": NamespaceStatus("planned", "Built-in plugin interface target, not a stable core namespace yet."),
    "repl": NamespaceStatus("planned", "Built-in plugin interface target, not a stable core namespace yet."),
}


def parse_version(value: str | ApiVersion) -> ApiVersion:
    if isinstance(value, tuple):
        if len(value) == 3 and all(isinstance(part, int) for part in value):
            return value
        raise PluginVersionError(f"Invalid API version tuple: {value!r}")

    parts = value.split(".")
    if len(parts) != 3:
        raise PluginVersionError(f"Invalid API version string: {value!r}")
    try:
        parsed = tuple(int(part) for part in parts)
    except ValueError as exc:
        raise PluginVersionError(f"Invalid API version string: {value!r}") from exc
    return parsed  # type: ignore[return-value]


def format_version(version: str | ApiVersion) -> str:
    parsed = parse_version(version)
    return ".".join(str(part) for part in parsed)


def requires_version(min_version: str | ApiVersion, current_version: str | ApiVersion = VERSION) -> None:
    required = parse_version(min_version)
    current = parse_version(current_version)
    if current < required:
        raise PluginVersionError(
            f"Plugin requires API >= {format_version(required)}, current API is {format_version(current)}"
        )


def namespace_status(name: str) -> NamespaceStatus:
    return API_NAMESPACE_STATUS.get(name, NamespaceStatus("planned", "Unknown namespace."))
