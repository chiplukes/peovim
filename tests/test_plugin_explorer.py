"""
Phase 7f — Explorer plugin tests
"""

from __future__ import annotations

from types import SimpleNamespace

from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.parser import parse_ex_command
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.git import GIT_MODIFIED_COLOR, GIT_UNTRACKED_COLOR
from peovim.git.repository import GitStatusEntry
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine
from peovim.plugins.explorer import (
    _ExplorerController,
    _ExplorerSidebarPanel,
    _git_status_map,
    _make_nodes,
    _resolve_created_path,
    _resolve_rename_target,
    _status_marker,
    _suggest_copy_name,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api() -> EditorAPI:
    doc = Document()
    doc.load_string("hello")
    window = Window(doc)
    workspace = Workspace(window)
    registers = RegisterStore()
    editor_state = EditorState()
    command_registry = CommandRegistry()
    register_builtins(command_registry)
    engine = ModalEngine()
    engine.set_document(doc)
    dispatcher = ActionDispatcher(engine, window, registers, editor_state=editor_state)
    dispatcher._command_registry = command_registry
    api = EditorAPI(workspace, engine, dispatcher, editor_state, command_registry)
    key_interceptors: list = []
    api._event_loop = SimpleNamespace(
        _cmdline=SimpleNamespace(enter=lambda *args, **kwargs: None),
        _invalidate_cmdline=lambda: None,
        _key_interceptors=key_interceptors,
        attach_key_interceptor=key_interceptors.append,
        detach_key_interceptor=lambda i: key_interceptors.remove(i) if i in key_interceptors else None,
    )
    return api


def _controller_with_selection(api: EditorAPI, node) -> _ExplorerController:
    """Build a controller whose explorer panel has *node* as the selected entry."""
    from peovim.ui.tree_view import TreeView

    controller = _ExplorerController(api)
    tree = TreeView([node], title="Explorer", width=30)
    controller._panel = _ExplorerSidebarPanel(tree, width=30)
    return controller


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExplorerNodes:
    def test_make_nodes_lists_files_and_dirs(self, tmp_path):
        (tmp_path / "file.py").write_text("# hello")
        (tmp_path / "subdir").mkdir()
        nodes = _make_nodes(tmp_path)
        labels = [n.label for n in nodes]
        assert "file.py" in labels
        assert "subdir" in labels

    def test_dirs_sorted_before_files(self, tmp_path):
        (tmp_path / "alpha.py").write_text("")
        (tmp_path / "beta_dir").mkdir()
        (tmp_path / "gamma.py").write_text("")
        nodes = _make_nodes(tmp_path)
        # ".." comes first, then directories, then files
        non_up = [n for n in nodes if n.label != ".."]
        assert non_up[0].label == "beta_dir"
        assert non_up[0].children_fn is not None

    def test_dirs_and_files_alphabetically_sorted(self, tmp_path):
        (tmp_path / "c.py").write_text("")
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        nodes = _make_nodes(tmp_path)
        file_labels = [n.label for n in nodes if n.children_fn is None and n.label != ".."]
        assert file_labels == ["a.py", "b.py", "c.py"]

    def test_directory_nodes_are_lazy(self, tmp_path):
        subdir = tmp_path / "lazy_dir"
        subdir.mkdir()
        (subdir / "child.py").write_text("")

        nodes = _make_nodes(tmp_path)
        dir_node = next(n for n in nodes if n.label == "lazy_dir")

        # children_fn should exist but not be called yet
        assert dir_node.children_fn is not None
        assert dir_node._cached_children == []

    def test_directory_children_loaded_on_expand(self, tmp_path):
        subdir = tmp_path / "mydir"
        subdir.mkdir()
        (subdir / "inner.py").write_text("")

        nodes = _make_nodes(tmp_path)
        dir_node = next(n for n in nodes if n.label == "mydir")

        # Calling children_fn should return inner.py
        children = dir_node.children_fn()
        child_labels = [c.label for c in children]
        assert "inner.py" in child_labels

    def test_file_nodes_have_no_children_fn(self, tmp_path):
        (tmp_path / "script.py").write_text("pass")
        nodes = _make_nodes(tmp_path)
        file_node = next(n for n in nodes if n.label == "script.py")
        assert file_node.children_fn is None

    def test_empty_directory_returns_empty_list(self, tmp_path):
        empty = tmp_path / "empty_dir"
        empty.mkdir()
        nodes = _make_nodes(empty)
        # Only the ".." navigation entry is present for an empty directory
        assert [n.label for n in nodes] == [".."]

    def test_file_nodes_show_git_status_indicator(self, tmp_path):
        tracked = tmp_path / "tracked.py"
        tracked.write_text("print('hi')", encoding="utf-8")

        nodes = _make_nodes(
            tmp_path,
            status_map={
                str(tracked.resolve()): GitStatusEntry(
                    code="M",
                    path="tracked.py",
                    index_status=" ",
                    worktree_status="M",
                )
            },
        )

        assert any(node.label == "~ tracked.py" for node in nodes)

    def test_file_nodes_use_git_status_colors(self, tmp_path):
        tracked = tmp_path / "tracked.py"
        tracked.write_text("print('hi')", encoding="utf-8")
        new_file = tmp_path / "new.py"
        new_file.write_text("print('new')", encoding="utf-8")

        nodes = _make_nodes(
            tmp_path,
            status_map={
                str(tracked.resolve()): GitStatusEntry(
                    code="M",
                    path="tracked.py",
                    index_status=" ",
                    worktree_status="M",
                ),
                str(new_file.resolve()): GitStatusEntry(
                    code="??",
                    path="new.py",
                    index_status="?",
                    worktree_status="?",
                ),
            },
        )

        tracked_node = next(node for node in nodes if node.label.endswith("tracked.py"))
        untracked_node = next(node for node in nodes if node.label.endswith("new.py"))
        assert tracked_node.fg == GIT_MODIFIED_COLOR
        assert untracked_node.fg == GIT_UNTRACKED_COLOR

    def test_directory_nodes_inherit_child_git_status_color(self, tmp_path):
        nested_dir = tmp_path / "nested"
        nested_dir.mkdir()
        tracked = nested_dir / "tracked.py"
        tracked.write_text("print('hi')", encoding="utf-8")

        nodes = _make_nodes(
            tmp_path,
            status_map={
                str(tracked.resolve()): GitStatusEntry(
                    code="M",
                    path="nested/tracked.py",
                    index_status=" ",
                    worktree_status="M",
                ),
                str(nested_dir.resolve()): GitStatusEntry(
                    code="M",
                    path="nested/tracked.py",
                    index_status=" ",
                    worktree_status="M",
                ),
            },
        )

        nested_node = next(node for node in nodes if node.label.endswith("nested"))
        assert nested_node.label == "~ nested"
        assert nested_node.fg == GIT_MODIFIED_COLOR

    def test_directory_nodes_show_mixed_marker_for_modified_and_untracked_children(self, tmp_path):
        nested_dir = tmp_path / "nested"
        nested_dir.mkdir()
        tracked = nested_dir / "tracked.py"
        tracked.write_text("print('hi')", encoding="utf-8")
        new_file = nested_dir / "new.py"
        new_file.write_text("print('new')", encoding="utf-8")

        nodes = _make_nodes(
            tmp_path,
            status_map={
                str(nested_dir.resolve()): SimpleNamespace(
                    code="~",
                    index_status=" ",
                    worktree_status="M",
                    modified=True,
                    staged=False,
                    conflicted=False,
                    deleted=False,
                    untracked=True,
                    mixed=True,
                ),
                str(tracked.resolve()): GitStatusEntry(
                    code="M",
                    path="nested/tracked.py",
                    index_status=" ",
                    worktree_status="M",
                ),
                str(new_file.resolve()): GitStatusEntry(
                    code="??",
                    path="nested/new.py",
                    index_status="?",
                    worktree_status="?",
                ),
            },
        )

        nested_node = next(node for node in nodes if node.label.endswith("nested"))
        assert nested_node.label == "* nested"
        assert nested_node.fg == GIT_MODIFIED_COLOR


class TestExplorerPlugin:
    def test_explorer_command_registered(self):
        api = _make_api()
        from peovim.plugins.manager import PluginManager

        pm = PluginManager(api)
        pm.load("peovim.plugins.explorer")
        # :Explorer command should be registered
        assert api.commands._registry.get("Explorer") is not None

    def test_cr_on_file_opens_buffer(self, tmp_path):
        test_file = tmp_path / "test.py"
        test_file.write_text("# test content\n")

        api = _make_api()
        api.open_buffer(test_file)
        assert api._workspace.active_window.document.path == test_file

    def test_sidebar_panel_renders_help_hint(self):
        from peovim.ui.cell_grid import CellGrid
        from peovim.ui.tree_view import TreeView

        panel = _ExplorerSidebarPanel(TreeView([], title="Explorer", width=30), width=30)
        grid = CellGrid(30, 6)

        panel.render(grid)

        text = "".join(cell[0] for cell in grid._current[0]).rstrip()
        assert "<leader>f" in text
        assert "file ops" in text

    def test_click_on_directory_expands_it(self, tmp_path):
        (tmp_path / "subdir").mkdir()
        (tmp_path / "file.py").write_text("")
        api = _make_api()
        api.find_root = lambda markers=None: tmp_path
        controller = _ExplorerController(api)
        controller.toggle()
        panel = controller._panel
        assert panel is not None

        # Rows: 0 = hint, 1 = tree title, 2 = "..", 3 = first directory
        assert panel.click(3, 0)

        dir_node = next(n for n in panel.tree._visible_nodes() if n[0].label == "subdir")[0]
        assert dir_node.expanded

    def test_click_on_file_opens_buffer(self, tmp_path):
        test_file = tmp_path / "open_me.py"
        test_file.write_text("# content\n")
        api = _make_api()
        api.find_root = lambda markers=None: tmp_path
        controller = _ExplorerController(api)
        controller.toggle()
        panel = controller._panel
        assert panel is not None

        # Rows: 0 = hint, 1 = tree title, 2 = "..", 3 = the only file
        assert panel.click(3, 0)

        assert api._workspace.active_window.document.path == test_file

    def test_click_on_hint_row_is_consumed_without_action(self, tmp_path):
        api = _make_api()
        api.find_root = lambda markers=None: tmp_path
        controller = _ExplorerController(api)
        controller.toggle()
        panel = controller._panel
        assert panel is not None

        assert panel.click(0, 0)
        assert panel.tree._selected_idx == 0


class TestExplorerWindowChooser:
    """Integration: selecting a file from the explorer with >1 split starts a
    TargetChooser (unit-tested in tests/test_target_chooser.py)."""

    def test_open_selected_starts_chooser_with_multiple_windows(self, tmp_path):
        from peovim.ui.tree_view import TreeNode

        api = _make_api()
        api.find_root = lambda markers=None: tmp_path
        api._workspace.active_tab.split_vertical()
        assert len(api.list_tab_windows()) == 2

        target = tmp_path / "open_me.py"
        target.write_text("# content\n")
        controller = _ExplorerController(api)
        controller._root = tmp_path
        first_window = api.list_tab_windows()[0]

        controller._open_selected(TreeNode(label="open_me.py", value=str(target)))

        assert controller._window_chooser is not None
        assert controller._window_chooser.is_active
        assert api._event_loop._key_interceptors == [controller._window_chooser]
        # Not opened yet; chooser is waiting for the target window letter.
        assert api.active_buffer().path != target.resolve()

        controller._window_chooser.feed_key("A")

        assert not controller._window_chooser.is_active
        assert api._event_loop._key_interceptors == []
        assert api.active_buffer().path == target.resolve()
        assert api.active_window().win_id == first_window.win_id

    def test_open_selected_cancels_stale_chooser_on_second_open(self, tmp_path):
        """Selecting another file while a chooser is pending must cancel the first
        chooser instead of orphaning it and leaving its badges/interceptor behind."""
        from peovim.ui.tree_view import TreeNode

        api = _make_api()
        api.find_root = lambda markers=None: tmp_path
        api._workspace.active_tab.split_vertical()
        assert len(api.list_tab_windows()) == 2

        target_a = tmp_path / "first.py"
        target_b = tmp_path / "second.py"
        target_a.write_text("# a\n")
        target_b.write_text("# b\n")
        controller = _ExplorerController(api)
        controller._root = tmp_path

        controller._open_selected(TreeNode(label="first.py", value=str(target_a)))
        first_chooser = controller._window_chooser
        assert first_chooser is not None and first_chooser.is_active
        assert api._event_loop._key_interceptors == [first_chooser]

        controller._open_selected(TreeNode(label="second.py", value=str(target_b)))

        second_chooser = controller._window_chooser
        assert second_chooser is not None and second_chooser is not first_chooser
        assert not first_chooser.is_active
        assert second_chooser.is_active
        assert api._event_loop._key_interceptors == [second_chooser]

    def test_open_selected_opens_directly_with_single_window(self, tmp_path):
        from peovim.ui.tree_view import TreeNode

        api = _make_api()
        api.find_root = lambda markers=None: tmp_path
        assert len(api.list_tab_windows()) == 1

        target = tmp_path / "solo.py"
        target.write_text("# content\n")
        controller = _ExplorerController(api)
        controller._root = tmp_path

        controller._open_selected(TreeNode(label="solo.py", value=str(target)))

        assert controller._window_chooser is None
        assert api.active_buffer().path == target.resolve()
        assert api._event_loop._key_interceptors == []


class TestExplorerCommands:
    def test_toggle_opens_and_hides_persistent_sidebar(self, tmp_path):
        api = _make_api()
        controller = _ExplorerController(api)
        controller._root = tmp_path

        controller.toggle()
        assert api.ui.is_sidebar_visible("explorer")

        controller.toggle()
        assert not api.ui.is_sidebar_visible("explorer")

    def test_toggle_restores_last_selected_sidebar_panel(self, tmp_path):
        api = _make_api()
        controller = _ExplorerController(api)
        controller._root = tmp_path
        other_panel = SimpleNamespace(width=24, render=lambda grid: None, feed_key=lambda key: True)

        controller.toggle()
        api.ui.register_sidebar_panel("outline", other_panel)
        api.ui.show_sidebar_panel("outline", other_panel, focus=False)

        controller.toggle()
        assert not api.ui.is_sidebar_visible()

        controller.toggle()
        assert api.ui.is_sidebar_visible("outline")

    def test_resolve_created_path_marks_trailing_slash_as_directory(self, tmp_path):
        target, is_dir = _resolve_created_path(tmp_path, "nested/")
        assert is_dir
        assert target == tmp_path / "nested"

    def test_resolve_rename_target_uses_same_parent_for_simple_name(self, tmp_path):
        target = tmp_path / "old.txt"
        assert _resolve_rename_target(target, "new.txt") == tmp_path / "new.txt"

    def test_create_file_command_creates_and_opens_file(self, tmp_path):
        api = _make_api()
        controller = _ExplorerController(api)
        controller._root = tmp_path
        controller._pending_create_dir = tmp_path

        controller.command_create(parse_ex_command("ExplorerCreate new_file.py"))

        created = tmp_path / "new_file.py"
        assert created.exists()
        assert api._workspace.active_window.document.path == created

    def test_create_directory_command_creates_directory(self, tmp_path):
        api = _make_api()
        controller = _ExplorerController(api)
        controller._root = tmp_path
        controller._pending_create_dir = tmp_path

        controller.command_create(parse_ex_command("ExplorerCreate nested/"))

        assert (tmp_path / "nested").is_dir()

    def test_rename_command_renames_selected_entry(self, tmp_path):
        api = _make_api()
        controller = _ExplorerController(api)
        controller._root = tmp_path
        old_path = tmp_path / "old.txt"
        old_path.write_text("hello", encoding="utf-8")
        controller._pending_rename_path = old_path

        controller.command_rename(parse_ex_command("ExplorerRename new.txt"))

        assert not old_path.exists()
        assert (tmp_path / "new.txt").exists()

    def test_delete_command_deletes_selected_file(self, tmp_path):
        api = _make_api()
        controller = _ExplorerController(api)
        controller._root = tmp_path
        target = tmp_path / "delete_me.txt"
        target.write_text("bye", encoding="utf-8")
        controller._pending_delete_path = target

        controller.command_delete(parse_ex_command("ExplorerDelete"))

        assert not target.exists()

    def test_op_new_opens_create_prompt(self, tmp_path, monkeypatch):
        from peovim.ui.tree_view import TreeNode

        api = _make_api()
        prompts = []
        monkeypatch.setattr(api, "open_cmdline", lambda initial="", prompt=":": prompts.append((prompt, initial)))
        node = TreeNode(label="dir", value=str(tmp_path), children_fn=lambda: [])
        controller = _controller_with_selection(api, node)

        controller.op_new()

        assert prompts == [(":", "ExplorerCreate ")]

    def test_op_copy_copies_to_explorer_clipboard(self, tmp_path):
        from peovim.ui.tree_view import TreeNode

        api = _make_api()
        source = tmp_path / "copy.txt"
        source.write_text("copy", encoding="utf-8")
        node = TreeNode(label="copy.txt", value=str(source), children_fn=None)
        controller = _controller_with_selection(api, node)

        controller.op_copy()

        assert controller._clipboard_mode == "copy"
        assert controller._clipboard_path == source

    def test_op_move_marks_move_in_explorer_clipboard(self, tmp_path):
        from peovim.ui.tree_view import TreeNode

        api = _make_api()
        source = tmp_path / "move.txt"
        source.write_text("move", encoding="utf-8")
        node = TreeNode(label="move.txt", value=str(source), children_fn=None)
        controller = _controller_with_selection(api, node)

        controller.op_move()

        assert controller._clipboard_mode == "move"
        assert controller._clipboard_path == source

    def test_op_paste_copies_file_into_target_directory(self, tmp_path):
        from peovim.ui.tree_view import TreeNode

        api = _make_api()
        controller = _ExplorerController(api)
        controller._root = tmp_path
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        source = source_dir / "copy.txt"
        source.write_text("copy", encoding="utf-8")
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        controller._clipboard_path = source
        controller._clipboard_mode = "copy"
        node = TreeNode(label="dest", value=str(dest_dir), children_fn=lambda: [])
        controller = _controller_with_selection(api, node)
        controller._root = tmp_path
        controller._clipboard_path = source
        controller._clipboard_mode = "copy"

        controller.op_paste()

        assert source.exists()
        assert (dest_dir / "copy.txt").read_text(encoding="utf-8") == "copy"

    def test_op_paste_in_same_directory_opens_copy_rename_prompt(self, tmp_path, monkeypatch):
        from peovim.ui.tree_view import TreeNode

        api = _make_api()
        controller = _ExplorerController(api)
        controller._root = tmp_path
        source = tmp_path / "copy.txt"
        source.write_text("copy", encoding="utf-8")
        prompts = []
        monkeypatch.setattr(api, "open_cmdline", lambda initial="", prompt=":": prompts.append((prompt, initial)))
        node = TreeNode(label="copy.txt", value=str(source), children_fn=None)
        controller = _controller_with_selection(api, node)
        controller._root = tmp_path
        controller._clipboard_path = source
        controller._clipboard_mode = "copy"

        controller.op_paste()

        assert prompts == [(":", "ExplorerCopyAs copy copy.txt")]
        assert controller._pending_copy_source == source
        assert controller._pending_copy_destination_dir == tmp_path

    def test_op_paste_moves_file_into_target_directory(self, tmp_path):
        from peovim.ui.tree_view import TreeNode

        api = _make_api()
        controller = _ExplorerController(api)
        controller._root = tmp_path
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        source = source_dir / "move.txt"
        source.write_text("move", encoding="utf-8")
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        node = TreeNode(label="dest", value=str(dest_dir), children_fn=lambda: [])
        controller = _controller_with_selection(api, node)
        controller._root = tmp_path
        controller._clipboard_path = source
        controller._clipboard_mode = "move"

        controller.op_paste()

        assert not source.exists()
        assert (dest_dir / "move.txt").read_text(encoding="utf-8") == "move"
        assert controller._clipboard_path == dest_dir / "move.txt"

    def test_copy_as_command_creates_renamed_copy(self, tmp_path):
        api = _make_api()
        controller = _ExplorerController(api)
        controller._root = tmp_path
        source = tmp_path / "copy.txt"
        source.write_text("copy", encoding="utf-8")
        controller._pending_copy_source = source
        controller._pending_copy_destination_dir = tmp_path

        controller.command_copy_as(parse_ex_command("ExplorerCopyAs renamed.txt"))

        assert source.exists()
        assert (tmp_path / "renamed.txt").read_text(encoding="utf-8") == "copy"


class TestExplorerGitStatus:
    def test_status_marker_maps_untracked_new_and_modified(self):
        assert _status_marker("??") == "!"
        assert _status_marker("A") == "+"
        assert _status_marker("M") == "~"

    def test_git_status_map_resolves_repo_relative_paths(self, tmp_path):
        api = _make_api()
        repo = tmp_path / "repo"
        repo.mkdir()
        target = repo / "tracked.py"
        target.write_text("print('hi')", encoding="utf-8")
        api.git.root = lambda path=None: repo
        api.git.status_entries = lambda path=None: [
            GitStatusEntry(code="M", path="tracked.py", index_status=" ", worktree_status="M")
        ]

        status_map = _git_status_map(api, repo)

        assert status_map[str(target.resolve())].path == "tracked.py"

    def test_git_status_map_propagates_status_to_parent_directories(self, tmp_path):
        api = _make_api()
        repo = tmp_path / "repo"
        repo.mkdir()
        nested = repo / "src"
        nested.mkdir()
        target = nested / "tracked.py"
        target.write_text("print('hi')", encoding="utf-8")
        api.git.root = lambda path=None: repo
        api.git.status_entries = lambda path=None: [
            GitStatusEntry(code="M", path="src/tracked.py", index_status=" ", worktree_status="M")
        ]

        status_map = _git_status_map(api, repo)

        assert status_map[str(target.resolve())].path == "src/tracked.py"
        assert status_map[str(nested.resolve())].path == "src/tracked.py"

    def test_git_status_map_prefers_modified_over_untracked_for_parent(self, tmp_path):
        api = _make_api()
        repo = tmp_path / "repo"
        repo.mkdir()
        nested = repo / "src"
        nested.mkdir()
        api.git.root = lambda path=None: repo
        api.git.status_entries = lambda path=None: [
            GitStatusEntry(code="??", path="src/new.py", index_status="?", worktree_status="?"),
            GitStatusEntry(code="M", path="src/tracked.py", index_status=" ", worktree_status="M"),
        ]

        status_map = _git_status_map(api, repo)

        assert status_map[str(nested.resolve())].modified is True

    def test_git_status_map_marks_parent_mixed_for_modified_and_untracked(self, tmp_path):
        api = _make_api()
        repo = tmp_path / "repo"
        repo.mkdir()
        nested = repo / "src"
        nested.mkdir()
        api.git.root = lambda path=None: repo
        api.git.status_entries = lambda path=None: [
            GitStatusEntry(code="??", path="src/new.py", index_status="?", worktree_status="?"),
            GitStatusEntry(code="M", path="src/tracked.py", index_status=" ", worktree_status="M"),
        ]

        status_map = _git_status_map(api, repo)

        assert status_map[str(nested.resolve())].mixed is True


class TestExplorerHelpers:
    def test_suggest_copy_name_preserves_suffix(self):
        assert _suggest_copy_name("copy.txt") == "copy copy.txt"
        assert _suggest_copy_name("folder") == "folder copy"

    def test_op_rename_opens_rename_prompt(self, tmp_path, monkeypatch):
        from peovim.ui.tree_view import TreeNode

        api = _make_api()
        prompts = []
        monkeypatch.setattr(api, "open_cmdline", lambda initial="", prompt=":": prompts.append((prompt, initial)))
        target = tmp_path / "name.txt"
        target.write_text("", encoding="utf-8")
        node = TreeNode(label="name.txt", value=str(target), children_fn=None)
        controller = _controller_with_selection(api, node)

        controller.op_rename()

        assert prompts == [(":", "ExplorerRename name.txt")]

    def test_op_delete_opens_delete_confirmation(self, tmp_path, monkeypatch):
        from peovim.ui.tree_view import TreeNode

        api = _make_api()
        prompts = []
        monkeypatch.setattr(api, "open_cmdline", lambda initial="", prompt=":": prompts.append((prompt, initial)))
        target = tmp_path / "name.txt"
        target.write_text("", encoding="utf-8")
        node = TreeNode(label="name.txt", value=str(target), children_fn=None)
        controller = _controller_with_selection(api, node)

        controller.op_delete()

        assert prompts == [(":", "ExplorerDelete")]
