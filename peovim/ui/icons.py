"""Small, optional icon set; terminal identity is a hint rather than a font probe."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from peovim.core.filetype import detect_filetype

_FILE_ICONS = {
    "python": "\ue606",
    "javascript": "\ue60c",
    "typescript": "\ue628",
    "rust": "\ue7a8",
    "go": "\ue627",
    "markdown": "\ue609",
}


def nerd_icons_enabled(mode: str = "auto", environ: Mapping[str, str] | None = None) -> bool:
    if mode == "on":
        return True
    if mode != "auto":
        return False
    env = os.environ if environ is None else environ
    return env.get("TERM", "") != "dumb" and env.get("TERM_PROGRAM", "").casefold() == "wezterm"


def path_icon(path: str | Path, *, directory: bool = False, mode: str = "auto") -> str:
    if not nerd_icons_enabled(mode):
        return ""
    if directory:
        return "\uf07b"
    return _FILE_ICONS.get(detect_filetype(str(path)), "\uf016")


def path_label(label: str, path: str | Path | None, *, mode: str = "auto") -> str:
    icon = path_icon(path, mode=mode) if path is not None else ""
    return f"{icon} {label}" if icon else label


_SYMBOL_ICONS = {
    "text": "\uf15c",
    "function": "\uf121",
    "method": "\uf121",
    "constructor": "\uf0ad",
    "class": "\uf1b2",
    "struct": "\uf1b2",
    "interface": "\uf0e8",
    "module": "\uf1b3",
    "namespace": "\uf07b",
    "property": "\uf013",
    "field": "\uf0ce",
    "variable": "\uf040",
    "const": "\uf023",
    "enum": "\uf03a",
    "keyword": "\uf084",
    "snippet": "\uf0c4",
    "reference": "\uf0c1",
    "file": "\uf016",
    "folder": "\uf07b",
    "event": "\uf0e7",
    "type": "\uf1b2",
    "operator": "\uf067",
}

_COMPLETION_KINDS = {
    1: "text",
    2: "method",
    3: "function",
    4: "constructor",
    5: "field",
    6: "variable",
    7: "class",
    8: "interface",
    9: "module",
    10: "property",
    13: "enum",
    14: "keyword",
    15: "snippet",
    17: "reference",
    18: "file",
    19: "folder",
    20: "enum",
    21: "const",
    22: "struct",
    23: "event",
    24: "operator",
    25: "type",
}


def symbol_icon(kind: str, *, mode: str = "auto") -> str:
    return _SYMBOL_ICONS.get(kind.lower(), "") if nerd_icons_enabled(mode) else ""


def completion_icon(kind: int, *, mode: str = "auto") -> str:
    return symbol_icon(_COMPLETION_KINDS.get(kind, ""), mode=mode)


def diagnostic_icon(severity: str, *, mode: str = "auto") -> str:
    if not nerd_icons_enabled(mode):
        return ""
    return {"E": "\uf057", "W": "\uf071", "I": "\uf05a", "H": "\uf0eb"}.get(severity.upper(), "")
