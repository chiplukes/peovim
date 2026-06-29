"""Persistent references sidebar sourced from LSP references for the symbol under cursor."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI
    from peovim.ui.tree_view import TreeNode

_PANEL_NAME = "references"
_REFRESH_DELAY_MS = 200
_PREVIEW_CONTEXT = 5  # lines of context above and below the reference line
_PREVIEW_WIDTH = 72
_PREVIEW_HEIGHT = _PREVIEW_CONTEXT * 2 + 1 + 2  # content lines + border
_panel: _ReferencesSidebarPanel | None = None

# Background colour used to highlight the target reference line in the preview float
_HIGHLIGHT_BG: tuple = (60, 50, 90)

# ---------------------------------------------------------------------------
# Plugin-level configuration
# ---------------------------------------------------------------------------

_config: dict[str, Any] = {
    "preview_mode": "float",  # "float" | "cursor"
    "preview_syntax": True,  # apply syntax highlighting in float preview
}


def configure(**kwargs: Any) -> None:
    """Configure the references panel before or after plugin load.

    Args:
        preview_mode: ``"float"`` (non-navigating popup) or ``"cursor"``
                      (moves the editor cursor as in the outline panel).
        preview_syntax: Whether to apply syntax highlighting in the float preview.

    Example (in init.py, after loading the plugin)::

        from peovim.plugins import references_panel
        references_panel.configure(preview_mode="float", preview_syntax=True)
    """
    for key in ("preview_mode", "preview_syntax"):
        if key in kwargs:
            _config[key] = kwargs[key]
    if _panel is not None:
        _panel._apply_config()


# ---------------------------------------------------------------------------
# Panel class
# ---------------------------------------------------------------------------


class _ReferencesSidebarPanel:
    def __init__(self, api: Any, *, width: int = 34) -> None:
        from peovim.ui.tree_view import TreeView

        self._api = api
        self.width = width
        self._label = ""
        self._pending_label: str | None = None
        self._tree = TreeView(
            [], title="References", on_select=self._on_select, on_cursor_move=self._on_cursor_move, width=width
        )
        self._refresh_handle: Any = None
        self._preview_float: Any = None  # FloatHandle | None  (float mode)
        self._is_previewing: bool = False  # True while cursor-mode navigation is in progress

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

    def on_show(self) -> None:
        self.refresh()

    def on_focus(self) -> None:
        self._tree.focused = True
        self._preview_current()

    def on_blur(self) -> None:
        self._tree.focused = False
        self._close_preview()

    def on_hide(self) -> None:
        self._close_preview()
        self._cancel_scheduled_refresh()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _apply_config(self) -> None:
        """Re-read module config; tear down any state that no longer applies."""
        if _config["preview_mode"] != "float":
            self._close_preview()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def schedule_refresh(self, delay_ms: int = _REFRESH_DELAY_MS) -> None:
        self._cancel_scheduled_refresh()
        self._pending_label = _word_under_cursor(self._api)
        try:
            loop = asyncio.get_event_loop()
            self._refresh_handle = loop.call_later(delay_ms / 1000.0, self.refresh)
        except RuntimeError:
            self.refresh()

    def refresh(self) -> None:
        self._refresh_handle = None
        if self._pending_label is not None:
            self._label = self._pending_label
            self._pending_label = None
        else:
            self._label = _word_under_cursor(self._api)
        self._tree._title = _title_for_label(self._label)
        if self._api.lsp is None:
            self._tree.set_nodes(_message_nodes("LSP unavailable"))
            return

        def _apply(locs: list[dict]) -> None:
            self._tree._title = _title_for_label(self._label)
            self._tree.set_nodes(_build_reference_nodes(locs) or _message_nodes("No references found"))

        self._api.lsp.references_search(_apply)

    # ------------------------------------------------------------------
    # Preview callbacks
    # ------------------------------------------------------------------

    def _on_cursor_move(self, node: Any) -> None:
        """Show a preview for the reference under the cursor."""
        if node.value is None:
            self._close_preview()
            return
        path, line, col = node.value
        if _config["preview_mode"] == "cursor":
            self._preview_cursor(path, line, col)
        else:
            self._preview_float_popup(path, line, col)

    def _preview_cursor(self, path: str, line: int, col: int) -> None:
        """Navigate the editor cursor to the reference (outline-style preview)."""
        target = Path(path).resolve()
        active_buf = self._api.active_buffer()
        active_path = active_buf.path.resolve() if (active_buf and active_buf.path) else None
        self._is_previewing = True
        try:
            if active_path == target:
                win = self._api.active_window()
                win.set_cursor(line, col)
                win.scroll_to_cursor()
            else:
                self._api.open_buffer(target, line, col)
        finally:
            self._is_previewing = False

    def _preview_float_popup(self, path: str, line: int, col: int) -> None:
        """Show a non-focusable preview float centred on screen."""
        theme = _get_active_theme(self._api) if _config["preview_syntax"] else None
        content = _build_preview_content(path, line, col, theme=theme)
        title = f"{Path(path).name}:{line + 1}"
        if self._preview_float is not None and self._preview_float.is_open:
            self._preview_float.set_content(content)
            self._preview_float.set_title(title)
        else:
            self._preview_float = self._api.ui.open_float(
                content,
                title=title,
                width=_PREVIEW_WIDTH,
                height=_PREVIEW_HEIGHT,
                border=True,
                focusable=False,
            )

    def _on_select(self, node: Any) -> None:
        if node.value is None:
            return
        self._close_preview()
        path, line, col = node.value
        self._api.goto_location(Path(path), line, col)
        self._api.ui.blur_sidebar()

    def _preview_current(self) -> None:
        """Show a preview for whichever list item is currently selected."""
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

    def _cancel_scheduled_refresh(self) -> None:
        if self._refresh_handle is None:
            return
        with contextlib.suppress(Exception):
            self._refresh_handle.cancel()
        self._refresh_handle = None


# ---------------------------------------------------------------------------
# Plugin setup
# ---------------------------------------------------------------------------


def setup(api: EditorAPI) -> None:
    """Register the persistent references sidebar."""
    global _panel
    _panel = _ReferencesSidebarPanel(api)

    api.ui.register_sidebar_panel(_PANEL_NAME, _panel)
    api.keymap.define_plug("ReferencesPanel", lambda: _toggle_references(api), desc="References: toggle sidebar")
    api.keymap.nmap("<leader>cR", "<Plug>ReferencesPanel", desc="References: sidebar")
    api.commands.register("ReferencesPanel", lambda cmd, ctx: _toggle_references(api), min_abbrev=10)

    api.events.on("buffer_opened", lambda **kwargs: _refresh_if_visible(api, immediate=True))
    api.events.on("buffer_saved", lambda **kwargs: _refresh_if_visible(api, immediate=True))
    api.events.on("buffer_changed", lambda **kwargs: _refresh_if_visible(api, immediate=False))
    api.events.on("cursor_moved", lambda **kwargs: _refresh_if_visible(api, immediate=False))


def _toggle_references(api: Any) -> None:
    panel = api.ui.get_sidebar_panel(_PANEL_NAME)
    if panel is None:
        return
    if api.ui.is_sidebar_visible(_PANEL_NAME):
        api.ui.hide_sidebar()
        return
    panel.refresh()
    api.ui.show_sidebar_panel(_PANEL_NAME, panel, focus=True)


def _refresh_if_visible(api: Any, *, immediate: bool) -> None:
    if not api.ui.is_sidebar_visible(_PANEL_NAME):
        return
    panel = api.ui.get_sidebar_panel(_PANEL_NAME)
    if panel is None:
        return
    if getattr(panel, "_is_previewing", False):
        return
    if immediate or not hasattr(panel, "schedule_refresh"):
        panel.refresh()
        return
    panel.schedule_refresh()


# ---------------------------------------------------------------------------
# Helpers — theme & syntax
# ---------------------------------------------------------------------------


def _get_active_theme(api: Any) -> Any:
    """Return the currently active Theme object."""
    from peovim.syntax.themes import get_theme

    theme_name = getattr(getattr(api, "_editor_state", None), "active_theme", "catppuccin")
    return get_theme(theme_name) or get_theme("catppuccin")


def _get_syntax_spans(path: str, all_lines: list[str], start_line: int, end_line: int) -> list:
    """Parse the full file with tree-sitter and return spans in [start_line, end_line]."""
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
    """Convert syntax spans for a single doc_line into a FloatSegment list."""
    from peovim.core.style import Style

    n = len(raw_line)
    if n == 0:
        return [("", Style())]

    # Per-column foreground colour array; None means default
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

    # Collapse adjacent same-colour runs into segments
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


# ---------------------------------------------------------------------------
# Helpers — preview content
# ---------------------------------------------------------------------------


def _build_preview_content(path: str, target_line: int, col: int, *, theme: Any = None) -> list:
    """Return a list of FloatLines for the preview float.

    When *theme* is supplied each line is syntax-highlighted; the target line
    always carries a distinct background regardless.
    """
    from peovim.core.style import Style

    try:
        all_lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ["(unable to read file)"]

    start = max(0, target_line - _PREVIEW_CONTEXT)
    end = min(len(all_lines), target_line + _PREVIEW_CONTEXT + 1)

    spans = _get_syntax_spans(path, all_lines, start, end - 1) if theme is not None else []

    result: list = []
    for doc_line in range(start, end):
        raw_line = all_lines[doc_line]
        prefix = f"{doc_line + 1:4d} "
        is_target = doc_line == target_line

        if spans:
            line_segs = [(prefix, Style())] + _style_line(raw_line, doc_line, spans, theme)
            if is_target:
                line_segs = [(text, Style(fg=s.fg, bg=_HIGHLIGHT_BG)) for text, s in line_segs]
        elif is_target:
            line_segs = [(prefix + raw_line, Style(bg=_HIGHLIGHT_BG))]
        else:
            result.append(prefix + raw_line)
            continue

        result.append(line_segs)

    return result


def _title_for_label(label: str) -> str:
    return f"References [{label}]" if label else "References"


def _message_nodes(message: str) -> list[TreeNode]:
    from peovim.ui.tree_view import TreeNode

    return [TreeNode(label=message)]


def _build_reference_nodes(locs: list[dict]) -> list[TreeNode]:
    from peovim.ui.tree_view import TreeNode

    nodes = []
    for loc in locs:
        path = str(loc.get("path", ""))
        line = int(loc.get("line", 0))
        col = int(loc.get("col", 0))
        label = f"{Path(path).name}:{line + 1}:{col + 1}"
        nodes.append(TreeNode(label=label, value=(path, line, col)))
    return nodes


def _word_under_cursor(api: Any) -> str:
    win = api.active_window()
    buf = api.active_buffer()
    line_no, col = win.cursor
    line = buf.get_line(line_no)
    if not line:
        return ""
    col = min(col, max(0, len(line) - 1))
    if not (line[col].isalnum() or line[col] == "_"):
        if col > 0 and (line[col - 1].isalnum() or line[col - 1] == "_"):
            col -= 1
        else:
            return ""
    start = col
    end = col + 1
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
        start -= 1
    while end < len(line) and (line[end].isalnum() or line[end] == "_"):
        end += 1
    return line[start:end]
