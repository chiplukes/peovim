"""
config.project — Project root detection and local config trust store

editor.find_root() searches upward for markers (.git, pyproject.toml, etc.).
TrustStore tracks per-project .peovim/init.py execution permission.

Project-local config trust uses a persisted yes/no decision with a prompt on
first encounter.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.core.shada import ShadaStore

# Marker files/dirs that indicate a project root
_ROOT_MARKERS: list[str] = [
    ".git",
    ".peovim",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Cargo.toml",
    "go.mod",
    "package.json",
    "CMakeLists.txt",
]


def find_project_root(  # cm:b7a6d5
    start: Path,
    markers: list[str] | None = None,
) -> Path | None:
    """
    Walk up the directory tree from start looking for any marker.
    Returns the directory containing the marker, or None if not found.
    """
    if markers is None:
        markers = _ROOT_MARKERS
    current = start if start.is_dir() else start.parent
    while True:
        for m in markers:
            if (current / m).exists():
                return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def find_project_config(start: Path) -> Path | None:
    """
    Return the path to .peovim/init.py for the project containing start, or None.
    The project root is detected by find_project_root().
    """
    root = find_project_root(start)
    if root is None:
        return None
    candidate = root / ".peovim" / "init.py"
    return candidate if candidate.is_file() else None


class TrustStore:
    """
    Decides whether a project's .peovim/init.py may be executed.

    Decisions are persisted via shada. Unknown projects prompt once with a
    safe default of "No" when interactive input is unavailable.
    """

    def __init__(
        self,
        shada: ShadaStore | None = None,
        prompt_fn: Callable[[Path, Path], bool] | None = None,
    ) -> None:
        self._shada = shada
        self._prompt_fn = prompt_fn or _prompt_for_project_trust

    def attach_shada(self, shada: ShadaStore) -> None:
        self._shada = shada

    def get_decision(self, project_root: Path) -> bool | None:
        if self._shada is None:
            return None
        return self._shada.get_project_trust(str(project_root.resolve()))

    def is_trusted(self, project_root: Path) -> bool:
        """Return True if the project config may be executed, prompting if needed."""
        root = project_root.resolve()
        decision = self.get_decision(root)
        if decision is not None:
            return decision

        trusted = bool(self._prompt_fn(root, root / ".peovim" / "init.py"))
        self.set_trusted(root, trusted)
        return trusted

    def set_trusted(self, project_root: Path, trusted: bool) -> None:
        """Persist a trust decision."""
        if self._shada is not None:
            self._shada.set_project_trust(str(project_root.resolve()), trusted)


def _prompt_for_project_trust(project_root: Path, config_path: Path) -> bool:
    """Prompt for whether a project's config should be trusted."""
    try:
        reply = input(f"Trust project config? {config_path} [y/N]: ")
    except (EOFError, OSError):
        return False
    return reply.strip().lower() in {"y", "yes"}
