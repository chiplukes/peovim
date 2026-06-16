from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from peovim.core.diffing import parse_hunks as _parse_hunks_from_diff

_BRANCH_HEADER_RE = re.compile(r"^## (?P<head>.+?)(?:\.\.\.(?P<upstream>[^\s]+))?(?: \[(?P<tracking>[^\]]+)\])?$")


class GitCommandError(RuntimeError):
    def __init__(self, args: list[str], stderr: str = "", returncode: int | None = None) -> None:
        message = stderr.strip() or f"git command failed: {' '.join(args)}"
        super().__init__(message)
        self.args_list = list(args)
        self.stderr = stderr
        self.returncode = returncode


@dataclass(frozen=True)
class GitStatusEntry:
    code: str
    path: str
    index_status: str
    worktree_status: str
    original_path: str | None = None

    @property
    def display_path(self) -> str:
        if self.original_path is None:
            return self.path
        return f"{self.original_path} -> {self.path}"

    @property
    def staged(self) -> bool:
        return self.index_status not in (" ", "?")

    @property
    def modified(self) -> bool:
        return "M" in (self.index_status, self.worktree_status)

    @property
    def deleted(self) -> bool:
        return "D" in (self.index_status, self.worktree_status)

    @property
    def untracked(self) -> bool:
        return self.code == "??"

    @property
    def conflicted(self) -> bool:
        return self.code in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


@dataclass(frozen=True)
class GitBranchInfo:
    name: str
    upstream: str | None = None
    current: bool = False
    detached: bool = False
    remote: bool = False
    ahead: int = 0
    behind: int = 0
    gone: bool = False


@dataclass(frozen=True)
class GitRemote:
    name: str
    url: str


@dataclass(frozen=True)
class GitLogEntry:
    commit: str
    short_commit: str
    author: str
    relative_date: str
    subject: str
    refs: str = ""


@dataclass(frozen=True)
class GitRepoState:
    root: Path
    branch: GitBranchInfo
    status: list[GitStatusEntry]
    remotes: list[GitRemote]

    @property
    def is_clean(self) -> bool:
        return not self.status


def _parse_tracking_counts(tracking: str | None) -> tuple[int, int, bool]:
    if not tracking:
        return 0, 0, False
    ahead = 0
    behind = 0
    gone = False
    for part in tracking.split(","):
        token = part.strip()
        if token.startswith("ahead "):
            ahead = int(token.removeprefix("ahead "))
        elif token.startswith("behind "):
            behind = int(token.removeprefix("behind "))
        elif token == "gone":
            gone = True
    return ahead, behind, gone


def _parse_branch_header(line: str) -> GitBranchInfo:
    match = _BRANCH_HEADER_RE.match(line)
    if match is None:
        return GitBranchInfo(name="")

    head = match.group("head") or ""
    upstream = match.group("upstream")
    ahead, behind, gone = _parse_tracking_counts(match.group("tracking"))
    detached = head.startswith("HEAD ")
    name = "HEAD" if detached else head
    return GitBranchInfo(
        name=name,
        upstream=upstream,
        current=True,
        detached=detached,
        ahead=ahead,
        behind=behind,
        gone=gone,
    )


def _parse_status_entries(lines: list[str]) -> list[GitStatusEntry]:
    entries: list[GitStatusEntry] = []
    for line in lines:
        if not line or line.startswith("## ") or len(line) < 4:
            continue
        code = line[:2]
        path_text = line[3:]
        original_path: str | None = None
        path = path_text
        if " -> " in path_text:
            original_path, path = path_text.split(" -> ", 1)
        entries.append(
            GitStatusEntry(
                code=code.strip() or code,
                path=path,
                index_status=code[0],
                worktree_status=code[1],
                original_path=original_path,
            )
        )
    return entries


def _parse_log_entries(lines: list[str]) -> list[GitLogEntry]:
    entries: list[GitLogEntry] = []
    for line in lines:
        if not line:
            continue
        parts = line.split("\x1f")
        if len(parts) != 6:
            continue
        commit, short_commit, author, relative_date, refs, subject = parts
        entries.append(
            GitLogEntry(
                commit=commit,
                short_commit=short_commit,
                author=author,
                relative_date=relative_date,
                subject=subject,
                refs=refs,
            )
        )
    return entries


class GitRepository:
    def __init__(self) -> None:
        self._on_command: Callable[[list[str]], None] | None = None

    def _cwd_for(self, path: Path | None) -> str | None:
        if path is None:
            return None
        candidate = Path(path)
        if candidate.exists() and candidate.is_file():
            return str(candidate.parent)
        return str(candidate)

    def _run(
        self,
        args: list[str],
        *,
        path: Path | None = None,
        timeout: int = 10,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if self._on_command is not None:
            self._on_command(args)
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self._cwd_for(path),
            timeout=timeout,
        )
        if check and result.returncode != 0:
            raise GitCommandError(["git", *args], result.stderr, result.returncode)
        return result

    def root(self, path: Path | None = None) -> Path | None:
        try:
            result = self._run(["rev-parse", "--show-toplevel"], path=path, timeout=5)
        except Exception:
            return None
        if result.returncode != 0:
            return None
        stdout = result.stdout.strip()
        return Path(stdout) if stdout else None

    def branch_info(self, path: Path | None = None) -> GitBranchInfo:
        state = self.repo_state(path)
        if state is None:
            return GitBranchInfo(name="")
        return state.branch

    def branch(self, path: Path | None = None) -> str:
        return self.branch_info(path).name

    def status_entries(self, path: Path | None = None) -> list[GitStatusEntry]:
        try:
            result = self._run(["status", "--porcelain", "--branch"], path=path)
        except Exception:
            return []
        if result.returncode != 0:
            return []
        return _parse_status_entries(result.stdout.splitlines())

    def status(self, path: Path | None = None) -> list[tuple[str, str]]:
        return [(entry.code, entry.display_path) for entry in self.status_entries(path)]

    def repo_state(self, path: Path | None = None) -> GitRepoState | None:
        root = self.root(path)
        if root is None:
            return None
        try:
            result = self._run(["status", "--porcelain", "--branch"], path=root)
        except Exception:
            return GitRepoState(root=root, branch=GitBranchInfo(name=""), status=[], remotes=[])
        branch = GitBranchInfo(name="")
        lines = result.stdout.splitlines() if result.returncode == 0 else []
        if lines and lines[0].startswith("## "):
            branch = _parse_branch_header(lines[0])
        status = _parse_status_entries(lines)
        return GitRepoState(root=root, branch=branch, status=status, remotes=self.remotes(root))

    def list_branches(self, path: Path | None = None, *, include_remote: bool = False) -> list[GitBranchInfo]:
        root = self.root(path)
        if root is None:
            return []
        try:
            result = self._run(
                ["branch", "--all", "--format=%(HEAD)\t%(refname:short)\t%(upstream:short)\t%(refname)"],
                path=root,
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []
        current = self.branch_info(root)
        branches: list[GitBranchInfo] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            head_marker, name, upstream, full_ref = (line.split("\t") + ["", "", "", ""])[:4]
            is_remote = full_ref.startswith("refs/remotes/")
            if is_remote and name.endswith("/HEAD"):
                continue
            if is_remote and not include_remote:
                continue
            branches.append(
                GitBranchInfo(
                    name=name,
                    upstream=upstream or None,
                    current=head_marker == "*",
                    detached=False,
                    remote=is_remote,
                    ahead=current.ahead if current.name == name else 0,
                    behind=current.behind if current.name == name else 0,
                    gone=current.gone if current.name == name else False,
                )
            )
        return branches

    def remotes(self, path: Path | None = None) -> list[GitRemote]:
        root = self.root(path)
        if root is None:
            return []
        try:
            result = self._run(["remote"], path=root, timeout=5)
        except Exception:
            return []
        if result.returncode != 0:
            return []
        remotes: list[GitRemote] = []
        for name in result.stdout.splitlines():
            remote_name = name.strip()
            if not remote_name:
                continue
            url = self.remote_url(root, remote=remote_name)
            if url is not None:
                remotes.append(GitRemote(name=remote_name, url=url))
        return remotes

    def remote_url(self, path: Path | None = None, *, remote: str = "origin") -> str | None:
        root = self.root(path)
        if root is None:
            return None
        try:
            result = self._run(["remote", "get-url", remote], path=root, timeout=5)
        except Exception:
            return None
        if result.returncode != 0:
            return None
        stdout = result.stdout.strip()
        return stdout or None

    def commit(self, message: str, *, path: Path | None = None) -> None:
        self._run(["commit", "-m", message], path=path, timeout=30, check=True)

    def stage_file(self, file_path: str, *, path: Path | None = None) -> None:
        self._run(["add", file_path], path=path, check=True)

    def unstage_file(self, file_path: str, *, path: Path | None = None) -> None:
        self._run(["restore", "--staged", file_path], path=path, check=True)

    def discard_file(self, file_path: str, *, path: Path | None = None) -> None:
        self._run(["restore", file_path], path=path, check=True)

    def create_branch(self, name: str, *, path: Path | None = None, start_point: str | None = None) -> None:
        args = ["checkout", "-b", name]
        if start_point:
            args.append(start_point)
        self._run(args, path=path, check=True)

    def checkout(self, ref: str, *, path: Path | None = None) -> None:
        self._run(["checkout", ref], path=path, check=True)

    def merge(self, ref: str, *, path: Path | None = None) -> None:
        self._run(["merge", ref], path=path, timeout=30, check=True)

    def fetch(self, *, path: Path | None = None, remote: str | None = None) -> None:
        args = ["fetch", "--prune"]
        if remote:
            args.append(remote)
        self._run(args, path=path, timeout=30, check=True)

    def pull(self, *, path: Path | None = None, remote: str | None = None, branch: str | None = None) -> None:
        args = ["pull"]
        if remote:
            args.append(remote)
        if branch:
            args.append(branch)
        self._run(args, path=path, timeout=30, check=True)

    def push(
        self,
        *,
        path: Path | None = None,
        remote: str | None = None,
        branch: str | None = None,
        set_upstream: bool = False,
    ) -> None:
        args = ["push"]
        if set_upstream:
            args.append("-u")
        if remote:
            args.append(remote)
        if branch:
            args.append(branch)
        self._run(args, path=path, timeout=30, check=True)

    def show_file_text(self, repo_path: str, *, path: Path | None = None, ref: str = "HEAD") -> str | None:
        root = self.root(path)
        if root is None:
            return None
        git_path = Path(repo_path).as_posix()
        try:
            result = self._run(["show", f"{ref}:{git_path}"], path=root, timeout=10)
        except Exception:
            return None
        if result.returncode != 0:
            return None
        return result.stdout

    def log_entries(self, path: Path | None = None, *, limit: int = 30, ref: str | None = None) -> list[GitLogEntry]:
        root = self.root(path)
        if root is None:
            return []
        args = [
            "log",
            f"--max-count={max(1, limit)}",
            "--decorate=short",
            "--date=relative",
            "--pretty=format:%H%x1f%h%x1f%an%x1f%ar%x1f%D%x1f%s",
        ]
        if ref:
            args.append(ref)
        try:
            result = self._run(args, path=root, timeout=10)
        except Exception:
            return []
        if result.returncode != 0:
            return []
        return _parse_log_entries(result.stdout.splitlines())

    def get_hunks(self, buf_path: Path | None = None) -> list[dict]:
        if buf_path is None:
            return []
        try:
            result = self._run(["diff", "HEAD", "--", str(buf_path)], path=buf_path.parent, timeout=10)
        except Exception:
            return []
        if result.returncode != 0 or not result.stdout:
            return []
        return _parse_hunks_from_diff(result.stdout)
