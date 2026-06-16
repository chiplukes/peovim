"""Tests for the persistent document outline sidebar plugin."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
from peovim.plugins.outline import _build_outline_nodes, _OutlineSidebarPanel, setup


def _make_api() -> EditorAPI:
    doc = Document()
    doc.path = Path("/tmp/sample.py")
    doc.load_string("class Outer:\n    def inner(self):\n        return 1\n")
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


class TestOutlinePlugin:
    def test_setup_registers_panel_and_command(self):
        api = _make_api()

        setup(api)

        assert "outline" in api.ui.list_sidebar_panels()
        assert api.commands._registry.get("Outline") is not None
        assert api.ui.get_sidebar_panel("outline") is not None

    def test_toggle_shows_and_hides_sidebar(self):
        api = _make_api()
        api.lsp = SimpleNamespace(document_symbol_tree=lambda cb: cb([]))

        setup(api)
        api.commands.execute("Outline")
        assert api.ui.is_sidebar_visible("outline")

        api.commands.execute("Outline")
        assert not api.ui.is_sidebar_visible("outline")

    def test_refresh_without_lsp_shows_placeholder(self):
        api = _make_api()

        setup(api)
        panel = api.ui.get_sidebar_panel("outline")
        assert isinstance(panel, _OutlineSidebarPanel)

        panel.refresh()

        assert panel._tree._roots[0].label == "LSP unavailable"

    def test_refresh_builds_outline_nodes_from_document_symbols(self):
        api = _make_api()
        api.lsp = SimpleNamespace(
            document_symbol_tree=lambda cb: cb(
                [
                    {
                        "name": "Outer",
                        "kind": "class",
                        "detail": "",
                        "path": str(api._workspace.active_window.document.path),
                        "line": 0,
                        "col": 0,
                        "end_line": 2,
                        "end_col": 0,
                        "children": [
                            {
                                "name": "inner",
                                "kind": "method",
                                "detail": "Outer",
                                "path": str(api._workspace.active_window.document.path),
                                "line": 1,
                                "col": 4,
                                "end_line": 2,
                                "end_col": 0,
                                "children": [],
                            }
                        ],
                    }
                ]
            )
        )

        setup(api)
        panel = api.ui.get_sidebar_panel("outline")
        assert isinstance(panel, _OutlineSidebarPanel)

        panel.refresh()

        assert panel._tree._title == "Outline [sample.py]"
        assert panel._tree._roots[0].label == "Outer"
        assert panel._tree._roots[0].get_children()[0].label == "inner"

    def test_selecting_symbol_opens_buffer_and_blurs_sidebar(self, monkeypatch):
        api = _make_api()
        panel = _OutlineSidebarPanel(api)
        opened: list[tuple[Path, int, int]] = []
        blurred: list[bool] = []
        monkeypatch.setattr(api, "goto_location", lambda path, line=0, col=0: opened.append((Path(path), line, col)))
        monkeypatch.setattr(api.ui, "blur_sidebar", lambda: blurred.append(True))
        node = SimpleNamespace(value=(str(Path("/tmp/sample.py")), 2, 3, "function", "inner"))

        panel._on_select(node)

        assert opened == [(Path("/tmp/sample.py"), 2, 3)]
        assert blurred == [True]

    def test_cursor_move_in_outline_scrolls_editor_without_blurring(self, monkeypatch):
        api = _make_api()
        panel = _OutlineSidebarPanel(api)
        path = str(api._workspace.active_window.document.path)
        set_cursor_calls: list[tuple[int, int]] = []
        scroll_calls: list[bool] = []
        fake_win = SimpleNamespace(
            set_cursor=lambda ln, c: set_cursor_calls.append((ln, c)),
            scroll_to_cursor=lambda: scroll_calls.append(True),
        )
        monkeypatch.setattr(api, "active_window", lambda: fake_win)
        node = SimpleNamespace(value=(path, 5, 0, "function", "foo"))

        panel._on_cursor_move(node)

        assert set_cursor_calls == [(5, 0)]
        assert scroll_calls == [True]

    def test_cursor_move_in_outline_ignores_cross_file_symbols(self, monkeypatch):
        api = _make_api()
        panel = _OutlineSidebarPanel(api)
        set_cursor_calls: list = []
        fake_win = SimpleNamespace(set_cursor=lambda ln, c: set_cursor_calls.append((ln, c)))
        monkeypatch.setattr(api, "active_window", lambda: fake_win)
        node = SimpleNamespace(value=("/other/file.py", 5, 0, "function", "foo"))

        panel._on_cursor_move(node)

        assert set_cursor_calls == []

    def test_cursor_moved_event_refreshes_visible_outline_panel(self, monkeypatch):
        api = _make_api()
        api.lsp = SimpleNamespace(document_symbol_tree=lambda cb: cb([]))

        setup(api)
        panel = api.ui.get_sidebar_panel("outline")
        assert isinstance(panel, _OutlineSidebarPanel)
        api.ui.show_sidebar_panel("outline", panel, focus=False)
        scheduled: list[int] = []
        monkeypatch.setattr(panel, "schedule_refresh", lambda delay_ms=200: scheduled.append(delay_ms))

        api.events.emit("cursor_moved", buf_id=id(api._workspace.active_window.document), line=1, col=0)

        assert scheduled == [200]


class TestOutlineHelpers:
    def test_build_outline_nodes_marks_first_top_level_node_expanded(self):
        nodes = _build_outline_nodes(
            [
                {
                    "name": "Outer",
                    "kind": "class",
                    "detail": "",
                    "path": "/tmp/sample.py",
                    "line": 0,
                    "col": 0,
                    "end_line": 3,
                    "end_col": 0,
                    "children": [
                        {
                            "name": "inner",
                            "kind": "method",
                            "detail": "Outer",
                            "path": "/tmp/sample.py",
                            "line": 1,
                            "col": 4,
                            "end_line": 2,
                            "end_col": 0,
                            "children": [],
                        }
                    ],
                }
            ]
        )

        assert nodes[0].expanded
        assert nodes[0].get_children()[0].label == "inner"
