"""
Gutter signs and diff view for Subversion (SVN) working copies.

Detects .svn directories, parses `svn diff` output, and places per-line
signs identical in style to gitsigns.  Also provides a side-by-side diff
view (original from `svn cat` vs current file) and a status sidebar panel.

No write operations (commit/add/revert) are included.

Usage in init.py:
    plugins.load('peovim.plugins.svnsigns')

    # Optional keybind overrides:
    keymap.nmap(']h', lambda: api.commands.execute('SvnNextHunk'))
    keymap.nmap('[h', lambda: api.commands.execute('SvnPrevHunk'))
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from peovim.core.diffing import parse_hunks as _parse_hunks
from peovim.plugins import vcssigns as _vcs

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

_log = logging.getLogger(__name__)

_NAMESPACE = "svnsigns"

_SIGN_DEFS: dict[str, tuple[str, tuple[int, int, int]]] = {
    "svnsigns.add": ("│", (80, 200, 80)),
    "svnsigns.change": ("│", (200, 200, 80)),
    "svnsigns.delete": ("▁", (200, 80, 80)),
}

_HUNK_TYPE_TO_SIGN = {
    "add": "svnsigns.add",
    "change": "svnsigns.change",
    "delete": "svnsigns.delete",
}

# Module-level state
_debounce_timers: dict[int, Any] = {}
_status_panel: _SvnStatusPanel | None = None
_diff_temp_dir: tempfile.TemporaryDirectory | None = None  # type: ignore[type-arg]


# ---------------------------------------------------------------------------
# SVN detection + subprocess helpers
# ---------------------------------------------------------------------------


def _svn_available() -> bool:
    return shutil.which("svn") is not None


def _find_svn_root(path: str | Path) -> Path | None:
    """Walk up from path looking for an .svn directory; return the wc root."""
    p = Path(path).resolve()
    if p.is_file():
        p = p.parent
    candidate: Path | None = None
    while True:
        if (p / ".svn").is_dir():
            candidate = p
        parent = p.parent
        if parent == p:
            break
        p = parent
    return candidate


def _svn_run(args: list[str], cwd: str | None = None) -> str:
    """Run an svn subcommand and return stdout.  Returns '' on error."""
    try:
        result = subprocess.run(  # noqa: S603
            ["svn", *args],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd,
            check=False,
        )
        return result.stdout
    except Exception as exc:
        _log.debug("svn %s failed: %s", args[0] if args else "?", exc)
        return ""


def _get_hunks(path: str | Path) -> list[dict]:
    """Run `svn diff` on *path* and return parsed hunk list."""
    output = _svn_run(["diff", "--", str(path)])
    return _parse_hunks(output)


def _safe_get_hunks(path: str | Path) -> list[dict]:
    """Return `[]` when SVN is unavailable or path is not under version control."""
    if not _svn_available() or _find_svn_root(path) is None:
        return []
    return _get_hunks(path)


def _get_original_content(path: str | Path) -> str | None:
    """Fetch BASE revision content via `svn cat`."""
    output = _svn_run(["cat", "--", str(path)])
    return output if output else None


_VERSIONED_CODES = frozenset("MADC")


# ---------------------------------------------------------------------------
# SVN status entries (compatible with peovim.git.presentation helpers)
# ---------------------------------------------------------------------------


@dataclass
class SvnStatusEntry:
    """
    SVN status entry whose attributes mirror GitStatusEntry so that
    color_for_status_entry() and marker_for_status_entry() work unchanged.
    """

    path: str = ""
    code: str = ""
    index_status: str = " "
    worktree_status: str = " "
    modified: bool = False
    staged: bool = False
    conflicted: bool = False
    deleted: bool = False
    untracked: bool = False
    mixed: bool = False


def _svn_entry_for_code(code: str, path: str) -> SvnStatusEntry:
    return SvnStatusEntry(
        path=path,
        code=code,
        index_status="A" if code == "A" else " ",
        worktree_status=code if code != "A" else " ",
        modified=code == "M",
        staged=code == "A",
        conflicted=code == "C",
        deleted=code == "D",
    )


def _merge_svn_entries(existing: object | None, incoming: SvnStatusEntry) -> SvnStatusEntry:
    if not isinstance(existing, SvnStatusEntry):
        return incoming
    changed_a = existing.modified or existing.staged or existing.conflicted or existing.deleted
    changed_b = incoming.modified or incoming.staged or incoming.conflicted or incoming.deleted
    return SvnStatusEntry(
        path=existing.path or incoming.path,
        code=existing.code or incoming.code,
        modified=existing.modified or incoming.modified,
        staged=existing.staged or incoming.staged,
        conflicted=existing.conflicted or incoming.conflicted,
        deleted=existing.deleted or incoming.deleted,
        mixed=changed_a and changed_b,
    )


def get_svn_status_map(cwd: Path) -> dict[str, object]:
    """
    Return {resolved_path_str: SvnStatusEntry} for all versioned-changed files
    under *cwd*.  Parent directories are propagated so that the explorer can
    color folders that contain changes.

    Compatible with peovim.git.presentation.color_for_status_entry() and
    marker_for_status_entry().  Returns {} when svn is unavailable or cwd
    is not inside an SVN working copy.
    """
    if not _svn_available():
        return {}
    if _find_svn_root(cwd) is None:
        return {}
    entries = _get_status(cwd)
    status_map: dict[str, object] = {}
    cwd_resolved = cwd.resolve()
    for e in entries:
        code = e["status"]
        file_path = (cwd / e["path"]).resolve()
        svn_entry = _svn_entry_for_code(code, e["path"])
        status_map[str(file_path)] = svn_entry
        parent = file_path.parent
        while True:
            p_str = str(parent)
            status_map[p_str] = _merge_svn_entries(status_map.get(p_str), svn_entry)
            if parent == cwd_resolved or parent.parent == parent:
                break
            parent = parent.parent
    return status_map


def _get_status(cwd: Path) -> list[dict]:
    """
    Run `svn status` from *cwd* and return versioned-only {status, path} dicts.

    Only M (modified), A (added), D (deleted), C (conflicted) are included.
    Unversioned (?), missing (!), and other codes are silently skipped.
    Running from *cwd* naturally scopes results to that directory and below.
    """
    output = _svn_run(["status"], cwd=str(cwd))
    entries: list[dict] = []
    for line in output.splitlines():
        if len(line) < 2:
            continue
        code = line[0]
        if code not in _VERSIONED_CODES:
            continue
        file_path = line[8:].strip() if len(line) > 8 else line[1:].strip()
        entries.append({"status": code, "path": file_path})
    return entries


# ---------------------------------------------------------------------------
# Sidebar status panel
# ---------------------------------------------------------------------------


class _SvnStatusPanel:
    def __init__(self, api: Any, cwd: Path, *, width: int = 32) -> None:
        from peovim.ui.tree_view import TreeView

        self._api = api
        self._cwd = cwd
        self.width = width
        self._tree = TreeView([], title="SVN", width=width)

    # --- Panel protocol ---

    def render(self, grid: Any) -> None:
        self._tree.focused = getattr(self, "_sidebar_focused", False)
        self._tree.blink_on = getattr(self, "_sidebar_blink_on", True)
        self._tree._width = grid.width
        title = self._tree._title
        self._tree._title = ""
        try:
            self._tree.render(grid)
        finally:
            self._tree._title = title

    def feed_key(self, key: str) -> bool:
        if key == "R":
            self.refresh()
            return True
        # <CR> / l → open diff for M/C, open file otherwise
        # o       → always open the file in a buffer
        if key in ("<CR>", "l"):
            node = self._tree.selected_node
            if node is not None and node.value is not None:
                self._activate(node.value, diff=True)
            return True
        if key == "o":
            node = self._tree.selected_node
            if node is not None and node.value is not None:
                self._activate(node.value, diff=False)
            return True
        self._tree.feed_key(key)
        return True

    def on_focus(self) -> None:
        self._sidebar_focused = True  # type: ignore[attr-defined]

    def on_blur(self) -> None:
        self._sidebar_focused = False  # type: ignore[attr-defined]

    def on_show(self) -> None:
        self.refresh()

    def on_hide(self) -> None:
        pass

    # --- Logic ---

    _STATUS_COLOR = {
        "M": (200, 200, 80),
        "A": (80, 200, 80),
        "D": (200, 80, 80),
        "C": (200, 80, 200),
    }

    def refresh(self) -> None:
        from peovim.ui.tree_view import TreeNode

        if _find_svn_root(self._cwd) is None:
            self._tree.set_nodes([TreeNode(label="No SVN working copy", icon="", fg=(128, 128, 128))])
            return
        entries = _get_status(self._cwd)
        if not entries:
            self._tree.set_nodes([TreeNode(label="Working copy is clean", icon="", fg=(128, 128, 128))])
            return
        nodes: list[TreeNode] = []
        for e in entries:
            code = e["status"]
            label = e["path"]
            fg = self._STATUS_COLOR.get(code, (200, 200, 200))
            nodes.append(TreeNode(label=label, icon=code, fg=fg, value=e))
        self._tree.set_nodes(nodes)

    def _activate(self, entry: dict, *, diff: bool) -> None:
        """Open diff (for M/C) or just open the file in the editor."""
        code = entry.get("status", "")
        file_path = self._cwd / entry["path"]
        with contextlib.suppress(Exception):
            self._api.ui.blur_sidebar()
        if diff and code in ("M", "C"):
            _open_diff(self._api, file_path)
        elif file_path.exists():
            with contextlib.suppress(Exception):
                self._api.open_buffer(file_path)


# ---------------------------------------------------------------------------
# Sign management
# ---------------------------------------------------------------------------


def _update_signs(api: Any, buf: Any) -> None:
    _vcs.update_signs(api, buf, _NAMESPACE, _HUNK_TYPE_TO_SIGN, _safe_get_hunks)


# ---------------------------------------------------------------------------
# Diff view
# ---------------------------------------------------------------------------


def _open_diff(api: Any, file_path: Path | None = None) -> None:
    """Open a side-by-side diff of *file_path* (default: active buffer) vs SVN BASE."""
    if file_path is None:
        buf = api.active_buffer()
        if buf.path is None:
            api.ui.notify("SVN diff: buffer has no file path", level="warn")
            return
        file_path = Path(buf.path)
    if _find_svn_root(file_path) is None:
        api.ui.notify("Not in an SVN working copy", level="warn")
        return

    original = _get_original_content(file_path)
    if original is None:
        api.ui.notify("SVN diff: file is not under version control or has no BASE", level="warn")
        return

    # Write original to a temp file so compare can open it as a real buffer.
    # Keep the original filename (not the extension) so that filetype detection
    # and syntax highlighting work correctly in the diff pane.
    global _diff_temp_dir
    if _diff_temp_dir is None:
        _diff_temp_dir = tempfile.TemporaryDirectory(prefix="peovim_svn_")

    tmp_dir = Path(_diff_temp_dir.name) / "base"
    tmp_dir.mkdir(exist_ok=True)
    tmp_path = tmp_dir / file_path.name  # same extension → correct syntax
    tmp_path.write_text(original, encoding="utf-8", errors="replace")
    _refresh_loaded_document(api, tmp_path)
    _refresh_loaded_document(api, file_path, if_clean=True)

    # Emit the standard diff_selection_ready event (compare plugin handles this)
    # If compare plugin is not loaded, open a simple vsplit manually.
    listeners = _count_diff_listeners(api)
    if listeners:
        api.events.emit(
            "diff_selection_ready",
            left=str(tmp_path),
            right=str(file_path),
        )
    else:
        _open_manual_diff(api, tmp_path, file_path)


def _refresh_loaded_document(api: Any, path: Path, *, if_clean: bool = False) -> None:
    workspace = getattr(api, "_workspace", None)
    if workspace is None:
        return
    find_document = getattr(workspace, "find_document_by_path", None)
    if not callable(find_document):
        return
    doc = find_document(path)
    if doc is None:
        return
    if if_clean and getattr(doc, "dirty", False):
        return
    reload_doc = getattr(doc, "reload", None)
    if callable(reload_doc):
        reload_doc()


def _count_diff_listeners(api: Any) -> int:
    """Return number of handlers registered for diff_selection_ready."""
    try:
        return api.events.handler_count("diff_selection_ready")
    except Exception:
        return 0


def _open_manual_diff(api: Any, original_path: Path, current_path: Path) -> None:
    """Minimal vsplit diff when the compare plugin is not available."""
    from difflib import SequenceMatcher

    api.commands.execute("only")
    api.open_buffer(original_path)
    api.commands.execute("vsplit")
    api.open_buffer(current_path)

    # Find the two windows
    orig_win = cur_win = None
    for win in api.list_windows():
        b = win.buffer()
        if b.path is not None:
            if Path(b.path) == original_path:
                orig_win = win
            elif Path(b.path) == current_path:
                cur_win = win

    if orig_win is None or cur_win is None:
        return

    orig_buf = orig_win.buffer()
    cur_buf = cur_win.buffer()
    orig_lines = orig_buf.get_lines()
    cur_lines = cur_buf.get_lines()

    # Apply the same namespace/sign decorations as the compare plugin
    ns = "compare"
    orig_buf.clear_namespace(ns)
    cur_buf.clear_namespace(ns)

    sm = SequenceMatcher(None, orig_lines, cur_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            for ln in range(i1, i2):
                orig_buf.add_sign(ns, ln, "compare.change")
            for ln in range(j1, j2):
                cur_buf.add_sign(ns, ln, "compare.change")
        elif tag == "insert":
            for ln in range(j1, j2):
                cur_buf.add_sign(ns, ln, "compare.insert")
        elif tag == "delete":
            for ln in range(i1, i2):
                orig_buf.add_sign(ns, ln, "compare.delete")

    api.activate_window(cur_win)


# ---------------------------------------------------------------------------
# Hunk navigation + preview
# ---------------------------------------------------------------------------


def _current_hunks(api: Any) -> list[dict]:
    return _vcs.current_hunks(api, _safe_get_hunks)


def _next_hunk(api: Any) -> None:
    _vcs.next_hunk(api, _safe_get_hunks)


def _prev_hunk(api: Any) -> None:
    _vcs.prev_hunk(api, _safe_get_hunks)


def _hunk_preview(api: Any) -> None:
    try:
        win = api.active_window()
        cursor_line = win.cursor[0]
        hunks = _current_hunks(api)
        hunk = next(
            (h for h in hunks if h["start"] <= cursor_line <= h["end"]),
            None,
        )
        if hunk is None:
            api.ui.notify("No SVN hunk at cursor", level="info")
            return

        buf = api.active_buffer()
        original = _get_original_content(buf.path) if buf.path else None
        if original is None:
            lines = [
                f"  type:  {hunk['type']}",
                f"  lines: {hunk['start'] + 1}–{hunk['end'] + 1}",
            ]
        else:
            # Show the original lines for context
            orig_lines = original.splitlines()
            start = hunk["start"]
            end = min(hunk["end"] + 1, len(orig_lines))
            preview = [f"  - {ln}" for ln in orig_lines[start:end]] or ["  (no content)"]
            lines = [f"  [{hunk['type']}  line {hunk['start'] + 1}–{hunk['end'] + 1}]", ""] + preview
        api.ui.open_float(lines, title="SVN Hunk", width=60, height=min(len(lines) + 2, 20))
    except Exception as exc:
        _log.debug("hunk_preview error: %s", exc)


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def _on_buf_event(api: Any, **kwargs: Any) -> None:
    """buffer_opened / buffer_saved → update signs immediately."""
    buf_id: int | None = kwargs.get("buf_id")
    if buf_id is None:
        return
    for buf in api.list_buffers():
        if buf.buf_id == buf_id:
            _update_signs(api, buf)
            _refresh_panel_if_visible(api)
            return


def _on_buf_changed(api: Any, **kwargs: Any) -> None:
    """buffer_changed → debounced sign update (600 ms)."""
    buf_id: int | None = kwargs.get("buf_id")
    if buf_id is None:
        return
    old = _debounce_timers.pop(buf_id, None)
    if old is not None:
        with contextlib.suppress(Exception):
            old.cancel()
    target_buf = next((b for b in api.list_buffers() if b.buf_id == buf_id), None)
    if target_buf is None:
        return
    try:
        loop = asyncio.get_event_loop()

        def _debounced_callback() -> None:
            _update_signs(api, target_buf)
            _refresh_panel_if_visible(api)

        handle = loop.call_later(0.6, _debounced_callback)
        _debounce_timers[buf_id] = handle
    except RuntimeError:
        _update_signs(api, target_buf)


def _refresh_panel_if_visible(api: Any) -> None:
    with contextlib.suppress(Exception):
        if api.ui.is_sidebar_visible("svn-status"):
            panel = api.ui.get_sidebar_panel("svn-status")
            if panel is not None:
                panel.refresh()


# ---------------------------------------------------------------------------
# Plugin entry points
# ---------------------------------------------------------------------------


def setup(api: EditorAPI) -> None:
    global _status_panel

    if not _svn_available():
        _log.debug("svnsigns: svn not found on PATH — plugin inactive")
        return

    # Register sign definitions
    _vcs.register_sign_defs(api, _SIGN_DEFS)

    # Sidebar status panel — scoped to the directory peovim was launched from
    _cwd = Path.cwd()
    _status_panel = _SvnStatusPanel(api, _cwd)
    api.ui.register_sidebar_panel("svn-status", _status_panel)

    # Event subscriptions
    api.events.on("buffer_opened", lambda **kw: _on_buf_event(api, **kw))
    api.events.on("buffer_saved", lambda **kw: _on_buf_event(api, **kw))
    api.events.on("buffer_changed", lambda **kw: _on_buf_changed(api, **kw))

    # Commands
    api.commands.register("SvnDiff", lambda cmd, ctx: _open_diff(api))
    api.commands.register("SvnStatus", lambda cmd, ctx: _toggle_status(api))
    api.commands.register("SvnNextHunk", lambda cmd, ctx: _next_hunk(api))
    api.commands.register("SvnPrevHunk", lambda cmd, ctx: _prev_hunk(api))
    api.commands.register("SvnHunkPreview", lambda cmd, ctx: _hunk_preview(api))

    # Keybindings  (all guarded to SVN buffers)
    _nmap(api, "]h", lambda: _next_hunk(api), "SVN: next hunk")
    _nmap(api, "[h", lambda: _prev_hunk(api), "SVN: prev hunk")
    _nmap(api, "<leader>sd", lambda: _open_diff(api), "SVN: diff view")
    _nmap(api, "<leader>sp", lambda: _hunk_preview(api), "SVN: hunk preview")
    _nmap(api, "<leader>ss", lambda: _toggle_status(api), "SVN: status panel")

    # Initial sign pass for already-open buffers
    for buf in api.list_buffers():
        _update_signs(api, buf)


def teardown() -> None:
    global _status_panel, _diff_temp_dir
    for handle in _debounce_timers.values():
        with contextlib.suppress(Exception):
            handle.cancel()
    _debounce_timers.clear()
    _status_panel = None
    if _diff_temp_dir is not None:
        with contextlib.suppress(Exception):
            _diff_temp_dir.cleanup()
        _diff_temp_dir = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nmap(api: Any, keys: str, fn: Any, desc: str) -> None:
    """Register a normal-mode binding, guarded to SVN working copies."""

    def _guarded() -> None:
        with contextlib.suppress(Exception):
            buf = api.active_buffer()
            if buf.path and _find_svn_root(buf.path) is not None:
                fn()

    with contextlib.suppress(Exception):
        api.keymap.nmap(keys, _guarded, desc)


def _toggle_status(api: Any) -> None:
    with contextlib.suppress(Exception):
        if api.ui.is_sidebar_visible("svn-status"):
            api.ui.hide_sidebar()
        else:
            api.ui.show_sidebar("svn-status")
