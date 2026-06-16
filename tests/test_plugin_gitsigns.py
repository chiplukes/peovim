"""
tests.test_plugin_gitsigns — Tests for peovim.plugins.gitsigns (Phase 6g)

Covers: setup, sign registration, sign placement per hunk type,
namespace clearing, hunk navigation, command stubs, debounce edge cases.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from peovim.git import GIT_MODIFIED_COLOR, GIT_UNTRACKED_COLOR
from peovim.git.repository import GitStatusEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api(hunks: list[dict] | None = None, buf_path: Path | None = None) -> tuple:
    api = MagicMock()
    buf = _make_buf(buf_path=buf_path)
    api.active_buffer.return_value = buf
    api.active_window.return_value = MagicMock()
    api.active_window.return_value.cursor = (5, 0)
    api.list_buffers.return_value = [buf]
    api.git.get_hunks.return_value = hunks or []
    return api, buf


def _make_buf(buf_id: int = 1, buf_path: Path | None = None) -> MagicMock:
    buf = MagicMock()
    buf.buf_id = buf_id
    buf.path = buf_path or Path("/fake/file.py")
    return buf


# ---------------------------------------------------------------------------
# setup()
# ---------------------------------------------------------------------------


class TestSetup:
    def test_registers_sign_types(self):
        from peovim.plugins.gitsigns import setup

        api, _ = _make_api()
        setup(api)
        names = [c.args[0] for c in api.register_sign_type.call_args_list]
        assert "gitsigns.add" in names
        assert "gitsigns.change" in names
        assert "gitsigns.delete" in names

    def test_subscribes_to_buffer_events(self):
        from peovim.plugins.gitsigns import setup

        api, _ = _make_api()
        setup(api)
        events = [c.args[0] for c in api.events.on.call_args_list]
        assert "buffer_opened" in events
        assert "buffer_saved" in events
        assert "buffer_changed" in events

    def test_registers_commands(self):
        from peovim.plugins.gitsigns import setup

        api, _ = _make_api()
        setup(api)
        cmd_names = [c.args[0] for c in api.commands.register.call_args_list]
        assert "GitBranchCreate" in cmd_names
        assert "GitCheckout" in cmd_names
        assert "GitFetch" in cmd_names
        assert "GitMergeBranch" in cmd_names
        assert "GitPull" in cmd_names
        assert "GitPush" in cmd_names
        assert "GitLog" in cmd_names
        assert "GitStageFile" in cmd_names
        assert "GitUnstageFile" in cmd_names
        assert "GitDiscardFile" in cmd_names
        assert "GitCompareFile" in cmd_names
        assert "GitDiffFile" in cmd_names
        assert "gitpanel" in cmd_names
        assert "githunkpreview" in cmd_names
        assert "gitstagunk" in cmd_names
        assert "gitresethunk" in cmd_names
        assert "gitstatuspanel" in cmd_names

    def test_registers_hunk_navigation_keys(self):
        from peovim.plugins.gitsigns import setup

        api, _ = _make_api()
        setup(api)
        keys = [c.args[0] for c in api.keymap.nmap.call_args_list]
        assert "]c" in keys
        assert "[c" in keys
        assert "<leader>gs" in keys

    def test_registers_sidebar_panel(self):
        from peovim.plugins.gitsigns import setup

        api, _ = _make_api()
        setup(api)
        api.ui.register_sidebar_panel.assert_called()

    def test_scans_existing_buffers_on_setup(self):
        from peovim.plugins.gitsigns import setup

        api, buf = _make_api()
        setup(api)
        buf.clear_namespace.assert_called_with("gitsigns")


# ---------------------------------------------------------------------------
# _update_signs
# ---------------------------------------------------------------------------


class TestUpdateSigns:
    def test_clears_namespace_before_placing(self):
        from peovim.plugins.gitsigns import _update_signs

        api, buf = _make_api(hunks=[{"type": "add", "start": 2, "end": 3}])
        _update_signs(api, buf)
        buf.clear_namespace.assert_called_with("gitsigns")

    def test_places_add_sign(self):
        from peovim.plugins.gitsigns import _update_signs

        api, buf = _make_api(hunks=[{"type": "add", "start": 0, "end": 0}])
        _update_signs(api, buf)
        buf.add_sign.assert_called()
        sign_type = buf.add_sign.call_args_list[0].args[2]
        assert sign_type == "gitsigns.add"

    def test_places_change_sign(self):
        from peovim.plugins.gitsigns import _update_signs

        api, buf = _make_api(hunks=[{"type": "change", "start": 5, "end": 7}])
        _update_signs(api, buf)
        for c in buf.add_sign.call_args_list:
            assert c.args[2] == "gitsigns.change"

    def test_places_delete_sign(self):
        from peovim.plugins.gitsigns import _update_signs

        api, buf = _make_api(hunks=[{"type": "delete", "start": 3, "end": 3}])
        _update_signs(api, buf)
        buf.add_sign.assert_called_once()
        assert buf.add_sign.call_args_list[0].args[2] == "gitsigns.delete"

    def test_one_sign_per_hunk_line(self):
        from peovim.plugins.gitsigns import _update_signs

        # 3-line change hunk → 3 signs
        api, buf = _make_api(hunks=[{"type": "change", "start": 2, "end": 4}])
        _update_signs(api, buf)
        assert buf.add_sign.call_count == 3

    def test_no_path_skips_git_call(self):
        from peovim.plugins.gitsigns import _update_signs

        api = MagicMock()
        buf = MagicMock()
        buf.path = None
        _update_signs(api, buf)
        api.git.get_hunks.assert_not_called()

    def test_git_exception_is_swallowed(self):
        from peovim.plugins.gitsigns import _update_signs

        api, buf = _make_api()
        api.git.get_hunks.side_effect = RuntimeError("git broke")
        _update_signs(api, buf)  # must not raise

    def test_no_signs_when_no_hunks(self):
        from peovim.plugins.gitsigns import _update_signs

        api, buf = _make_api(hunks=[])
        _update_signs(api, buf)
        buf.add_sign.assert_not_called()


# ---------------------------------------------------------------------------
# Hunk navigation
# ---------------------------------------------------------------------------


class TestHunkNavigation:
    def test_next_hunk_moves_cursor_forward(self):
        from peovim.plugins.gitsigns import _next_hunk

        api, _ = _make_api(hunks=[{"type": "add", "start": 10, "end": 10}])
        win = api.active_window.return_value
        win.cursor = (3, 0)
        _next_hunk(api)
        win.set_cursor.assert_called_with(10, 0)

    def test_next_hunk_skips_current_line(self):
        """Cursor is on line 10 → jump to 15, not back to 10."""
        from peovim.plugins.gitsigns import _next_hunk

        api, _ = _make_api(
            hunks=[
                {"type": "add", "start": 10, "end": 10},
                {"type": "add", "start": 15, "end": 15},
            ]
        )
        win = api.active_window.return_value
        win.cursor = (10, 0)
        _next_hunk(api)
        win.set_cursor.assert_called_with(15, 0)

    def test_next_hunk_no_hunks_after_cursor(self):
        from peovim.plugins.gitsigns import _next_hunk

        api, _ = _make_api(hunks=[{"type": "add", "start": 2, "end": 2}])
        win = api.active_window.return_value
        win.cursor = (10, 0)
        _next_hunk(api)
        win.set_cursor.assert_not_called()

    def test_prev_hunk_moves_cursor_backward(self):
        from peovim.plugins.gitsigns import _prev_hunk

        api, _ = _make_api(hunks=[{"type": "change", "start": 3, "end": 4}])
        win = api.active_window.return_value
        win.cursor = (10, 0)
        _prev_hunk(api)
        win.set_cursor.assert_called_with(3, 0)

    def test_prev_hunk_no_hunks_before_cursor(self):
        from peovim.plugins.gitsigns import _prev_hunk

        api, _ = _make_api(hunks=[{"type": "change", "start": 20, "end": 21}])
        win = api.active_window.return_value
        win.cursor = (5, 0)
        _prev_hunk(api)
        win.set_cursor.assert_not_called()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


class TestCommands:
    def test_stage_hunk_notifies_not_implemented(self):
        from peovim.plugins.gitsigns import _cmd_stub

        api = MagicMock()
        _cmd_stub(api, "GitStageHunk")
        api.ui.notify.assert_called_once()
        assert "GitStageHunk" in api.ui.notify.call_args.args[0]

    def test_reset_hunk_notifies_not_implemented(self):
        from peovim.plugins.gitsigns import _cmd_stub

        api = MagicMock()
        _cmd_stub(api, "GitResetHunk")
        api.ui.notify.assert_called_once()

    def test_hunk_preview_opens_float(self):
        from peovim.plugins.gitsigns import _cmd_hunk_preview

        api, _ = _make_api(hunks=[{"type": "change", "start": 5, "end": 7}])
        win = api.active_window.return_value
        win.cursor = (6, 0)
        _cmd_hunk_preview(api)
        api.ui.open_float.assert_called_once()

    def test_hunk_preview_no_hunk_at_cursor(self):
        from peovim.plugins.gitsigns import _cmd_hunk_preview

        api, _ = _make_api(hunks=[{"type": "add", "start": 20, "end": 21}])
        win = api.active_window.return_value
        win.cursor = (5, 0)
        _cmd_hunk_preview(api)
        api.ui.notify.assert_called()
        api.ui.open_float.assert_not_called()

    def test_branch_create_calls_git_api(self):
        from peovim.plugins.gitsigns import _cmd_branch_create

        api, _ = _make_api()
        api.git.root.return_value = Path("/repo")

        _cmd_branch_create(api, "feature/test")

        api.git.create_branch.assert_called_once_with("feature/test", path=Path("/repo"))

    def test_checkout_calls_git_api(self):
        from peovim.plugins.gitsigns import _cmd_checkout

        api, _ = _make_api()
        api.git.root.return_value = Path("/repo")

        _cmd_checkout(api, "feature/test")

        api.git.checkout.assert_called_once_with("feature/test", path=Path("/repo"))

    def test_merge_branch_calls_git_api(self):
        from peovim.plugins.gitsigns import _cmd_merge_branch

        api, _ = _make_api()
        api.git.root.return_value = Path("/repo")

        _cmd_merge_branch(api, "feature/test")

        api.git.merge.assert_called_once_with("feature/test", path=Path("/repo"))

    def test_fetch_calls_git_api(self):
        from peovim.plugins.gitsigns import _cmd_fetch

        api, _ = _make_api()
        api.git.root.return_value = Path("/repo")

        _cmd_fetch(api, "origin")

        api.git.fetch.assert_called_once_with(path=Path("/repo"), remote="origin")

    def test_pull_calls_git_api(self):
        from peovim.plugins.gitsigns import _cmd_pull

        api, _ = _make_api()
        api.git.root.return_value = Path("/repo")

        _cmd_pull(api, "origin main")

        api.git.pull.assert_called_once_with(path=Path("/repo"), remote="origin", branch="main")

    def test_push_calls_git_api(self):
        from peovim.plugins.gitsigns import _cmd_push

        api, _ = _make_api()
        api.git.root.return_value = Path("/repo")

        _cmd_push(api, "origin main")

        api.git.push.assert_called_once_with(path=Path("/repo"), remote="origin", branch="main")

    def test_log_opens_generated_log_buffer(self, tmp_path):
        from peovim.plugins.gitsigns import _cmd_log

        repo = tmp_path / "repo"
        repo.mkdir()

        api, buf = _make_api(buf_path=repo / "src" / "app.py")
        buf.path = repo / "src" / "app.py"
        api.git.root.return_value = repo
        api.git.repo_state.return_value = SimpleNamespace(
            root=repo,
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=0, behind=0, gone=False),
            status=[],
            remotes=[],
        )
        api.git.log_entries.return_value = [
            SimpleNamespace(
                commit="abc123def",
                short_commit="abc123d",
                author="Alice",
                relative_date="2 hours ago",
                refs="HEAD -> main, origin/main",
                subject="Add log browser",
            )
        ]

        assert _cmd_log(api, "") is True

        api.git.log_entries.assert_called_once_with(path=repo, limit=50, ref="main")
        opened_path = Path(api.open_buffer.call_args.args[0])
        assert opened_path.exists()
        content = opened_path.read_text(encoding="utf-8")
        assert "Git log for main" in content
        assert "Add log browser" in content

    def test_log_uses_selected_branch_name_when_provided(self, tmp_path):
        from peovim.plugins.gitsigns import _cmd_log

        repo = tmp_path / "repo"
        repo.mkdir()

        api, buf = _make_api(buf_path=repo / "src" / "app.py")
        buf.path = repo / "src" / "app.py"
        api.git.root.return_value = repo
        api.git.repo_state.return_value = SimpleNamespace(
            root=repo,
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=0, behind=0, gone=False),
            status=[],
            remotes=[],
        )
        api.git.log_entries.return_value = [
            SimpleNamespace(
                commit="abc123def",
                short_commit="abc123d",
                author="Alice",
                relative_date="2 hours ago",
                refs="feature/demo",
                subject="Feature commit",
            )
        ]

        assert _cmd_log(api, "feature/demo") is True

        api.git.log_entries.assert_called_once_with(path=repo, limit=50, ref="feature/demo")

    def test_log_snapshot_names_do_not_collide_for_similar_refs(self, tmp_path):
        from peovim.plugins.gitsigns import _cmd_log

        repo = tmp_path / "repo"
        repo.mkdir()

        api, buf = _make_api(buf_path=repo / "src" / "app.py")
        buf.path = repo / "src" / "app.py"
        api.git.root.return_value = repo
        api.git.repo_state.return_value = SimpleNamespace(
            root=repo,
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=0, behind=0, gone=False),
            status=[],
            remotes=[],
        )
        api.git.log_entries.return_value = [
            SimpleNamespace(
                commit="abc123def",
                short_commit="abc123d",
                author="Alice",
                relative_date="2 hours ago",
                refs="feature/demo",
                subject="Feature commit",
            )
        ]

        assert _cmd_log(api, "feature/demo") is True
        first_path = Path(api.open_buffer.call_args.args[0])

        assert _cmd_log(api, "feature_demo") is True
        second_path = Path(api.open_buffer.call_args.args[0])

        assert first_path != second_path
        assert first_path.read_text(encoding="utf-8").startswith("Git log for feature/demo")
        assert second_path.read_text(encoding="utf-8").startswith("Git log for feature_demo")

    def test_compare_file_emits_compare_event_for_modified_file(self, tmp_path):
        from peovim.plugins.gitsigns import _cmd_compare_file

        repo = tmp_path / "repo"
        target = repo / "src" / "app.py"
        target.parent.mkdir(parents=True)
        target.write_text("print('new')\n", encoding="utf-8")

        api, buf = _make_api(buf_path=target)
        buf.path = target
        api.git.root.return_value = repo
        api.git.repo_state.return_value = SimpleNamespace(
            root=repo,
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=0, behind=0, gone=False),
            status=[GitStatusEntry(code="M", path="src/app.py", index_status=" ", worktree_status="M")],
            remotes=[],
        )
        api.git.show_file_text.return_value = "print('old')\n"

        assert _cmd_compare_file(api, str(target)) is True

        api.events.emit.assert_called_once()
        assert api.events.emit.call_args.args == ("diff_selection_ready",)
        kwargs = api.events.emit.call_args.kwargs
        assert kwargs["right"] == str(target.resolve())
        assert Path(kwargs["left"]).read_text(encoding="utf-8") == "print('old')\n"

    def test_compare_file_uses_empty_worktree_snapshot_for_deleted_file(self, tmp_path):
        from peovim.plugins.gitsigns import _cmd_compare_file

        repo = tmp_path / "repo"
        repo.mkdir()
        target = repo / "src" / "gone.py"

        api, buf = _make_api(buf_path=repo / "README.md")
        buf.path = repo / "README.md"
        api.git.root.return_value = repo
        api.git.repo_state.return_value = SimpleNamespace(
            root=repo,
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=0, behind=0, gone=False),
            status=[GitStatusEntry(code="D", path="src/gone.py", index_status=" ", worktree_status="D")],
            remotes=[],
        )
        api.git.show_file_text.return_value = "old contents\n"

        assert _cmd_compare_file(api, str(target)) is True

        kwargs = api.events.emit.call_args.kwargs
        assert Path(kwargs["left"]).read_text(encoding="utf-8") == "old contents\n"
        assert Path(kwargs["right"]).read_text(encoding="utf-8") == ""

    def test_compare_file_writes_snapshot_inside_project_ed_dir(self, tmp_path):
        from peovim.plugins.gitsigns import _cmd_compare_file

        repo = tmp_path / "repo"
        target = repo / "src" / "app.py"
        target.parent.mkdir(parents=True)
        target.write_text("print('new')\n", encoding="utf-8")

        api, buf = _make_api(buf_path=target)
        buf.path = target
        api.git.root.return_value = repo
        api.git.repo_state.return_value = SimpleNamespace(
            root=repo,
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=0, behind=0, gone=False),
            status=[GitStatusEntry(code="M", path="src/app.py", index_status=" ", worktree_status="M")],
            remotes=[],
        )
        api.git.show_file_text.return_value = "print('old')\n"

        assert _cmd_compare_file(api, str(target)) is True

        snapshot_path = Path(api.events.emit.call_args.kwargs["left"])
        assert snapshot_path.is_relative_to((repo / ".peovim").resolve())
        assert snapshot_path.read_text(encoding="utf-8") == "print('old')\n"

    def test_compare_snapshot_sanitizes_parent_segments(self, tmp_path):
        from peovim.plugins.gitsigns import _write_compare_snapshot

        repo = tmp_path / "repo"
        repo.mkdir()

        snapshot_path = _write_compare_snapshot(repo, "head", "../escape.txt", "safe\n")

        assert snapshot_path.is_relative_to((repo / ".peovim").resolve())
        assert snapshot_path.read_text(encoding="utf-8") == "safe\n"


class TestStatusSidebar:
    def test_build_status_nodes_reports_clean_repo(self):
        from peovim.plugins.gitsigns import _build_status_nodes

        api, _buf = _make_api()
        api.git.root.return_value = Path("/repo")
        api.git.repo_state.return_value = SimpleNamespace(
            root=Path("/repo"),
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=0, behind=0, gone=False),
            status=[],
            remotes=[],
        )
        api.git.list_branches.return_value = [
            SimpleNamespace(name="main", upstream="origin/main", current=True, ahead=0, behind=0, remote=False)
        ]

        title, nodes = _build_status_nodes(api)

        assert title == "Git [main]"
        assert [node.label for node in nodes] == ["Summary", "Branches", "Status", "Remotes"]
        assert nodes[0].get_children()[2].label == "Sync: origin/main"
        assert nodes[2].get_children()[0].label == "Working tree clean"

    def test_build_status_nodes_formats_changed_files(self):
        from peovim.plugins.gitsigns import _build_status_nodes

        api, _buf = _make_api()
        api.git.root.return_value = Path("/repo")
        api.git.repo_state.return_value = SimpleNamespace(
            root=Path("/repo"),
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=1, behind=0, gone=False),
            status=[
                GitStatusEntry(code="M", path="src/app.py", index_status=" ", worktree_status="M"),
                GitStatusEntry(code="??", path="new.txt", index_status="?", worktree_status="?"),
            ],
            remotes=[SimpleNamespace(name="origin", url="git@example.com:repo.git")],
        )
        api.git.list_branches.return_value = [
            SimpleNamespace(name="main", upstream="origin/main", current=True, ahead=1, behind=0, remote=False),
            SimpleNamespace(name="feature", upstream="origin/feature", current=False, ahead=0, behind=0, remote=False),
            SimpleNamespace(name="origin/review", upstream=None, current=False, ahead=0, behind=0, remote=True),
        ]

        title, nodes = _build_status_nodes(api)
        status_groups = nodes[2].get_children()

        assert title == "Git [main]"
        assert nodes[0].get_children()[2].label == "Sync: origin/main · ahead 1"
        # status is grouped: Unstaged (1), Untracked (1)
        assert status_groups[0].label == "Unstaged (1)"
        assert status_groups[1].label == "Untracked (1)"
        app_node = status_groups[0].get_children()[0]
        new_node = status_groups[1].get_children()[0]
        assert app_node.label.endswith("src/app.py")
        assert new_node.label.endswith("new.txt")
        assert app_node.value == ("status", str((Path("/repo") / "src/app.py").resolve()))
        assert app_node.fg == GIT_MODIFIED_COLOR
        assert new_node.fg == GIT_UNTRACKED_COLOR
        assert nodes[1].get_children()[0].label.startswith("* main")
        assert "remote" in nodes[1].get_children()[2].label
        assert nodes[3].get_children()[0].label.startswith("origin:")

    def test_build_status_nodes_requests_remote_branches(self):
        from peovim.plugins.gitsigns import _build_status_nodes

        api, _buf = _make_api()
        api.git.root.return_value = Path("/repo")
        api.git.repo_state.return_value = SimpleNamespace(
            root=Path("/repo"),
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=0, behind=0, gone=False),
            status=[],
            remotes=[],
        )
        api.git.list_branches.return_value = []

        _build_status_nodes(api)

        api.git.list_branches.assert_called_once_with(Path("/repo"), include_remote=True)

    def test_build_status_nodes_reports_not_in_repo(self):
        from peovim.plugins.gitsigns import _build_status_nodes

        api, _buf = _make_api()
        api.git.root.return_value = None

        title, nodes = _build_status_nodes(api)

        assert title == "Git"
        assert nodes[0].label == "Not in a git repository"

    def test_toggle_status_panel_shows_sidebar(self):
        from peovim.plugins.gitsigns import _toggle_status_panel

        api, _buf = _make_api()
        panel = MagicMock()
        api.ui.get_sidebar_panel.return_value = panel
        api.ui.is_sidebar_visible.return_value = False

        _toggle_status_panel(api)

        api.ui.show_sidebar_panel.assert_called_once_with("git-status", panel, focus=True)

    def test_panel_on_show_notifies_help_hint(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        api.git.root.return_value = None
        panel = _GitStatusSidebarPanel(api)

        panel.on_show()

        api.ui.notify.assert_called_with("Git panel: press ? for help", level="info")

    def test_panel_key_question_mark_shows_help(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        panel = _GitStatusSidebarPanel(api)

        assert panel._on_key("?", None) is True
        message = api.ui.notify.call_args.args[0]
        assert "Git panel keys:" in message
        assert "? help" in message
        assert "d diff selected status file" in message

    def test_panel_key_h_remains_tree_motion(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        panel = _GitStatusSidebarPanel(api)

        assert panel._on_key("h", None) is False
        api.ui.notify.assert_not_called()

    def test_panel_key_c_opens_branch_create_prompt(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        panel = _GitStatusSidebarPanel(api)

        assert panel._on_key("c", None) is True
        api.open_cmdline.assert_called_once_with("GitBranchCreate ")

    def test_panel_key_f_opens_fetch_prompt_with_upstream_remote(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        api.git.root.return_value = Path("/repo")
        api.git.repo_state.return_value = SimpleNamespace(
            root=Path("/repo"),
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=0, behind=2, gone=False),
            status=[],
            remotes=[],
        )
        panel = _GitStatusSidebarPanel(api)

        assert panel._on_key("f", None) is True
        api.open_cmdline.assert_called_once_with("GitFetch origin")

    def test_panel_key_p_opens_pull_prompt_with_upstream(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        api.git.root.return_value = Path("/repo")
        api.git.repo_state.return_value = SimpleNamespace(
            root=Path("/repo"),
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=0, behind=2, gone=False),
            status=[],
            remotes=[],
        )
        panel = _GitStatusSidebarPanel(api)

        assert panel._on_key("p", None) is True
        api.open_cmdline.assert_called_once_with("GitPull origin main")

    def test_panel_key_shift_p_opens_push_prompt_with_upstream(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        api.git.root.return_value = Path("/repo")
        api.git.repo_state.return_value = SimpleNamespace(
            root=Path("/repo"),
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=1, behind=0, gone=False),
            status=[],
            remotes=[],
        )
        panel = _GitStatusSidebarPanel(api)

        assert panel._on_key("P", None) is True
        api.open_cmdline.assert_called_once_with("GitPush origin main")

    def test_panel_key_a_stages_file_directly(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        api.git.get_root.return_value = "/repo"
        panel = _GitStatusSidebarPanel(api)
        node = SimpleNamespace(value=("status", "/repo/src/app.py"))

        assert panel._on_key("a", node) is True
        api.git.stage_file.assert_called_once()
        api.ui.focus_sidebar.assert_called()

    def test_panel_key_u_unstages_file_directly(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        api.git.get_root.return_value = "/repo"
        panel = _GitStatusSidebarPanel(api)
        node = SimpleNamespace(value=("status", "/repo/src/app.py"))

        assert panel._on_key("u", node) is True
        api.git.unstage_file.assert_called_once()
        api.ui.focus_sidebar.assert_called()

    def test_panel_key_x_discards_file_directly(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        api.git.get_root.return_value = "/repo"
        panel = _GitStatusSidebarPanel(api)
        node = SimpleNamespace(value=("status", "/repo/src/app.py"))

        assert panel._on_key("x", node) is True
        api.git.discard_file.assert_called_once()
        api.ui.focus_sidebar.assert_called()

    def test_panel_key_d_launches_compare_for_status_row(self, tmp_path):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        repo = tmp_path / "repo"
        target = repo / "src" / "app.py"
        target.parent.mkdir(parents=True)
        target.write_text("print('new')\n", encoding="utf-8")

        api, buf = _make_api(buf_path=target)
        buf.path = target
        api.git.root.return_value = repo
        api.git.repo_state.return_value = SimpleNamespace(
            root=repo,
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=0, behind=0, gone=False),
            status=[GitStatusEntry(code="M", path="src/app.py", index_status=" ", worktree_status="M")],
            remotes=[],
        )
        api.git.show_file_text.return_value = "print('old')\n"
        panel = _GitStatusSidebarPanel(api)
        node = SimpleNamespace(value=("status", str(target.resolve())))

        assert panel._on_key("d", node) is True
        api.events.emit.assert_called_once()
        api.ui.blur_sidebar.assert_called_once()

    def test_panel_key_l_launches_log_for_branch_node(self, tmp_path):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        repo = tmp_path / "repo"
        repo.mkdir()

        api, buf = _make_api(buf_path=repo / "src" / "app.py")
        buf.path = repo / "src" / "app.py"
        api.git.root.return_value = repo
        api.git.repo_state.return_value = SimpleNamespace(
            root=repo,
            branch=SimpleNamespace(name="main", upstream="origin/main", ahead=0, behind=0, gone=False),
            status=[],
            remotes=[],
        )
        api.git.log_entries.return_value = [
            SimpleNamespace(
                commit="abc123def",
                short_commit="abc123d",
                author="Alice",
                relative_date="2 hours ago",
                refs="feature/test",
                subject="Feature commit",
            )
        ]
        panel = _GitStatusSidebarPanel(api)
        node = SimpleNamespace(value=("branch", "feature/test"))

        assert panel._on_key("l", node) is True
        api.git.log_entries.assert_called_once_with(path=repo, limit=50, ref="feature/test")
        api.ui.blur_sidebar.assert_called_once()

    def test_panel_key_s_opens_checkout_prompt_for_branch_node(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        panel = _GitStatusSidebarPanel(api)
        node = SimpleNamespace(value=("branch", "feature/test", False))

        assert panel._on_key("s", node) is True
        api.open_cmdline.assert_called_once_with("GitCheckout feature/test")

    def test_panel_key_s_ignores_remote_branch_node(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        panel = _GitStatusSidebarPanel(api)
        node = SimpleNamespace(value=("branch", "origin/feature/test", True))

        assert panel._on_key("s", node) is False
        api.open_cmdline.assert_not_called()

    def test_panel_key_m_opens_merge_prompt_for_branch_node(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        panel = _GitStatusSidebarPanel(api)
        node = SimpleNamespace(value=("branch", "feature/test", False))

        assert panel._on_key("m", node) is True
        api.open_cmdline.assert_called_once_with("GitMergeBranch feature/test")

    def test_panel_key_m_opens_merge_prompt_for_remote_branch_node(self):
        from peovim.plugins.gitsigns import _GitStatusSidebarPanel

        api, _buf = _make_api()
        panel = _GitStatusSidebarPanel(api)
        node = SimpleNamespace(value=("branch", "origin/feature/test", True))

        assert panel._on_key("m", node) is True
        api.open_cmdline.assert_called_once_with("GitMergeBranch origin/feature/test")
