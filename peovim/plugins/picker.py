"""
Fuzzy picker for files, buffers, and live grep.

Implemented against the public peovim.api — no internal imports.
See notes/plugins.md for plugin development.

Keybindings (normal mode):
  <leader>ff — find files under project root
  <leader>fb — list open buffers
  <leader>fg — live grep

Ex commands:
  :Find  — same as <leader>ff
  :Grep [query] — open grep picker, optionally pre-seeded with query
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from peovim.core.filetype import detect_filetype
from peovim.syntax.themes import get_theme
from peovim.ui.markdown import RichLine, render_rich_code_preview

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

_PREVIEW_LINES = 60  # max lines to show in file preview


@dataclass(frozen=True)
class _PathItem:
    label: str
    path: Path

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class _LocationItem:
    label: str
    path: Path
    line: int
    col: int = 0

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class _BufferItem:
    label: str
    path: Path | None
    lines: tuple[str, ...]

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class _LineItem:
    label: str
    line: int

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class _CommandItem:
    label: str
    command: str

    def __str__(self) -> str:
        return self.label


def setup(api: EditorAPI) -> None:  # cm:c2f9a1
    """Register the picker plugin with the editor."""
    api.keymap.ngroup("<leader>s", "Search")

    api.keymap.define_plug("PickerFindFiles", lambda: _find_files(api), desc="Picker: find files")
    api.keymap.define_plug("PickerRecentFiles", lambda: _find_recent_files(api), desc="Picker: recent files")
    api.keymap.define_plug("PickerFindBuffers", lambda: _find_buffers(api), desc="Picker: list buffers")
    api.keymap.define_plug("PickerLiveGrep", lambda: _live_grep(api), desc="Picker: live grep")
    api.keymap.define_plug("PickerWordGrep", lambda: _grep_word_under_cursor(api), desc="Picker: grep word")
    api.keymap.define_plug("PickerBufferLines", lambda: _search_buffer_lines(api), desc="Picker: search lines")
    api.keymap.define_plug("PickerDiagnostics", lambda: _find_diagnostics(api), desc="Picker: diagnostics")
    api.keymap.define_plug("PickerCommands", lambda: _find_commands(api), desc="Picker: commands")

    api.keymap.nmap("<leader>ff", "<Plug>PickerFindFiles", desc="Picker: find files")
    api.keymap.nmap("<leader>fb", "<Plug>PickerFindBuffers", desc="Picker: list buffers")
    api.keymap.nmap("<leader>fg", "<Plug>PickerLiveGrep", desc="Picker: live grep")
    api.keymap.nmap("<leader>sf", "<Plug>PickerFindFiles", desc="Search: find files")
    api.keymap.nmap("<leader>sr", "<Plug>PickerRecentFiles", desc="Search: recent files")
    api.keymap.nmap("<leader>sg", "<Plug>PickerLiveGrep", desc="Search: live grep")
    api.keymap.nmap("<leader>sw", "<Plug>PickerWordGrep", desc="Search: grep word")
    api.keymap.nmap("<leader>s/", "<Plug>PickerBufferLines", desc="Search: buffer lines")
    api.keymap.nmap("<leader>sb", "<Plug>PickerFindBuffers", desc="Search: buffers")
    api.keymap.nmap("<leader>sd", "<Plug>PickerDiagnostics", desc="Search: diagnostics")
    api.keymap.nmap("<leader>sp", "<Plug>PickerCommands", desc="Search: commands")

    api.commands.register("find", lambda cmd, ctx: _find_files(api), min_abbrev=2)
    api.commands.register(
        "grep",
        lambda cmd, ctx: _grep_with_args(api, cmd.args if hasattr(cmd, "args") else ""),
        min_abbrev=2,
    )


def teardown() -> None:
    pass


# ---------------------------------------------------------------------------
# Find files
# ---------------------------------------------------------------------------


def _find_files(api: Any) -> None:
    """Open a fuzzy picker over all files in the project root."""
    root = _get_root(api)
    try:
        paths = api.find_files("**/*", root=root)
    except Exception:
        paths = []

    items = [_path_item(path, root) for path in paths]

    def _on_confirm(item: _PathItem) -> None:
        with contextlib.suppress(Exception):
            api.open_buffer(item.path)

    api.ui.open_picker(
        "Find Files",
        items,
        on_confirm=_on_confirm,
        preview=lambda item: _preview_file(item.path, api=api),
    )


def _find_recent_files(api: Any) -> None:
    """Open a picker over recent files stored in shada."""
    root = _get_root(api)
    items = [_path_item(path, root) for path in api.recent_files()]

    api.ui.open_picker(
        "Recent Files",
        items,
        on_confirm=lambda item: api.open_buffer(item.path),
        preview=lambda item: _preview_file(item.path, api=api),
    )


# ---------------------------------------------------------------------------
# Buffer list
# ---------------------------------------------------------------------------


def _find_buffers(api: Any) -> None:
    """Open a picker listing all open buffers."""
    bufs = api.list_buffers()
    items = [
        _BufferItem(
            str(buffer.path) if buffer.path else f"<buffer {buffer.buf_id}>",
            buffer.path,
            tuple(_buffer_preview_lines(buffer)),
        )
        for buffer in bufs
    ]

    def _on_confirm(item: _BufferItem) -> None:
        if item.path:
            api.open_buffer(item.path)

    api.ui.open_picker(
        "Buffers",
        items,
        on_confirm=_on_confirm,
        preview=lambda item: _preview_file(item.path, api=api) if item.path else list(item.lines),
    )


# ---------------------------------------------------------------------------
# Live grep
# ---------------------------------------------------------------------------


def _live_grep(api: Any) -> None:
    _grep_with_args(api, "")


def _grep_with_args(api: Any, query: str, *, label: str | None = None) -> None:
    """Open grep picker, optionally pre-seeded with a query."""
    root = _get_root(api)

    def _source(q: str) -> list[_LocationItem]:
        if not q:
            return []
        try:
            hits = api.grep(q, root=root)
        except Exception:
            return []
        items: list[_LocationItem] = []
        for path, line_num, text in hits:
            display_path = _display_path(path, root)
            items.append(_LocationItem(f"{display_path}:{line_num + 1}: {text}", path, line_num, 0))
        return items

    if query:
        source = _source(query)
        if not source:
            _set_status(api, f"No matches for '{label or query}'")
            return
    else:
        source = _source

    api.ui.open_picker(
        f"Grep: {label}" if label else "Grep",
        source,
        on_confirm=lambda item: api.open_buffer(item.path, item.line, item.col),
        preview=lambda item: _preview_location(item.path, item.line, api=api),
        debounce_ms=300,
    )


def _grep_word_under_cursor(api: Any) -> None:
    """Grep for the word under the cursor."""
    word = _word_under_cursor(api)
    if not word:
        _set_status(api, "No word under cursor")
        return
    _grep_with_args(api, rf"\b{re.escape(word)}\b", label=word)


def _search_buffer_lines(api: Any) -> None:
    """Open a picker over the current buffer lines."""
    buf = api.active_buffer()
    items = [
        _LineItem(f"{line_num + 1:>4}: {buf.get_line(line_num)}", line_num) for line_num in range(buf.line_count())
    ]

    def _on_confirm(item: _LineItem) -> None:
        win = api.active_window()
        win.set_cursor(item.line, 0)

    api.ui.open_picker("Buffer Lines", items, on_confirm=_on_confirm)


def _find_diagnostics(api: Any) -> None:
    """Open a picker listing current diagnostics across open buffers."""
    root = _get_root(api)
    items = []
    for diagnostic in api.list_diagnostics():
        path = Path(diagnostic["path"])
        display_path = _display_path(path, root)
        severity = diagnostic.get("severity") or "?"
        message = diagnostic.get("message") or ""
        items.append(
            _LocationItem(
                f"{severity} {display_path}:{diagnostic['line'] + 1}: {message}",
                path,
                int(diagnostic["line"]),
                int(diagnostic.get("col", 0)),
            )
        )

    api.ui.open_picker(
        "Diagnostics",
        items,
        on_confirm=lambda item: api.open_buffer(item.path, item.line, item.col),
        preview=lambda item: _preview_location(item.path, item.line, api=api),
    )


def _find_commands(api: Any) -> None:
    """Open a picker listing registered ex commands."""
    items = [_CommandItem(f":{name}", name) for name in api.commands.list_commands()]
    api.ui.open_picker(
        "Commands (executes on Enter)", items, on_confirm=lambda item: api.commands.execute(item.command)
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_root(api: Any):
    """Return the project root Path, or None."""
    try:
        return api.find_root()
    except Exception:
        return None


def _display_path(path: Path, root: Path | None) -> str:
    """Display path relative to root when possible."""
    try:
        return str(path.relative_to(root)) if root else str(path)
    except Exception:
        return str(path)


def _path_item(path: Path, root: Path | None) -> _PathItem:
    return _PathItem(_display_path(path, root), path)


def _word_under_cursor(api: Any) -> str:
    """Return the current word under the cursor."""
    win = api.active_window()
    buf = api.active_buffer()
    line_num, col = win.cursor
    if line_num < 0 or line_num >= buf.line_count():
        return ""

    text = buf.get_line(line_num)
    if not text:
        return ""
    col = max(0, min(col, len(text) - 1))

    def _is_word_char(ch: str) -> bool:
        return ch.isalnum() or ch == "_"

    if not _is_word_char(text[col]):
        if col > 0 and _is_word_char(text[col - 1]):
            col -= 1
        else:
            return ""

    start = col
    end = col + 1
    while start > 0 and _is_word_char(text[start - 1]):
        start -= 1
    while end < len(text) and _is_word_char(text[end]):
        end += 1
    return text[start:end]


def _buffer_preview_lines(buffer: Any) -> list[str]:
    """Return a bounded preview for a buffer item."""
    try:
        line_count = int(buffer.line_count())
    except Exception:
        line_count = _PREVIEW_LINES
    try:
        return list(buffer.get_lines(0, min(line_count, _PREVIEW_LINES)))
    except Exception:
        return []


def _set_status(api: Any, message: str) -> None:
    """Surface a short status message to the user."""
    with contextlib.suppress(Exception):
        api.ui.notify(message)
    editor_state = getattr(api, "_editor_state", None)
    if editor_state is not None:
        editor_state.message = message


def _preview_location(path: Any, line: int, *, api: Any | None = None) -> list[RichLine]:
    """Read a contextual preview centered on a file location."""
    return _preview_file(path, center_line=line, api=api)


def _preview_file(path: Any, *, center_line: int | None = None, api: Any | None = None) -> list[RichLine]:
    """Read a bounded file preview, optionally centered on a line."""
    import pathlib

    try:
        p = pathlib.Path(path) if path else None
        if p is None or not p.is_file():
            return []
        lines: list[str] = []
        with open(p, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if center_line is None and i >= _PREVIEW_LINES:
                    break
                lines.append(line.rstrip())
        theme = _preview_theme(api)
        lang = detect_filetype(str(p))
        if center_line is None:
            return render_rich_code_preview(lines, lang=lang, theme=theme)
        return _format_centered_preview(lines, center_line, lang=lang, theme=theme)
    except Exception:
        return []


def _format_centered_preview(
    lines: list[str], center_line: int, *, lang: str = "", theme: Any = None
) -> list[RichLine]:
    """Format a preview window around the selected line."""
    if not lines:
        return []
    center = max(0, min(center_line, len(lines) - 1))
    half = _PREVIEW_LINES // 2
    start = max(0, center - half)
    end = min(len(lines), start + _PREVIEW_LINES)
    start = max(0, end - _PREVIEW_LINES)
    return render_rich_code_preview(
        lines[start:end],
        lang=lang,
        theme=theme,
        start_line=start + 1,
        highlight_line=center + 1,
        show_line_numbers=True,
    )


def _preview_theme(api: Any | None = None) -> Any:
    theme_name = "catppuccin"
    editor_state = getattr(api, "_editor_state", None) if api is not None else None
    if editor_state is not None:
        theme_name = getattr(editor_state, "active_theme", theme_name) or theme_name
    return get_theme(theme_name) or get_theme("catppuccin")
