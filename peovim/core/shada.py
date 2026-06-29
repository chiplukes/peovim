"""
core.shada — Persistent state across editor restarts (shada file)

Persists: global marks (A-Z), numbered registers (0-9), command history,
search history, jump list, and recent files. Written on exit, read on startup.
Storage: platformdirs.user_data_dir("peovim") / "shada" (msgpack format).

Atomic write: write to <path>.tmp then os.replace() so a crash mid-write
never leaves a corrupt shada file.

Multi-instance merge (merge_write):
  Merge strategies per key type — see _merge_into() docstring for the table.
  A portalocker advisory lock prevents simultaneous writes from two instances
  producing a partial/torn state.
"""

from __future__ import annotations

import contextlib
import logging
import pathlib
from typing import Any

import msgpack  # type: ignore[import-untyped]
import platformdirs
import portalocker

from peovim.core.persistence import atomic_write_bytes

_log = logging.getLogger("peovim.shada")

_CMD_HISTORY_MAX = 100
_SEARCH_HISTORY_MAX = 50
_RECENT_FILES_MAX = 20


def _merge_history(in_memory: list[str], on_disk: list[str], cap: int) -> list[str]:
    """Return a merged history list: in-memory items first, unique disk items appended, capped."""
    seen: set[str] = set(in_memory)
    merged: list[str] = list(in_memory)
    for item in on_disk:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged[:cap]


class ShadaStore:
    """Persistent cross-session state store (shada format)."""

    def __init__(self, path: pathlib.Path | None = None) -> None:
        if path is None:
            data_dir = pathlib.Path(platformdirs.user_data_dir("peovim"))
            path = data_dir / "shada"
        self._path = path
        # Internal state
        self._global_marks: dict[str, tuple[str, int, int]] = {}  # name → (path_str, line, col)
        self._registers: list[str | None] = [None] * 10  # indices 0-9
        self._command_history: list[str] = []
        self._search_history: list[str] = []
        self._jump_list: list[tuple[str, int, int, int]] = []
        self._recent_files: list[str] = []
        self._file_positions: dict[str, tuple[int, int]] = {}  # path → (line, col)
        self._project_trust: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def read(self) -> None:
        """Load shada from disk. Missing file = empty state. Corrupt file = warning."""
        if not self._path.exists():
            return
        try:
            raw = self._path.read_bytes()
            data: dict[str, Any] = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            _log.warning("Corrupt shada file %s: %s — starting with empty state", self._path, exc)
            return

        try:
            marks = data.get("global_marks", {})
            for name, entry in marks.items():
                if isinstance(entry, list | tuple) and len(entry) == 3:
                    self._global_marks[str(name)] = (str(entry[0]), int(entry[1]), int(entry[2]))

            regs = data.get("registers", [])
            for i, val in enumerate(regs):
                if i < 10:
                    self._registers[i] = str(val) if val is not None else None

            self._command_history = [str(x) for x in data.get("command_history", [])]
            self._search_history = [str(x) for x in data.get("search_history", [])]

            jl = data.get("jump_list", [])
            self._jump_list = [
                (str(e[0]), int(e[1]), int(e[2]), int(e[3]) if len(e) >= 4 else 0)
                for e in jl
                if isinstance(e, list | tuple) and len(e) >= 3
            ]

            self._recent_files = [str(x) for x in data.get("recent_files", [])]

            fp = data.get("file_positions", {})
            for path_str, entry in fp.items():
                if isinstance(entry, list | tuple) and len(entry) == 2:
                    self._file_positions[str(path_str)] = (int(entry[0]), int(entry[1]))

            project_trust = data.get("project_trust", {})
            if isinstance(project_trust, dict):
                self._project_trust = {str(path_str): bool(trusted) for path_str, trusted in project_trust.items()}
        except Exception as exc:
            _log.warning("Error parsing shada data: %s", exc)

    def write(self) -> None:
        """Serialize state to disk atomically (no locking — use merge_write at exit)."""
        data: dict[str, Any] = {
            "global_marks": {name: list(entry) for name, entry in self._global_marks.items()},
            "registers": list(self._registers),
            "command_history": list(self._command_history),
            "search_history": list(self._search_history),
            "jump_list": [list(e) for e in self._jump_list],
            "recent_files": list(self._recent_files),
            "file_positions": {k: list(v) for k, v in self._file_positions.items()},
            "project_trust": dict(self._project_trust),
        }
        raw = msgpack.packb(data, use_bin_type=True)
        try:
            atomic_write_bytes(self._path, raw)
        except Exception as exc:
            _log.warning("Failed to write shada: %s", exc)

    def merge_write(self) -> None:
        """Lock, read-merge on-disk shada, write merged state atomically.

        Merge strategies per key
        ------------------------
        command_history   — append-merge: in-memory first, unique disk entries appended, cap 100
        search_history    — append-merge: same, cap 50
        recent_files      — append-merge: same, cap 20
        global_marks      — per-key: disk provides keys absent in-memory; in-memory wins per key
        file_positions    — per-path: disk provides paths absent in-memory; in-memory wins per path
        project_trust     — union: add any disk entries not in memory (never remove)
        registers         — no merge: in-memory (session state) wins
        jump_list         — no merge: in-memory (session state) wins
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = pathlib.Path(str(self._path) + ".lock")
        with contextlib.suppress(Exception):
            lock_path.touch(exist_ok=True)
        try:
            # Use LOCK_EX | LOCK_NB on platforms that support non-blocking;
            # fall back to plain blocking LOCK_EX if LOCK_NB is unavailable.
            try:
                lock_flags = portalocker.LOCK_EX | portalocker.LOCK_NB
                with portalocker.Lock(str(lock_path), flags=lock_flags, timeout=5, fail_when_locked=True):
                    self._locked_merge_and_write()
            except (portalocker.LockException, portalocker.AlreadyLocked):
                _log.warning("shada: could not acquire lock — writing without merge")
                self.write()
        except AttributeError:
            # LOCK_NB not available on this platform — fall back to blocking lock
            with portalocker.Lock(str(lock_path), flags=portalocker.LOCK_EX):
                self._locked_merge_and_write()
        except Exception as exc:
            _log.warning("shada merge_write failed: %s — falling back to plain write", exc)
            self.write()

    def _locked_merge_and_write(self) -> None:
        """Read on-disk state, merge into self, then write. Must be called inside a lock."""
        disk = ShadaStore(self._path)
        disk.read()
        self._merge_from_disk(disk)
        self.write()

    def _merge_from_disk(self, disk: ShadaStore) -> None:
        """Merge *disk* state into self (in-memory) using the per-key strategies."""
        # Append-merge: in-memory entries first (most recent), unique disk entries appended
        self._command_history = _merge_history(self._command_history, disk._command_history, _CMD_HISTORY_MAX)
        self._search_history = _merge_history(self._search_history, disk._search_history, _SEARCH_HISTORY_MAX)
        self._recent_files = _merge_history(self._recent_files, disk._recent_files, _RECENT_FILES_MAX)

        # Per-key: disk fills in absent keys; in-memory wins for keys it already has
        for key, value in disk._global_marks.items():
            if key not in self._global_marks:
                self._global_marks[key] = value

        for path_str, pos in disk._file_positions.items():
            if path_str not in self._file_positions:
                self._file_positions[path_str] = pos

        # Union: add any disk trust entries not already in memory
        for project, trusted in disk._project_trust.items():
            if project not in self._project_trust:
                self._project_trust[project] = trusted

        # registers and jump_list: no merge (in-memory session state wins)

    # ------------------------------------------------------------------
    # Global marks A-Z
    # ------------------------------------------------------------------

    def get_global_mark(self, name: str) -> tuple[pathlib.Path, int, int] | None:
        """Return (filepath, line, col) for a global mark, or None."""
        entry = self._global_marks.get(name.upper())
        if entry is None:
            return None
        path_str, line, col = entry
        return pathlib.Path(path_str), line, col

    def set_global_mark(self, name: str, filepath: pathlib.Path, line: int, col: int) -> None:
        """Store a global mark."""
        self._global_marks[name.upper()] = (str(filepath), line, col)

    # ------------------------------------------------------------------
    # Numbered registers 0-9
    # ------------------------------------------------------------------

    def get_register(self, index: int) -> str | None:
        """Return register contents at index (0-9)."""
        if 0 <= index <= 9:
            return self._registers[index]
        return None

    def set_register(self, index: int, text: str) -> None:
        """
        Set register at index, rotating existing entries.

        set_register(0, new) → 0=new, 1=old[0], 2=old[1], ..., 9=old[8]
        (old[9] is dropped)
        """
        if not (0 <= index <= 9):
            return
        # Rotate: everything shifts up one position, old[9] is dropped
        for i in range(9, index, -1):
            self._registers[i] = self._registers[i - 1]
        self._registers[index] = text

    # ------------------------------------------------------------------
    # Command history
    # ------------------------------------------------------------------

    def get_command_history(self) -> list[str]:
        """Return command history, most recent first, max 100."""
        return list(self._command_history)

    def push_command_history(self, cmd: str) -> None:
        """Add a command to history. Deduplicates consecutive identical entries."""
        if not cmd:
            return
        if self._command_history and self._command_history[0] == cmd:
            return
        self._command_history.insert(0, cmd)
        if len(self._command_history) > _CMD_HISTORY_MAX:
            self._command_history = self._command_history[:_CMD_HISTORY_MAX]

    # ------------------------------------------------------------------
    # Search history
    # ------------------------------------------------------------------

    def get_search_history(self) -> list[str]:
        """Return search history, most recent first, max 50."""
        return list(self._search_history)

    def push_search_history(self, pattern: str) -> None:
        """Add a search pattern. Deduplicates consecutive identical entries."""
        if not pattern:
            return
        if self._search_history and self._search_history[0] == pattern:
            return
        self._search_history.insert(0, pattern)
        if len(self._search_history) > _SEARCH_HISTORY_MAX:
            self._search_history = self._search_history[:_SEARCH_HISTORY_MAX]

    # ------------------------------------------------------------------
    # Jump list
    # ------------------------------------------------------------------

    def get_jump_list(self) -> list[tuple[pathlib.Path, int, int, int]]:
        """Return the jump list as (filepath, line, col, scroll_line) tuples."""
        return [(pathlib.Path(e[0]), e[1], e[2], e[3]) for e in self._jump_list]

    def set_jump_list(self, entries: list[tuple[pathlib.Path, int, int, int]]) -> None:
        """Replace the jump list."""
        self._jump_list = [(str(e[0]), e[1], e[2], e[3]) for e in entries]

    # ------------------------------------------------------------------
    # Recent files
    # ------------------------------------------------------------------

    def get_recent_files(self) -> list[str]:
        """Return recent file paths, most recent first, max 20."""
        return list(self._recent_files)

    # ------------------------------------------------------------------
    # File cursor positions
    # ------------------------------------------------------------------

    def get_file_pos(self, path: str) -> tuple[int, int] | None:
        """Return (line, col) of last cursor position in file, or None."""
        return self._file_positions.get(path)

    def set_file_pos(self, path: str, line: int, col: int) -> None:
        """Save last cursor position for a file."""
        if path:
            self._file_positions[path] = (line, col)

    # ------------------------------------------------------------------
    # Recent files
    # ------------------------------------------------------------------

    def push_recent_file(self, path: str) -> None:
        """Add a file to recent files. Deduplicates and trims to 20."""
        if not path:
            return
        # Remove existing occurrence (dedup)
        self._recent_files = [f for f in self._recent_files if f != path]
        self._recent_files.insert(0, path)
        if len(self._recent_files) > _RECENT_FILES_MAX:
            self._recent_files = self._recent_files[:_RECENT_FILES_MAX]

    # ------------------------------------------------------------------
    # Project config trust
    # ------------------------------------------------------------------

    def get_project_trust(self, project_root: str) -> bool | None:
        """Return the persisted trust decision for a project root, or None if unknown."""
        return self._project_trust.get(project_root)

    def set_project_trust(self, project_root: str, trusted: bool) -> None:
        """Persist a trust decision for a project root."""
        if project_root:
            self._project_trust[project_root] = trusted
