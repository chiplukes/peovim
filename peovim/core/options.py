"""
core.options — Typed options system with global/window/buffer scopes.

Scope chain (first-wins): buffer → window → global → default.
Supports :set command integration, option_changed events, and plugin-defined options.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

_log = logging.getLogger(__name__)

Scope = Literal["global", "window", "buffer"]


@dataclass
class OptionDef:
    name: str
    type: type  # int, bool, or str
    default: Any
    scope: tuple[str, ...]
    validator: Callable[[Any], bool] | None = None
    doc: str = ""


# ---------------------------------------------------------------------------
# Built-in option definitions (~40)
# ---------------------------------------------------------------------------

_OPTION_DEFS: list[OptionDef] = [
    # --- display ---
    OptionDef("number", bool, False, ("global", "window")),
    OptionDef("relativenumber", bool, False, ("global", "window")),
    OptionDef("wrap", bool, False, ("global", "window")),
    OptionDef("cursorline", bool, False, ("global", "window")),
    OptionDef("cursorcolumn", bool, False, ("global", "window")),
    OptionDef("colorcolumn", str, "", ("global", "window")),
    OptionDef("signcolumn", str, "yes", ("global", "window"), lambda v: v in ("auto", "yes", "no", "number")),
    OptionDef("laststatus", int, 2, ("global",), lambda v: v in (0, 1, 2, 3)),
    OptionDef("showtabline", int, 1, ("global",), lambda v: v in (0, 1, 2)),
    OptionDef("cmdheight", int, 1, ("global",), lambda v: v >= 0),
    OptionDef("showmode", bool, True, ("global",)),
    OptionDef("showcmd", bool, True, ("global",)),
    OptionDef("ruler", bool, False, ("global",)),
    OptionDef("statusline", str, "", ("global", "window")),
    OptionDef("cursorblink", bool, False, ("global", "window")),
    OptionDef("insertcursor", str, "block", ("global", "window"), lambda v: v in ("block", "bar", "line")),
    OptionDef("scrollbar", bool, False, ("global", "window")),
    # --- indentation ---
    OptionDef("tabstop", int, 4, ("global", "window", "buffer"), lambda v: v >= 1),
    OptionDef("shiftwidth", int, 4, ("global", "window", "buffer"), lambda v: v >= 0),
    OptionDef("expandtab", bool, True, ("global", "window", "buffer")),
    OptionDef("autoindent", bool, False, ("global", "window", "buffer")),
    OptionDef("smartindent", bool, False, ("global", "window", "buffer")),
    OptionDef("textwidth", int, 0, ("global", "window", "buffer"), lambda v: v >= 0),
    # --- scrolling ---
    OptionDef("scrolloff", int, 0, ("global", "window"), lambda v: v >= 0),
    # --- search ---
    OptionDef("hlsearch", bool, True, ("global",)),
    OptionDef("ignorecase", bool, False, ("global",)),
    OptionDef("smartcase", bool, False, ("global",)),
    OptionDef("incsearch", bool, False, ("global",)),
    OptionDef("wrapscan", bool, True, ("global",)),
    # --- leader keys ---
    OptionDef("leader", str, "\\", ("global",)),
    OptionDef("localleader", str, "\\", ("global",)),
    # --- file / buffer ---
    OptionDef("filetype", str, "", ("buffer",)),
    OptionDef("syntax", str, "", ("buffer",)),
    OptionDef("fileencoding", str, "utf-8", ("buffer",)),
    OptionDef("fileformat", str, "unix", ("buffer",), lambda v: v in ("unix", "dos", "mac")),
    OptionDef("readonly", bool, False, ("buffer", "window")),
    OptionDef("modifiable", bool, True, ("buffer", "window")),
    # --- misc ---
    OptionDef("spell", bool, False, ("global", "window")),
    OptionDef("clipboard", str, "", ("global",)),
    OptionDef("mouse", str, "", ("global",)),
    OptionDef("backspace", str, "indent,eol,start", ("global",)),
    OptionDef("parallelrender", str, "auto", ("global",), lambda v: v in ("auto", "on", "off")),
    OptionDef("parallelrenderworkers", int, 0, ("global",), lambda v: v >= 0),
    # --- indent guides ---
    OptionDef("indentguides", str, "none", ("global", "window"), lambda v: v in ("none", "yes", "rainbow")),
    # --- status bar ---
    OptionDef("statusline_filepath", str, "filename", ("global",), lambda v: v in ("filename", "full", "relative")),
    # --- formatting ---
    OptionDef("trim_trailing_whitespace", bool, False, ("global", "window", "buffer")),
    # --- autosave / recovery ---
    OptionDef("autosave_interval", int, 30, ("global",), lambda v: v >= 0),
]

_DEFS_BY_NAME: dict[str, OptionDef] = {d.name: d for d in _OPTION_DEFS}


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class OptionError(ValueError):
    """Raised when setting an unknown or invalid option."""


# ---------------------------------------------------------------------------
# OptionsStore
# ---------------------------------------------------------------------------


class OptionsStore:
    """
    Typed, scoped options store.

    get(name, win_id, buf_id) follows the scope chain:
        buffer override → window override → global value → option default
    """

    def __init__(self, event_bus=None) -> None:
        self._event_bus = event_bus
        self._global: dict[str, Any] = {}
        self._window: dict[int, dict[str, Any]] = {}  # win_id → {name: val}
        self._buffer: dict[int, dict[str, Any]] = {}  # buf_id → {name: val}
        self._untyped: dict[str, Any] = {}  # plugin/unknown options set before define()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, name: str, win_id: int | None = None, buf_id: int | None = None) -> Any:
        """Return the effective value for an option, following scope chain."""
        defn = _DEFS_BY_NAME.get(name)
        if defn is None:
            return self._untyped.get(name)
        if buf_id is not None and "buffer" in defn.scope:
            bdict = self._buffer.get(buf_id)
            if bdict and name in bdict:
                return bdict[name]
        if win_id is not None and "window" in defn.scope:
            wdict = self._window.get(win_id)
            if wdict and name in wdict:
                return wdict[name]
        return self._global.get(name, defn.default)

    def global_as_dict(self) -> dict:
        """Return all globally-set option values as a plain dict (for snapshot merging)."""
        return dict(self._global)

    def is_known(self, name: str) -> bool:
        return name in _DEFS_BY_NAME

    def default(self, name: str) -> Any:
        defn = _DEFS_BY_NAME.get(name)
        return defn.default if defn else None

    def definition(self, name: str) -> OptionDef | None:
        return _DEFS_BY_NAME.get(name)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def set(
        self, name: str, value: Any, scope: Scope = "global", win_id: int | None = None, buf_id: int | None = None
    ) -> None:
        """Set an option value. Validates type and value."""
        defn = _DEFS_BY_NAME.get(name)
        if defn is None:
            # Unknown option — store untyped (allows plugin options set before define())
            _log.debug("set %s=%r  scope=%s  (untyped)", name, value, scope)
            self._untyped[name] = value
            if self._event_bus is not None:
                self._event_bus.emit("option_changed", name=name, value=value, scope=scope)
            return
        if scope not in defn.scope:
            raise OptionError(f"Option '{name}' does not support {scope} scope")
        value = self._coerce(defn, value)
        if defn.validator and not defn.validator(value):
            raise OptionError(f"Invalid value for '{name}': {value!r}")
        if scope == "buffer" and buf_id is not None:
            self._buffer.setdefault(buf_id, {})[name] = value
            _log.debug("set %s=%r  scope=buffer  buf_id=%s", name, value, buf_id)
        elif scope == "window" and win_id is not None:
            self._window.setdefault(win_id, {})[name] = value
            _log.debug("set %s=%r  scope=window  win_id=%s", name, value, win_id)
        else:
            self._global[name] = value
            _log.debug("set %s=%r  scope=global", name, value)
        if self._event_bus is not None:
            self._event_bus.emit("option_changed", name=name, value=value, scope=scope)

    def set_global(self, name: str, value: Any) -> None:
        self.set(name, value, scope="global")

    def set_window(self, win_id: int, name: str, value: Any) -> None:
        self.set(name, value, scope="window", win_id=win_id)

    def set_buffer(self, buf_id: int, name: str, value: Any) -> None:
        self.set(name, value, scope="buffer", buf_id=buf_id)

    # ------------------------------------------------------------------
    # Plugin-defined options
    # ------------------------------------------------------------------

    def define(
        self,
        name: str,
        type_: type,
        default: Any,
        scope: tuple[str, ...] = ("global",),
        validator: Callable[[Any], bool] | None = None,
        doc: str = "",
    ) -> None:
        """Register a new option (for plugins). No-op if name already known."""
        if name in _DEFS_BY_NAME:
            return
        defn = OptionDef(name, type_, default, scope, validator, doc)
        _OPTION_DEFS.append(defn)
        _DEFS_BY_NAME[name] = defn
        # Promote any pre-set untyped value into the properly typed global store
        if name in self._untyped:
            try:
                self.set_global(name, self._untyped.pop(name))
            except OptionError:
                self._untyped.pop(name, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce(defn: OptionDef, value: Any) -> Any:
        if defn.type is bool:
            if isinstance(value, str):
                return value.lower() not in ("0", "false", "no", "off", "")
            return bool(value)
        if defn.type is int:
            return int(value)
        if defn.type is str:
            return str(value)
        return value
