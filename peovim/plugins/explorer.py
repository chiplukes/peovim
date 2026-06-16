"""
plugins.explorer — File explorer tree view

Registers <leader>e and :Explorer command to toggle a file tree panel.
Uses ui.open_tree() with lazy directory expansion.
"""

from __future__ import annotations

import os
import pathlib
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from peovim.git import color_for_status_entry, marker_for_status_entry
from peovim.ui.cell_grid import CellGrid

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI
    from peovim.commands.parser import ParsedCommand
    from peovim.ui.tree_view import TreeNode

_controller: _ExplorerController | None = None


@dataclass(frozen=True)
class _ExplorerStatusAggregate:
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


class _ExplorerSidebarPanel:
    """Explorer sidebar with a one-line operations hint above the tree."""

    _HINT = "c-py  C-ut  p-st  r-en  d-el"

    def __init__(self, tree, *, width: int = 30) -> None:
        self.tree = tree
        self.width = width

    def render(self, grid: CellGrid) -> None:
        self.tree.focused = getattr(self, "_sidebar_focused", False)
        self.tree.blink_on = getattr(self, "_sidebar_blink_on", True)
        hint = self._HINT[: grid.width].ljust(grid.width)
        grid.fill(0, 0, grid.width)
        grid.write_str(0, 0, hint, fg=(140, 140, 160))
        if grid.height <= 1:
            return
        tree_grid = CellGrid(grid.width, grid.height - 1)
        self.tree._width = grid.width
        self.tree.render(tree_grid)
        grid.blit(tree_grid, 0, 1)

    def feed_key(self, key: str) -> bool:
        self.tree.feed_key(key)
        return True

    def on_focus(self) -> None:
        self.tree.focused = True

    def on_blur(self) -> None:
        self.tree.focused = False


class _ExplorerController:  # cm:1f4c6a
    def __init__(self, api: EditorAPI) -> None:
        self._api = api
        self._root = api.find_root() or pathlib.Path.cwd()
        self._panel: _ExplorerSidebarPanel | None = None
        self._pending_create_dir: pathlib.Path | None = None
        self._pending_rename_path: pathlib.Path | None = None
        self._pending_delete_path: pathlib.Path | None = None
        self._clipboard_path: pathlib.Path | None = None
        self._clipboard_mode: str | None = None
        self._pending_copy_source: pathlib.Path | None = None
        self._pending_copy_destination_dir: pathlib.Path | None = None

    def toggle(self) -> None:
        self._root = self._api.find_root() or pathlib.Path.cwd()

        if self._panel is None:
            from peovim.ui.tree_view import TreeView

            tree = TreeView(
                _make_nodes(self._root, status_map=_combined_status_map(self._api, self._root)),
                title="Explorer",
                width=30,
                on_select=self._open_selected,
                on_key=self._on_tree_key,
            )
            self._panel = _ExplorerSidebarPanel(tree, width=30)
            self._api.ui.show_sidebar_panel("explorer", self._panel, focus=True)
            return

        self._api.ui.register_sidebar_panel("explorer", self._panel)

        if self._api.ui.is_sidebar_visible():
            self._api.ui.hide_sidebar()
            return

        if self._api.ui.active_sidebar_panel_name() == "explorer":
            self.refresh()

        if self._api.ui.show_active_sidebar_panel(focus=True) is not None:
            return

        self.refresh()
        self._api.ui.show_sidebar_panel("explorer", self._panel, focus=True)

    def command_create(self, cmd: ParsedCommand) -> None:
        raw = cmd.args.strip()
        base_dir = self._pending_create_dir or self._root
        self._pending_create_dir = None
        if not raw:
            _set_status(self._api, "ExplorerCreate expects a file or directory name")
            return
        target, is_dir = _resolve_created_path(base_dir, raw)
        if target.exists():
            _set_status(self._api, f"Already exists: {target}")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_dir:
            target.mkdir(parents=True, exist_ok=False)
            self.refresh(selected_path=target)
            _set_status(self._api, f"Created directory: {target.name}")
            return
        target.touch(exist_ok=False)
        self.refresh(selected_path=target)
        self._api.open_buffer(target)
        self._api.ui.blur_sidebar()
        _set_status(self._api, f"Created file: {target.name}")

    def command_rename(self, cmd: ParsedCommand) -> None:
        raw = cmd.args.strip()
        target = self._pending_rename_path
        self._pending_rename_path = None
        if target is None or not target.exists():
            _set_status(self._api, "No explorer entry selected for rename")
            return
        if not raw:
            _set_status(self._api, "ExplorerRename expects a new name")
            return
        destination = _resolve_rename_target(target, raw)
        if destination.exists() and destination != target:
            _set_status(self._api, f"Already exists: {destination}")
            return
        target.rename(destination)
        self.refresh(selected_path=destination)
        _set_status(self._api, f"Renamed to: {destination.name}")

    def command_delete(self, _cmd: ParsedCommand) -> None:
        target = self._pending_delete_path
        self._pending_delete_path = None
        if target is None or not target.exists():
            _set_status(self._api, "No explorer entry selected for delete")
            return
        select_after = target.parent if target.parent != target else self._root
        if target.is_dir():
            shutil.rmtree(target)
            self.refresh(selected_path=select_after)
            _set_status(self._api, f"Deleted directory: {target.name}")
            return
        target.unlink()
        self.refresh(selected_path=select_after)
        _set_status(self._api, f"Deleted file: {target.name}")

    def command_copy_as(self, cmd: ParsedCommand) -> None:
        raw = cmd.args.strip()
        source = self._pending_copy_source
        destination_dir = self._pending_copy_destination_dir
        self._pending_copy_source = None
        self._pending_copy_destination_dir = None
        if source is None or destination_dir is None or not source.exists():
            _set_status(self._api, "No explorer copy is waiting for rename")
            return
        if not raw:
            _set_status(self._api, "ExplorerCopyAs expects a new name")
            return
        destination = _resolve_rename_target(destination_dir / source.name, raw)
        if destination.exists():
            _set_status(self._api, f"Already exists: {destination}")
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination)
            _set_status(self._api, f"Copied directory as: {destination.name}")
        else:
            shutil.copy2(source, destination)
            _set_status(self._api, f"Copied file as: {destination.name}")
        self.refresh(selected_path=destination)

    def refresh(self, *, selected_path: pathlib.Path | None = None) -> None:
        if self._panel is None:
            return
        tree = self._panel.tree
        expanded = _expanded_paths(tree)
        current = tree.selected_node
        if selected_path is None and current is not None and current.value is not None:
            selected_path = pathlib.Path(str(current.value))
        tree.set_nodes(
            _make_nodes(self._root, expanded_paths=expanded, status_map=_combined_status_map(self._api, self._root))
        )
        if selected_path is not None:
            path = selected_path
            while True:
                if tree.select_value(str(path)):
                    break
                if path == self._root or path.parent == path:
                    break
                path = path.parent

    def _open_selected(self, node: TreeNode) -> None:
        if node.value is None:
            return
        path = pathlib.Path(str(node.value))
        if path.is_dir():
            old_root = self._root
            self._root = path
            self.refresh(selected_path=old_root)
            return
        if path.is_file():
            self._api.open_buffer(path)
            self._api.ui.blur_sidebar()

    def _paste_into(self, target: pathlib.Path) -> bool:
        source = self._clipboard_path
        mode = self._clipboard_mode
        if source is None or mode not in {"copy", "move"}:
            _set_status(self._api, "Explorer clipboard is empty")
            return False
        if not source.exists():
            self._clipboard_path = None
            self._clipboard_mode = None
            _set_status(self._api, "Clipboard source no longer exists")
            return False

        destination_dir = target if target.is_dir() else target.parent
        destination = destination_dir / source.name
        if destination.resolve() == source.resolve():
            if mode == "copy":
                self._pending_copy_source = source
                self._pending_copy_destination_dir = destination_dir
                suggestion = _suggest_copy_name(source.name)
                self._api.open_cmdline(f"ExplorerCopyAs {suggestion}")
                _set_status(self._api, f"Rename copied {source.name}:")
                return True
            _set_status(self._api, "Source and destination are the same")
            return False
        if destination.exists():
            _set_status(self._api, f"Already exists: {destination}")
            return False

        destination.parent.mkdir(parents=True, exist_ok=True)
        if mode == "copy":
            if source.is_dir():
                shutil.copytree(source, destination)
                _set_status(self._api, f"Copied directory: {source.name}")
            else:
                shutil.copy2(source, destination)
                _set_status(self._api, f"Copied file: {source.name}")
        else:
            source.rename(destination)
            self._clipboard_path = destination
            _set_status(self._api, f"Moved to: {destination}")

        self.refresh(selected_path=destination)
        return True

    def _on_tree_key(self, key: str, node: TreeNode | None) -> bool:
        if node is None or node.value is None:
            return False
        if node.label == "..":
            return False  # No file ops on the parent-navigation entry
        path = pathlib.Path(str(node.value))
        if key == "a":
            self._pending_create_dir = path if path.is_dir() else path.parent
            self._api.open_cmdline("ExplorerCreate ")
            return True
        if key == "r":
            self._pending_rename_path = path
            self._api.open_cmdline(f"ExplorerRename {path.name}")
            return True
        if key == "d":
            self._pending_delete_path = path
            _set_status(self._api, f"Delete {path.name}? Press Enter to confirm or Esc to cancel")
            self._api.open_cmdline("ExplorerDelete")
            return True
        if key == "c":
            self._clipboard_path = path
            self._clipboard_mode = "copy"
            _set_status(self._api, f"Copied {path.name} to explorer clipboard")
            return True
        if key == "C":
            self._clipboard_path = path
            self._clipboard_mode = "move"
            _set_status(self._api, f"Marked {path.name} to move")
            return True
        if key == "p":
            return self._paste_into(path)
        return False


def setup(api: EditorAPI) -> None:
    """Register explorer keybindings and commands."""
    global _controller
    _controller = _ExplorerController(api)

    api.keymap.define_plug("ExplorerToggle", _controller.toggle, desc="Explorer: toggle file tree")
    api.keymap.nmap("<leader>e", "<Plug>ExplorerToggle", desc="Explorer: toggle file tree")
    api.commands.register("Explorer", lambda cmd, ctx: _controller.toggle(), min_abbrev=3)
    api.commands.register("ExplorerCreate", lambda cmd, ctx: _controller.command_create(cmd), min_abbrev=12)
    api.commands.register("ExplorerCopyAs", lambda cmd, ctx: _controller.command_copy_as(cmd), min_abbrev=12)
    api.commands.register("ExplorerRename", lambda cmd, ctx: _controller.command_rename(cmd), min_abbrev=12)
    api.commands.register("ExplorerDelete", lambda cmd, ctx: _controller.command_delete(cmd), min_abbrev=12)


def _make_nodes(
    path: pathlib.Path,
    *,
    expanded_paths: set[str] | None = None,
    status_map: dict[str, object] | None = None,
) -> list:
    """Build tree nodes for a directory, sorted dirs-first then files."""
    from peovim.ui.tree_view import TreeNode

    expanded_paths = expanded_paths or set()
    status_map = status_map or {}

    try:
        entries = list(os.scandir(path))
    except (PermissionError, OSError):
        return []

    # Sort: directories first, then files, both alphabetically
    dirs = sorted([e for e in entries if e.is_dir()], key=lambda e: e.name.lower())
    files = sorted([e for e in entries if e.is_file()], key=lambda e: e.name.lower())

    nodes = []

    # Add ".." entry for navigating to parent directory
    parent = path.parent
    if parent != path:
        nodes.append(TreeNode(label="..", value=str(parent), fg=(140, 140, 160)))

    for entry in dirs:
        entry_path = pathlib.Path(entry.path)
        status_entry = status_map.get(str(entry_path.resolve()))
        node = TreeNode(
            label=_node_label(entry.name, str(entry_path), status_map),
            value=str(entry_path),
            fg=color_for_status_entry(status_entry, surface="explorer") if status_entry is not None else None,
            children_fn=lambda p=entry_path, expanded=expanded_paths, statuses=status_map: _make_nodes(
                p, expanded_paths=expanded, status_map=statuses
            ),
        )
        node.expanded = str(entry_path) in expanded_paths
        nodes.append(node)

    for entry in files:
        entry_path = pathlib.Path(entry.path)
        status_entry = status_map.get(str(entry_path.resolve()))
        node = TreeNode(
            label=_node_label(entry.name, entry.path, status_map),
            value=str(entry.path),
            fg=color_for_status_entry(status_entry, surface="explorer") if status_entry is not None else None,
            children_fn=None,
        )
        nodes.append(node)

    return nodes


def _expanded_paths(tree) -> set[str]:
    expanded: set[str] = set()

    def _walk(nodes: list[TreeNode]) -> None:
        for node in nodes:
            if node.expanded and node.value is not None:
                expanded.add(str(node.value))
                _walk(node.get_children())

    _walk(tree._roots)
    return expanded


def _resolve_created_path(base_dir: pathlib.Path, raw: str) -> tuple[pathlib.Path, bool]:
    is_dir = raw.endswith(("/", "\\"))
    clean = raw.rstrip("/\\")
    target = pathlib.Path(clean)
    if not target.is_absolute():
        target = base_dir / target
    return target, is_dir


def _resolve_rename_target(target: pathlib.Path, raw: str) -> pathlib.Path:
    candidate = pathlib.Path(raw)
    if candidate.is_absolute():
        return candidate
    if candidate.parent != pathlib.Path("."):
        return (target.parent / candidate).resolve()
    return target.with_name(raw)


def _suggest_copy_name(name: str) -> str:
    path = pathlib.Path(name)
    if path.suffix:
        return f"{path.stem} copy{path.suffix}"
    return f"{path.name} copy"


def _set_status(api: EditorAPI, message: str) -> None:
    api.set_status(message)


def _git_status_map(api: EditorAPI, root: pathlib.Path) -> dict[str, object]:
    repo_root = api.git.root(root)
    if repo_root is None:
        return {}
    status_map: dict[str, object] = {}
    try:
        for entry in api.git.status_entries(repo_root):
            target = (repo_root / entry.path).resolve()
            marker = marker_for_status_entry(entry)
            if marker:
                status_map[str(target)] = _aggregate_status(None, entry)
                _propagate_status_to_parents(status_map, target.parent, repo_root, entry)
    except Exception:
        return {}
    return status_map


def _svn_status_map(root: pathlib.Path) -> dict[str, object]:
    try:
        from peovim.plugins.svnsigns import get_svn_status_map

        return get_svn_status_map(root)
    except Exception:
        return {}


def _combined_status_map(api: EditorAPI, root: pathlib.Path) -> dict[str, object]:
    git = _git_status_map(api, root)
    svn = _svn_status_map(root)
    return {**git, **svn}


def _propagate_status_to_parents(
    status_map: dict[str, object],
    start: pathlib.Path,
    stop: pathlib.Path,
    entry: object,
) -> None:
    current = start.resolve()
    stop = stop.resolve()
    while True:
        status_map[str(current)] = _aggregate_status(status_map.get(str(current)), entry)
        if current == stop or current.parent == current:
            break
        current = current.parent


def _aggregate_status(existing: object, candidate: object) -> _ExplorerStatusAggregate:
    base = existing if isinstance(existing, _ExplorerStatusAggregate) else _aggregate_from_entry(existing)
    incoming = _aggregate_from_entry(candidate)
    yellow = (
        base.modified or base.staged or base.conflicted or incoming.modified or incoming.staged or incoming.conflicted
    )
    green = base.untracked or incoming.untracked
    return _ExplorerStatusAggregate(
        path=base.path or incoming.path,
        code="~" if yellow else ("??" if green else (base.code or incoming.code)),
        index_status="A" if base.index_status == "A" or incoming.index_status == "A" else " ",
        worktree_status="M" if yellow else (base.worktree_status or incoming.worktree_status),
        modified=yellow,
        staged=base.staged or incoming.staged,
        conflicted=base.conflicted or incoming.conflicted,
        deleted=base.deleted or incoming.deleted,
        untracked=green,
        mixed=yellow and green,
    )


def _aggregate_from_entry(entry: object) -> _ExplorerStatusAggregate:
    if isinstance(entry, _ExplorerStatusAggregate):
        return entry
    if entry is None:
        return _ExplorerStatusAggregate()
    return _ExplorerStatusAggregate(
        path=str(getattr(entry, "path", "")),
        code=getattr(entry, "code", ""),
        index_status=getattr(entry, "index_status", " "),
        worktree_status=getattr(entry, "worktree_status", " "),
        modified=bool(getattr(entry, "modified", False)),
        staged=bool(getattr(entry, "staged", False)),
        conflicted=bool(getattr(entry, "conflicted", False)),
        deleted=bool(getattr(entry, "deleted", False)),
        untracked=bool(getattr(entry, "untracked", False)),
        mixed=bool(getattr(entry, "mixed", False)),
    )


def _status_marker(status: str) -> str:
    from peovim.git.repository import GitStatusEntry

    entry = GitStatusEntry(
        code=(status or "").strip(),
        path="",
        index_status=((status or "  ") + "  ")[0],
        worktree_status=((status or "  ") + "  ")[1],
    )
    return marker_for_status_entry(entry)


def _node_label(name: str, path: str, status_map: dict[str, object]) -> str:
    entry = status_map.get(str(pathlib.Path(path).resolve()))
    marker = marker_for_status_entry(entry) if entry is not None else ""
    if not marker:
        return name
    return f"{marker} {name}"
