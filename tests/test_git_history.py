"""Commit browsing and comparisons against real repositories."""

from pathlib import Path
from subprocess import run
from unittest.mock import MagicMock

import pytest

from peovim.api.git_api import GitAPI
from peovim.plugins import gitsigns


def git(repo, *args):
    return run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test Author")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "app.py").write_text("old\n")
    (repo / "old name.py").write_text("rename me\n")
    (repo / "gone.py").write_text("deleted\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial files")
    initial = git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("committed\n")
    (repo / "old name.py").rename(repo / "new name.py")
    (repo / "gone.py").unlink()
    (repo / "tab\tand\nnewline.py").write_text("added\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "Change files")
    head = git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("working\n")
    api = MagicMock()
    api.git = GitAPI()
    api.active_buffer.return_value.path = repo / "app.py"
    api.events.handler_count.return_value = 1
    yield repo, api, initial, head
    gitsigns._revision_sources.clear()


def compared_text(api):
    args = api.events.emit.call_args.kwargs
    return tuple(Path(args[side]).read_text() for side in ("left", "right"))


def test_commit_files_preserve_renames_deletions_and_unusual_paths(history):
    repo, api, initial, head = history
    files = {e.path: e for e in api.git.commit_files(head, path=repo)}
    assert files["new name.py"].original_path == "old name.py"
    assert files["gone.py"].code == "D"
    assert files["tab\tand\nnewline.py"].code == "A"
    assert {e.code for e in api.git.commit_files(initial, path=repo)} == {"A"}


def test_merge_commit_files_compare_only_first_parent(history):
    repo, api, _initial, _head = history
    git(repo, "restore", "app.py")
    git(repo, "checkout", "-b", "feature")
    (repo / "feature.py").write_text("feature\n")
    git(repo, "add", "feature.py")
    git(repo, "commit", "-m", "Feature")
    git(repo, "checkout", "main")
    (repo / "main.py").write_text("main\n")
    git(repo, "add", "main.py")
    git(repo, "commit", "-m", "Main")
    git(repo, "merge", "--no-ff", "feature", "-m", "Merge")
    assert [e.path for e in api.git.commit_files("HEAD", path=repo)] == ["feature.py"]


def test_history_loads_files_lazily_and_preserves_expansion(history):
    repo, api, _initial, head = history
    api.git.commit_files = MagicMock(wraps=api.git.commit_files)
    panel = gitsigns._GitStatusSidebarPanel(api)
    panel.refresh()
    api.git.commit_files.assert_not_called()
    section = panel._tree._roots[-1]
    commit = section.get_children()[0]
    assert head.startswith(commit.label.split()[0])
    panel._tree.expand(commit)
    api.git.commit_files.assert_called_once_with(head, path=repo)
    leaf = next(n for n in commit.get_children() if n.value[-1].path == "app.py")
    panel._tree.select_value(leaf.value)
    panel.refresh()
    assert panel._tree.selected_node.value == leaf.value
    panel.feed_key("<CR>")
    assert compared_text(api) == ("old\n", "committed\n")
    panel.diff_selected(working=True)
    assert compared_text(api) == ("committed\n", "working\n")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("app.py", ("old\n", "committed\n")),
        ("gone.py", ("deleted\n", "")),
        ("new name.py", ("rename me\n", "rename me\n")),
        ("tab\tand\nnewline.py", ("", "added\n")),
    ],
)
def test_previous_compares_committed_content_with_parent(history, name, expected):
    repo, api, _initial, _head = history
    assert gitsigns._cmd_diff_previous(api, str(repo / name))
    assert compared_text(api) == expected


def test_initial_commit_compares_against_empty_file(history):
    repo, api, initial, _head = history
    entry = next(e for e in api.git.commit_files(initial, path=repo) if e.path == "app.py")
    assert gitsigns._diff_commit_file(api, repo, initial, entry, working=False)
    assert compared_text(api) == ("", "old\n")


def test_revision_snapshot_remembers_commit_for_working_and_previous(history):
    _repo, api, _initial, _head = history
    assert gitsigns._cmd_diff_previous(api, "")
    revision = api.events.emit.call_args.kwargs["right"]
    api.active_buffer.return_value.path = Path(revision)
    assert gitsigns._cmd_diff_head(api, "")
    assert compared_text(api) == ("committed\n", "working\n")
    assert gitsigns._cmd_diff_previous(api, "")
    assert compared_text(api) == ("old\n", "committed\n")


def test_missing_viewer_and_unborn_repository_do_not_launch_diff(history, tmp_path):
    _repo, api, _initial, _head = history
    api.events.handler_count.return_value = 0
    assert not gitsigns._cmd_diff_previous(api, "")
    api.events.emit.assert_not_called()
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init")
    assert not gitsigns._cmd_diff_previous(api, str(empty / "new.py"))
    assert gitsigns._recent_commit_nodes(api, empty)[0].label == "No commits yet"
