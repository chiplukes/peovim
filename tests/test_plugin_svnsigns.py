from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine
from peovim.plugins import svnsigns as svn_mod


def _make_real_api(active_path: Path) -> EditorAPI:
    doc = Document(path=active_path)
    doc.load(active_path)
    window = Window(doc)
    workspace = Workspace(window)
    registers = RegisterStore()
    editor_state = EditorState()
    command_registry = CommandRegistry()
    register_builtins(command_registry)
    engine = ModalEngine()
    engine.set_document(doc)
    dispatcher = ActionDispatcher(
        engine,
        window,
        registers,
        editor_state=editor_state,
        workspace=workspace,
    )
    dispatcher._command_registry = command_registry
    return EditorAPI(workspace, engine, dispatcher, editor_state, command_registry)


class TestSvnDiffView:
    def test_open_diff_refreshes_clean_loaded_file_before_compare(self, tmp_path, monkeypatch):
        from peovim.plugins import compare as compare_mod
        from peovim.plugins import svnsigns as svn_mod

        target = tmp_path / "tracked.v"
        target.write_text("base\n", encoding="utf-8")

        api = _make_real_api(target)
        compare_mod.setup(api)

        target.write_text("changed\n", encoding="utf-8")

        monkeypatch.setattr(svn_mod, "_find_svn_root", lambda path: tmp_path)
        monkeypatch.setattr(svn_mod, "_get_original_content", lambda path: "base\n")

        svn_mod._open_diff(api, target)

        assert compare_mod._controller is not None
        summary = compare_mod._controller.session_summary()
        assert summary is not None
        assert summary["blocks"] == 1

        windows_by_path = {
            window.document.path: window
            for window in api._workspace.active_tab.all_windows()
            if window.document.path is not None
        }
        assert windows_by_path[target.resolve()].document.get_line(0) == "changed"


@pytest.fixture
def svn_history(tmp_path, monkeypatch):
    if not shutil.which("svn") or not shutil.which("svnadmin"):
        pytest.skip("svn and svnadmin are required for repository integration tests")
    repository = tmp_path / "repository"
    wc = tmp_path / "wc"
    config = tmp_path / "config"
    subprocess.run(["svnadmin", "create", str(repository)], check=True, capture_output=True)

    def svn(*args):
        return subprocess.run(
            ["svn", "--non-interactive", "--config-dir", str(config), *map(str, args)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout

    svn("checkout", repository.as_uri(), wc)
    (wc / "src").mkdir()
    (wc / "other").mkdir()
    for name, text in {
        "src/app.py": "old\n",
        "other/app.py": "other\n",
        "old name.py": "same\n",
        "gone.py": "deleted\n",
        "empty.py": "",
        "at@name.py": "at old\n",
    }.items():
        (wc / name).write_text(text, encoding="utf-8")
    svn(
        "add",
        wc / "src",
        wc / "other",
        wc / "old name.py",
        wc / "gone.py",
        wc / "empty.py",
        str(wc / "at@name.py") + "@",
    )
    svn("commit", wc, "-m", "Initial files", "--username", "Tester")
    svn("update", wc)
    (wc / "src/app.py").write_text("committed\n", encoding="utf-8")
    (wc / "at@name.py").write_text("at committed\n", encoding="utf-8")
    svn("move", wc / "old name.py", wc / "new name.py")
    svn("delete", wc / "gone.py")
    (wc / "added file.py").write_text("added\n", encoding="utf-8")
    svn("add", wc / "added file.py")
    svn("commit", wc, "-m", "Change files", "--username", "Tester")
    svn("update", wc)
    (wc / "src/app.py").write_text("working\n", encoding="utf-8")
    monkeypatch.chdir(wc)
    run = svn_mod._svn_run
    monkeypatch.setattr(
        svn_mod, "_svn_run", lambda args, cwd=None, **kwargs: run(["--config-dir", str(config), *args], cwd, **kwargs)
    )
    api = _make_real_api(wc / "src/app.py")
    comparisons = []
    api.events.on("diff_selection_ready", lambda **kw: comparisons.append(kw))
    svn_mod.setup(api)
    panel = api.ui.get_sidebar_panel("svn-status")
    try:
        yield wc, api, panel, comparisons, svn
    finally:
        svn_mod.teardown()
        api.ui._log_output_tab.detach()


def _compared_text(comparisons):
    return tuple(Path(comparisons[-1][side]).read_text(encoding="utf-8") for side in ("left", "right"))


def _revision_files(panel, revision):
    panel.refresh()
    history = panel._tree._roots[-1]
    panel._tree.expand(history)
    node = next(node for node in history.get_children() if node.label.startswith(f"r{revision} "))
    panel._tree.expand(node)
    return {n.value.file_path.name: n for n in node.get_children() if n.value is not None}


def test_status_file_diff_working_and_previous(svn_history):
    wc, api, panel, comparisons, _svn = svn_history
    panel.refresh()
    selected = panel._tree._roots[0].get_children()[0]
    assert selected.value["path"] == "src/app.py"
    panel._tree.select_value(selected.value)
    api.keymap.invoke_plug("SvnsignsPanelDiffWorking")
    assert _compared_text(comparisons) == ("committed\n", "working\n")
    api.keymap.invoke_plug("SvnsignsPanelDiffPrevious")
    assert _compared_text(comparisons) == ("old\n", "committed\n")
    assert (wc / "src/app.py").read_text() == "working\n"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("app.py", ("old\n", "committed\n")),
        ("gone.py", ("deleted\n", "")),
        ("new name.py", ("same\n", "same\n")),
        ("added file.py", ("", "added\n")),
        ("at@name.py", ("at old\n", "at committed\n")),
    ],
)
def test_history_file_diff_previous(svn_history, name, expected):
    _wc, _api, panel, comparisons, _svn = svn_history
    node = _revision_files(panel, 2)[name]
    panel._tree.select_value(node.value)
    panel.feed_key("<CR>")
    assert _compared_text(comparisons) == expected


def test_history_revision_vs_working_and_initial_revision(svn_history):
    _wc, _api, panel, comparisons, _svn = svn_history
    node = _revision_files(panel, 1)["app.py"]
    panel._tree.select_value(node.value)
    panel.diff_selected(previous=False)
    assert _compared_text(comparisons) == ("old\n", "working\n")
    panel.diff_selected(previous=True)
    assert _compared_text(comparisons) == ("", "old\n")


def test_history_is_lazy_and_scope_is_filtered(svn_history, monkeypatch):
    wc, api, _panel, _comparisons, _svn = svn_history
    calls = []
    run = svn_mod._svn_run

    def record(args, *args_, **kwargs):
        calls.append(args)
        return run(args, *args_, **kwargs)

    monkeypatch.setattr(svn_mod, "_svn_run", record)
    panel = svn_mod._SvnStatusPanel(api, wc / "src")
    panel.refresh()
    assert not any("log" in args for args in calls)
    files = _revision_files(panel, 2)
    assert set(files) == {"app.py"}
    panel._tree.select_value(files["app.py"].value)
    panel.refresh()
    assert panel._tree.selected_node.value == files["app.py"].value


def test_added_deleted_and_empty_working_files(svn_history):
    wc, api, _panel, comparisons, svn = svn_history
    assert svn_mod._open_diff(api, wc / "empty.py")
    assert _compared_text(comparisons) == ("", "")
    (wc / "new.py").write_text("new\n")
    svn("add", wc / "new.py")
    assert svn_mod._open_diff(api, wc / "new.py")
    assert _compared_text(comparisons) == ("", "new\n")
    assert not svn_mod._open_diff_previous(api, wc / "new.py")
    svn("delete", "--force", wc / "src/app.py")
    assert svn_mod._open_diff(api, wc / "src/app.py")
    assert _compared_text(comparisons) == ("committed\n", "")


def test_previous_command_follows_copy_history_and_snapshot_context(svn_history):
    wc, api, _panel, comparisons, _svn = svn_history
    assert svn_mod._open_diff_previous(api, wc / "new name.py")
    assert _compared_text(comparisons) == ("same\n", "same\n")
    assert svn_mod._open_diff_previous(api, wc / "src/app.py")
    historical = Path(comparisons[-1]["right"])
    assert svn_mod._open_diff(api, historical)
    assert _compared_text(comparisons) == ("committed\n", "working\n")


def test_snapshots_preserve_paths_for_duplicate_basenames(svn_history):
    wc, api, _panel, comparisons, _svn = svn_history
    assert svn_mod._open_diff(api, wc / "src/app.py")
    first = Path(comparisons[-1]["left"])
    assert svn_mod._open_diff(api, wc / "other/app.py")
    second = Path(comparisons[-1]["left"])
    assert first != second
    assert first.read_text() == "committed\n"
    assert second.read_text() == "other\n"


def test_repository_errors_are_not_treated_as_empty_files(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1, "svn", stderr="svn: E170013: Unable to connect\nsvn: E200009: Could not cat all targets"
        )

    monkeypatch.setattr(svn_mod, "_svn_run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        svn_mod._revision_content("file:///repository/file.py", 2)


def test_missing_repository_file_is_an_empty_side(monkeypatch):
    def missing(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1, "svn", stderr="svn: W160013: File not found\nsvn: E200009: Could not cat all targets"
        )

    monkeypatch.setattr(svn_mod, "_svn_run", missing)
    assert svn_mod._revision_content("file:///repository/gone.py", 2) is None


def test_failed_history_query_is_shown_in_panel(tmp_path, monkeypatch):
    target = tmp_path / "file.py"
    target.write_text("working\n")
    api = _make_real_api(target)
    monkeypatch.setattr(svn_mod, "_find_svn_root", lambda path: tmp_path)
    monkeypatch.setattr(svn_mod, "_get_status", lambda cwd: [])

    def offline(*args, **kwargs):
        raise RuntimeError("Repository is offline")

    monkeypatch.setattr(svn_mod, "_svn_info", offline)
    panel = svn_mod._SvnStatusPanel(api, tmp_path)
    panel.refresh()
    assert panel._tree._roots[0].get_children()[0].label == "Working copy is clean"
    history = panel._tree._roots[-1]
    assert history.get_children()[0].label == "Cannot load revisions: Repository is offline"
    api.ui._log_output_tab.detach()


@pytest.mark.parametrize("order", [("svn", "git", "explorer"), ("git", "svn", "explorer"), ("git", "explorer", "svn")])
def test_panel_diff_shortcuts_coexist_with_git_and_explorer(tmp_path, monkeypatch, order):
    from peovim.plugins import explorer, gitsigns

    target = tmp_path / "file.py"
    target.write_text("working\n")
    api = _make_real_api(target)
    monkeypatch.setattr(api, "find_root", lambda: tmp_path)
    monkeypatch.setattr(svn_mod, "_svn_available", lambda: True)
    monkeypatch.setattr(svn_mod, "_safe_get_hunks", lambda path: [])
    plugins = {"svn": svn_mod, "git": gitsigns, "explorer": explorer}
    for name in order:
        plugins[name].setup(api)
    try:
        for suffix, plugs in (
            ("dw", ("GitsignsDiffHead", "ExplorerDiffHead", "GitsignsPanelDiffWorking", "SvnsignsPanelDiffWorking")),
            (
                "dp",
                (
                    "GitsignsDiffPrevious",
                    "ExplorerDiffPrevious",
                    "GitsignsPanelDiffPrevious",
                    "SvnsignsPanelDiffPrevious",
                ),
            ),
        ):
            calls = []
            for plug in plugs:
                api.keymap.define_plug(plug, lambda p=plug, out=calls: out.append(p))
            for scope, expected in zip((None, "explorer", "git-status", "svn-status"), plugs, strict=True):
                api.binding_registry.set_scope_resolver(lambda s=scope: s)
                api.keymap.feed_keys(api.keymap.leader + suffix)
                assert calls[-1] == expected
    finally:
        svn_mod.teardown()
        gitsigns.teardown()
        api.ui._log_output_tab.detach()


def test_working_diff_preserves_unsaved_buffer_content(svn_history):
    from peovim.plugins import compare

    wc, api, _panel, _comparisons, _svn = svn_history
    api.active_buffer().insert(0, 0, "unsaved ")
    compare.setup(api)
    try:
        assert svn_mod._open_diff(api, wc / "src/app.py")
        doc = api._workspace.find_document_by_path(wc / "src/app.py")
        assert doc.get_line(0) == "unsaved working"
        assert doc.dirty
        assert (wc / "src/app.py").read_text() == "working\n"
    finally:
        api.keymap.invoke_plug("CompareStop")


@pytest.mark.parametrize("deleted", [False, True])
def test_copied_and_deleted_directories_expand_to_changed_files(svn_history, deleted):
    wc, _api, panel, comparisons, svn = svn_history
    svn("revert", wc / "src/app.py")
    svn("copy", wc / "src", wc / "copied")
    svn("commit", wc, "-m", "Copy directory", "--username", "Tester")
    svn("update", wc)
    revision = 3
    if deleted:
        svn("delete", wc / "copied")
        svn("commit", wc, "-m", "Delete directory", "--username", "Tester")
        svn("update", wc)
        revision = 4
    panel.feed_key("R")
    history = panel._tree._roots[-1]
    panel._tree.expand(history)
    commit = next(n for n in history.get_children() if n.label.startswith(f"r{revision} "))
    panel._tree.expand(commit)
    directory = next(n for n in commit.get_children() if n.label == "copied/")
    panel._tree.expand(directory)
    file = directory.get_children()[0]
    assert file.label == "app.py"
    panel._tree.select_value(file.value)
    panel.diff_selected(previous=True)
    assert _compared_text(comparisons) == ("committed\n", "" if deleted else "committed\n")


def test_scoped_history_follows_a_copied_directory(svn_history):
    wc, api, _panel, comparisons, svn = svn_history
    svn("revert", wc / "src/app.py")
    svn("copy", wc / "src", wc / "copied")
    svn("commit", wc, "-m", "Copy directory", "--username", "Tester")
    svn("update", wc)
    panel = svn_mod._SvnStatusPanel(api, wc / "copied")
    initial = _revision_files(panel, 1)["app.py"]
    assert initial.value.file_path == wc / "copied/app.py"
    panel._tree.select_value(initial.value)
    panel.diff_selected(previous=False)
    assert _compared_text(comparisons) == ("old\n", "committed\n")


def test_replaced_directory_includes_removed_and_added_files(svn_history):
    wc, _api, panel, comparisons, svn = svn_history
    svn("revert", wc / "src/app.py")
    (wc / "src/removed.py").write_text("removed\n")
    (wc / "other/new.py").write_text("new\n")
    svn("add", wc / "src/removed.py", wc / "other/new.py")
    svn("commit", wc, "-m", "Prepare replacement", "--username", "Tester")
    svn("update", wc)
    svn("delete", wc / "src")
    svn("copy", wc / "other", wc / "src")
    svn("commit", wc, "-m", "Replace directory", "--username", "Tester")
    svn("update", wc)
    panel.feed_key("R")
    history = panel._tree._roots[-1]
    panel._tree.expand(history)
    revision = next(n for n in history.get_children() if n.label.startswith("r4 "))
    panel._tree.expand(revision)
    directory = next(n for n in revision.get_children() if n.label == "src/")
    panel._tree.expand(directory)
    files = {n.label: n for n in directory.get_children()}
    assert set(files) == {"app.py", "new.py", "removed.py"}
    for name, expected in {
        "app.py": ("committed\n", "other\n"),
        "new.py": ("", "new\n"),
        "removed.py": ("removed\n", ""),
    }.items():
        panel._tree.select_value(files[name].value)
        panel.diff_selected(previous=True)
        assert _compared_text(comparisons) == expected
