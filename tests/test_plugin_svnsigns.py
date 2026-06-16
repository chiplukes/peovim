from __future__ import annotations

from pathlib import Path

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
