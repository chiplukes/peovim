"""
core.recovery — Crash-recovery store for unsaved buffer content.

Writes periodic snapshots of dirty buffers to a recovery directory so that
edits survive an unexpected process death.  Recovery files are keyed by a
per-instance UUID so multiple editor instances never clobber each other.

Usage pattern
-------------
On startup:
    store = RecoveryStore.for_this_session()
    store.write_lockfile()
    for path_str, rec_file in store.list_orphans():
        print(f"Recovery file found for {path_str}")

On each autosave tick:
    store.write(doc.path, doc.get_text())

On successful :w save:
    store.delete(doc.path)

On clean exit:
    store.cleanup_session(list_of_paths)   # delete our own recovery files
    store.delete_lockfile()
"""

from __future__ import annotations

import contextlib
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs

from peovim.core.persistence import atomic_write_text

if TYPE_CHECKING:
    pass

# Maximum sanitized-path segment length to stay well under MAX_PATH on Windows.
_MAX_SEGMENT = 180


def _sanitize_path(path: Path) -> str:
    """Convert a filesystem path to a safe filename fragment."""
    text = str(path)
    # Replace path separators and common problematic characters
    sanitized = re.sub(r'[:/\\<>"|?* ]', "_", text)
    # Collapse consecutive underscores
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return sanitized[:_MAX_SEGMENT] or "unnamed"


def _recovery_dir() -> Path:
    return Path(platformdirs.user_data_dir("peovim")) / "recovery"


class RecoveryStore:
    """Manages per-instance crash-recovery files."""

    def __init__(self, instance_uuid: str, recovery_dir: Path | None = None) -> None:
        self._uuid = instance_uuid
        self._dir = recovery_dir or _recovery_dir()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def for_this_session(cls, recovery_dir: Path | None = None) -> RecoveryStore:
        """Create a store with a fresh UUID for this editor session."""
        return cls(str(uuid.uuid4()), recovery_dir)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _recovery_file(self, path: Path) -> Path:
        name = f"{self._uuid}_{_sanitize_path(path)}.txt"
        return self._dir / name

    def _lockfile(self) -> Path:
        return self._dir / f"{self._uuid}.lock"

    # ------------------------------------------------------------------
    # Lockfile (marks this session as alive)
    # ------------------------------------------------------------------

    def write_lockfile(self) -> None:
        """Create the per-session lockfile.  Must be called on startup."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lockfile().write_text(self._uuid, encoding="utf-8")

    def delete_lockfile(self) -> None:
        """Delete the lockfile on clean exit."""
        with contextlib.suppress(OSError):
            self._lockfile().unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Recovery file I/O
    # ------------------------------------------------------------------

    def write(self, path: Path, text: str) -> None:
        """Atomically write a recovery snapshot for *path*."""
        self._dir.mkdir(parents=True, exist_ok=True)
        # save policy: single-writer (best-effort; two instances on the same file share
        # a recovery path — last write wins, which is acceptable for crash recovery)
        atomic_write_text(self._recovery_file(path), text)

    def exists_for_path(self, path: Path) -> bool:
        return self._recovery_file(path).exists()

    def read(self, path: Path) -> str:
        return self._recovery_file(path).read_text(encoding="utf-8")

    def delete(self, path: Path) -> None:
        """Delete the recovery file for *path* (e.g. after a successful save)."""
        with contextlib.suppress(OSError):
            self._recovery_file(path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Orphan discovery (crashed sessions)
    # ------------------------------------------------------------------

    def list_orphans(self) -> list[tuple[str, Path]]:
        """Return ``(original_path_str, recovery_file)`` pairs from crashed sessions.

        A recovery file is considered an orphan when:
        - Its UUID prefix does NOT match the current session (we skip our own).
        - No ``<uuid>.lock`` file exists for that UUID (the session crashed).
        """
        if not self._dir.exists():
            return []

        orphans: list[tuple[str, Path]] = []
        # Map uuid → lockfile-exists for caching within this call
        lock_exists: dict[str, bool] = {}

        for rec_file in sorted(self._dir.glob("*.txt")):
            name = rec_file.stem  # e.g. "abc123_C__foo_bar_py"
            # First segment is the UUID (36-char hyphenated or shorter)
            idx = name.find("_")
            if idx < 1:
                continue
            file_uuid = name[:idx]
            path_fragment = name[idx + 1 :]

            # Skip files from the current session
            if file_uuid == self._uuid:
                continue

            # Check lock once per UUID
            if file_uuid not in lock_exists:
                lock_exists[file_uuid] = (self._dir / f"{file_uuid}.lock").exists()

            if lock_exists[file_uuid]:
                # Another live session owns this file — skip
                continue

            # Convert sanitized fragment back to something readable (best-effort)
            orphans.append((path_fragment, rec_file))

        return orphans

    # ------------------------------------------------------------------
    # Clean-quit housekeeping
    # ------------------------------------------------------------------

    def cleanup_session(self, paths: list[Path]) -> None:
        """Delete all recovery files we wrote for *paths*."""
        for path in paths:
            self.delete(path)
