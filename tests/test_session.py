"""
Phase 7b — SessionAPI tests
"""

from __future__ import annotations

import json

import pytest

from peovim.api.session_api import SessionAPI, SessionNotFoundError
from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core import persistence as persistence_mod
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import HSplitNode, VSplitNode, Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_api(content: str = "hello\nworld\n") -> tuple[SessionAPI, Workspace, Window]:
    doc = Document()
    doc.load_string(content)
    window = Window(doc)
    workspace = Workspace(window)
    engine = ModalEngine()
    engine.set_document(doc)
    registers = RegisterStore()
    editor_state = EditorState()
    command_registry = CommandRegistry()
    register_builtins(command_registry)
    dispatcher = ActionDispatcher(engine, window, registers, editor_state=editor_state, workspace=workspace)
    dispatcher._command_registry = command_registry
    return SessionAPI(workspace, engine, dispatcher), workspace, window


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSessionAPI:
    def test_save_creates_json_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        api, workspace, window = _make_session_api()
        api.save("test_sess")
        assert (tmp_path / "sessions" / "test_sess.json").exists()

    def test_save_json_has_correct_schema(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        api, workspace, window = _make_session_api()
        api.save("mytest")
        data = json.loads((tmp_path / "sessions" / "mytest.json").read_text())
        assert data["version"] == 3
        assert "cwd" in data
        assert "tabs" in data
        assert isinstance(data["tabs"], list)
        assert len(data["tabs"]) >= 1
        assert "windows" in data["tabs"][0]
        assert "layout" in data["tabs"][0]

    def test_save_overwrites_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        api, workspace, window = _make_session_api()
        api.save("ow_test")
        api.save("ow_test")
        data = json.loads((tmp_path / "sessions" / "ow_test.json").read_text())
        assert data["version"] == 3

    def test_save_preserves_existing_session_when_replace_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        api, workspace, window = _make_session_api()
        api.save("atomic")
        session_path = tmp_path / "sessions" / "atomic.json"
        original = session_path.read_text(encoding="utf-8")

        def _boom(src, dst) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(persistence_mod.os, "replace", _boom)

        with pytest.raises(OSError, match="replace failed"):
            api.save("atomic")

        assert session_path.read_text(encoding="utf-8") == original
        assert list(session_path.parent.glob("*.tmp")) == []

    def test_list_sessions_returns_sorted(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        api, _, _ = _make_session_api()
        api.save("bravo")
        api.save("alpha")
        api.save("charlie")
        names = api.list_sessions()
        assert names == ["alpha", "bravo", "charlie"]

    def test_list_sessions_empty_when_no_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "no_sessions")
        api, _, _ = _make_session_api()
        assert api.list_sessions() == []

    def test_delete_removes_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        api, _, _ = _make_session_api()
        api.save("to_delete")
        api.delete("to_delete")
        assert "to_delete" not in api.list_sessions()

    def test_delete_raises_on_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        api, _, _ = _make_session_api()
        with pytest.raises(SessionNotFoundError):
            api.delete("nonexistent")

    def test_restore_raises_on_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        api, _, _ = _make_session_api()
        with pytest.raises(SessionNotFoundError):
            api.restore("nonexistent")

    def test_restore_sets_document_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        # Create a real file to load
        test_file = tmp_path / "test.py"
        test_file.write_text("# hello\n")

        api, workspace, window = _make_session_api()
        # Manually write a session pointing to test_file
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        session_data = {
            "version": 1,
            "cwd": str(tmp_path),
            "active_tab": 0,
            "windows": [{"path": str(test_file), "cursor": [0, 0], "scroll": 0}],
        }
        (session_dir / "myfile.json").write_text(json.dumps(session_data))
        api.restore("myfile")
        assert workspace.active_window.document.path == test_file

    def test_save_window_cursor_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        api, workspace, window = _make_session_api()
        window.cursor.line = 3
        window.cursor.col = 7
        api.save("cur_test")
        data = json.loads((tmp_path / "sessions" / "cur_test.json").read_text())
        assert data["tabs"][0]["windows"][0]["cursor"] == [3, 7]

    def test_default_session_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        api, _, _ = _make_session_api()
        api.save()
        assert "default" in api.list_sessions()

    def test_restore_rebuilds_split_layout_and_active_window(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        left = tmp_path / "left.txt"
        top = tmp_path / "top.txt"
        bottom = tmp_path / "bottom.txt"
        left.write_text("left\n", encoding="utf-8")
        top.write_text("top\n", encoding="utf-8")
        bottom.write_text("bottom\n", encoding="utf-8")

        api, workspace, window = _make_session_api()
        workspace.active_tab.split_vertical()
        right = workspace.active_window
        workspace.active_tab.split_horizontal()
        bottom_right = workspace.active_window
        workspace.active_tab.focus_window(window)
        workspace.active_tab.resize_active("h", 2)
        workspace.active_tab.focus_window(right)
        workspace.active_tab.resize_active("v", 2)

        window.document = Document(path=left)
        window.document.load(left)
        right.document = Document(path=top)
        right.document.load(top)
        bottom_right.document = Document(path=bottom)
        bottom_right.document.load(bottom)
        window.cursor.move_to(0, 1)
        right.cursor.move_to(0, 2)
        bottom_right.cursor.move_to(0, 3)
        workspace.active_tab.focus_window(bottom_right)

        api.save("layout")

        replacement = tmp_path / "replacement.txt"
        replacement.write_text("replacement\n", encoding="utf-8")
        window.document = Document(path=replacement)
        window.document.load(replacement)
        workspace.active_tab.only_window()

        api.restore("layout")

        assert isinstance(workspace.active_tab.root, VSplitNode)
        assert workspace.active_tab.root.ratio > 0.55
        assert isinstance(workspace.active_tab.root.right, HSplitNode)
        assert workspace.active_tab.root.right.ratio > 0.55
        leaves = workspace.active_tab.all_leaves()
        assert len(leaves) == 3
        paths = [leaf.window.document.path for leaf in leaves]
        assert paths == [left.resolve(), top.resolve(), bottom.resolve()]
        assert workspace.active_tab.active_window.document.path == bottom.resolve()
        assert workspace.active_tab.active_window.cursor.col == 3

    def test_restore_legacy_session_without_layout(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        test_file = tmp_path / "legacy.txt"
        test_file.write_text("legacy\n", encoding="utf-8")

        api, workspace, window = _make_session_api()
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        session_data = {
            "version": 1,
            "cwd": str(tmp_path),
            "active_tab": 0,
            "windows": [{"path": str(test_file), "cursor": [0, 0], "scroll": 0}],
        }
        (session_dir / "legacy.json").write_text(json.dumps(session_data), encoding="utf-8")

        api.restore("legacy")

        assert workspace.active_window.document.path == test_file.resolve()

    def test_restore_rebuilds_all_tabs_and_active_tab(self, tmp_path, monkeypatch):
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        left = tmp_path / "left.txt"
        right = tmp_path / "right.txt"
        tab2 = tmp_path / "tab2.txt"
        left.write_text("left\n", encoding="utf-8")
        right.write_text("right\n", encoding="utf-8")
        tab2.write_text("tab2\n", encoding="utf-8")

        api, workspace, window = _make_session_api()
        workspace.active_tab.split_vertical()
        right_window = workspace.active_window
        window.document = Document(path=left)
        window.document.load(left)
        right_window.document = Document(path=right)
        right_window.document.load(right)
        right_window.cursor.move_to(0, 2)

        second_window = Window(Document(path=tab2))
        second_window.document.load(tab2)
        second_window.cursor.move_to(0, 1)
        workspace.new_tab(second_window)

        api.save("tabs")

        workspace.goto_tab(0)
        workspace.active_tab.only_window()
        replacement = tmp_path / "replacement.txt"
        replacement.write_text("replacement\n", encoding="utf-8")
        workspace.active_window.document = Document(path=replacement)
        workspace.active_window.document.load(replacement)
        while len(workspace.tabs) > 1:
            workspace.close_tab(len(workspace.tabs) - 1)

        api.restore("tabs")

        assert len(workspace.tabs) == 2
        assert workspace.active_tab_index == 1
        workspace.goto_tab(0)
        assert isinstance(workspace.active_tab.root, VSplitNode)
        tab1_paths = [leaf.window.document.path for leaf in workspace.active_tab.all_leaves()]
        assert tab1_paths == [left.resolve(), right.resolve()]
        workspace.goto_tab(1)
        assert workspace.active_window.document.path == tab2.resolve()
        assert workspace.active_window.cursor.col == 1

    def test_restore_emits_buffer_opened_for_each_file(self, tmp_path, monkeypatch):
        """buffer_opened must fire for every restored document so LSP and plugins attach."""
        monkeypatch.setattr("peovim.api.session_api.SessionAPI._sessions_dir", tmp_path / "sessions")
        api, workspace, _win = _make_session_api()

        f1 = tmp_path / "alpha.py"
        f1.write_text("x = 1\n")
        f2 = tmp_path / "beta.py"
        f2.write_text("y = 2\n")

        workspace.active_window.document = Document(path=f1)
        workspace.active_window.document.load(f1)
        api.save("opened_event_test")

        # Replace the workspace with a different file so restore is non-trivial
        workspace.active_window.document = Document(path=f2)
        workspace.active_window.document.load(f2)

        opened: list[str] = []
        api._dispatcher._editor_state.event_bus.on(
            "buffer_opened", lambda path=None, **kw: opened.append(path) if path else None
        )

        api.restore("opened_event_test")

        assert str(f1.resolve()) in opened
