"""
syntax.languages — Language registry: filetype → tree-sitter grammar + highlight query

Maps filetype strings (e.g. 'python', 'rust') to installed tree-sitter grammar
packages. Plugins can register additional languages via register_language().

Grammar packages are optional extras (uv sync --extra grammars). If a package is
not installed, get_language_info() returns None — files render in default colours.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LanguageInfo:  # cm:8b6f1d
    """Describes a registered language grammar."""

    filetype: str
    module_name: str  # e.g. 'tree_sitter_python'
    language_attr: str = "language"  # callable on module → capsule
    highlights_attr: str = "HIGHLIGHTS_QUERY"  # str attribute on module
    # Cached objects — populated lazily on first use
    _language_obj: Any = field(default=None, repr=False)
    _query_str: str | None = field(default=None, repr=False)

    def get_language(self) -> Any | None:
        """Return the tree-sitter Language object, or None if not installed."""
        if self._language_obj is not None:
            return self._language_obj
        try:
            from tree_sitter import Language

            mod = importlib.import_module(self.module_name)
            lang_fn = getattr(mod, self.language_attr)
            self._language_obj = Language(lang_fn())
            return self._language_obj
        except (ImportError, AttributeError, Exception):
            return None

    def get_highlights_query(self) -> str | None:
        """Return the highlights .scm query string, or None if unavailable."""
        if self._query_str is not None:
            return self._query_str

        query_path = Path(__file__).with_name("queries") / f"{self.filetype}.scm"
        if query_path.exists():
            self._query_str = query_path.read_text(encoding="utf-8")
            return self._query_str

        try:
            mod = importlib.import_module(self.module_name)
            q = getattr(mod, self.highlights_attr, None)
            if q:
                self._query_str = q
                return self._query_str

            mod_file = getattr(mod, "__file__", None)
            if mod_file is not None:
                mod_dir = Path(mod_file).resolve().parent
                for bundled_query in (
                    mod_dir / "queries" / self.filetype / "highlights.scm",
                    mod_dir / "queries" / "highlights.scm",
                ):
                    if bundled_query.exists():
                        self._query_str = bundled_query.read_text(encoding="utf-8")
                        return self._query_str
        except ImportError:
            pass
        return None


# ---------------------------------------------------------------------------
# Built-in registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, LanguageInfo] = {}


def _register_builtin(filetype: str, module_name: str, **kwargs) -> None:
    _REGISTRY[filetype] = LanguageInfo(filetype=filetype, module_name=module_name, **kwargs)


# Python
_register_builtin("python", "tree_sitter_python")

# JavaScript / TypeScript
_register_builtin("javascript", "tree_sitter_javascript")
_register_builtin("jsx", "tree_sitter_javascript")
_register_builtin("typescript", "tree_sitter_typescript", language_attr="language_typescript")
_register_builtin("tsx", "tree_sitter_typescript", language_attr="language_tsx")

# Systems languages
_register_builtin("rust", "tree_sitter_rust")
_register_builtin("c", "tree_sitter_c")
_register_builtin("cpp", "tree_sitter_cpp")
_register_builtin("go", "tree_sitter_go")
_register_builtin("verilog", "tree_sitter_verilog")

# Scripting
_register_builtin("lua", "tree_sitter_lua")
_register_builtin("bash", "tree_sitter_bash")
# FPGA constraints (Tcl-like; reuses bash grammar with a custom highlights query)
_register_builtin("xdc", "tree_sitter_bash")

# Data / Config
_register_builtin("json", "tree_sitter_json")
_register_builtin("yaml", "tree_sitter_yaml")
_register_builtin("toml", "tree_sitter_toml")
_register_builtin("markdown", "tree_sitter_markdown")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_language_info(filetype: str) -> LanguageInfo | None:
    """Return LanguageInfo for the given filetype, or None if unknown."""
    return _REGISTRY.get(filetype)


def register_language(filetype: str, info: LanguageInfo) -> None:
    """Register or override a language. Called by plugins or user config."""
    _REGISTRY[filetype] = info


def supported_filetypes() -> list[str]:
    """Return the list of registered filetype names."""
    return sorted(_REGISTRY.keys())
