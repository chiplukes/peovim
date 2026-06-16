"""GitAPI — public facade over the Tier 8 core git wrapper."""

from __future__ import annotations

import pathlib
from collections.abc import Callable

from peovim.git import (
    GitBranchInfo,
    GitCommandError,
    GitLogEntry,
    GitRemote,
    GitRepository,
    GitRepoState,
    GitStatusEntry,
)

# Commands that represent user-triggered operations worth echoing.
# Background polling commands (rev-parse, diff, show, log, status used by
# gitsigns) are excluded so verbose mode isn't spammy.
_ECHO_CMDS = {
    "add",
    "branch",
    "checkout",
    "cherry-pick",
    "commit",
    "fetch",
    "merge",
    "mv",
    "pull",
    "push",
    "rebase",
    "reset",
    "restore",
    "rm",
    "stash",
    "switch",
    "tag",
}


class GitAPI:
    """Git integration API. Spawns git subprocesses through `peovim.git`."""

    def __init__(self) -> None:
        self._repo = GitRepository()
        self._verbose: bool = False
        self._notify_fn: Callable[..., None] | None = None

    @property
    def verbose(self) -> bool:
        """Echo user-triggered git commands as notifications when True."""
        return self._verbose

    @verbose.setter
    def verbose(self, value: bool) -> None:
        self._verbose = value
        self._repo._on_command = self._echo_command if value else None

    def _echo_command(self, args: list[str]) -> None:
        if not args or args[0] not in _ECHO_CMDS:
            return
        cmd = "git " + " ".join(args)
        notify = self._notify_fn
        if callable(notify):
            notify(cmd, level="info", title="git", timeout=4.0)

    def root(self, path: pathlib.Path | None = None) -> pathlib.Path | None:
        return self._repo.root(path)

    def branch(self, path: pathlib.Path | None = None) -> str:
        return self._repo.branch(path)

    def branch_info(self, path: pathlib.Path | None = None) -> GitBranchInfo:
        return self._repo.branch_info(path)

    def status(self, path: pathlib.Path | None = None) -> list[tuple[str, str]]:
        return self._repo.status(path)

    def status_entries(self, path: pathlib.Path | None = None) -> list[GitStatusEntry]:
        return self._repo.status_entries(path)

    def repo_state(self, path: pathlib.Path | None = None) -> GitRepoState | None:
        return self._repo.repo_state(path)

    def list_branches(self, path: pathlib.Path | None = None, *, include_remote: bool = False) -> list[GitBranchInfo]:
        return self._repo.list_branches(path, include_remote=include_remote)

    def remotes(self, path: pathlib.Path | None = None) -> list[GitRemote]:
        return self._repo.remotes(path)

    def log_entries(
        self, path: pathlib.Path | None = None, *, limit: int = 30, ref: str | None = None
    ) -> list[GitLogEntry]:
        return self._repo.log_entries(path, limit=limit, ref=ref)

    def remote_url(self, path: pathlib.Path | None = None, *, remote: str = "origin") -> str | None:
        return self._repo.remote_url(path, remote=remote)

    def create_branch(self, name: str, *, path: pathlib.Path | None = None, start_point: str | None = None) -> None:
        self._repo.create_branch(name, path=path, start_point=start_point)

    def checkout(self, ref: str, *, path: pathlib.Path | None = None) -> None:
        self._repo.checkout(ref, path=path)

    def merge(self, ref: str, *, path: pathlib.Path | None = None) -> None:
        self._repo.merge(ref, path=path)

    def fetch(self, *, path: pathlib.Path | None = None, remote: str | None = None) -> None:
        self._repo.fetch(path=path, remote=remote)

    def pull(self, *, path: pathlib.Path | None = None, remote: str | None = None, branch: str | None = None) -> None:
        self._repo.pull(path=path, remote=remote, branch=branch)

    def push(
        self,
        *,
        path: pathlib.Path | None = None,
        remote: str | None = None,
        branch: str | None = None,
        set_upstream: bool = False,
    ) -> None:
        self._repo.push(path=path, remote=remote, branch=branch, set_upstream=set_upstream)

    def show_file_text(self, repo_path: str, *, path: pathlib.Path | None = None, ref: str = "HEAD") -> str | None:
        return self._repo.show_file_text(repo_path, path=path, ref=ref)

    def commit(self, message: str, *, path: pathlib.Path | None = None) -> None:
        self._repo.commit(message, path=path)

    def stage_file(self, file_path: str, *, path: pathlib.Path | None = None) -> None:
        self._repo.stage_file(file_path, path=path)

    def unstage_file(self, file_path: str, *, path: pathlib.Path | None = None) -> None:
        self._repo.unstage_file(file_path, path=path)

    def discard_file(self, file_path: str, *, path: pathlib.Path | None = None) -> None:
        self._repo.discard_file(file_path, path=path)

    def get_hunks(self, buf_path: pathlib.Path | None = None) -> list[dict]:
        return self._repo.get_hunks(buf_path)


__all__ = [
    "GitAPI",
    "GitBranchInfo",
    "GitCommandError",
    "GitLogEntry",
    "GitRemote",
    "GitRepoState",
    "GitStatusEntry",
]
