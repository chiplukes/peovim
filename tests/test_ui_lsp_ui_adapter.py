"""Tests for peovim.ui.lsp_ui_adapter.LspUiAdapter.goto_location.

This is the code path LSP definition/implementation/type-definition/references
actually jump through for a single-location result (see LspAPI._jump_from_locations_request)
— distinct from (and, unlike) EditorAPI.goto_location, so it needs its own diff/compare
split guard. See test_api.py::TestEditorAPI for the EditorAPI.goto_location equivalent.
"""

from __future__ import annotations

from types import SimpleNamespace

from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine
from peovim.ui.lsp_ui_adapter import LspUiAdapter


def _make_host(active_path):
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
    dispatcher = ActionDispatcher(engine, window, registers, editor_state=editor_state, workspace=workspace)
    dispatcher._command_registry = command_registry
    return SimpleNamespace(
        _dispatcher=dispatcher,
        _workspace=workspace,
        _editor_state=editor_state,
        _invalidate=lambda *_a, **_k: None,
    )


class TestGotoLocation:
    def test_opens_target_in_active_window_by_default(self, tmp_path):
        source = tmp_path / "source.py"
        target = tmp_path / "target.py"
        source.write_text("alpha\n", encoding="utf-8")
        target.write_text("beta\nother\n", encoding="utf-8")

        host = _make_host(source)
        adapter = LspUiAdapter(host)

        adapter.goto_location({"path": str(target), "line": 1, "col": 0})

        windows = host._workspace.active_tab.all_windows()
        assert len(windows) == 1
        assert host._workspace.active_window.document.path == target.resolve()
        assert host._workspace.active_window.cursor.line == 1

    def test_opens_new_split_when_active_window_is_a_compare_pane(self, tmp_path):
        source = tmp_path / "source.py"
        target = tmp_path / "target.py"
        source.write_text("alpha\n", encoding="utf-8")
        target.write_text("beta\n", encoding="utf-8")

        host = _make_host(source)
        active_win = host._workspace.active_window
        host._editor_state.compare_window_ids = (id(active_win), id(active_win))
        adapter = LspUiAdapter(host)

        adapter.goto_location({"path": str(target), "line": 0, "col": 0})

        windows = host._workspace.active_tab.all_windows()
        assert len(windows) == 2
        paths = {w.document.path for w in windows}
        assert paths == {source.resolve(), target.resolve()}
        assert host._workspace.active_window.document.path == target.resolve()

    def test_does_not_split_for_same_file_jump_in_compare_pane(self, tmp_path):
        source = tmp_path / "source.py"
        source.write_text("alpha\nbeta\n", encoding="utf-8")

        host = _make_host(source)
        active_win = host._workspace.active_window
        host._editor_state.compare_window_ids = (id(active_win), id(active_win))
        adapter = LspUiAdapter(host)

        adapter.goto_location({"path": str(source), "line": 1, "col": 0})

        windows = host._workspace.active_tab.all_windows()
        assert len(windows) == 1
        assert host._workspace.active_window.cursor.line == 1

    def test_does_not_split_outside_a_compare_session(self, tmp_path):
        source = tmp_path / "source.py"
        target = tmp_path / "target.py"
        source.write_text("alpha\n", encoding="utf-8")
        target.write_text("beta\n", encoding="utf-8")

        host = _make_host(source)
        adapter = LspUiAdapter(host)

        adapter.goto_location({"path": str(target), "line": 0, "col": 0})

        windows = host._workspace.active_tab.all_windows()
        assert len(windows) == 1

    def test_no_path_is_a_noop(self, tmp_path):
        source = tmp_path / "source.py"
        source.write_text("alpha\n", encoding="utf-8")

        host = _make_host(source)
        adapter = LspUiAdapter(host)

        adapter.goto_location({"line": 0, "col": 0})

        windows = host._workspace.active_tab.all_windows()
        assert len(windows) == 1
        assert host._workspace.active_window.document.path == source.resolve()
