"""
core.persistence_undo — Persistent per-file undo storage.

Stores UndoStack entries on disk so edit history survives buffer close and
editor restart.  Each undo file is keyed by the SHA256 of the resolved
file path and stores a msgpack record containing:

  - file_hash: SHA256 of the file content at the time of the last save
    (used to detect external modifications and discard stale undo on load)
  - stack: serialized undo groups (list of lists of edit dicts)
  - redo: serialized redo groups
  - dirty_count: number of unsaved stack entries (= _change_counter - _clean_counter)

When a file is opened, the undo file is loaded.  If its stored file_hash
matches the current on-disk content, the last *dirty_count* entries are
replayed forward to reconstruct the unsaved document state; all entries
are then loaded into the undo stack.  If the hash differs, the undo file
is discarded as stale.

See notes/plan_persistent_history.md for the full design.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
from pathlib import Path

import msgpack  # type: ignore[import-untyped]
import platformdirs

from peovim.core.buffer import Edit
from peovim.core.persistence import atomic_write_bytes

log = logging.getLogger(__name__)


def _undo_dir() -> Path:
    return Path(platformdirs.user_data_dir("peovim")) / "undo"


def _undo_path(filepath: str | Path) -> Path:
    key = hashlib.sha256(str(Path(filepath).resolve()).encode("utf-8")).hexdigest()
    return _undo_dir() / f"{key}.undo"


def _file_hash(path: str | Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, FileNotFoundError):
        return ""


def _serialize_entries(entries: list[list[Edit]]) -> list[list[dict[str, object]]]:
    return [[{"k": e.kind, "p": e.pos, "t": e.text} for e in group] for group in entries]


def _deserialize_entries(data: list[list[dict[str, object]]]) -> list[list[Edit]]:
    result: list[list[Edit]] = []
    for group in data:
        edits: list[Edit] = []
        for e in group:
            kind_value = e["k"]
            kind = "insert" if kind_value == "insert" else "delete"
            edits.append(Edit(kind=kind, pos=e["p"], text=e["t"]))  # type: ignore[arg-type]
        result.append(edits)
    return result


def write_undo_file(filepath: str | Path, stack: list[list[Edit]], redo: list[list[Edit]], dirty_count: int) -> None:
    content = msgpack.packb(
        {
            "v": 1,
            "h": _file_hash(filepath),
            "s": _serialize_entries(stack),
            "r": _serialize_entries(redo),
            "dc": dirty_count,
        }
    )
    path = _undo_path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, content)


def read_undo_file(filepath: str | Path) -> tuple[list[list[Edit]], list[list[Edit]], int] | None:
    """Return (stack, redo, dirty_count) or None if the file is missing, corrupt, or stale."""
    path = _undo_path(filepath)
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        data = msgpack.unpackb(raw)
    except (msgpack.ExtraData, msgpack.FormatError, ValueError, TypeError):
        log.debug("undo file corrupt: %s", path)
        return None

    version = data.get("v", 0)
    if version != 1:
        return None

    stored_hash = data.get("h", "")
    current_hash = _file_hash(filepath)
    if stored_hash != current_hash:
        log.debug("undo file stale (file changed externally): %s", filepath)
        return None

    stack = _deserialize_entries(data.get("s", []))
    redo = _deserialize_entries(data.get("r", []))
    dirty_count = data.get("dc", 0)
    return stack, redo, dirty_count


def delete_undo_file(filepath: str | Path) -> None:
    path = _undo_path(filepath)
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def undo_directory_size() -> tuple[int, int]:
    """Return (file_count, total_bytes) for all undo files."""
    d = _undo_dir()
    if not d.exists():
        return 0, 0
    files = list(d.glob("*.undo"))
    total = sum(f.stat().st_size for f in files if f.is_file())
    return len(files), total
