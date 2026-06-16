"""Named marker groups with gutter signs and a sidebar viewer."""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import portalocker

from peovim.core.persistence import atomic_write_text
from peovim.core.text_edits import transform_position
from peovim.ui.cell_grid import CellGrid

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI
    from peovim.commands.parser import ParsedCommand
    from peovim.ui.tree_view import TreeNode

_NAMESPACE = "markers"
_ANNOTATION_NAMESPACE = "markers.annotation"
_SIGN_TYPE = "markers.bookmark"
_PANEL_NAME = "markers"
_DEFAULT_GROUP = "default"
_SIGN_CHAR = "●"
_SIGN_COLOR = (220, 180, 90)
_controller: _MarkersController | None = None
_log = logging.getLogger("peovim.markers")


@dataclass(frozen=True)
class _GroupItem:
    name: str
    count: int
    active: bool = False

    def __str__(self) -> str:
        prefix = "* " if self.active else "  "
        suffix = f" ({self.count})"
        return f"{prefix}{self.name}{suffix}"


class MarkerStore:  # cm:9a7f2c
    """Persistent named marker groups backed by `StoreAPI`."""

    def __init__(self, api: Any) -> None:
        self._api = api
        self._global_store = api.store.get_store("markers")
        self._path_stores: dict[str, _PathStore] = {}
        self._root_cache: dict[str, Path | None] = {}  # file path → project root (cached per session)
        self._ensure_defaults(self._backend())

    def storage_path(self, path: str | Path | None = None) -> Path:
        store = self._backend(path)
        if hasattr(store, "path"):
            return Path(store.path)
        return Path(store._path)

    def active_group(self) -> str:
        store = self._backend()
        self._ensure_defaults(store)
        return str(store.get("active_group", _DEFAULT_GROUP))

    def set_active_group(self, name: str) -> bool:
        store = self._backend()
        groups = self._groups(store)
        if name not in groups:
            return False
        store.set("active_group", name)
        return True

    def group_names(self) -> list[str]:
        store = self._backend()
        groups = self._groups(store)
        active = str(store.get("active_group", _DEFAULT_GROUP))
        names = sorted(groups)
        if active in names:
            names.remove(active)
            names.insert(0, active)
        return names

    def create_group(self, name: str) -> bool:
        clean = name.strip()
        if not clean:
            return False
        store = self._backend()
        groups = self._groups(store)
        if clean in groups:
            return False
        groups[clean] = []
        self._save_groups(store, groups)
        store.set("active_group", clean)
        return True

    def rename_group(self, old_name: str, new_name: str) -> bool:
        old_clean = old_name.strip()
        new_clean = new_name.strip()
        store = self._backend()
        groups = self._groups(store)
        if not old_clean or old_clean not in groups or not new_clean:
            return False
        if old_clean == new_clean:
            return True
        if new_clean in groups:
            return False
        groups[new_clean] = groups.pop(old_clean)
        self._save_groups(store, groups)
        if str(store.get("active_group", _DEFAULT_GROUP)) == old_clean:
            store.set("active_group", new_clean)
        return True

    def delete_group(self, name: str) -> bool:
        clean = name.strip()
        store = self._backend()
        groups = self._groups(store)
        if clean not in groups or len(groups) <= 1:
            return False
        groups.pop(clean)
        self._save_groups(store, groups)
        if str(store.get("active_group", _DEFAULT_GROUP)) == clean:
            store.set("active_group", sorted(groups)[0])
        return True

    def markers(self, group: str | None = None, *, path: str | Path | None = None) -> list[dict[str, Any]]:
        store = self._backend(path)
        groups = self._groups(store)
        target = group or str(store.get("active_group", _DEFAULT_GROUP))
        return [dict(marker) for marker in groups.get(target, [])]

    def all_markers(self, *, path: str | Path | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        store = self._backend(path)
        groups = self._groups(store)
        active = str(store.get("active_group", _DEFAULT_GROUP))
        names = sorted(groups)
        if active in names:
            names.remove(active)
            names.insert(0, active)
        for group in names:
            for marker in self.markers(group, path=path):
                result.append({**marker, "group": group})
        return result

    def marker_at(
        self,
        group: str,
        path: str | Path,
        line: int,
        *,
        col: int | None = None,
    ) -> dict[str, Any] | None:
        clean_path = str(Path(path).resolve())
        for marker in self.markers(group, path=path):
            if marker.get("path") != clean_path or int(marker.get("line", -1)) != line:
                continue
            if col is not None and int(marker.get("col", 0)) != col:
                continue
            return dict(marker)
        return None

    def add_marker(self, group: str, path: str, line: int, col: int, annotation: str | None = None) -> None:
        store = self._backend(path)
        groups = self._groups(store)
        markers = groups.setdefault(group, [])
        clean_path = str(Path(path).resolve())
        for marker in markers:
            if marker.get("path") == clean_path and int(marker.get("line", -1)) == line:
                marker["col"] = col
                if annotation is not None:
                    marker["annotation"] = annotation
                self._save_groups(store, groups)
                return
        markers.append({"path": clean_path, "line": line, "col": col, "annotation": annotation or ""})
        markers.sort(key=lambda item: (str(item.get("path", "")), int(item.get("line", 0)), int(item.get("col", 0))))
        self._save_groups(store, groups)

    def delete_marker(self, group: str, path: str, line: int, col: int | None = None) -> bool:
        store = self._backend(path)
        groups = self._groups(store)
        markers = groups.get(group, [])
        clean_path = str(Path(path).resolve())
        kept = []
        removed = False
        for marker in markers:
            same_line = marker.get("path") == clean_path and int(marker.get("line", -1)) == line
            same_col = col is None or int(marker.get("col", 0)) == col
            if same_line and same_col and not removed:
                removed = True
                continue
            kept.append(marker)
        if not removed:
            return False
        groups[group] = kept
        self._save_groups(store, groups)
        return True

    def apply_text_change(
        self,
        path: str | Path,
        *,
        start_line: int,
        start_col: int,
        end_line: int,
        end_col: int,
        new_text: str,
    ) -> bool:
        store = self._backend(path)
        groups = self._groups(store)
        clean_path = str(Path(path).resolve())
        changed = False
        for group_name, markers in groups.items():
            updated: list[dict[str, Any]] = []
            for marker in markers:
                if marker.get("path") != clean_path:
                    updated.append(dict(marker))
                    continue
                line = int(marker.get("line", 0))
                col = int(marker.get("col", 0))
                new_line, new_col = transform_position(
                    line,
                    col,
                    start_line=start_line,
                    start_col=start_col,
                    end_line=end_line,
                    end_col=end_col,
                    new_text=new_text,
                )
                updated_marker = dict(marker)
                updated_marker["line"] = new_line
                updated_marker["col"] = new_col
                if new_line != line or new_col != col:
                    changed = True
                updated.append(updated_marker)
            updated.sort(
                key=lambda item: (str(item.get("path", "")), int(item.get("line", 0)), int(item.get("col", 0)))
            )
            groups[group_name] = _dedupe_markers(updated)
        if changed:
            self._save_groups(store, groups)
        return changed

    def _ensure_defaults(self, store: Any) -> None:
        groups = store.get("groups", {})
        if not isinstance(groups, dict) or not groups:
            groups = {_DEFAULT_GROUP: []}
            store.set("groups", groups)
        active = store.get("active_group", _DEFAULT_GROUP)
        if active not in groups:
            store.set("active_group", sorted(groups)[0])

    def _groups(self, store: Any) -> dict[str, list[dict[str, Any]]]:
        self._ensure_defaults(store)
        raw = store.get("groups", {})
        groups: dict[str, list[dict[str, Any]]] = {}
        if not isinstance(raw, dict):
            return {_DEFAULT_GROUP: []}
        for name, markers in raw.items():
            if not isinstance(name, str):
                continue
            normalized: list[dict[str, Any]] = []
            if isinstance(markers, list):
                for marker in markers:
                    if not isinstance(marker, dict) or "path" not in marker or "line" not in marker:
                        continue
                    normalized.append(
                        {
                            "path": str(Path(str(marker.get("path", ""))).resolve()),
                            "line": int(marker.get("line", 0)),
                            "col": int(marker.get("col", 0)),
                            "annotation": str(marker.get("annotation", "")),
                        }
                    )
            groups[name] = normalized
        if not groups:
            groups[_DEFAULT_GROUP] = []
            self._save_groups(store, groups)
        return groups

    def _save_groups(self, store: Any, groups: dict[str, list[dict[str, Any]]]) -> None:
        store.set("groups", groups)

    def _backend(self, path: str | Path | None = None) -> Any:
        if path is not None:
            cache_key = str(Path(path).resolve())
            if cache_key not in self._root_cache:
                self._root_cache[cache_key] = _project_root(self._api, path)
            root = self._root_cache[cache_key]
        else:
            # Resolve active file path and cache by it to avoid repeated filesystem walks
            buf = self._api.active_buffer()
            active_path = getattr(buf, "path", None)
            if active_path is not None:
                cache_key = str(Path(active_path).resolve())
                if cache_key not in self._root_cache:
                    self._root_cache[cache_key] = _project_root(self._api, None)
                root = self._root_cache[cache_key]
            else:
                root = _project_root(self._api, None)
        if root is None:
            return self._global_store
        key = str(root.resolve())
        if key not in self._path_stores:
            self._path_stores[key] = _PathStore(root / ".peovim" / "markers.json")
        return self._path_stores[key]


class _PathStore:
    """Minimal JSON-backed key-value store for a specific file path."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] | None = None

    def get(self, key: str, default: Any = None) -> Any:
        self._load()
        assert self._data is not None
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._load()
        assert self._data is not None
        self._data[key] = value
        self._save()

    def _load(self) -> None:
        if self._data is not None:
            return
        if not self.path.exists():
            self._data = {}
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                self._data = json.load(handle)
        except Exception:
            self._data = {}

    def _save(self) -> None:
        # save policy: lock-protected merge-write — multiple editor instances share
        # markers.json; lock prevents torn writes, re-read merges concurrent additions.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(".json.lock")
        try:
            try:
                lock_flags = portalocker.LOCK_EX | portalocker.LOCK_NB
                with portalocker.Lock(str(lock_path), flags=lock_flags, timeout=3, fail_when_locked=True):
                    self._merge_from_disk()
                    atomic_write_text(self.path, json.dumps(self._data, indent=2), encoding="utf-8")
            except (portalocker.LockException, portalocker.AlreadyLocked):
                _log.warning("markers: could not acquire lock — writing without merge")
                atomic_write_text(self.path, json.dumps(self._data, indent=2), encoding="utf-8")
            except AttributeError:
                with portalocker.Lock(str(lock_path), flags=portalocker.LOCK_EX):
                    self._merge_from_disk()
                    atomic_write_text(self.path, json.dumps(self._data, indent=2), encoding="utf-8")
        except Exception as exc:
            _log.warning("markers: write failed (%s)", exc)
            with contextlib.suppress(Exception):
                atomic_write_text(self.path, json.dumps(self._data, indent=2), encoding="utf-8")

    def _merge_from_disk(self) -> None:
        """Read on-disk state and merge group names absent from in-memory state."""
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                disk: dict[str, Any] = json.load(handle)
        except Exception:
            return
        assert self._data is not None
        disk_groups = disk.get("groups", {})
        if not isinstance(disk_groups, dict):
            return
        memory_groups = self._data.get("groups", {})
        if not isinstance(memory_groups, dict):
            return
        for group_name, markers in disk_groups.items():
            if group_name not in memory_groups:
                memory_groups[group_name] = markers
        self._data["groups"] = memory_groups


class _MarkersSidebarPanel:
    _HINT = "g-o  e-dit"

    def __init__(self, api: Any, controller: _MarkersController, *, width: int = 38) -> None:
        from peovim.ui.tree_view import TreeView

        self._api = api
        self._controller = controller
        self.width = width
        self._tree = TreeView(
            [],
            title="Markers",
            on_select=self._on_select,
            on_cursor_move=self._on_cursor_move,
            on_key=self._on_key,
            width=width,
        )

    @property
    def tree(self):
        return self._tree

    def render(self, grid: Any) -> None:
        focused = getattr(self, "_sidebar_focused", False)
        blink_on = getattr(self, "_sidebar_blink_on", True)
        self._tree.focused = focused
        self._tree.blink_on = blink_on
        hint = self._HINT[: grid.width].ljust(grid.width)
        grid.fill(0, 0, grid.width)
        grid.write_str(0, 0, hint, fg=(140, 140, 160))
        if grid.height <= 1:
            return
        tree_grid = CellGrid(grid.width, grid.height - 1)
        self._tree._width = grid.width
        self._tree.render(tree_grid)
        grid.blit(tree_grid, 0, 1)

    def feed_key(self, key: str) -> bool:
        if key == "R":
            self.refresh()
            return True
        self._tree.feed_key(key)
        return True

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._tree._title = f"Markers [{self._controller.store.active_group()}]"
        self._tree.set_nodes(self._controller.build_nodes(_expanded_values(self._tree)))

    def cursor_row(self, panel_height: int) -> int | None:
        """Return the panel-local row for the terminal cursor (hint row + tree row)."""
        tree_height = panel_height - 1  # row 0 is the hint bar
        if tree_height <= 0:
            return None
        tree_row = self._tree.cursor_row(tree_height)
        if tree_row is None:
            return None
        return 1 + tree_row

    def on_focus(self) -> None:
        self._tree.focused = True

    def on_blur(self) -> None:
        self._tree.focused = False

    def _on_select(self, node: Any) -> None:
        if not isinstance(node.value, tuple) or not node.value or node.value[0] != "marker":
            return
        _kind, path, line, col = node.value
        self._controller.jump_to_marker(Path(path), line, col)

    def _on_cursor_move(self, node: Any) -> None:
        """Preview the marker under the cursor without leaving the panel."""
        if not isinstance(node.value, tuple) or not node.value or node.value[0] != "marker":
            return
        _kind, path, line, col = node.value
        active_buf = self._api.active_buffer()
        active_path = str(active_buf.path) if active_buf and active_buf.path else None
        if active_path is not None and Path(path).resolve() == Path(active_path).resolve():
            win = self._api.active_window()
            win.set_cursor(line, col)
            win.scroll_to_cursor()
        else:
            self._api.open_buffer(Path(path), line, col)
        self._controller.refresh_annotation_ghost_text()

    def _on_key(self, key: str, node: Any) -> bool:
        if node is None:
            return False
        if not isinstance(node.value, tuple) or not node.value or node.value[0] != "marker":
            return False
        _kind, path, line, col = node.value
        if key == "g":
            self._controller.jump_to_marker(Path(path), line, col)
            return True
        if key == "e":
            self._controller.prompt_marker_text(Path(path), line, col)
            return True
        return False


class _MarkersController:
    def __init__(self, api: EditorAPI) -> None:
        self._api = api
        self.store = MarkerStore(api)
        self._panel: _MarkersSidebarPanel | None = None
        self._pending_annotation_target: tuple[Path, int, int] | None = None

    def add_marker(self) -> None:
        buf = self._api.active_buffer()
        if buf.path is None:
            _set_status(self._api, "Markers require a file-backed buffer")
            return
        line, col = self._api.active_window().cursor
        group = self.store.active_group()
        self.store.add_marker(group, str(buf.path), line, col)
        self._on_store_changed(selected_path=Path(buf.path), selected_line=line)
        _set_status(self._api, f"Added marker to {group}: {Path(buf.path).name}:{line + 1}")

    def next_marker(self) -> None:
        self._jump_marker(forward=True)

    def prev_marker(self) -> None:
        self._jump_marker(forward=False)

    def delete_marker(self) -> None:
        buf = self._api.active_buffer()
        if buf.path is None:
            _set_status(self._api, "Markers require a file-backed buffer")
            return
        line, col = self._api.active_window().cursor
        group = self.store.active_group()
        if not self.store.delete_marker(group, str(buf.path), line, col):
            _set_status(self._api, f"No marker at {Path(buf.path).name}:{line + 1} in {group}")
            return
        self._on_store_changed(selected_path=Path(buf.path), selected_line=line)
        _set_status(self._api, f"Deleted marker from {group}: {Path(buf.path).name}:{line + 1}")

    def toggle_panel(self) -> None:
        panel = self._get_panel()
        if self._api.ui.is_sidebar_visible(_PANEL_NAME):
            self._api.ui.hide_sidebar()
            return
        panel.refresh()
        self._api.ui.show_sidebar_panel(_PANEL_NAME, panel, focus=True)

    def prompt_marker_text(self, path: Path | None = None, line: int | None = None, col: int | None = None) -> None:
        buf = self._api.active_buffer()
        target_path = path or getattr(buf, "path", None)
        if target_path is None:
            _set_status(self._api, "Markers require a file-backed buffer")
            return
        if line is None or col is None:
            line, col = self._api.active_window().cursor
        target = Path(target_path)
        self._pending_annotation_target = (target, line, col)
        marker = self.store.marker_at(self.store.active_group(), str(target), line)
        initial = f"MarkerText {str(marker.get('annotation', ''))}" if marker is not None else "MarkerText "
        self._api.open_cmdline(initial)

    def prompt_create_group(self) -> None:
        self._api.open_cmdline("MarkerGroupCreate ")

    def prompt_select_group(self) -> None:
        items = [
            _GroupItem(name, len(self.store.markers(name)), name == self.store.active_group())
            for name in self.store.group_names()
        ]

        def _on_confirm(item: _GroupItem | None) -> None:
            if item is None:
                return
            self.select_group(item.name)

        self._api.ui.open_picker("Marker Groups", items, on_confirm=_on_confirm)

    def prompt_rename_group(self) -> None:
        self._api.open_cmdline(f"MarkerGroupRename {self.store.active_group()}")

    def prompt_delete_group(self) -> None:
        self._api.open_cmdline(f"MarkerGroupDelete {self.store.active_group()}")

    def command_group_create(self, cmd: ParsedCommand) -> None:
        name = cmd.args.strip()
        if not name:
            _set_status(self._api, "MarkerGroupCreate expects a group name")
            return
        if not self.store.create_group(name):
            _set_status(self._api, f"Group already exists: {name}")
            return
        self._on_store_changed()
        _set_status(self._api, f"Created marker group: {name}")

    def command_group_rename(self, cmd: ParsedCommand) -> None:
        old = self.store.active_group()
        new = cmd.args.strip()
        if not new:
            _set_status(self._api, "MarkerGroupRename expects a new name")
            return
        if not self.store.rename_group(old, new):
            _set_status(self._api, f"Cannot rename {old} to {new}")
            return
        self._on_store_changed()
        _set_status(self._api, f"Renamed marker group to: {new}")

    def command_group_delete(self, cmd: ParsedCommand) -> None:
        name = cmd.args.strip() or self.store.active_group()
        if not self.store.delete_group(name):
            _set_status(self._api, f"Cannot delete group: {name}")
            return
        self._on_store_changed()
        _set_status(self._api, f"Deleted marker group: {name}")

    def command_marker_text(self, cmd: ParsedCommand) -> None:
        pending = self._pending_annotation_target
        self._pending_annotation_target = None
        buf = self._api.active_buffer()
        path = pending[0] if pending is not None else getattr(buf, "path", None)
        if path is None:
            _set_status(self._api, "Markers require a file-backed buffer")
            return
        if pending is not None:
            _path, line, col = pending
        else:
            line, col = self._api.active_window().cursor
        group = self.store.active_group()
        annotation = cmd.args.strip()
        marker = self.store.marker_at(group, str(path), line)
        if not annotation and marker is None:
            _set_status(self._api, "MarkerText expects text or an existing marker")
            return
        self.store.add_marker(group, str(path), line, col, annotation)
        self._on_store_changed(selected_path=Path(path), selected_line=line)
        if annotation:
            _set_status(self._api, f"Updated marker text in {group}: {Path(path).name}:{line + 1}")
            return
        _set_status(self._api, f"Cleared marker text in {group}: {Path(path).name}:{line + 1}")

    def select_group(self, name: str) -> None:
        if not self.store.set_active_group(name):
            _set_status(self._api, f"Unknown marker group: {name}")
            return
        self._on_store_changed()
        _set_status(self._api, f"Active marker group: {name}")

    def refresh_annotation_ghost_text(self) -> None:
        active = self._api.active_buffer()
        for buf in self._api.list_buffers():
            buf.clear_ghost_text(_ANNOTATION_NAMESPACE)
        path = getattr(active, "path", None)
        if path is None:
            return
        line, col = self._api.active_window().cursor
        marker = self.store.marker_at(self.store.active_group(), str(path), line)
        if marker is None:
            return
        annotation = str(marker.get("annotation", "")).strip()
        if not annotation:
            return
        from peovim.core.style import Style

        line_text = active.get_line(line)
        display_col = max(len(line_text), col + 1)
        active.set_ghost_text(
            _ANNOTATION_NAMESPACE,
            line,
            display_col,
            f"  ← {annotation}",
            Style(fg=(150, 150, 150)),
        )

    def on_cursor_moved(self, **_kwargs: Any) -> None:
        self.refresh_annotation_ghost_text()

    def on_text_changed(self, **kwargs: Any) -> None:
        path = kwargs.get("path")
        if not path:
            return
        if not self.store.apply_text_change(
            path,
            start_line=int(kwargs.get("start_line", 0)),
            start_col=int(kwargs.get("start_col", 0)),
            end_line=int(kwargs.get("end_line", 0)),
            end_col=int(kwargs.get("end_col", 0)),
            new_text=str(kwargs.get("new_text", "")),
        ):
            return
        self._on_store_changed()

    def marker_list(self) -> list[dict[str, Any]]:
        group = self.store.active_group()
        markers = self.store.markers(group)
        markers.sort(key=lambda item: (str(item.get("path", "")), int(item.get("line", 0)), int(item.get("col", 0))))
        return markers

    def jump_to_marker(self, path: Path, line: int, col: int) -> None:
        self._api.goto_location(path, line, col)
        self._api.ui.blur_sidebar()

    def build_nodes(self, expanded_values: set[tuple] | None = None) -> list[TreeNode]:
        from peovim.ui.tree_view import TreeNode

        expanded_values = expanded_values or set()
        active = self.store.active_group()
        nodes: list[TreeNode] = []
        for group in self.store.group_names():
            markers = self.store.markers(group)
            child_nodes = [self._marker_node(marker) for marker in markers]
            value = ("group", group)
            label = f"* {group} ({len(markers)})" if group == active else f"{group} ({len(markers)})"
            node = TreeNode(label=label, value=value, children_fn=(lambda items=child_nodes: items))
            node._cached_children = child_nodes
            node.expanded = group == active or value in expanded_values
            nodes.append(node)
        return nodes

    def refresh_signs_for_open_buffers(self) -> None:
        for buf in self._api.list_buffers():
            self._apply_signs_to_buffer(buf)

    def on_buffer_opened(self, **kwargs: Any) -> None:
        buf_id = kwargs.get("buf_id")
        for buf in self._api.list_buffers():
            if buf.buf_id == buf_id:
                self._apply_signs_to_buffer(buf)
                break
        self.refresh_annotation_ghost_text()
        if self._api.ui.is_sidebar_visible(_PANEL_NAME):
            self._get_panel().refresh()

    def _apply_signs_to_buffer(self, buf: Any) -> None:
        buf.clear_namespace(_NAMESPACE)
        if buf.path is None:
            return
        target = str(Path(buf.path).resolve())
        seen: set[int] = set()
        for marker in self.store.all_markers(path=buf.path):
            if marker.get("path") != target:
                continue
            line = int(marker.get("line", 0))
            if line in seen:
                continue
            seen.add(line)
            buf.add_sign(_NAMESPACE, line, _SIGN_TYPE)

    def _marker_node(self, marker: dict[str, Any]):
        from peovim.ui.tree_view import TreeNode

        path = Path(str(marker.get("path", "")))
        line = int(marker.get("line", 0))
        col = int(marker.get("col", 0))
        annotation = str(marker.get("annotation", "")).strip()
        preview = _read_line(path, line)
        label = f"{path.name}:{line + 1}:{col + 1} {preview}".rstrip()
        if annotation:
            label = f"{label} — {annotation}"
        context_nodes = [TreeNode(label=text) for text in _context_lines(path, line, radius=2)]
        node = TreeNode(
            label=label,
            value=("marker", str(path), line, col),
            children_fn=(lambda items=context_nodes: items) if context_nodes else None,
        )
        if context_nodes:
            node._cached_children = context_nodes
        return node

    def _get_panel(self) -> _MarkersSidebarPanel:
        if self._panel is None:
            self._panel = _MarkersSidebarPanel(self._api, self)
            self._api.ui.register_sidebar_panel(_PANEL_NAME, self._panel)
        return self._panel

    def _on_store_changed(self, *, selected_path: Path | None = None, selected_line: int | None = None) -> None:
        self.refresh_signs_for_open_buffers()
        self.refresh_annotation_ghost_text()
        if self._api.ui.get_sidebar_panel(_PANEL_NAME) is not None:
            panel = self._get_panel()
            panel.refresh()
            if selected_path is not None and selected_line is not None:
                marker = self.store.marker_at(self.store.active_group(), selected_path, selected_line)
                col = int(marker.get("col", 0)) if marker is not None else 0
                panel.tree.select_value(("marker", str(selected_path.resolve()), selected_line, col))

    def _jump_marker(self, *, forward: bool) -> None:
        buf = self._api.active_buffer()
        if buf.path is None:
            _set_status(self._api, "Markers require a file-backed buffer")
            return
        markers = self.marker_list()
        if not markers:
            _set_status(self._api, f"No markers in {self.store.active_group()}")
            return
        line, col = self._api.active_window().cursor
        current = (str(Path(buf.path).resolve()), line, col)
        tuples = [
            (str(Path(str(marker.get("path", ""))).resolve()), int(marker.get("line", 0)), int(marker.get("col", 0)))
            for marker in markers
        ]
        target_index = 0 if forward else len(tuples) - 1
        if forward:
            for index, item in enumerate(tuples):
                if item > current:
                    target_index = index
                    break
        else:
            for index in range(len(tuples) - 1, -1, -1):
                if tuples[index] < current:
                    target_index = index
                    break
        path, target_line, target_col = tuples[target_index]
        self.jump_to_marker(Path(path), target_line, target_col)
        self._on_store_changed(selected_path=Path(path), selected_line=target_line)
        annotation = str(markers[target_index].get("annotation", "")).strip()
        detail = f" — {annotation}" if annotation else ""
        _set_status(self._api, f"{Path(path).name}:{target_line + 1}{detail}")


def setup(api: EditorAPI) -> None:
    """Register marker groups plugin."""
    global _controller
    from peovim.core.style import Style

    _controller = _MarkersController(api)
    api.register_sign_type(_SIGN_TYPE, _SIGN_CHAR, Style(fg=_SIGN_COLOR))

    api.keymap.ngroup("<leader>m", "Markers")
    api.keymap.ngroup("<leader>mg", "Marker groups")
    api.keymap.define_plug("MarkerAdd", lambda: _controller.add_marker(), desc="Markers: add")
    api.keymap.define_plug("MarkerDelete", lambda: _controller.delete_marker(), desc="Markers: delete")
    api.keymap.define_plug("MarkerNext", lambda: _controller.next_marker(), desc="Markers: next")
    api.keymap.define_plug("MarkerPrev", lambda: _controller.prev_marker(), desc="Markers: previous")
    api.keymap.define_plug("MarkerText", lambda: _controller.prompt_marker_text(), desc="Markers: text")
    api.keymap.define_plug("MarkerView", lambda: _controller.toggle_panel(), desc="Markers: toggle sidebar")
    api.keymap.define_plug("MarkerGroupCreate", lambda: _controller.prompt_create_group(), desc="Markers: create group")
    api.keymap.define_plug("MarkerGroupSelect", lambda: _controller.prompt_select_group(), desc="Markers: select group")
    api.keymap.define_plug("MarkerGroupRename", lambda: _controller.prompt_rename_group(), desc="Markers: rename group")
    api.keymap.define_plug("MarkerGroupDelete", lambda: _controller.prompt_delete_group(), desc="Markers: delete group")

    api.keymap.nmap("<leader>ma", "<Plug>MarkerAdd", desc="Markers: add")
    api.keymap.nmap("<leader>md", "<Plug>MarkerDelete", desc="Markers: delete")
    api.keymap.nmap("<leader>mv", "<Plug>MarkerView", desc="Markers: toggle sidebar")
    api.keymap.nmap("mn", "<Plug>MarkerNext", desc="Markers: next")
    api.keymap.nmap("mp", "<Plug>MarkerPrev", desc="Markers: previous")
    api.keymap.nmap("me", "<Plug>MarkerText", desc="Markers: text")
    api.keymap.nmap("<leader>mgc", "<Plug>MarkerGroupCreate", desc="Markers: create group")
    api.keymap.nmap("<leader>mgs", "<Plug>MarkerGroupSelect", desc="Markers: select group")
    api.keymap.nmap("<leader>mgr", "<Plug>MarkerGroupRename", desc="Markers: rename group")
    api.keymap.nmap("<leader>mgd", "<Plug>MarkerGroupDelete", desc="Markers: delete group")

    api.commands.register("Markers", lambda cmd, ctx: _controller.toggle_panel(), min_abbrev=3)
    api.commands.register("MarkerText", lambda cmd, ctx: _controller.command_marker_text(cmd), min_abbrev=10)
    api.commands.register("MarkerGroupCreate", lambda cmd, ctx: _controller.command_group_create(cmd), min_abbrev=12)
    api.commands.register("MarkerGroupRename", lambda cmd, ctx: _controller.command_group_rename(cmd), min_abbrev=12)
    api.commands.register("MarkerGroupDelete", lambda cmd, ctx: _controller.command_group_delete(cmd), min_abbrev=12)

    api.events.on("buffer_opened", lambda **kwargs: _controller.on_buffer_opened(**kwargs))
    api.events.on("cursor_moved", lambda **kwargs: _controller.on_cursor_moved(**kwargs))
    api.events.on("buffer_text_changed", lambda **kwargs: _controller.on_text_changed(**kwargs))
    _controller.refresh_signs_for_open_buffers()
    _controller.refresh_annotation_ghost_text()


def _expanded_values(tree: Any) -> set[tuple]:
    expanded: set[tuple] = set()

    def _walk(nodes: list[Any]) -> None:
        for node in nodes:
            if node.expanded and node.value is not None:
                expanded.add(node.value)
                _walk(node.get_children())

    _walk(tree._roots)
    return expanded


def _read_line(path: Path, line: int) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for index, text in enumerate(handle):
                if index == line:
                    return text.strip()
    except Exception:
        return "[missing]"
    return ""


def _context_lines(path: Path, line: int, *, radius: int = 2) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = [text.rstrip() for text in handle]
    except Exception:
        return ["  [missing file]"]
    if not lines:
        return []
    start = max(0, line - radius)
    end = min(len(lines), line + radius + 1)
    rendered: list[str] = []
    width = len(str(end))
    for index in range(start, end):
        marker = ">" if index == line else " "
        rendered.append(f"{marker}{index + 1:>{width}}: {lines[index]}")
    return rendered


def _set_status(api: Any, message: str) -> None:
    api.set_status(message)


def _dedupe_markers(markers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for marker in markers:
        current = dict(marker)
        if (
            deduped
            and current.get("path") == deduped[-1].get("path")
            and int(current.get("line", 0)) == int(deduped[-1].get("line", 0))
        ):
            deduped[-1]["col"] = min(int(deduped[-1].get("col", 0)), int(current.get("col", 0)))
            if not str(deduped[-1].get("annotation", "")).strip() and str(current.get("annotation", "")).strip():
                deduped[-1]["annotation"] = current.get("annotation", "")
            continue
        deduped.append(current)
    return deduped


def _project_root(api: Any, path: str | Path | None = None) -> Path | None:
    markers = [".git", "pyproject.toml", "setup.py", "Cargo.toml"]
    if path is None:
        buf = api.active_buffer()
        path = getattr(buf, "path", None)
        if path is None:
            return None
    candidate = Path(path)
    current = (candidate.parent if candidate.suffix else candidate).resolve()
    while True:
        for marker in markers:
            if (current / marker).exists():
                return current
        if current.parent == current:
            break
        current = current.parent
    try:
        return api.git.root(candidate.parent if candidate.suffix else candidate)
    except Exception:
        return None
