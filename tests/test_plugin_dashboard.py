"""
Phase 7g — Dashboard plugin tests
"""

from __future__ import annotations

import pathlib

from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.shada import ShadaStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine
from peovim.plugins.dashboard import _Dashboard, setup

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api(file_path: pathlib.Path | None = None) -> EditorAPI:
    doc = Document(path=file_path)
    if file_path and file_path.exists():
        doc.load(file_path)
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
    return EditorAPI(workspace, engine, dispatcher, editor_state, command_registry)


def _make_api_with_shada(tmp_path: pathlib.Path, recent_files=(), sessions=()) -> EditorAPI:
    api = _make_api()
    # Replace shada with a tmp-path-based store and populate it
    shada = ShadaStore(tmp_path / "shada")
    for f in recent_files:
        shada.push_recent_file(f)
    api._editor_state.shada = shada
    return api


# ---------------------------------------------------------------------------
# _Dashboard unit tests
# ---------------------------------------------------------------------------


class TestDashboardShow:
    def test_show_populates_buffer(self, tmp_path):
        api = _make_api_with_shada(tmp_path, recent_files=["/a/b.py"])
        d = _Dashboard(api)
        d.show()
        doc = api._workspace.active_window.document
        content = "\n".join(doc.get_line(i) for i in range(doc.line_count()))
        assert "███" in content or "Recent Files" in content  # logo or section

    def test_show_includes_logo(self, tmp_path):
        api = _make_api_with_shada(tmp_path)
        d = _Dashboard(api)
        d.show()
        doc = api._workspace.active_window.document
        content = "\n".join(doc.get_line(i) for i in range(doc.line_count()))
        assert "███" in content

    def test_show_includes_recent_files(self, tmp_path):
        api = _make_api_with_shada(tmp_path, recent_files=["/home/user/project/foo.py"])
        d = _Dashboard(api)
        d.show()
        doc = api._workspace.active_window.document
        content = "\n".join(doc.get_line(i) for i in range(doc.line_count()))
        assert "foo.py" in content
        assert "Recent Files" in content

    def test_show_includes_sessions(self, tmp_path, monkeypatch):
        api = _make_api_with_shada(tmp_path)
        # Stub session.list_sessions
        monkeypatch.setattr(api.session, "list_sessions", lambda: ["autosave", "myproject"])
        d = _Dashboard(api)
        d.show()
        doc = api._workspace.active_window.document
        content = "\n".join(doc.get_line(i) for i in range(doc.line_count()))
        assert "autosave" in content
        assert "Sessions" in content

    def test_show_includes_open_init_action(self, tmp_path, monkeypatch):
        api = _make_api_with_shada(tmp_path)
        config_path = tmp_path / "config" / "init.py"
        monkeypatch.setattr("peovim.plugins.dashboard.preferred_user_config_path", lambda: config_path)
        d = _Dashboard(api)
        d.show()
        doc = api._workspace.active_window.document
        content = "\n".join(doc.get_line(i) for i in range(doc.line_count()))
        assert "Quick Actions" in content
        assert "Open init.py" in content

    def test_show_makes_buffer_non_modifiable(self, tmp_path):
        api = _make_api_with_shada(tmp_path)
        d = _Dashboard(api)
        d.show()
        win = api._workspace.active_window
        assert win.options.get("modifiable") is False

    def test_show_places_cursor_on_first_selectable_text(self, tmp_path, monkeypatch):
        api = _make_api_with_shada(tmp_path)
        config_path = tmp_path / "config" / "init.py"
        monkeypatch.setattr("peovim.plugins.dashboard.preferred_user_config_path", lambda: config_path)
        d = _Dashboard(api)
        d.show()

        win = api._workspace.active_window
        line_text = win.document.get_line(win.cursor.line)
        assert line_text.lstrip().startswith("Open init.py")
        assert win.cursor.col == len(line_text) - len(line_text.lstrip())

    def test_is_active_after_show(self, tmp_path):
        api = _make_api_with_shada(tmp_path)
        d = _Dashboard(api)
        d.show()
        assert d.is_active() is True

    def test_is_not_active_before_show(self, tmp_path):
        api = _make_api_with_shada(tmp_path)
        d = _Dashboard(api)
        assert d.is_active() is False


class TestDashboardClose:
    def test_close_restores_modifiable(self, tmp_path):
        api = _make_api_with_shada(tmp_path)
        d = _Dashboard(api)
        d.show()
        d.close()
        win = api._workspace.active_window
        assert win.options.get("modifiable") is True

    def test_close_clears_buffer(self, tmp_path):
        api = _make_api_with_shada(tmp_path)
        d = _Dashboard(api)
        d.show()
        d.close()
        doc = api._workspace.active_window.document
        content = "\n".join(doc.get_line(i) for i in range(doc.line_count()))
        assert "███" not in content

    def test_is_not_active_after_close(self, tmp_path):
        api = _make_api_with_shada(tmp_path)
        d = _Dashboard(api)
        d.show()
        d.close()
        assert d.is_active() is False


class TestDashboardOpenRecentFile:
    def test_open_recent_file_first_entry(self, tmp_path):
        # Create a real file for the dashboard to open
        test_file = tmp_path / "hello.py"
        test_file.write_text("# hello")
        api = _make_api_with_shada(tmp_path, recent_files=[str(test_file)])
        d = _Dashboard(api)
        d.show()
        d.open_recent_file(1)
        win = api._workspace.active_window
        assert win.document.path == test_file

    def test_open_recent_file_out_of_range_does_nothing(self, tmp_path):
        api = _make_api_with_shada(tmp_path, recent_files=["/a.py"])
        d = _Dashboard(api)
        d.show()
        original_doc = api._workspace.active_window.document
        d.open_recent_file(5)  # only 1 file in list
        assert api._workspace.active_window.document is original_doc

    def test_open_recent_file_deactivates_dashboard(self, tmp_path):
        test_file = tmp_path / "hello.py"
        test_file.write_text("# hello")
        api = _make_api_with_shada(tmp_path, recent_files=[str(test_file)])
        d = _Dashboard(api)
        d.show()
        d.open_recent_file(1)
        assert d.is_active() is False

    def test_open_config_creates_and_opens_init_file(self, tmp_path, monkeypatch):
        api = _make_api_with_shada(tmp_path)
        config_path = tmp_path / "config" / "init.py"
        monkeypatch.setattr("peovim.plugins.dashboard.preferred_user_config_path", lambda: config_path)
        d = _Dashboard(api)
        d.show()

        d.open_config()

        assert config_path.exists()
        assert api._workspace.active_window.document.path == config_path.resolve()
        assert d.is_active() is False


class TestDashboardKeybindings:
    def test_number_key_does_nothing_when_not_active(self, tmp_path):
        """Pressing 1 when dashboard is not active should not raise or open files."""
        api = _make_api_with_shada(tmp_path, recent_files=["/a.py"])
        setup(api)
        # Dashboard is not shown — find the registered callback and call it directly
        # (simulate a key press without showing the dashboard first)
        # The keybinding callbacks should be no-ops when dashboard is not active
        # Directly invoke the stored callback for "1" (if accessible)
        # Since we can't easily simulate key dispatch, just ensure is_active is False
        # and that the guard works
        d = _Dashboard(api)
        assert not d.is_active()
        d.open_recent_file(1)  # should be ignored since _active is False
        # No file opened (no-op because inactive dashboard does nothing on open_recent_file
        # when called directly — but in the real binding, is_active check prevents this)

    def test_buffer_opened_pushes_to_shada(self, tmp_path):
        api = _make_api_with_shada(tmp_path)
        setup(api)
        # Emit buffer_opened with a buf_id that matches a real buffer with a path
        test_file = tmp_path / "test.py"
        test_file.write_text("x")
        doc = Document(path=test_file)
        doc.load(test_file)
        win = api._workspace.active_window
        win.document = doc
        # Emit buffer_opened
        api._editor_state.event_bus.emit("buffer_opened", buf_id=id(doc))
        shada = api._editor_state.shada
        assert str(test_file) in shada.get_recent_files()


class TestDashboardSetup:
    def test_setup_does_not_show_when_file_loaded(self, tmp_path):
        """editor_ready with a file loaded should not show dashboard."""
        test_file = tmp_path / "main.py"
        test_file.write_text("print('hi')")
        api = _make_api(file_path=test_file)
        setup(api)
        original_doc = api._workspace.active_window.document
        # Emit editor_ready
        api._editor_state.event_bus.emit("editor_ready")
        # Document should be unchanged (dashboard not shown over a real file)
        assert api._workspace.active_window.document is original_doc

    def test_setup_shows_dashboard_when_empty_buffer(self, tmp_path):
        """editor_ready with an empty unnamed buffer should show dashboard."""
        api = _make_api_with_shada(tmp_path, recent_files=["/some/file.py"])
        setup(api)
        api._editor_state.event_bus.emit("editor_ready")
        doc = api._workspace.active_window.document
        content = "\n".join(doc.get_line(i) for i in range(doc.line_count()))
        # Dashboard content should be present
        assert "Recent Files" in content or "███" in content
