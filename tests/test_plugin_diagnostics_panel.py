"""Tests for the persistent diagnostics sidebar plugin."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
from peovim.plugins.diagnostics_panel import _build_diagnostic_nodes, _DiagnosticsSidebarPanel, setup


def _make_api() -> EditorAPI:
    doc = Document()
    doc.path = Path("/tmp/sample.py")
    doc.load_string("alpha beta gamma\n")
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
    api._event_loop = SimpleNamespace(
        _cmdline=SimpleNamespace(enter=lambda *args, **kwargs: None), _invalidate_cmdline=lambda: None
    )
    return api


class TestDiagnosticsPanelPlugin:
    def test_setup_registers_panel_and_command(self):
        api = _make_api()

        setup(api)

        assert "diagnostics" in api.ui.list_sidebar_panels()
        assert api.commands._registry.get("DiagnosticsPanel") is not None
        assert api.ui.get_sidebar_panel("diagnostics") is not None

    def test_toggle_shows_and_hides_sidebar(self, monkeypatch):
        api = _make_api()
        monkeypatch.setattr(api, "list_diagnostics", lambda: [])

        setup(api)
        api.commands.execute("DiagnosticsPanel")
        assert api.ui.is_sidebar_visible("diagnostics")

        api.commands.execute("DiagnosticsPanel")
        assert not api.ui.is_sidebar_visible("diagnostics")

    def test_refresh_without_diagnostics_shows_placeholder(self, monkeypatch):
        api = _make_api()
        monkeypatch.setattr(api, "list_diagnostics", lambda: [])

        setup(api)
        panel = api.ui.get_sidebar_panel("diagnostics")
        assert isinstance(panel, _DiagnosticsSidebarPanel)

        panel.refresh()

        assert panel._tree._roots[0].label == "No diagnostics"

    def test_refresh_builds_grouped_nodes_from_diagnostics(self, monkeypatch):
        api = _make_api()
        monkeypatch.setattr(
            api,
            "list_diagnostics",
            lambda: [
                {
                    "path": Path("/tmp/other.py"),
                    "line": 8,
                    "col": 1,
                    "severity": "E",
                    "message": "broken",
                },
                {
                    "path": Path("/tmp/other.py"),
                    "line": 10,
                    "col": 0,
                    "severity": "W",
                    "message": "unused",
                },
            ],
        )

        setup(api)
        panel = api.ui.get_sidebar_panel("diagnostics")
        assert isinstance(panel, _DiagnosticsSidebarPanel)

        panel.refresh()

        assert panel._tree._title == "Diagnostics [2]"
        assert panel._tree._roots[0].label == "other.py (2)"
        children = panel._tree._roots[0].get_children()
        assert children[0].label == "9:2 broken"
        assert children[0].icon == "err"
        assert children[1].label == "11:1 unused"

    def test_selecting_diagnostic_opens_buffer_and_blurs_sidebar(self, monkeypatch):
        api = _make_api()
        panel = _DiagnosticsSidebarPanel(api)
        opened: list[tuple[Path, int, int]] = []
        blurred: list[bool] = []
        monkeypatch.setattr(api, "goto_location", lambda path, line=0, col=0: opened.append((Path(path), line, col)))
        monkeypatch.setattr(api.ui, "blur_sidebar", lambda: blurred.append(True))
        node = SimpleNamespace(value=(str(Path("/tmp/other.py")), 8, 1))

        panel._on_select(node)

        assert opened == [(Path("/tmp/other.py"), 8, 1)]
        assert blurred == [True]

    def test_cursor_move_same_file_scrolls_editor_without_blurring(self, monkeypatch):
        api = _make_api()
        panel = _DiagnosticsSidebarPanel(api)
        path = str(api._workspace.active_window.document.path)
        set_cursor_calls: list[tuple[int, int]] = []
        scroll_calls: list[bool] = []
        fake_win = SimpleNamespace(
            set_cursor=lambda ln, c: set_cursor_calls.append((ln, c)),
            scroll_to_cursor=lambda: scroll_calls.append(True),
        )
        monkeypatch.setattr(api, "active_window", lambda: fake_win)
        node = SimpleNamespace(value=(path, 3, 0))

        panel._on_cursor_move(node)

        assert set_cursor_calls == [(3, 0)]
        assert scroll_calls == [True]

    def test_cursor_move_cross_file_opens_buffer_without_blurring(self, monkeypatch):
        api = _make_api()
        panel = _DiagnosticsSidebarPanel(api)
        opened: list[tuple[Path, int, int]] = []
        blurred: list[bool] = []
        monkeypatch.setattr(api, "open_buffer", lambda path, line=0, col=0: opened.append((Path(path), line, col)))
        monkeypatch.setattr(api.ui, "blur_sidebar", lambda: blurred.append(True))
        node = SimpleNamespace(value=("/other/file.py", 5, 2))

        panel._on_cursor_move(node)

        assert opened == [(Path("/other/file.py"), 5, 2)]
        assert blurred == []

    def test_cursor_move_on_file_group_node_is_ignored(self, monkeypatch):
        api = _make_api()
        panel = _DiagnosticsSidebarPanel(api)
        opened: list = []
        monkeypatch.setattr(api, "open_buffer", lambda *a, **k: opened.append(a))
        node = SimpleNamespace(value=("file", "/tmp/sample.py"))

        panel._on_cursor_move(node)

        assert opened == []

    def test_visible_panel_refreshes_on_diagnostics_updated(self, monkeypatch):
        api = _make_api()
        monkeypatch.setattr(api, "list_diagnostics", lambda: [])

        setup(api)
        panel = api.ui.get_sidebar_panel("diagnostics")
        assert isinstance(panel, _DiagnosticsSidebarPanel)
        api.ui.show_sidebar_panel("diagnostics", panel, focus=False)
        refreshed: list[bool] = []
        monkeypatch.setattr(panel, "refresh", lambda: refreshed.append(True))

        api.events.emit("diagnostics_updated", path=str(api.active_buffer().path), diagnostics=[], count=0)

        assert refreshed == [True]

    def test_panel_show_does_not_start_polling_interval(self, monkeypatch):
        api = _make_api()
        monkeypatch.setattr(api, "list_diagnostics", lambda: [])
        interval_calls: list[tuple[Any, int]] = []
        monkeypatch.setattr(api, "set_interval", lambda fn, interval_ms: interval_calls.append((fn, interval_ms)))

        panel = _DiagnosticsSidebarPanel(api)

        panel.on_show()

        assert interval_calls == []


class TestDiagnosticsPanelHelpers:
    def test_build_diagnostic_nodes_groups_by_file(self):
        nodes = _build_diagnostic_nodes(
            [
                {"path": "/tmp/other.py", "line": 8, "col": 1, "severity": "E", "message": "broken"},
                {"path": "/tmp/other.py", "line": 10, "col": 0, "severity": "W", "message": "unused"},
            ]
        )

        assert nodes[0].label == "other.py (2)"
        assert len(nodes[0].get_children()) == 2
