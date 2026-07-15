"""
core.health — HealthItem, HealthRegistry, run_all_checks

A health check is a named function: check(api) -> list[HealthItem].
Registered by name so built-ins and plugins share the same registry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI
    from peovim.core.style import Style

# Status values ordered by severity (lowest → highest)
_STATUS_ORDER = {"info": 0, "ok": 1, "warn": 2, "error": 3}

STATUS_ICONS = {
    "ok": "  ✓ ",
    "warn": "  ⚠ ",
    "error": "  ✗ ",
    "info": "  · ",
}

# ---------------------------------------------------------------------------
# Colour palette (Catppuccin Mocha inspired)
# ---------------------------------------------------------------------------

_C_HEADING = (203, 166, 247)  # mauve  — section ## headings
_C_OK = (166, 227, 161)  # green  — ✓ ok items
_C_WARN = (249, 226, 175)  # yellow — ⚠ warn items
_C_ERROR = (243, 139, 168)  # red    — ✗ error items
_C_INFO = (148, 156, 187)  # muted  — · info items
_C_DETAIL = (108, 112, 134)  # dim    — indented detail lines


@dataclass
class HealthItem:
    status: str  # "ok", "warn", "error", "info"
    message: str
    detail: str = ""  # optional extra detail shown on the next line


CheckerFn = Callable[["EditorAPI", Any, Any], list[HealthItem]]


class HealthRegistry:
    """Ordered registry of named health checkers."""

    def __init__(self) -> None:
        # Insertion-ordered dict: name → (fn, label)
        self._checkers: dict[str, tuple[CheckerFn, str]] = {}

    def register(self, name: str, fn: CheckerFn, label: str = "") -> None:
        """Register a checker function.

        fn(api, plugin_manager, config_loader) -> list[HealthItem]
        label is the section heading; defaults to name.
        """
        self._checkers[name] = (fn, label or name)

    def run_all(
        self,
        api: EditorAPI,
        plugin_manager: Any = None,
        config_loader: Any = None,
    ) -> dict[str, tuple[str, list[HealthItem]]]:
        """Run all registered checkers. Returns {name: (label, [items])}."""
        results: dict[str, tuple[str, list[HealthItem]]] = {}
        for name, (fn, label) in self._checkers.items():
            try:
                items = fn(api, plugin_manager, config_loader)
            except Exception as exc:
                items = [HealthItem("error", f"Checker crashed: {exc}")]
            results[name] = (label, items)
        return results


def format_report(results: dict[str, tuple[str, list[HealthItem]]]) -> str:
    """Convert run_all() output into a human-readable string."""
    lines: list[str] = []
    for _name, (label, items) in results.items():
        lines.append(f"## {label}")
        lines.append("")
        for item in items:
            icon = STATUS_ICONS.get(item.status, "  ? ")
            lines.append(f"{icon}{item.message}")
            if item.detail:
                for dl in item.detail.splitlines():
                    lines.append(f"       {dl}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Highlight spans for the formatted report
# ---------------------------------------------------------------------------

# (line_index, col_start, col_end, Style)
HighlightSpan = tuple[int, int, int, "Style"]

_ICON_LEN = len("  ✓ ")  # all status icons are the same character length (4)
_DETAIL_PREFIX = "       "  # 7-space indent used for detail lines


def highlight_report(text: str) -> list[HighlightSpan]:
    """Return colour highlight spans for a formatted :checkhealth report.

    Each span is (line_index, col_start, col_end, Style).  Suitable for
    adding to a DecorationsStore as HighlightRegion decorations.
    """
    from peovim.core.style import Style
    from peovim.ui.backend import ATTR_BOLD

    _STATUS_COLORS = {
        "ok": _C_OK,
        "warn": _C_WARN,
        "error": _C_ERROR,
        "info": _C_INFO,
    }
    _ICON_BY_STATUS = {st: STATUS_ICONS[st] for st in STATUS_ICONS}
    # Build a reverse map: icon_prefix → (status, color)
    _PREFIX_MAP: dict[str, tuple[str, tuple[int, int, int]]] = {
        icon: (st, _STATUS_COLORS[st]) for st, icon in _ICON_BY_STATUS.items()
    }

    spans: list[HighlightSpan] = []

    for lineno, line in enumerate(text.splitlines()):
        if not line:
            continue

        if line.startswith("## "):
            # Full heading line — bold mauve
            spans.append((lineno, 0, len(line), Style(fg=_C_HEADING, attrs=ATTR_BOLD)))
            continue

        # Check for a status icon prefix
        matched = False
        for prefix, (_st, color) in _PREFIX_MAP.items():
            if line.startswith(prefix):
                # Icon (bold + colored)
                spans.append((lineno, 0, _ICON_LEN, Style(fg=color, attrs=ATTR_BOLD)))
                # Message text (same color, no bold)
                if len(line) > _ICON_LEN:
                    spans.append((lineno, _ICON_LEN, len(line), Style(fg=color)))
                matched = True
                break

        if not matched and line.startswith(_DETAIL_PREFIX):
            # Detail / continuation line — dim
            spans.append((lineno, 0, len(line), Style(fg=_C_DETAIL)))

    return spans


def build_registry() -> HealthRegistry:
    """Build and return a HealthRegistry pre-loaded with built-in checkers."""
    from peovim.core.health_checks import (
        check_config,
        check_data_dirs,
        check_editor_version,
        check_lsp,
        check_native_renderer,
        check_optional_deps,
        check_persistence,
        check_plugins,
        check_python_env,
        check_render_runtime,
        check_syntax,
        check_terminal,
    )

    reg = HealthRegistry()
    reg.register("version", check_editor_version, label="Editor Version")
    reg.register("python", check_python_env, label="Python Environment")
    reg.register("syntax", check_syntax, label="Syntax Highlighting")
    reg.register("optional", check_optional_deps, label="Optional Dependencies")
    reg.register("native", check_native_renderer, label="Native Renderer")
    reg.register("render", check_render_runtime, label="Render Runtime")
    reg.register("terminal", check_terminal, label="Terminal Environment")
    reg.register("data_dirs", check_data_dirs, label="Data Directories")
    reg.register("persistence", check_persistence, label="Persistence")
    reg.register("config", check_config, label="User Configuration")
    reg.register("plugins", check_plugins, label="Plugins")
    reg.register("lsp", check_lsp, label="Language Server Protocol")
    return reg
