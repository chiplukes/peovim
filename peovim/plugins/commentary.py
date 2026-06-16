"""
gcc / gc{motion} comment toggling.

gcc  — toggle comment on the current line
gcj  — toggle comments on current line + next line
gck  — toggle comments on current line + previous line
gc   — in visual mode, toggle comments on all selected lines

Comment syntax is determined by the buffer's filetype. Falls back to # for
unknown filetypes.

Implemented against the public peovim.api — no internal imports.
See notes/plugins.md for plugin development.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

# filetype → (line_comment_string, add_space)
_COMMENT_STRINGS: dict[str, str] = {
    "python": "#",
    "ruby": "#",
    "perl": "#",
    "bash": "#",
    "sh": "#",
    "zsh": "#",
    "fish": "#",
    "r": "#",
    "yaml": "#",
    "toml": "#",
    "dockerfile": "#",
    "makefile": "#",
    "cmake": "#",
    "ini": ";",
    "lua": "--",
    "haskell": "--",
    "sql": "--",
    "ada": "--",
    "javascript": "//",
    "typescript": "//",
    "jsx": "//",
    "tsx": "//",
    "c": "//",
    "cpp": "//",
    "cs": "//",
    "java": "//",
    "go": "//",
    "rust": "//",
    "swift": "//",
    "kotlin": "//",
    "scala": "//",
    "dart": "//",
    "php": "//",
    "verilog": "//",
    "systemverilog": "//",
}
_DEFAULT_COMMENT = "#"


def setup(api: EditorAPI) -> None:
    """Register gcc and gc* normal-mode bindings."""
    api.keymap.define_plug("CommentaryLine", lambda ctx: _gcc(ctx, api), desc="Commentary: toggle line comment")
    api.keymap.define_plug(
        "CommentaryDown", lambda ctx: _gc_range(ctx, api, 0, 1), desc="Commentary: comment current+next"
    )
    api.keymap.define_plug(
        "CommentaryUp", lambda ctx: _gc_range(ctx, api, -1, 0), desc="Commentary: comment prev+current"
    )
    api.keymap.define_plug(
        "CommentaryVisual", lambda ctx: _gc_visual(ctx, api), desc="Commentary: toggle comments on selection"
    )
    api.keymap.nmap("gcc", "<Plug>CommentaryLine", desc="Commentary: toggle line comment")
    api.keymap.nmap("gcj", "<Plug>CommentaryDown", desc="Commentary: comment current+next")
    api.keymap.nmap("gck", "<Plug>CommentaryUp", desc="Commentary: comment prev+current")
    api.keymap.vmap("gc", "<Plug>CommentaryVisual", desc="Commentary: toggle comments on selection")


def teardown() -> None:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _comment_str(buf: Any) -> str:
    ft = getattr(buf, "filetype", "") or ""
    return _COMMENT_STRINGS.get(ft.lower(), _DEFAULT_COMMENT)


def _add_comment(buf: Any, line: int, marker: str | None = None) -> None:
    """Add a comment marker to a line (no-op if already commented)."""
    if marker is None:
        marker = _comment_str(buf)
    text = buf.get_line(line)
    stripped = text.lstrip()
    if stripped.startswith(marker):
        return  # already commented
    indent = text[: len(text) - len(stripped)]
    new_text = indent + marker + " " + stripped
    buf.replace(line, 0, line, len(text), new_text)


def _remove_comment(buf: Any, line: int, marker: str | None = None) -> None:
    """Remove a comment marker from a line (no-op if not commented)."""
    if marker is None:
        marker = _comment_str(buf)
    text = buf.get_line(line)
    stripped = text.lstrip()
    indent = text[: len(text) - len(stripped)]
    if stripped.startswith(marker + " "):
        new_text = indent + stripped[len(marker) + 1 :]
        buf.replace(line, 0, line, len(text), new_text)
    elif stripped.startswith(marker):
        new_text = indent + stripped[len(marker) :]
        buf.replace(line, 0, line, len(text), new_text)


def toggle_line_comment(buf: Any, line: int) -> None:
    """Toggle the comment on a single line. Public for testing."""
    text = buf.get_line(line)
    marker = _comment_str(buf)
    stripped = text.lstrip()
    if stripped.startswith(marker):
        _remove_comment(buf, line, marker)
    else:
        _add_comment(buf, line, marker)


def _gcc(ctx: Any, api: Any) -> None:
    try:
        buf = api.active_buffer()
        line, _ = ctx.cursor
        toggle_line_comment(buf, line)
    except Exception:
        pass


def _gc_range(ctx: Any, api: Any, start_offset: int, end_offset: int) -> None:
    try:
        buf = api.active_buffer()
        line, _ = ctx.cursor
        start = max(0, line + start_offset)
        end = min(buf.line_count() - 1, line + end_offset)
        with buf.batch():
            for ln in range(start, end + 1):
                toggle_line_comment(buf, ln)
    except Exception:
        pass


def _gc_visual(ctx: Any, api: Any) -> None:
    """Toggle comments on the visual selection lines.

    If ANY line in the selection is uncommented, comment ALL lines.
    Only uncomment all lines when every line is already commented.
    """
    try:
        buf = api.active_buffer()
        if ctx.visual_range and not ctx.is_repeat:
            start_line, end_line = ctx.visual_range
        else:
            start_line = ctx.cursor[0]
            end_line = start_line + ctx.visual_line_count - 1
        end_line = min(end_line, buf.line_count() - 1)
        marker = _comment_str(buf)
        lines = range(start_line, end_line + 1)
        all_commented = all(buf.get_line(ln).lstrip().startswith(marker) for ln in lines)
        with buf.batch():
            for ln in lines:
                if all_commented:
                    _remove_comment(buf, ln, marker)
                else:
                    _add_comment(buf, ln, marker)
    except Exception:
        pass
