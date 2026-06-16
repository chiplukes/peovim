"""Local per-file save history plugin."""

from __future__ import annotations

import contextlib
import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from peovim.core.persistence import atomic_write_text

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI


def _default_history_root() -> Path:
    try:
        import platformdirs

        return Path(platformdirs.user_data_dir("peovim", ensure_exists=True)) / "history"
    except Exception:
        return Path(tempfile.gettempdir()) / "peovim" / "history"


def _set_status(api: Any, message: str, *, level: str = "info") -> None:
    with contextlib.suppress(Exception):
        api.set_status(message, level=level)


def _history_timestamp(now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _snapshot_name(timestamp: str, source_name: str) -> str:
    safe_timestamp = timestamp.replace(":", "-")
    return f"{safe_timestamp}__{source_name}"


def _display_age(timestamp: str) -> str:
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return "unknown age"
    delta = datetime.now(UTC) - moment.astimezone(UTC)
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


@dataclass(frozen=True)
class _HistoryEntry:
    timestamp: str
    snapshot: str
    content_hash: str
    size: int
    reason: str = "save"


@dataclass(frozen=True)
class _HistoryItem:
    index: int
    entry: _HistoryEntry
    snapshot_path: Path

    def __str__(self) -> str:
        stamp = self.entry.timestamp.replace("T", " ").replace("Z", " UTC")
        return f"{self.index}. {stamp}  {_display_age(self.entry.timestamp)}  {self.entry.size} chars"


class _LocalHistoryStore:
    def __init__(self, api: EditorAPI) -> None:
        self._api = api

    def root(self) -> Path:
        raw = str(self._api.options.get("local_history_root") or "").strip()
        if not raw:
            return _default_history_root()
        root = Path(raw).expanduser()
        return root.resolve() if root.is_absolute() else (Path.cwd() / root).resolve()

    def entries(self, path: str | Path) -> list[_HistoryEntry]:
        manifest = self._load_manifest(path)
        entries = manifest.get("entries", [])
        result: list[_HistoryEntry] = []
        if not isinstance(entries, list):
            return result
        for item in entries:
            if not isinstance(item, dict):
                continue
            timestamp = str(item.get("timestamp", "")).strip()
            snapshot = str(item.get("snapshot", "")).strip()
            if not timestamp or not snapshot:
                continue
            result.append(
                _HistoryEntry(
                    timestamp=timestamp,
                    snapshot=snapshot,
                    content_hash=str(item.get("content_hash", "")),
                    size=int(item.get("size", 0)),
                    reason=str(item.get("reason", "save") or "save"),
                )
            )
        result.sort(key=lambda entry: entry.timestamp, reverse=True)
        return result

    def items(self, path: str | Path) -> list[_HistoryItem]:
        target = Path(path).resolve()
        directory = self._file_dir(target)
        return [
            _HistoryItem(index=index, entry=entry, snapshot_path=directory / entry.snapshot)
            for index, entry in enumerate(self.entries(target), start=1)
        ]

    def capture(self, path: str | Path, text: str, *, reason: str = "save") -> _HistoryEntry | None:
        target = Path(path).resolve()
        content_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()
        existing = self.entries(target)
        latest = existing[0] if existing else None
        if latest is not None and latest.content_hash == content_hash:
            return None

        directory = self._file_dir(target)
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = _history_timestamp()
        snapshot_name = _snapshot_name(timestamp, target.name)
        snapshot_path = directory / snapshot_name
        # save policy: single-writer (snapshot name includes timestamp, so each save is a new file)
        atomic_write_text(snapshot_path, text, encoding="utf-8")

        entry = _HistoryEntry(
            timestamp=timestamp,
            snapshot=snapshot_name,
            content_hash=content_hash,
            size=len(text),
            reason=reason,
        )
        manifest = self._load_manifest(target)
        manifest["path"] = str(target)
        manifest_entries = manifest.setdefault("entries", [])
        manifest_entries.insert(
            0,
            {
                "timestamp": entry.timestamp,
                "snapshot": entry.snapshot,
                "content_hash": entry.content_hash,
                "size": entry.size,
                "reason": entry.reason,
            },
        )
        self._prune_manifest(target, manifest)
        return entry

    def prune(self, path: str | Path) -> int:
        target = Path(path).resolve()
        manifest = self._load_manifest(target)
        before = len(self.entries(target))
        self._prune_manifest(target, manifest)
        after = len(self.entries(target))
        return max(0, before - after)

    def read_snapshot(self, item: _HistoryItem) -> str:
        return item.snapshot_path.read_text(encoding="utf-8", errors="replace")

    def resolve_item(self, path: str | Path, spec: str | None = None) -> _HistoryItem | None:
        items = self.items(path)
        if not items:
            return None
        if spec is None or not str(spec).strip():
            return items[0]
        raw = str(spec).strip()
        if raw.isdigit():
            index = int(raw)
            for item in items:
                if item.index == index:
                    return item
            return None
        for item in items:
            if item.entry.timestamp.startswith(raw) or item.entry.snapshot == raw:
                return item
        return None

    def _load_manifest(self, path: str | Path) -> dict[str, Any]:
        manifest_path = self._manifest_path(path)
        if not manifest_path.exists():
            return {"version": 1, "path": str(Path(path).resolve()), "entries": []}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "path": str(Path(path).resolve()), "entries": []}
        if not isinstance(data, dict):
            return {"version": 1, "path": str(Path(path).resolve()), "entries": []}
        data.setdefault("version", 1)
        data.setdefault("path", str(Path(path).resolve()))
        data.setdefault("entries", [])
        return data

    def _save_manifest(self, path: str | Path, manifest: dict[str, Any]) -> None:
        manifest_path = self._manifest_path(path)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        # save policy: mergeable-risk (two instances on the same file share the manifest path;
        # last write wins — acceptable because entries are append-only and pruning is idempotent)
        atomic_write_text(manifest_path, json.dumps(manifest, indent=2), encoding="utf-8")

    def _prune_manifest(self, path: str | Path, manifest: dict[str, Any]) -> None:
        target = Path(path).resolve()
        directory = self._file_dir(target)
        directory.mkdir(parents=True, exist_ok=True)
        raw_entries = manifest.get("entries", [])
        normalized_entries = []
        if isinstance(raw_entries, list):
            for item in raw_entries:
                if not isinstance(item, dict):
                    continue
                timestamp = str(item.get("timestamp", "")).strip()
                snapshot = str(item.get("snapshot", "")).strip()
                if not timestamp or not snapshot:
                    continue
                normalized_entries.append(
                    {
                        "timestamp": timestamp,
                        "snapshot": snapshot,
                        "content_hash": str(item.get("content_hash", "")),
                        "size": int(item.get("size", 0)),
                        "reason": str(item.get("reason", "save") or "save"),
                    }
                )
        normalized_entries.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)

        max_entries = max(1, int(self._api.options.get("local_history_max_entries") or 50))
        max_age_days = int(self._api.options.get("local_history_max_age_days") or 0)
        cutoff: datetime | None = None
        if max_age_days > 0:
            cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

        kept: list[dict[str, Any]] = []
        for item in normalized_entries:
            if len(kept) >= max_entries:
                self._delete_snapshot_file(directory / str(item["snapshot"]))
                continue
            if cutoff is not None:
                try:
                    moment = datetime.fromisoformat(str(item["timestamp"]).replace("Z", "+00:00"))
                except ValueError:
                    moment = None
                if moment is not None and moment.astimezone(UTC) < cutoff:
                    self._delete_snapshot_file(directory / str(item["snapshot"]))
                    continue
            kept.append(item)

        referenced = {str(item["snapshot"]) for item in kept}
        for child in directory.iterdir() if directory.exists() else ():
            if child.name == "manifest.json":
                continue
            if child.name not in referenced:
                self._delete_snapshot_file(child)

        manifest["entries"] = kept
        self._save_manifest(target, manifest)

    @staticmethod
    def _delete_snapshot_file(path: Path) -> None:
        with contextlib.suppress(FileNotFoundError, OSError):
            path.unlink()

    def _file_dir(self, path: str | Path) -> Path:
        target = Path(path).resolve()
        key = hashlib.sha1(str(target).encode("utf-8")).hexdigest()
        return self.root() / "files" / key

    def _manifest_path(self, path: str | Path) -> Path:
        return self._file_dir(path) / "manifest.json"


class _LocalHistoryController:
    def __init__(self, api: EditorAPI) -> None:
        self._api = api
        self._store = _LocalHistoryStore(api)
        self._log = api.get_logger("local_history")

    def on_buffer_saved(self, *, buf_id: int = 0, path: str | None = None, **_kwargs: Any) -> None:
        if not self._enabled():
            return
        buf = self._api.buffer_by_id(buf_id)
        if buf is None:
            return
        target = Path(path).resolve() if path else getattr(buf, "path", None)
        if target is None:
            return
        try:
            self._store.capture(target, buf.get_text(), reason="save")
        except Exception:
            self._log.exception("local history capture failed for %s", target)

    def show_history(self) -> None:
        buf = self._api.active_buffer()
        path = getattr(buf, "path", None)
        if path is None:
            _set_status(self._api, "History requires a file-backed buffer", level="warn")
            return
        items = self._store.items(path)
        if not items:
            _set_status(self._api, "No local history for this file", level="info")
            return

        def _preview(item: _HistoryItem | None) -> list[str]:
            if item is None:
                return []
            with contextlib.suppress(Exception):
                return self._store.read_snapshot(item).splitlines()[:40]
            return []

        self._api.ui.open_picker(
            "Local History",
            items,
            on_confirm=lambda item: self.open_entry(item.entry.snapshot if item else None),
            preview=_preview,
        )

    def open_entry(self, spec: str | None = None) -> None:
        buf = self._api.active_buffer()
        path = getattr(buf, "path", None)
        if path is None:
            _set_status(self._api, "History requires a file-backed buffer", level="warn")
            return
        item = self._store.resolve_item(path, spec)
        if item is None:
            _set_status(self._api, "No matching history entry", level="warn")
            return
        self._api.open_buffer(item.snapshot_path)
        win = self._api.active_window()
        win.set_option("readonly", True)
        win.set_option("modifiable", False)
        _set_status(self._api, f"Opened local history snapshot: {item.snapshot_path.name}")

    def restore_entry(self, spec: str | None = None) -> None:
        buf = self._api.active_buffer()
        path = getattr(buf, "path", None)
        if path is None:
            _set_status(self._api, "History restore requires a file-backed buffer", level="warn")
            return
        item = self._store.resolve_item(path, spec)
        if item is None:
            _set_status(self._api, "No matching history entry", level="warn")
            return
        text = self._store.read_snapshot(item)
        self._replace_buffer_text(buf, text)
        _set_status(self._api, f"Restored local history snapshot into buffer: {item.index}")

    def prune_current(self) -> None:
        buf = self._api.active_buffer()
        path = getattr(buf, "path", None)
        if path is None:
            _set_status(self._api, "History prune requires a file-backed buffer", level="warn")
            return
        removed = self._store.prune(path)
        _set_status(self._api, f"Pruned {removed} local history entr{'y' if removed == 1 else 'ies'}")

    def _enabled(self) -> bool:
        value = self._api.options.get("local_history_enabled")
        return True if value is None else bool(value)

    @staticmethod
    def _replace_buffer_text(buf: Any, text: str) -> None:
        line_count = buf.line_count()
        end_line = max(0, line_count - 1)
        end_col = len(buf.get_line(end_line)) if line_count > 0 else 0
        buf.replace(0, 0, end_line, end_col, text)


_controller: _LocalHistoryController | None = None
_PANEL_NAME = "local-history"


class _LocalHistoryPanel:
    """Sidebar panel showing save history for the active file."""

    def __init__(self, api: Any) -> None:
        from peovim.ui.tree_view import TreeView

        self._api = api
        self.width = 40
        self._tree = TreeView(
            [],
            title="Local History",
            on_select=self._on_select,
            width=self.width,
        )
        self._current_path: Path | None = None

    def render(self, grid: Any) -> None:
        self._tree.focused = getattr(self, "_sidebar_focused", False)
        self._tree.blink_on = getattr(self, "_sidebar_blink_on", True)
        self._tree._width = grid.width
        self._tree.render(grid)

    def feed_key(self, key: str) -> bool:
        if key == "r" and self._current_path is not None:
            node = self._tree.selected_node()
            if node is not None and isinstance(node.value, _HistoryItem) and _controller is not None:
                _controller.restore_entry(node.value.entry.snapshot)
            return True
        if key == "R":
            self.refresh()
            return True
        self._tree.feed_key(key)
        return True

    def on_focus(self) -> None:
        self._tree.focused = True

    def on_blur(self) -> None:
        self._tree.focused = False

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        from peovim.ui.tree_view import TreeNode

        buf = self._api.active_buffer()
        path = getattr(buf, "path", None)
        self._current_path = path

        if path is None:
            self._tree._title = "Local History"
            self._tree.set_nodes([TreeNode(label="No file-backed buffer", value=None)])
            return

        items = _controller._store.items(path) if _controller else []
        self._tree._title = f"History: {path.name}"

        if not items:
            self._tree.set_nodes([TreeNode(label="No history snapshots", value=None)])
            return

        nodes = [TreeNode(label=str(item), value=item) for item in items]
        self._tree.set_nodes(nodes)

    def _on_select(self, node: Any) -> None:
        if not isinstance(node.value, _HistoryItem) or _controller is None:
            return
        _controller.open_entry(node.value.entry.snapshot)
        self._api.ui.blur_sidebar()


def setup(api: EditorAPI) -> None:
    """Register local history commands, options, and save hooks."""
    global _controller
    _controller = _LocalHistoryController(api)

    api.options.define(
        "local_history_enabled",
        bool,
        True,
        doc="Capture local history snapshots whenever a file-backed buffer is saved.",
    )
    api.options.define(
        "local_history_root",
        str,
        "",
        doc="Root directory for local history snapshots. Empty uses the default data-dir history folder.",
    )
    api.options.define(
        "local_history_max_entries",
        int,
        50,
        validator=lambda value: value >= 1,
        doc="Maximum number of local history snapshots to keep per file.",
    )
    api.options.define(
        "local_history_max_age_days",
        int,
        30,
        validator=lambda value: value >= 0,
        doc="Maximum age in days for local history snapshots; 0 disables age pruning.",
    )

    panel = _LocalHistoryPanel(api)
    api.ui.register_sidebar_panel(_PANEL_NAME, panel)

    api.keymap.define_plug("LocalHistory", lambda: _toggle_panel(api), desc="Local history: sidebar")
    api.keymap.define_plug("LocalHistoryPicker", lambda: _controller.show_history(), desc="Local history: picker")

    api.commands.register("History", lambda cmd, ctx: _controller.show_history(), min_abbrev=4)
    api.commands.register(
        "HistoryOpen",
        lambda cmd, ctx: _controller.open_entry(cmd.args.strip() or None),
        min_abbrev=8,
    )
    api.commands.register(
        "HistoryRestore",
        lambda cmd, ctx: _controller.restore_entry(cmd.args.strip() or None),
        min_abbrev=8,
    )
    api.commands.register("HistoryPrune", lambda cmd, ctx: _controller.prune_current(), min_abbrev=8)

    api.events.on("buffer_saved", lambda **kwargs: _on_buffer_saved_refresh(api, **kwargs))
    api.events.on("buffer_opened", lambda **kwargs: _refresh_if_visible(api))


def _toggle_panel(api: Any) -> None:
    panel = api.ui.get_sidebar_panel(_PANEL_NAME)
    if panel is None:
        return
    if api.ui.is_sidebar_visible(_PANEL_NAME):
        api.ui.hide_sidebar()
        return
    panel.refresh()
    api.ui.show_sidebar_panel(_PANEL_NAME, panel, focus=True)


def _refresh_if_visible(api: Any) -> None:
    if not api.ui.is_sidebar_visible(_PANEL_NAME):
        return
    panel = api.ui.get_sidebar_panel(_PANEL_NAME)
    if panel is not None and hasattr(panel, "refresh"):
        panel.refresh()


def _on_buffer_saved_refresh(api: Any, **kwargs: Any) -> None:
    if _controller is not None:
        _controller.on_buffer_saved(**kwargs)
    _refresh_if_visible(api)
