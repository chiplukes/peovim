"""
Gutter signs and diff view for Subversion (SVN) working copies.

Detects .svn directories, parses `svn diff` output, and places per-line
signs identical in style to gitsigns.  Also provides a side-by-side diff
view, a status sidebar panel, and expandable recent revision history.

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
import hashlib
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote
from xml.etree import ElementTree as ET

from peovim.core.diffing import parse_hunks as _parse_hunks
from peovim.core.persistence import atomic_write_text
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
_revision_sources: dict[Path, _SvnRevision] = {}


@dataclass(frozen=True)
class _SvnRevision:
    root: Path
    file_path: Path
    url: str
    revision: int
    action: str = ""
    copyfrom_url: str | None = None
    copyfrom_revision: int | None = None


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


def _svn_run(args: list[str], cwd: str | None = None, *, check: bool = False) -> str:
    """Return stdout; errors propagate with check=True, otherwise return ''."""
    try:
        result = subprocess.run(  # noqa: S603
            ["svn", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            cwd=cwd,
            check=check,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception as exc:
        _log.debug("svn %s failed: %s", args[0] if args else "?", exc)
        if check:
            raise
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
    try:
        return _svn_run(["cat", "-r", "BASE", "--", f"{path}@"], check=True)
    except Exception:
        return None


def _svn_info(path: Path) -> ET.Element:
    xml = _svn_run(["info", "--xml", "--", f"{path}@"], check=True)
    entry = ET.fromstring(xml).find("entry")
    if entry is None:
        raise ValueError("SVN returned no file information")
    return entry


def _working_revision(file_path: Path, root: Path) -> _SvnRevision:
    entry = _svn_info(file_path)
    if entry.get("kind") != "file":
        raise ValueError("Select a versioned file")
    if entry.findtext("wc-info/schedule") == "add":
        raise ValueError("File has no committed revision yet")
    revision = int(entry.get("revision", "0"))
    url = entry.findtext("url")
    if not url or revision < 1:
        raise ValueError("File has no committed revision yet")
    return _SvnRevision(root, file_path, url, revision)


def _revision_content(url: str, revision: int) -> str | None:
    if revision < 1:
        return None
    try:
        return _svn_run(["cat", "-r", str(revision), "--", f"{url}@{revision}"], check=True)
    except subprocess.CalledProcessError as exc:
        # Only a missing repository path represents an empty side. Connection,
        # authentication, and other failures must be reported instead.
        if re.search(r"[EW](160013|195012)\b", exc.stderr or ""):
            return None
        raise


def _revision_details(source: _SvnRevision) -> _SvnRevision:
    """Resolve add/copy history for a BASE revision before comparing its parent."""
    if source.revision < 1:
        raise ValueError("No previous revision")
    repository_url = _svn_info(source.root).findtext("repository/root")
    if not repository_url or not source.url.startswith(repository_url.rstrip("/") + "/"):
        raise ValueError("Cannot determine repository path")
    repo_path = unquote(source.url[len(repository_url.rstrip("/")) :])
    xml = _svn_run(
        ["log", "--xml", "--verbose", "-r", str(source.revision), "--", f"{source.url}@{source.revision}"],
        check=True,
    )
    for changed in ET.fromstring(xml).findall("logentry/paths/path"):
        if changed.text == repo_path:
            copyfrom = changed.get("copyfrom-path")
            return replace(
                source,
                action=changed.get("action", "M"),
                copyfrom_url=repository_url.rstrip("/") + quote(copyfrom, safe="/") if copyfrom else None,
                copyfrom_revision=int(changed.attrib["copyfrom-rev"]) if copyfrom else None,
            )
    return replace(source, action="M")


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
        self._history_node: Any = None

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
            self._history_node = None
            self.refresh()
            return True
        if key == "?":
            self._api.ui.notify(
                "SVN: Enter/l/d diff file; <leader>dw vs working; <leader>dp vs previous; o open file; R refresh",
                level="info",
            )
            return True
        # <CR> / l → expand revision/section, or diff the selected file
        # o       → always open the file in a buffer
        if key in ("<CR>", "l", "d"):
            node = self._tree.selected_node
            if node is not None and node.has_children():
                if key != "d":
                    self._tree.toggle(node)
            elif node is not None and node.value is not None:
                self._activate(node.value, diff=True)
            return True
        if key == "o":
            node = self._tree.selected_node
            if node is not None and node.value is not None:
                self._activate(node.value, diff=False)
            return True
        self._tree.feed_key(key)
        return True

    def click(self, row: int, col: int) -> bool:
        # The tree renders without its title row, so rows map directly.
        visible = self._tree._visible_nodes()
        vis_idx = row + self._tree._scroll_top
        if vis_idx < 0 or vis_idx >= len(visible):
            return False
        self._tree._selected_idx = vis_idx
        node, _depth = visible[vis_idx]
        if node.has_children():
            self._tree.toggle(node)
        elif node.value is not None:
            self._activate(node.value, diff=True)
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
        nodes: list[TreeNode] = []
        for e in entries:
            code = e["status"]
            label = e["path"]
            fg = self._STATUS_COLOR.get(code, (200, 200, 200))
            nodes.append(TreeNode(label=label, icon=code, fg=fg, value=e))
        selected = self._tree.selected_node
        status = TreeNode(label="Status", children_fn=lambda: nodes, expanded=True)
        if not nodes:
            nodes.append(TreeNode(label="Working copy is clean", fg=(128, 128, 128)))
        if self._history_node is None:
            # Loading history can contact the repository. Do it only when the
            # user explicitly expands this section, not on every buffer event.
            self._history_node = TreeNode(label="Recent revisions", children_fn=self._history_nodes)
        self._tree.set_nodes([status, self._history_node])
        if selected is not None and selected.value is not None:
            self._tree.select_value(selected.value)

    def _history_nodes(self) -> list[Any]:
        from peovim.ui.tree_view import TreeNode

        try:
            info = _svn_info(self._cwd)
            repository_url = info.findtext("repository/root")
            scope = unquote(info.findtext("relative-url", "^/")[1:]).rstrip("/")
            if not repository_url:
                raise ValueError("SVN returned no repository URL")
            xml = _svn_run(["log", "--xml", "--verbose", "--limit", "30", "--", f"{self._cwd}@"], check=True)
            nodes = []
            for entry in ET.fromstring(xml).findall("logentry"):
                revision = int(entry.attrib["revision"])
                files = []
                next_scope = scope
                for changed in entry.findall("paths/path"):
                    repo_path = changed.text or ""
                    if repo_path == scope and changed.get("kind") == "dir":
                        relative = ""
                    elif repo_path.startswith(scope + "/"):
                        relative = repo_path[len(scope) + 1 :]
                    else:
                        continue
                    # Repository names are untrusted; keep local targets inside
                    # the panel's scope, including on Windows.
                    local = (self._cwd / relative).resolve()
                    if not local.is_relative_to(self._cwd.resolve()):
                        continue
                    copyfrom = changed.get("copyfrom-path")
                    source = _SvnRevision(
                        _find_svn_root(self._cwd) or self._cwd,
                        local,
                        repository_url.rstrip("/") + quote(repo_path, safe="/"),
                        revision,
                        changed.get("action", "M"),
                        repository_url.rstrip("/") + quote(copyfrom, safe="/") if copyfrom else None,
                        int(changed.attrib["copyfrom-rev"]) if copyfrom else None,
                    )
                    if changed.get("kind") == "dir":
                        if repo_path == scope and copyfrom:
                            next_scope = copyfrom.rstrip("/")
                        if source.action in ("A", "D", "R"):
                            files.append(
                                TreeNode(
                                    label=(relative or ".") + "/",
                                    icon=source.action,
                                    children_fn=partial(self._directory_nodes, source),
                                )
                            )
                    else:
                        label = f"{relative} ← {copyfrom} @r{source.copyfrom_revision}" if copyfrom else relative
                        files.append(
                            TreeNode(
                                label=label, icon=source.action, value=source, fg=self._STATUS_COLOR.get(source.action)
                            )
                        )
                message = entry.findtext("msg", "").splitlines()
                subject = message[0] if message else "(no message)"
                author = entry.findtext("author", "")
                date = entry.findtext("date", "")[:10]
                nodes.append(
                    TreeNode(
                        label=f"r{revision} {subject} · {author} · {date}",
                        children_fn=partial(list, files or [TreeNode(label="No changed files in this directory")]),
                    )
                )
                scope = next_scope
            return nodes or [TreeNode(label="No revisions yet")]
        except Exception as exc:
            _log.debug("SVN history failed", exc_info=True)
            return [TreeNode(label=f"Cannot load revisions: {exc}")]

    def _directory_nodes(self, directory: _SvnRevision) -> list[Any]:
        from peovim.ui.tree_view import TreeNode

        revision = directory.revision - 1 if directory.action == "D" else directory.revision
        try:

            def file_names(number: int) -> set[str]:
                xml = _svn_run(
                    ["list", "--xml", "--recursive", "-r", str(number), "--", f"{directory.url}@{number}"], check=True
                )
                return {
                    e.findtext("name", "") for e in ET.fromstring(xml).findall("list/entry") if e.get("kind") == "file"
                }

            current = file_names(revision)
            previous = file_names(revision - 1) if directory.action == "R" else set()
            nodes = []
            for name in sorted(current | previous):
                if not name:
                    continue
                local = (directory.file_path / name).resolve()
                if not local.is_relative_to(self._cwd.resolve()):
                    continue
                source = replace(
                    directory,
                    file_path=local,
                    url=directory.url.rstrip("/") + "/" + quote(name, safe="/"),
                    copyfrom_url=directory.copyfrom_url.rstrip("/") + "/" + quote(name, safe="/")
                    if directory.copyfrom_url
                    else None,
                )
                if directory.action == "R":
                    action = "D" if name not in current else "A" if name not in previous else "R"
                    source = replace(source, action=action, copyfrom_url=None, copyfrom_revision=None)
                nodes.append(
                    TreeNode(label=name, icon=source.action, value=source, fg=self._STATUS_COLOR.get(source.action))
                )
            return nodes or [TreeNode(label="No files in this directory")]
        except Exception as exc:
            return [TreeNode(label=f"Cannot load files: {exc}")]

    def diff_selected(self, *, previous: bool) -> None:
        node = self._tree.selected_node
        if node is None or node.value is None:
            return
        source = node.value if isinstance(node.value, _SvnRevision) else None
        path = source.file_path if source else self._cwd / node.value["path"]
        opened = (
            _open_diff_previous(self._api, path, source=source)
            if previous
            else _open_diff(self._api, path, source=source)
        )
        if opened:
            self._api.ui.blur_sidebar()

    def _activate(self, entry: dict | _SvnRevision, *, diff: bool) -> None:
        """Diff a revision/status file, or open its working file."""
        file_path = entry.file_path if isinstance(entry, _SvnRevision) else self._cwd / entry["path"]
        if diff:
            if isinstance(entry, _SvnRevision):
                opened = _open_diff_previous(self._api, file_path, source=entry)
            else:
                opened = _open_diff(self._api, file_path)
            if opened:
                self._api.ui.blur_sidebar()
        elif file_path.exists():
            with contextlib.suppress(Exception):
                self._api.open_buffer(file_path)
                self._api.ui.blur_sidebar()


# ---------------------------------------------------------------------------
# Sign management
# ---------------------------------------------------------------------------


def _update_signs(api: Any, buf: Any) -> None:
    _vcs.update_signs(api, buf, _NAMESPACE, _HUNK_TYPE_TO_SIGN, _safe_get_hunks)


# ---------------------------------------------------------------------------
# Diff view
# ---------------------------------------------------------------------------


def _diff_path(api: Any, file_path: Path | None) -> Path | None:
    if file_path is None:
        path = getattr(api.active_buffer(), "path", None)
        if path is None:
            api.ui.notify("SVN diff: buffer has no file path", level="warn")
            return None
        file_path = Path(path)
    return file_path.resolve()


def _snapshot(
    api: Any, root: Path, file_path: Path, bucket: str, text: str, source: _SvnRevision | None = None
) -> Path:
    global _diff_temp_dir
    if _diff_temp_dir is None:
        _diff_temp_dir = tempfile.TemporaryDirectory(prefix="peovim_svn_")
    relative = file_path.relative_to(root)
    root_key = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    if source is not None:
        bucket += "-" + hashlib.sha256(source.url.encode("utf-8")).hexdigest()[:12]
    path = Path(_diff_temp_dir.name) / root_key / bucket / relative
    atomic_write_text(path, text, encoding="utf-8")
    if source is not None:
        _revision_sources[path.resolve()] = source
    _refresh_loaded_document(api, path)
    return path


def _launch_diff(api: Any, left: Path, right: Path) -> bool:
    _refresh_loaded_document(api, right, if_clean=True)
    if _count_diff_listeners(api):
        api.events.emit("diff_selection_ready", left=str(left), right=str(right))
    else:
        _open_manual_diff(api, left, right)
    return True


def _open_diff(api: Any, file_path: Path | None = None, *, source: _SvnRevision | None = None) -> bool:
    """Compare a selected revision (BASE by default) against the working file."""
    file_path = _diff_path(api, file_path)
    if file_path is None:
        return False
    source = source or _revision_sources.get(file_path)
    if source is not None:
        file_path = source.file_path
    root = source.root if source else _find_svn_root(file_path)
    if root is None:
        api.ui.notify("Not in an SVN working copy", level="warn")
        return False
    if file_path.is_dir():
        api.ui.notify("SVN diff requires a file, not a directory", level="warn")
        return False
    try:
        if source is not None:
            original = None if source.action == "D" else _revision_content(source.url, source.revision)
            bucket = f"r{source.revision}"
        else:
            original = _get_original_content(file_path)
            if original is None:
                info = _svn_info(file_path)
                if info.findtext("wc-info/schedule") != "add":
                    raise ValueError("File is not under version control or has no BASE")
            bucket = "base"
            # Keep the source of BASE snapshots so commands also work when a
            # historical pane is the active buffer. This reads local metadata.
            with contextlib.suppress(Exception):
                source = _working_revision(file_path, root)
        left = _snapshot(api, root, file_path, bucket, original or "", source)
        right = file_path if file_path.exists() else _snapshot(api, root, file_path, "working", "", source)
        return _launch_diff(api, left, right)
    except Exception as exc:
        api.ui.notify(f"SVN diff failed: {exc}", level="error")
        return False


def _open_diff_previous(api: Any, file_path: Path | None = None, *, source: _SvnRevision | None = None) -> bool:
    """Compare a selected revision (BASE by default) with its predecessor."""
    file_path = _diff_path(api, file_path)
    if file_path is None:
        return False
    source = source or _revision_sources.get(file_path)
    if source is None:
        root = _find_svn_root(file_path)
        if root is None:
            api.ui.notify("Not in an SVN working copy", level="warn")
            return False
        try:
            source = _working_revision(file_path, root)
        except Exception as exc:
            api.ui.notify(f"SVN diff failed: {exc}", level="error")
            return False
    try:
        if not source.action:
            source = _revision_details(source)
        previous_url = source.copyfrom_url if source.action == "A" and source.copyfrom_url else source.url
        previous_revision = (
            source.copyfrom_revision
            if source.action == "A" and source.copyfrom_revision is not None
            else source.revision - 1
        )
        previous = (
            None
            if source.action == "A" and not source.copyfrom_url
            else _revision_content(previous_url, previous_revision)
        )
        current = None if source.action == "D" else _revision_content(source.url, source.revision)
        left_source = _SvnRevision(source.root, source.file_path, previous_url, previous_revision)
        left = _snapshot(
            api, source.root, source.file_path, f"r{previous_revision}-previous", previous or "", left_source
        )
        right = _snapshot(api, source.root, source.file_path, f"r{source.revision}", current or "", source)
        return _launch_diff(api, left, right)
    except Exception as exc:
        api.ui.notify(f"SVN diff failed: {exc}", level="error")
        return False


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
    api.commands.register(
        "SvnDiff", lambda cmd, ctx: _open_diff(api, Path(cmd.args.strip()) if cmd.args.strip() else None)
    )
    api.commands.register(
        "SvnDiffWorking", lambda cmd, ctx: _open_diff(api, Path(cmd.args.strip()) if cmd.args.strip() else None)
    )
    api.commands.register(
        "SvnDiffPrevious",
        lambda cmd, ctx: _open_diff_previous(api, Path(cmd.args.strip()) if cmd.args.strip() else None),
    )
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

    api.keymap.define_plug("SvnsignsDiffWorking", lambda: _open_diff(api), desc="SVN: diff BASE vs working file")
    api.keymap.define_plug("SvnsignsDiffPrevious", lambda: _open_diff_previous(api), desc="SVN: diff BASE vs previous")
    api.keymap.define_plug(
        "SvnsignsPanelDiffWorking", lambda: _panel_diff(api, previous=False), desc="SVN: diff revision vs working file"
    )
    api.keymap.define_plug(
        "SvnsignsPanelDiffPrevious", lambda: _panel_diff(api, previous=True), desc="SVN: diff revision vs previous"
    )
    # Scoped defaults supersede global defaults in the keymap registry. Restore
    # Git's defaults after these variants if Git/explorer already installed them.
    restore = []
    for keys, panel_plug, git_plug in (
        ("<leader>dw", "SvnsignsPanelDiffWorking", "GitsignsDiffHead"),
        ("<leader>dp", "SvnsignsPanelDiffPrevious", "GitsignsDiffPrevious"),
    ):
        if keys in api.keymap.find_keys_for_plug(git_plug):
            restore.append((keys, git_plug))
        description = "SVN: diff vs working file" if keys.endswith("dw") else "SVN: diff vs previous revision"
        api.keymap.nmap(keys, f"<Plug>{panel_plug}", desc=description, scope="svn-status")
    for keys, plug in restore:
        description = "Diff working file vs HEAD" if keys.endswith("dw") else "Diff HEAD vs parent"
        api.keymap.nmap(keys, f"<Plug>{plug}", desc=description)

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
    _revision_sources.clear()
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


def _panel_diff(api: Any, *, previous: bool) -> None:
    panel = api.ui.get_sidebar_panel("svn-status")
    if panel is not None:
        panel.diff_selected(previous=previous)
