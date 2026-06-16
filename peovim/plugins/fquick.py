"""Fast file navigation on an ``f`` prefix.

Usage in init.py:
    plugins.load("peovim.plugins.fquick")

Default normal-mode mappings:
    fh  - cycle to the next older session file
    fl  - cycle to the next newer session file
    fj  - open a session-files picker (ready to move down with j)
    fk  - open a session-files picker (ready to move up with k)
    f/  - open a fuzzy workspace-files picker

This plugin is intentionally opinionated: when loaded, it repurposes the
built-in ``f{char}`` motion prefix for file navigation.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from peovim.plugins.picker import _display_path, _preview_file

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

_controller: _FquickController | None = None


@dataclass(frozen=True)
class _FileItem:
    label: str
    path: Path

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class _ViewState:
    cursor: tuple[int, int]
    scroll_line: int


class _FquickController:
    def __init__(self, api: Any) -> None:
        self._api = api
        self._history: list[Path] = []
        self._view_state_by_path: dict[Path, _ViewState] = {}
        self._cycle_paths: list[Path] = []
        self._cycle_index: int = 0
        self._expected_cycle_path: Path | None = None
        self._event_tokens: list[int] = []
        self._event_tokens.append(api.events.on("buffer_opened", self._on_buffer_opened))
        self._event_tokens.append(api.events.on("cursor_moved", self._on_cursor_moved))
        self._seed_history()
        self._remember_active_view()

    def teardown(self) -> None:
        for token in self._event_tokens:
            with contextlib.suppress(Exception):
                self._api.events.off(token)
        self._event_tokens.clear()

    def cycle_older(self) -> None:
        self._cycle(1)

    def cycle_newer(self) -> None:
        self._cycle(-1)

    def open_session_picker(self, *, initial_step: int = 0) -> None:
        items = self._session_items()
        if not items:
            self._notify("No session files")
            return
        self._api.ui.open_picker(
            "Session Files",
            items,
            on_confirm=lambda item: self._open_item(item),
            preview=lambda item: _preview_file(item.path, api=self._api),
            keymap=self._picker_keymap(),
        )
        if initial_step > 0:
            self._feed_picker("<C-n>")
        elif initial_step < 0:
            self._feed_picker("<C-p>")

    def open_workspace_picker(self) -> None:
        root = self._root() or Path.cwd()
        try:
            paths = self._api.find_files("**/*", root=root)
        except Exception:
            paths = []
        items = [_FileItem(_display_path(path, root), path) for path in paths]
        self._api.ui.open_picker(
            "Workspace Files",
            items,
            on_confirm=lambda item: self._open_item(item),
            preview=lambda item: _preview_file(item.path, api=self._api),
            keymap=self._picker_keymap(),
        )

    def _cycle(self, step: int) -> None:
        paths = self._ensure_cycle_paths()
        if len(paths) < 2:
            self._notify("No other session files")
            return
        self._cycle_index = (self._cycle_index + step) % len(paths)
        target = paths[self._cycle_index]
        self._expected_cycle_path = target
        self._open_path(target)

    def _ensure_cycle_paths(self) -> list[Path]:
        current = self._current_path()
        if not self._cycle_paths or current is None:
            self._cycle_paths = self._ordered_session_paths()
            self._cycle_index = 0
            return self._cycle_paths
        if self._cycle_index >= len(self._cycle_paths) or self._cycle_paths[self._cycle_index] != current:
            self._cycle_paths = self._ordered_session_paths()
            self._cycle_index = 0
        return self._cycle_paths

    def _ordered_session_paths(self) -> list[Path]:
        paths = [path for path in self._history if path.exists()]
        current = self._current_path()
        if current is not None and current.exists() and current not in paths:
            paths.insert(0, current)
        if current is None or current not in paths:
            return paths
        index = paths.index(current)
        return paths[index:] + paths[:index]

    def _session_items(self) -> list[_FileItem]:
        root = self._root()
        modified = self._modified_paths()
        return [
            _FileItem(
                f"** {_display_path(path, root)}" if path in modified else _display_path(path, root),
                path,
            )
            for path in self._ordered_session_paths()
        ]

    def _modified_paths(self) -> set[Path]:
        modified: set[Path] = set()
        try:
            buffers = self._api.list_buffers()
        except Exception:
            buffers = []
        for buffer in buffers:
            path = self._resolve_path(getattr(buffer, "path", None))
            if path is None:
                continue
            try:
                if buffer.is_modified():
                    modified.add(path)
            except Exception:
                continue
        return modified

    def _seed_history(self) -> None:
        paths: list[Path] = []
        current = self._current_path()
        if current is not None:
            paths.append(current)
        try:
            buffers = self._api.list_buffers()
        except Exception:
            buffers = []
        for buffer in buffers:
            path = self._resolve_path(getattr(buffer, "path", None))
            if path is not None and path not in paths:
                paths.append(path)
        for path in reversed(paths):
            self._touch_history(path)

    def _on_buffer_opened(self, path: str | None = None, **_kwargs: Any) -> None:
        resolved = self._resolve_path(path)
        if resolved is None:
            return
        self._touch_history(resolved)
        if self._current_path() == resolved and resolved not in self._view_state_by_path:
            self._remember_active_view()
        if self._expected_cycle_path is not None and resolved == self._expected_cycle_path:
            self._expected_cycle_path = None
            if resolved in self._cycle_paths:
                self._cycle_index = self._cycle_paths.index(resolved)
                return
        self._reset_cycle()

    def _on_cursor_moved(self, line: int | None = None, col: int | None = None, **_kwargs: Any) -> None:
        path = self._current_path()
        if path is None:
            return
        scroll_line = self._current_scroll_line()
        if line is None or col is None:
            try:
                cursor = self._api.active_window().cursor
            except Exception:
                return
        else:
            cursor = (max(0, int(line)), max(0, int(col)))
        self._view_state_by_path[path] = _ViewState(cursor=cursor, scroll_line=scroll_line)

    def _touch_history(self, path: Path) -> None:
        self._history = [existing for existing in self._history if existing != path]
        self._history.insert(0, path)

    def _reset_cycle(self) -> None:
        self._cycle_paths = []
        self._cycle_index = 0
        self._expected_cycle_path = None

    def _root(self) -> Path | None:
        try:
            return self._api.find_root()
        except Exception:
            return None

    def _current_path(self) -> Path | None:
        try:
            return self._resolve_path(self._api.active_buffer().path)
        except Exception:
            return None

    def _current_scroll_line(self) -> int:
        try:
            return max(0, int(self._api.active_window().visible_range()[0]))
        except Exception:
            return 0

    @staticmethod
    def _resolve_path(path: Any) -> Path | None:
        if not path:
            return None
        try:
            return Path(path).resolve()
        except Exception:
            return None

    def _open_item(self, item: _FileItem | None) -> None:
        if item is None:
            return
        with contextlib.suppress(Exception):
            self._open_path(item.path)

    def _remember_active_view(self) -> None:
        path = self._current_path()
        if path is None:
            return
        try:
            cursor = self._api.active_window().cursor
        except Exception:
            return
        self._view_state_by_path[path] = _ViewState(
            cursor=(max(0, int(cursor[0])), max(0, int(cursor[1]))),
            scroll_line=self._current_scroll_line(),
        )

    def _open_path(self, path: Path) -> None:
        self._remember_active_view()
        view = self._view_state_by_path.get(path)
        if view is None:
            self._api.open_buffer(path)
            return
        self._api.open_buffer(path, line=view.cursor[0], col=view.cursor[1])
        with contextlib.suppress(Exception):
            self._api.active_window().set_scroll_line(view.scroll_line)

    def _notify(self, message: str) -> None:
        with contextlib.suppress(Exception):
            self._api.ui.notify(message)

    def _feed_picker(self, key: str) -> None:
        picker = getattr(self._api.ui, "_picker", None)
        if picker is None:
            return
        with contextlib.suppress(Exception):
            picker.feed_key(key)

    def _picker_keymap(self) -> dict[str, Any]:
        return {
            "j": lambda: self._feed_picker("<C-n>"),
            "k": lambda: self._feed_picker("<C-p>"),
        }


def setup(api: EditorAPI) -> None:
    """Register fquick mappings, plugs, and commands."""
    global _controller
    teardown()
    _controller = _FquickController(api)

    api.keymap.ngroup("f", "Fquick")
    api.keymap.define_plug("FquickOlder", lambda: _controller.cycle_older(), desc="Fquick: older session file")
    api.keymap.define_plug("FquickNewer", lambda: _controller.cycle_newer(), desc="Fquick: newer session file")
    api.keymap.define_plug(
        "FquickSessionPickerDown",
        lambda: _controller.open_session_picker(initial_step=1),
        desc="Fquick: session picker down",
    )
    api.keymap.define_plug(
        "FquickSessionPickerUp",
        lambda: _controller.open_session_picker(initial_step=-1),
        desc="Fquick: session picker up",
    )
    api.keymap.define_plug(
        "FquickWorkspacePicker",
        lambda: _controller.open_workspace_picker(),
        desc="Fquick: workspace files",
    )

    api.keymap.nmap("fh", "<Plug>FquickOlder", desc="Fquick older file")
    api.keymap.nmap("fl", "<Plug>FquickNewer", desc="Fquick newer file")
    api.keymap.nmap("fj", "<Plug>FquickSessionPickerDown", desc="Fquick session files")
    api.keymap.nmap("fk", "<Plug>FquickSessionPickerUp", desc="Fquick session files")
    api.keymap.nmap("f/", "<Plug>FquickWorkspacePicker", desc="Fquick workspace files")

    api.commands.register("FquickSession", lambda cmd, ctx: _controller.open_session_picker(), min_abbrev=8)
    api.commands.register("FquickWorkspace", lambda cmd, ctx: _controller.open_workspace_picker(), min_abbrev=9)


def teardown() -> None:
    global _controller
    if _controller is None:
        return
    _controller.teardown()
    _controller = None
