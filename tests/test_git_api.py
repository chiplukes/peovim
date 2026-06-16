"""Tests for the public git API and Tier 8 core wrapper behavior."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from peovim.git.repository import GitBranchInfo


def _run_result(stdout: str, returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.returncode = returncode
    return r


class TestGetHunks:
    def test_returns_empty_for_none_path(self):
        from peovim.api.git_api import GitAPI

        assert GitAPI().get_hunks(None) == []

    def test_returns_empty_on_git_failure(self, tmp_path):
        from peovim.api.git_api import GitAPI

        f = tmp_path / "f.py"
        f.touch()
        with patch("subprocess.run", return_value=_run_result("", returncode=1)):
            assert GitAPI().get_hunks(f) == []

    def test_returns_empty_on_exception(self, tmp_path):
        from peovim.api.git_api import GitAPI

        f = tmp_path / "f.py"
        f.touch()
        with patch("subprocess.run", side_effect=OSError("no git")):
            assert GitAPI().get_hunks(f) == []

    def test_returns_empty_on_clean_diff(self, tmp_path):
        from peovim.api.git_api import GitAPI

        f = tmp_path / "f.py"
        f.touch()
        with patch("subprocess.run", return_value=_run_result("")):
            assert GitAPI().get_hunks(f) == []

    def test_parses_add_hunk(self, tmp_path):
        from peovim.api.git_api import GitAPI

        f = tmp_path / "f.py"
        f.touch()
        diff = "@@ -5,0 +6,3 @@ def foo():\n+line1\n+line2\n+line3\n"
        with patch("subprocess.run", return_value=_run_result(diff)):
            hunks = GitAPI().get_hunks(f)
        assert len(hunks) == 1
        h = hunks[0]
        assert h["type"] == "add"
        assert h["start"] == 5  # 0-based: new_start=6 → 5
        assert h["end"] == 7  # 5 + 3 - 1

    def test_parses_delete_hunk(self, tmp_path):
        from peovim.api.git_api import GitAPI

        f = tmp_path / "f.py"
        f.touch()
        diff = "@@ -3,2 +3,0 @@ def foo():\n-line1\n-line2\n"
        with patch("subprocess.run", return_value=_run_result(diff)):
            hunks = GitAPI().get_hunks(f)
        assert len(hunks) == 1
        h = hunks[0]
        assert h["type"] == "delete"
        assert h["start"] == h["end"]  # single-line indicator

    def test_parses_change_hunk(self, tmp_path):
        from peovim.api.git_api import GitAPI

        f = tmp_path / "f.py"
        f.touch()
        diff = "@@ -10,3 +10,3 @@ def bar():\n-old\n-old\n-old\n+new\n+new\n+new\n"
        with patch("subprocess.run", return_value=_run_result(diff)):
            hunks = GitAPI().get_hunks(f)
        assert len(hunks) == 1
        h = hunks[0]
        assert h["type"] == "change"
        assert h["start"] == 9  # 0-based
        assert h["end"] == 11

    def test_parses_multiple_hunks(self, tmp_path):
        from peovim.api.git_api import GitAPI

        f = tmp_path / "f.py"
        f.touch()
        diff = "@@ -1,0 +2,1 @@ header\n+added\n@@ -20,1 +21,0 @@ footer\n-deleted\n"
        with patch("subprocess.run", return_value=_run_result(diff)):
            hunks = GitAPI().get_hunks(f)
        assert len(hunks) == 2
        assert hunks[0]["type"] == "add"
        assert hunks[1]["type"] == "delete"

    def test_single_line_hunk_no_comma(self, tmp_path):
        """@@ -5 +5 @@ (no count defaults to 1)"""
        from peovim.api.git_api import GitAPI

        f = tmp_path / "f.py"
        f.touch()
        diff = "@@ -5 +5 @@\n-old\n+new\n"
        with patch("subprocess.run", return_value=_run_result(diff)):
            hunks = GitAPI().get_hunks(f)
        assert len(hunks) == 1
        assert hunks[0]["type"] == "change"
        assert hunks[0]["start"] == 4  # 0-based

    def test_delete_at_line_zero_clamped(self, tmp_path):
        """Deletion at the very start of the file should not give start=-1."""
        from peovim.api.git_api import GitAPI

        f = tmp_path / "f.py"
        f.touch()
        diff = "@@ -1,1 +1,0 @@\n-first\n"
        with patch("subprocess.run", return_value=_run_result(diff)):
            hunks = GitAPI().get_hunks(f)
        assert hunks[0]["start"] >= 0


class TestStatusAndBranchQueries:
    def test_root_uses_parent_for_file_paths(self, tmp_path):
        from peovim.api.git_api import GitAPI

        repo = tmp_path / "repo"
        repo.mkdir()
        file_path = repo / "file.py"
        file_path.write_text("print('x')")

        with patch("subprocess.run", return_value=_run_result(str(repo))) as run_mock:
            root = GitAPI().root(file_path)

        assert root == repo
        assert run_mock.call_args.kwargs["cwd"] == str(repo)

    def test_branch_info_parses_upstream_and_tracking(self, tmp_path):
        from peovim.api.git_api import GitAPI

        repo = tmp_path / "repo"
        repo.mkdir()

        def _run_side_effect(cmd, **kwargs):
            if cmd[1:] == ["rev-parse", "--show-toplevel"]:
                return _run_result(str(repo))
            if cmd[1:] == ["status", "--porcelain", "--branch"]:
                return _run_result("## main...origin/main [ahead 2, behind 1]\n M src/app.py\n")
            if cmd[1:] == ["remote"]:
                return _run_result("")
            raise AssertionError(cmd)

        with patch("subprocess.run", side_effect=_run_side_effect):
            branch = GitAPI().branch_info(repo)

        assert branch == GitBranchInfo(
            name="main",
            upstream="origin/main",
            current=True,
            detached=False,
            ahead=2,
            behind=1,
            gone=False,
        )

    def test_status_entries_parse_untracked_and_rename(self, tmp_path):
        from peovim.api.git_api import GitAPI

        repo = tmp_path / "repo"
        repo.mkdir()

        def _run_side_effect(cmd, **kwargs):
            if cmd[1:] == ["status", "--porcelain", "--branch"]:
                return _run_result("## feature\n?? new.txt\nR  old.py -> new.py\n")
            raise AssertionError(cmd)

        with patch("subprocess.run", side_effect=_run_side_effect):
            api = GitAPI()
            entries = api.status_entries(repo)
            status_pairs = api.status(repo)

        assert len(entries) == 2
        assert entries[0].untracked is True
        assert entries[0].path == "new.txt"
        assert entries[1].original_path == "old.py"
        assert entries[1].path == "new.py"
        assert status_pairs == [("??", "new.txt"), ("R", "old.py -> new.py")]

    def test_repo_state_collects_branch_status_and_remotes(self, tmp_path):
        from peovim.api.git_api import GitAPI

        repo = tmp_path / "repo"
        repo.mkdir()

        def _run_side_effect(cmd, **kwargs):
            match cmd[1:]:
                case ["rev-parse", "--show-toplevel"]:
                    return _run_result(str(repo))
                case ["status", "--porcelain", "--branch"]:
                    return _run_result("## main...origin/main [ahead 1]\n M src/app.py\n")
                case ["remote"]:
                    return _run_result("origin\nupstream\n")
                case ["remote", "get-url", "origin"]:
                    return _run_result("git@example.com:origin/repo.git\n")
                case ["remote", "get-url", "upstream"]:
                    return _run_result("git@example.com:upstream/repo.git\n")
                case _:
                    raise AssertionError(cmd)

        with patch("subprocess.run", side_effect=_run_side_effect):
            state = GitAPI().repo_state(repo)

        assert state is not None
        assert state.root == repo
        assert state.branch.name == "main"
        assert state.branch.ahead == 1
        assert state.status[0].modified is True
        assert [remote.name for remote in state.remotes] == ["origin", "upstream"]

    def test_list_branches_marks_current_branch(self, tmp_path):
        from peovim.api.git_api import GitAPI

        repo = tmp_path / "repo"
        repo.mkdir()

        def _run_side_effect(cmd, **kwargs):
            match cmd[1:]:
                case ["rev-parse", "--show-toplevel"]:
                    return _run_result(str(repo))
                case ["status", "--porcelain", "--branch"]:
                    return _run_result("## main...origin/main [ahead 3]\n")
                case ["branch", "--all", "--format=%(HEAD)\t%(refname:short)\t%(upstream:short)\t%(refname)"]:
                    return _run_result(
                        "*\tmain\torigin/main\trefs/heads/main\n"
                        " \tfeature\torigin/feature\trefs/heads/feature\n"
                        " \torigin/main\t\trefs/remotes/origin/main\n"
                    )
                case ["remote"]:
                    return _run_result("")
                case _:
                    raise AssertionError(cmd)

        with patch("subprocess.run", side_effect=_run_side_effect):
            branches = GitAPI().list_branches(repo)

        assert len(branches) == 2
        assert branches[0].current is True
        assert branches[0].ahead == 3
        assert branches[1].current is False

    def test_list_branches_can_include_remote_tracking_refs(self, tmp_path):
        from peovim.api.git_api import GitAPI

        repo = tmp_path / "repo"
        repo.mkdir()

        def _run_side_effect(cmd, **kwargs):
            match cmd[1:]:
                case ["rev-parse", "--show-toplevel"]:
                    return _run_result(str(repo))
                case ["status", "--porcelain", "--branch"]:
                    return _run_result("## main...origin/main\n")
                case ["branch", "--all", "--format=%(HEAD)\t%(refname:short)\t%(upstream:short)\t%(refname)"]:
                    return _run_result(
                        "*\tmain\torigin/main\trefs/heads/main\n"
                        " \tfeature\torigin/feature\trefs/heads/feature\n"
                        " \torigin/feature\t\trefs/remotes/origin/feature\n"
                        " \torigin/HEAD\t\trefs/remotes/origin/HEAD\n"
                    )
                case ["remote"]:
                    return _run_result("")
                case _:
                    raise AssertionError(cmd)

        with patch("subprocess.run", side_effect=_run_side_effect):
            branches = GitAPI().list_branches(repo, include_remote=True)

        assert [branch.name for branch in branches] == ["main", "feature", "origin/feature"]
        assert branches[2].remote is True


class TestGitCommands:
    def test_git_commands_force_utf8_decoding(self, tmp_path):
        from peovim.api.git_api import GitAPI

        repo = tmp_path / "repo"
        repo.mkdir()

        def _run_side_effect(cmd, **kwargs):
            assert kwargs["text"] is True
            assert kwargs["encoding"] == "utf-8"
            assert kwargs["errors"] == "replace"
            match cmd[1:]:
                case ["rev-parse", "--show-toplevel"]:
                    return _run_result(str(repo))
                case ["show", "HEAD:src/app.py"]:
                    return _run_result("print('↔')\n")
                case _:
                    raise AssertionError(cmd)

        with patch("subprocess.run", side_effect=_run_side_effect):
            text = GitAPI().show_file_text("src/app.py", path=repo)

        assert text == "print('↔')\n"

    def test_remote_url_returns_none_when_not_in_repo(self, tmp_path):
        from peovim.api.git_api import GitAPI

        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_run_result("", returncode=1)):
            assert GitAPI().remote_url(repo) is None

    def test_create_branch_invokes_checkout_b(self, tmp_path):
        from peovim.api.git_api import GitAPI

        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_run_result("")) as run_mock:
            GitAPI().create_branch("feature/test", path=repo, start_point="main")

        assert run_mock.call_args.args[0] == ["git", "checkout", "-b", "feature/test", "main"]

    def test_checkout_merge_fetch_pull_and_push_use_expected_git_commands(self, tmp_path):
        from peovim.api.git_api import GitAPI

        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_run_result("")) as run_mock:
            api = GitAPI()
            api.checkout("feature/test", path=repo)
            api.merge("feature/test", path=repo)
            api.fetch(path=repo, remote="origin")
            api.pull(path=repo, remote="origin", branch="main")
            api.push(path=repo, remote="origin", branch="main", set_upstream=True)

        commands = [call.args[0] for call in run_mock.call_args_list]
        assert commands == [
            ["git", "checkout", "feature/test"],
            ["git", "merge", "feature/test"],
            ["git", "fetch", "--prune", "origin"],
            ["git", "pull", "origin", "main"],
            ["git", "push", "-u", "origin", "main"],
        ]

    def test_show_file_text_uses_git_show(self, tmp_path):
        from peovim.api.git_api import GitAPI

        repo = tmp_path / "repo"
        repo.mkdir()

        def _run_side_effect(cmd, **kwargs):
            match cmd[1:]:
                case ["rev-parse", "--show-toplevel"]:
                    return _run_result(str(repo))
                case ["show", "HEAD:src/app.py"]:
                    return _run_result("print('old')\n")
                case _:
                    raise AssertionError(cmd)

        with patch("subprocess.run", side_effect=_run_side_effect):
            text = GitAPI().show_file_text("src/app.py", path=repo)

        assert text == "print('old')\n"

    def test_log_entries_parse_commit_metadata(self, tmp_path):
        from peovim.api.git_api import GitAPI

        repo = tmp_path / "repo"
        repo.mkdir()

        def _run_side_effect(cmd, **kwargs):
            match cmd[1:]:
                case ["rev-parse", "--show-toplevel"]:
                    return _run_result(str(repo))
                case [*prefix, "main"] if prefix[:4] == [
                    "log",
                    "--max-count=30",
                    "--decorate=short",
                    "--date=relative",
                ]:
                    return _run_result(
                        "abc123def\x1fabc123d\x1fAlice\x1f2 hours ago\x1fHEAD -> main, origin/main\x1fAdd log browser\n"
                    )
                case _:
                    raise AssertionError(cmd)

        with patch("subprocess.run", side_effect=_run_side_effect):
            entries = GitAPI().log_entries(repo, ref="main")

        assert len(entries) == 1
        assert entries[0].author == "Alice"
        assert entries[0].subject == "Add log browser"
        assert entries[0].refs == "HEAD -> main, origin/main"
