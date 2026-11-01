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
