"""Persistence helpers for durable file writes."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically replace ``path`` with ``data`` written in the same directory.

    Falls back to a direct, non-atomic write to ``path`` if a temp file can't
    even be *created* there (e.g. the directory permits modifying an existing
    file but not creating new entries in it — restrictive directory ACLs, some
    network mounts, etc.). This mirrors what other editors (Vim included) do in
    that situation: prefer atomic replace, but don't refuse to save a file the
    user can plainly write to just because the directory is locked down.

    A failure *after* the temp file is created (writing to it, or the final
    `os.replace`) is not downgraded this way — that's a different, less
    predictable failure mode, and the original file is left untouched and the
    error re-raised rather than risking a partial overwrite. The one
    exception is `os.replace` itself raising `FileExistsError`: on a real
    POSIX filesystem `rename()` always atomically replaces an existing
    destination and can never fail with EEXIST, so seeing it here means the
    filesystem doesn't actually support atomic replace-over-an-existing-file
    (notably GVFS's FUSE bridge for AFP/SMB network mounts). In that specific
    case we retry once via unlink-then-replace — the temp file's contents are
    already fully written and fsynced, so this only narrows (not removes) the
    atomicity guarantee rather than failing the save outright.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    except OSError:
        _write_bytes_in_place(path, data)
        return
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            with contextlib.suppress(OSError):
                os.fsync(handle.fileno())
        try:
            os.replace(tmp_path, path)
        except FileExistsError:
            os.remove(path)
            os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError, OSError):
            tmp_path.unlink()
        raise


def _write_bytes_in_place(path: Path, data: bytes) -> None:
    """Non-atomic fallback: write ``data`` directly into ``path``."""
    with open(path, "wb") as handle:
        handle.write(data)
        handle.flush()
        with contextlib.suppress(OSError):
            os.fsync(handle.fileno())


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace ``path`` with encoded text."""
    atomic_write_bytes(path, text.encode(encoding))
