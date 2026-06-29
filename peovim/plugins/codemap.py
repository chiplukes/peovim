"""
Codemap — repo-committed navigation waypoints backed by in-code anchor comments.

Workflow
--------
1. Press <leader>Mi at any line to insert a ``// cm:XXXXXX`` anchor comment
   (6 random hex chars).  The comment style adapts to the file type.
2. Edit ``.codemap.md`` at the project root to reference the anchor::

       ## Clock Domain Crossing
       - [SDM Step Register](cm://a1b2c3) — sdmdata_step always-block, clk100

3. Press <leader>Mt to browse the Codemap sidebar panel, or <leader>Mm to
   open the fuzzy picker.

The ``.codemap.md`` file is checked into the repo.  Because anchors live
*inside the code* as comments, they move with the code automatically through
edits, refactors, and merges — no plugin-side position tracking is needed.

.codemap.md format
------------------
::

    # My Project Codemap

    ## Clock Domain Crossing
    - [SDM Step Register](cm://a1b2c3) — sdmdata_step always-block, clk100
    - [Async Vector](cm://d4e5f6)

    ## Initialization
    - [Reset Logic](cm://g7h8i9) — power-on sequencer

Sections are ``## Heading`` lines.  Entries are list items with a markdown
link whose URL is ``cm://XXXXXX`` (six lowercase hex chars).  An optional
description follows a ``—`` or ``-`` separator on the same line.

Anchor comments in code
-----------------------
Any file in the project may contain a comment of the form ``cm:XXXXXX`` for
the scanner to discover.  The plugin inserts the appropriate style:

* ``// cm:a1b2c3``  (Verilog, C/C++, JavaScript, Go, Rust …)
* ``# cm:a1b2c3``   (Python, Ruby, shell …)
* ``-- cm:a1b2c3``  (Lua, SQL, Haskell …)

Keybindings (normal mode)
-------------------------
  <leader>Mm  Open codemap picker
  <leader>Mt  Toggle codemap sidebar panel
  <leader>Mi  Insert anchor comment at cursor line
  <leader>Mo  Open .codemap.md file (creates a starter if absent)
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PANEL_NAME = "codemap"
_MAP_FILENAME = ".codemap.md"

# Matches ``cm:abcdef`` anywhere in a line (anchor comment body)
_ANCHOR_PATTERN = re.compile(r"\bcm:([0-9a-f]{6})\b")

# Matches a .codemap.md list entry:  - [Label](cm://abcdef) [— description]
_ENTRY_PATTERN = re.compile(
    r"^\s*-\s+\[([^\]]+)\]\(cm://([0-9a-f]{6})\)"
    r"(?:\s+[—\-]\s+(.*))?$"
)

_PREVIEW_CONTEXT = 15  # lines of context above/below anchor in preview
_HIGHLIGHT_BG: tuple = (60, 50, 90)  # background colour for the target line in preview

# Dirs to skip when scanning for anchors
_SKIP_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    "dist",
    "build",
    ".mypy_cache",
}

# File extensions to scan for anchor comments
_CODE_EXTENSIONS = {
    ".v",
    ".sv",
    ".vh",
    ".sva",
    ".svh",
    ".py",
    ".pyw",
    ".c",
    ".h",
    ".cpp",
    ".cc",
    ".cxx",
    ".hpp",
    ".js",
    ".mjs",
    ".ts",
    ".tsx",
    ".rs",
    ".go",
    ".java",
    ".cs",
    ".swift",
    ".kt",
    ".lua",
    ".rb",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".tcl",
    ".yaml",
    ".yml",
    ".toml",
    ".sql",
}

# Comment prefix per filetype (falls back to //)
_COMMENT_BY_FILETYPE: dict[str, str] = {
    "verilog": "//",
    "systemverilog": "//",
    "c": "//",
    "cpp": "//",
    "javascript": "//",
    "typescript": "//",
    "java": "//",
    "csharp": "//",
    "go": "//",
    "rust": "//",
    "scala": "//",
    "swift": "//",
    "kotlin": "//",
    "python": "#",
    "ruby": "#",
    "sh": "#",
    "bash": "#",
    "zsh": "#",
    "fish": "#",
    "r": "#",
    "yaml": "#",
    "toml": "#",
    "lua": "--",
    "haskell": "--",
    "sql": "--",
}
_DEFAULT_COMMENT = "//"

_panel: _CodemapSidebarPanel | None = None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodemapEntry:
    """A single entry parsed from ``.codemap.md``."""

    anchor_id: str
    label: str
    section: str
    description: str = ""


@dataclass(frozen=True)
class AnchorLocation:
    """Resolved codebase location of a ``cm:ID`` anchor comment."""

    file: str
    line: int  # 0-based


# ---------------------------------------------------------------------------
# .codemap.md parser
# ---------------------------------------------------------------------------


def parse_codemap_file(path: Path) -> list[CodemapEntry]:
    """Return all ``CodemapEntry`` objects from *path* in document order."""
    entries: list[CodemapEntry] = []
    current_section = ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            current_section = stripped[3:].strip()
        elif stripped.startswith("# "):
            pass  # top-level title — skip
        else:
            m = _ENTRY_PATTERN.match(raw_line)
            if m:
                label, anchor_id, desc = m.group(1), m.group(2), m.group(3) or ""
                entries.append(
                    CodemapEntry(
                        anchor_id=anchor_id,
                        label=label,
                        section=current_section,
                        description=desc.strip(),
                    )
                )
    return entries


# ---------------------------------------------------------------------------
# Anchor scanner
# ---------------------------------------------------------------------------


def _get_files_to_scan(root: Path) -> list[Path]:
    """Return files to scan for anchors.

    Prefers ``git ls-files`` so only version-controlled files are visited.
    Falls back to os.walk when git is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            files: list[Path] = []
            for line in result.stdout.splitlines():
                p = root / line.strip()
                if p.suffix.lower() in _CODE_EXTENSIONS and p.is_file():
                    files.append(p)
            return files
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Fallback: os.walk with skip-dirs filter
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for filename in filenames:
            if Path(filename).suffix.lower() not in _CODE_EXTENSIONS:
                continue
            files.append(Path(dirpath) / filename)
    return files


def scan_anchors(root: Path) -> dict[str, AnchorLocation]:
    """Return a mapping of ``anchor_id → AnchorLocation`` for *root*.

    Only scans version-controlled files (via git ls-files) when possible.
    The first occurrence of each ID wins.
    """
    index: dict[str, AnchorLocation] = {}
    for filepath in _get_files_to_scan(root):
        try:
            with open(filepath, encoding="utf-8", errors="replace") as fh:
                for lineno, line_text in enumerate(fh):
                    for m in _ANCHOR_PATTERN.finditer(line_text):
                        anchor_id = m.group(1)
                        if anchor_id not in index:
                            index[anchor_id] = AnchorLocation(file=str(filepath), line=lineno)
        except OSError:
            continue
    return index


# ---------------------------------------------------------------------------
# Anchor insertion
# ---------------------------------------------------------------------------


def generate_anchor_id() -> str:
    """Return a random 6-char hex anchor ID."""
    return "".join(random.choices("0123456789abcdef", k=6))


def _comment_prefix(api: Any) -> str:
    buf = api.active_buffer()
    if buf is None:
        return _DEFAULT_COMMENT
    ft = buf.filetype or ""
    return _COMMENT_BY_FILETYPE.get(ft, _DEFAULT_COMMENT)


def insert_anchor_at_cursor(api: Any) -> str | None:
    """Append a ``cm:ID`` tag to the cursor line.  Returns the new ID."""
    buf = api.active_buffer()
    win = api.active_window()
    if buf is None or win is None:
        return None
    cursor_line, _col = win.cursor
    anchor_id = generate_anchor_id()
    prefix = _comment_prefix(api)
    current_line = buf.get_line(cursor_line)
    new_line = current_line.rstrip() + f"  {prefix} cm:{anchor_id}"
    # replace() replaces the content of this line in place
    buf.replace(cursor_line, 0, cursor_line, len(current_line), new_line)
    return anchor_id


# ---------------------------------------------------------------------------
# Sidebar panel
# ---------------------------------------------------------------------------


class _CodemapSidebarPanel:
    def __init__(self, api: Any, *, width: int = 36) -> None:
        from peovim.ui.tree_view import TreeView

        self._api = api
        self.width = width
        self._entries: list[CodemapEntry] = []
        self._anchor_index: dict[str, AnchorLocation] = {}
        self._tree = TreeView(
            [],
            title="Codemap",
            on_select=self._on_select,
            on_cursor_move=self._on_cursor_move,
            width=width,
        )
        self._preview_float: Any = None
        self._refresh_gen: int = 0

    # ------------------------------------------------------------------
    # Sidebar protocol
    # ------------------------------------------------------------------

    def render(self, grid: Any) -> None:
        self._tree.focused = getattr(self, "_sidebar_focused", False)
        self._tree.blink_on = getattr(self, "_sidebar_blink_on", True)
        self._tree._width = grid.width
        self._tree.render(grid)

    def feed_key(self, key: str) -> bool:
        if key == "R":
            self.refresh()
            return True
        self._tree.feed_key(key)
        return True

    def on_focus(self) -> None:
        self._tree.focused = True
        self._preview_current()

    def on_blur(self) -> None:
        self._tree.focused = False
        self._close_preview()

    def on_show(self) -> None:
        self._schedule_refresh()

    def on_hide(self) -> None:
        self._close_preview()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        """Kick off an async refresh; any in-flight scan is superseded."""
        self._refresh_gen += 1
        with contextlib.suppress(RuntimeError):
            asyncio.ensure_future(self._async_refresh(self._refresh_gen))

    async def _async_refresh(self, gen: int) -> None:
        root = _project_root(self._api)
        if root is None:
            self._tree.set_nodes(_message_nodes("No project root found"))
            return
        map_path = root / _MAP_FILENAME
        if not map_path.is_file():
            self._tree.set_nodes(_message_nodes(f"{_MAP_FILENAME} not found in project root"))
            return
        self._entries = parse_codemap_file(map_path)
        self._tree.set_nodes(_message_nodes("Scanning…"))
        loop = asyncio.get_event_loop()
        anchor_index = await loop.run_in_executor(None, scan_anchors, root)
        if self._refresh_gen != gen:
            return  # superseded by a newer refresh
        self._anchor_index = anchor_index
        count = len(self._entries)
        self._tree._title = f"Codemap [{count}]" if count else "Codemap"
        nodes = _build_tree_nodes(self._entries, self._anchor_index, root)
        self._tree.set_nodes(nodes or _message_nodes(f"{_MAP_FILENAME} has no entries"))

    # ------------------------------------------------------------------
    # Selection / preview
    # ------------------------------------------------------------------

    def _on_select(self, node: Any) -> None:
        if node.value is None:
            return
        self._close_preview()
        file_path, line = node.value
        self._api.goto_location(Path(file_path), line, 0)
        self._api.ui.blur_sidebar()

    def _on_cursor_move(self, node: Any) -> None:
        if node.value is None:
            self._close_preview()
            return
        file_path, line = node.value
        self._preview_float = _show_preview_float(
            self._api,
            file_path,
            line,
            handle=self._preview_float,
            sidebar_width=self.width,
        )

    def _preview_current(self) -> None:
        visible = self._tree._visible_nodes()
        if not visible:
            return
        idx = max(0, min(self._tree._selected_idx, len(visible) - 1))
        node, _ = visible[idx]
        self._on_cursor_move(node)

    def _close_preview(self) -> None:
        if self._preview_float is not None:
            with contextlib.suppress(Exception):
                if self._preview_float.is_open:
                    self._preview_float.close()
            self._preview_float = None


# ---------------------------------------------------------------------------
# Tree builder
# ---------------------------------------------------------------------------


def _build_tree_nodes(
    entries: list[CodemapEntry],
    anchor_index: dict[str, AnchorLocation],
    root: Path,
) -> list:
    from peovim.ui.tree_view import TreeNode

    # Colors
    _FG_LABEL_OK = (180, 220, 180)  # resolved entry label — soft green
    _FG_LABEL_MISS = (160, 100, 100)  # unresolved label — muted red
    _FG_DESC = (140, 140, 140)  # description after em-dash — dim gray
    _FG_LOC = (90, 100, 110)  # file:line suffix — darker dim

    # Group entries into ordered sections (dict preserves insertion order)
    sections: dict[str, list[CodemapEntry]] = {}
    for entry in entries:
        key = entry.section or "(unsectioned)"
        sections.setdefault(key, []).append(entry)

    roots: list[TreeNode] = []
    for section_name, section_entries in sections.items():
        children: list[TreeNode] = []
        for entry in section_entries:
            loc = anchor_index.get(entry.anchor_id)
            if loc is not None:
                rel = _rel_path(loc.file, root)
                loc_text = f"  {rel}:{loc.line + 1}"
                value: tuple | None = (loc.file, loc.line)
                label_fg = _FG_LABEL_OK
            else:
                loc_text = "  (anchor not found)"
                value = None
                label_fg = _FG_LABEL_MISS
            desc_part = f" \u2014 {entry.description}" if entry.description else ""
            segments: list[tuple[str, Any]] = [
                (entry.label, label_fg),
                (desc_part, _FG_DESC),
                (loc_text, _FG_LOC),
            ]
            children.append(TreeNode(label=entry.label + desc_part + loc_text, value=value, label_segments=segments))

        section_node = TreeNode(
            label=section_name,
            fg=(140, 180, 255),
            children_fn=lambda ch=children: ch,  # type: ignore[misc]
            expanded=True,
        )
        section_node._cached_children = children
        roots.append(section_node)
    return roots


# ---------------------------------------------------------------------------
# Picker
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PickerItem:
    label: str
    entry: CodemapEntry
    loc: AnchorLocation | None

    def __str__(self) -> str:
        return self.label


def _open_picker(api: Any) -> None:
    root = _project_root(api)
    if root is None:
        api.ui.notify("Codemap: no project root found", level="warn")
        return
    map_path = root / _MAP_FILENAME
    if not map_path.is_file():
        api.ui.notify(f"Codemap: {_MAP_FILENAME} not found", level="warn")
        return
    entries = parse_codemap_file(map_path)
    if not entries:
        api.ui.notify("Codemap: no entries in map file", level="info")
        return
    anchor_index = scan_anchors(root)

    items: list[_PickerItem] = []
    for entry in entries:
        loc = anchor_index.get(entry.anchor_id)
        section_prefix = f"[{entry.section}] " if entry.section else ""
        loc_text = f"  ({_rel_path(loc.file, root)}:{loc.line + 1})" if loc else "  (anchor not found)"
        desc_part = f" — {entry.description}" if entry.description else ""
        label = f"{section_prefix}{entry.label}{desc_part}{loc_text}"
        items.append(_PickerItem(label=label, entry=entry, loc=loc))

    def _on_confirm(item: _PickerItem) -> None:
        if item.loc is None:
            api.ui.notify(
                f"Codemap: anchor cm:{item.entry.anchor_id} not found in codebase",
                level="warn",
            )
            return
        api.open_buffer(Path(item.loc.file), item.loc.line, 0)

    def _preview(item: _PickerItem) -> list[str]:
        if item.loc is None:
            return [f"  Anchor cm:{item.entry.anchor_id} not found in project files."]
        return _build_preview_lines(item.loc.file, item.loc.line)

    api.ui.open_picker("Codemap", items, on_confirm=_on_confirm, preview=_preview)


# ---------------------------------------------------------------------------
# Preview helpers
# ---------------------------------------------------------------------------


def _get_active_theme(api: Any) -> Any:
    from peovim.syntax.themes import get_theme

    theme_name = getattr(getattr(api, "_editor_state", None), "active_theme", "catppuccin")
    return get_theme(theme_name) or get_theme("catppuccin")


def _get_syntax_spans(path: str, all_lines: list[str], start_line: int, end_line: int) -> list:
    from peovim.core.filetype import detect_filetype
    from peovim.syntax.engine import HighlightSpan
    from peovim.syntax.languages import get_language_info

    filetype = detect_filetype(path)
    if not filetype:
        return []
    info = get_language_info(filetype)
    if not info:
        return []
    lang = info.get_language()
    if not lang:
        return []
    query_str = info.get_highlights_query()
    if not query_str:
        return []
    try:
        from tree_sitter import Parser, Query, QueryCursor

        parser = Parser(lang)
        tree = parser.parse("\n".join(all_lines).encode("utf-8"))
        q = Query(lang, query_str)
        cursor = QueryCursor(q)
        captures = cursor.captures(tree.root_node)
        spans: list[HighlightSpan] = []
        for name, nodes in captures.items():
            group = name.lstrip("@")
            for node in nodes:
                sp = node.start_point
                ep = node.end_point
                if ep[0] < start_line or sp[0] > end_line:
                    continue
                spans.append(HighlightSpan(sp[0], sp[1], ep[0], ep[1], group))
        spans.sort(key=lambda s: (s.start_line, s.start_col))
        return spans
    except Exception:
        return []


def _style_line(raw_line: str, doc_line: int, spans: list, theme: Any) -> list:
    """Convert syntax spans for a single doc_line into a list of (text, Style) segments."""
    from peovim.core.style import Style

    n = len(raw_line)
    if n == 0:
        return [("", Style())]

    col_fg: list[Any] = [None] * n
    for span in spans:
        if span.end_line < doc_line or span.start_line > doc_line:
            continue
        c0 = span.start_col if span.start_line == doc_line else 0
        c1 = span.end_col if span.end_line == doc_line else n
        c0, c1 = max(0, c0), min(n, c1)
        if c0 >= c1:
            continue
        style = theme.resolve(span.group)
        if style.fg is not None:
            for c in range(c0, c1):
                col_fg[c] = style.fg

    segments: list = []
    seg_start = 0
    cur_fg = col_fg[0]
    for c in range(1, n):
        if col_fg[c] != cur_fg:
            segments.append((raw_line[seg_start:c], Style(fg=cur_fg)))
            seg_start = c
            cur_fg = col_fg[c]
    segments.append((raw_line[seg_start:], Style(fg=cur_fg)))
    return [(text, style) for text, style in segments if text]


def _build_preview_lines(file_path: str, target_line: int, *, theme: Any = None) -> list:
    """Return preview lines centred on *target_line*, with syntax highlighting when *theme* is given."""
    from peovim.core.style import Style

    try:
        lines = Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [f"  Cannot read {file_path}"]
    start = max(0, target_line - _PREVIEW_CONTEXT)
    end = min(len(lines), target_line + _PREVIEW_CONTEXT + 1)

    spans = _get_syntax_spans(file_path, lines, start, end - 1) if theme is not None else []

    result: list = []
    for i in range(start, end):
        prefix = f"{'▶' if i == target_line else ' '} {i + 1:>4}: "
        raw_line = lines[i]
        is_target = i == target_line

        if spans:
            line_segs = [(prefix, Style())] + _style_line(raw_line, i, spans, theme)
            if is_target:
                line_segs = [(text, Style(fg=s.fg, bg=_HIGHLIGHT_BG)) for text, s in line_segs]
        elif is_target:
            line_segs = [(prefix + raw_line, Style(bg=_HIGHLIGHT_BG))]
        else:
            result.append(prefix + raw_line)
            continue

        result.append(line_segs)
    return result


def _preview_size(api: Any, sidebar_width: int) -> tuple[int, int]:
    """Return (width, height) for the preview float: 3/4 of the available space."""
    term_cols, term_rows = api.terminal_size()
    available_cols = max(40, term_cols - sidebar_width - 1)  # -1 for separator
    width = max(30, int(available_cols * 3 / 4))
    height = max(10, int(term_rows * 3 / 4))
    return width, height


def _show_preview_float(
    api: Any,
    file_path: str,
    line: int,
    *,
    handle: Any = None,
    sidebar_width: int = 0,
) -> Any:
    """Update or create a preview float; return the (possibly new) handle."""
    from peovim.ui.float_manager import Absolute

    theme = _get_active_theme(api)
    content = _build_preview_lines(file_path, line, theme=theme)
    title = f"{Path(file_path).name}:{line + 1}"
    anchor = Absolute(x=sidebar_width + 1, y=1) if sidebar_width else None
    width, height = _preview_size(api, sidebar_width)
    if handle is not None and handle.is_open:
        handle.set_content(content)
        handle.set_title(title)
        handle.set_size(width, height)
        if anchor is not None:
            handle.set_anchor(anchor)
        return handle
    return api.ui.open_float(
        content,
        anchor=anchor,
        title=title,
        width=width,
        height=height,
        border=True,
        focusable=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_root(api: Any) -> Path | None:
    _markers = [".git", ".svn", "pyproject.toml", "setup.py", "Cargo.toml", ".codemap.md"]
    buf = api.active_buffer()
    start_path: Path | None = None
    if buf is not None and buf.path is not None:
        start_path = buf.path.parent
    if start_path is None:
        return None
    current = start_path.resolve()
    while True:
        for marker in _markers:
            if (current / marker).exists():
                return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return start_path


def _rel_path(file_path: str, root: Path) -> str:
    with contextlib.suppress(Exception):
        return str(Path(file_path).relative_to(root))
    return Path(file_path).name


def _message_nodes(message: str) -> list:
    from peovim.ui.tree_view import TreeNode

    return [TreeNode(label=message)]


# ---------------------------------------------------------------------------
# Plugin setup
# ---------------------------------------------------------------------------


def setup(api: EditorAPI) -> None:
    """Register the codemap plugin."""
    global _panel
    _panel = _CodemapSidebarPanel(api)
    api.ui.register_sidebar_panel(_PANEL_NAME, _panel)

    api.keymap.ngroup("<leader>M", "Codemap")
    api.keymap.define_plug("CodemapPicker", lambda: _open_picker(api), desc="Codemap: picker")
    api.keymap.define_plug("CodemapToggle", lambda: _toggle_panel(api), desc="Codemap: toggle sidebar")
    api.keymap.define_plug("CodemapInsertAnchor", lambda: _cmd_insert_anchor(api), desc="Codemap: insert anchor")
    api.keymap.define_plug("CodemapOpenFile", lambda: _cmd_open_map_file(api), desc="Codemap: open map file")
    api.keymap.define_plug(
        "CodemapGotoAnchor", lambda: _cmd_goto_anchor_at_cursor(api), desc="Codemap: jump to anchor under cursor"
    )

    api.keymap.nmap("<leader>Mm", "<Plug>CodemapPicker", desc="Codemap: picker")
    api.keymap.nmap("<leader>Mt", "<Plug>CodemapToggle", desc="Codemap: toggle sidebar")
    api.keymap.nmap("<leader>Mi", "<Plug>CodemapInsertAnchor", desc="Codemap: insert anchor")
    api.keymap.nmap("<leader>Mo", "<Plug>CodemapOpenFile", desc="Codemap: open map file")
    api.keymap.nmap("<leader>Mg", "<Plug>CodemapGotoAnchor", desc="Codemap: jump to anchor under cursor")

    api.commands.register("Codemap", lambda cmd, ctx: _open_picker(api), min_abbrev=3)

    # Refresh sidebar whenever the codemap file is saved
    def _on_save(*, path: str = "", **kwargs: Any) -> None:
        if Path(path).name == _MAP_FILENAME:
            _refresh_if_visible(api)

    api.events.on("buffer_saved", _on_save)


def teardown() -> None:
    pass


# ---------------------------------------------------------------------------
# Command helpers (called from keybindings)
# ---------------------------------------------------------------------------


def _cmd_goto_anchor_at_cursor(api: Any) -> None:
    """Jump to the cm:XXXXXX anchor referenced on the cursor line."""
    buf = api.active_buffer()
    win = api.active_window()
    if buf is None or win is None:
        return
    cursor_line, _ = win.cursor
    line_text = buf.get_line(cursor_line)
    m = re.search(r"cm://([0-9a-f]{6})", line_text)
    if m is None:
        m = re.search(r"\bcm:([0-9a-f]{6})\b", line_text)
    if m is None:
        api.ui.notify("Codemap: no cm:// anchor on this line", level="info")
        return
    anchor_id = m.group(1)
    root = _project_root(api)
    if root is None:
        api.ui.notify("Codemap: no project root found", level="warn")
        return
    index = scan_anchors(root)
    loc = index.get(anchor_id)
    if loc is None:
        api.ui.notify(f"Codemap: anchor cm:{anchor_id} not found in project", level="warn")
        return
    api.goto_location(Path(loc.file), loc.line, 0)


def _toggle_panel(api: Any) -> None:
    panel = api.ui.get_sidebar_panel(_PANEL_NAME)
    if panel is None:
        return
    if api.ui.is_sidebar_visible(_PANEL_NAME):
        api.ui.hide_sidebar()
        return
    panel.refresh()
    api.ui.show_sidebar_panel(_PANEL_NAME, panel, focus=True)


def _cmd_insert_anchor(api: Any) -> None:
    anchor_id = insert_anchor_at_cursor(api)
    if anchor_id is None:
        api.ui.notify("Codemap: no active buffer", level="warn")
        return
    api.ui.notify(
        f"Codemap: inserted  cm:{anchor_id}  — add it to {_MAP_FILENAME}",
        level="info",
        timeout=6.0,
    )


def _cmd_open_map_file(api: Any) -> None:
    root = _project_root(api)
    if root is None:
        api.ui.notify("Codemap: no project root found", level="warn")
        return
    map_path = root / _MAP_FILENAME
    if not map_path.is_file():
        map_path.write_text(
            "# Codemap\n\n## Overview\n<!-- Add entries:  - [Label](cm://abcdef) — description -->\n",
            encoding="utf-8",
        )
    api.open_buffer(map_path)


def _refresh_if_visible(api: Any) -> None:
    if not api.ui.is_sidebar_visible(_PANEL_NAME):
        return
    panel = api.ui.get_sidebar_panel(_PANEL_NAME)
    if panel is not None and hasattr(panel, "refresh"):
        panel.refresh()
