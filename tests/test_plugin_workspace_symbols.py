"""Tests for the persistent workspace symbols sidebar plugin."""

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
from peovim.plugins.workspace_symbols import _build_symbol_nodes, _WorkspaceSymbolsSidebarPanel, setup


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


class TestWorkspaceSymbolsPlugin:
    def test_setup_registers_panel_and_command(self):
        api = _make_api()

        setup(api)

        assert "workspace-symbols" in api.ui.list_sidebar_panels()
        assert api.commands._registry.get("WorkspaceSymbolsPanel") is not None
        assert api.ui.get_sidebar_panel("workspace-symbols") is not None

    def test_toggle_uses_word_under_cursor_query(self):
        api = _make_api()
        api._workspace.active_window.cursor.move_to(0, 7)
        api.lsp = SimpleNamespace(workspace_symbol_search=lambda query, cb: cb([]))

        setup(api)
        api.commands.execute("WorkspaceSymbolsPanel")

        assert api.ui.is_sidebar_visible("workspace-symbols")
        panel = api.ui.get_sidebar_panel("workspace-symbols")
        assert isinstance(panel, _WorkspaceSymbolsSidebarPanel)
        assert panel._query == "beta"

    def test_toggle_hides_visible_panel_when_query_is_unchanged(self):
        api = _make_api()
        api._workspace.active_window.cursor.move_to(0, 7)
        api.lsp = SimpleNamespace(workspace_symbol_search=lambda query, cb: cb([]))

        setup(api)
        api.commands.execute("WorkspaceSymbolsPanel")
        assert api.ui.is_sidebar_visible("workspace-symbols")

        api.commands.execute("WorkspaceSymbolsPanel")
        assert not api.ui.is_sidebar_visible("workspace-symbols")

    def test_refresh_without_lsp_shows_placeholder(self):
        api = _make_api()

        setup(api)
        panel = api.ui.get_sidebar_panel("workspace-symbols")
        assert isinstance(panel, _WorkspaceSymbolsSidebarPanel)
        panel.set_query("beta")

        panel.refresh()

        assert panel._tree._roots[0].label == "LSP unavailable"

    def test_refresh_without_query_shows_placeholder(self):
        api = _make_api()
        api.lsp = SimpleNamespace(workspace_symbol_search=lambda query, cb: cb([]))

        setup(api)
        panel = api.ui.get_sidebar_panel("workspace-symbols")
        assert isinstance(panel, _WorkspaceSymbolsSidebarPanel)

        panel.refresh()

        assert panel._tree._roots[0].label == "No workspace symbol query"

    def test_on_show_uses_word_under_cursor_when_query_is_blank(self):
        api = _make_api()
        api._workspace.active_window.cursor.move_to(0, 7)
        seen_queries: list[str] = []
        api.lsp = SimpleNamespace(workspace_symbol_search=lambda query, cb: (seen_queries.append(query), cb([]))[-1])

        setup(api)
        panel = api.ui.get_sidebar_panel("workspace-symbols")
        assert isinstance(panel, _WorkspaceSymbolsSidebarPanel)

        panel.on_show()

        assert panel._query == "beta"
        assert seen_queries == ["beta"]

    def test_refresh_builds_nodes_from_workspace_symbols(self):
        api = _make_api()
        api.lsp = SimpleNamespace(
            workspace_symbol_search=lambda query, cb: cb(
                [
                    {
                        "name": "beta",
                        "kind": "function",
                        "detail": "pkg.mod",
                        "path": str(Path("/tmp/other.py")),
                        "line": 8,
                        "col": 1,
                    }
                ]
            )
        )

        setup(api)
        panel = api.ui.get_sidebar_panel("workspace-symbols")
        assert isinstance(panel, _WorkspaceSymbolsSidebarPanel)
        panel.set_query("beta")

        panel.refresh()

        assert panel._tree._title == "Workspace Symbols [beta]"
        assert panel._tree._roots[0].label == "function     beta — pkg.mod"

    def test_selecting_symbol_opens_buffer_and_blurs_sidebar(self, monkeypatch):
        api = _make_api()
        panel = _WorkspaceSymbolsSidebarPanel(api)
        opened: list[tuple[Path, int, int]] = []
        blurred: list[bool] = []
        monkeypatch.setattr(api, "goto_location", lambda path, line=0, col=0: opened.append((Path(path), line, col)))
        monkeypatch.setattr(api.ui, "blur_sidebar", lambda: blurred.append(True))
        node = SimpleNamespace(value=(str(Path("/tmp/other.py")), 8, 1, "function", "beta"))

        panel._on_select(node)

        assert opened == [(Path("/tmp/other.py"), 8, 1)]
        assert blurred == [True]

    def test_cursor_move_same_file_scrolls_editor_without_blurring(self, monkeypatch):
        api = _make_api()
        panel = _WorkspaceSymbolsSidebarPanel(api)
        path = str(api._workspace.active_window.document.path)
        set_cursor_calls: list[tuple[int, int]] = []
        scroll_calls: list[bool] = []
        fake_win = SimpleNamespace(
            set_cursor=lambda ln, c: set_cursor_calls.append((ln, c)),
            scroll_to_cursor=lambda: scroll_calls.append(True),
        )
        monkeypatch.setattr(api, "active_window", lambda: fake_win)
        node = SimpleNamespace(value=(path, 3, 0, "function", "foo"))

        panel._on_cursor_move(node)

        assert set_cursor_calls == [(3, 0)]
        assert scroll_calls == [True]

    def test_cursor_move_cross_file_opens_buffer_without_blurring(self, monkeypatch):
        api = _make_api()
        panel = _WorkspaceSymbolsSidebarPanel(api)
        opened: list[tuple[Path, int, int]] = []
        blurred: list[bool] = []
        monkeypatch.setattr(api, "open_buffer", lambda path, line=0, col=0: opened.append((Path(path), line, col)))
        monkeypatch.setattr(api.ui, "blur_sidebar", lambda: blurred.append(True))
        node = SimpleNamespace(value=("/other/file.py", 5, 2, "class", "Foo"))

        panel._on_cursor_move(node)

        assert opened == [(Path("/other/file.py"), 5, 2)]
        assert blurred == []

    def test_slash_key_opens_query_prompt(self, monkeypatch):
        api = _make_api()
        panel = _WorkspaceSymbolsSidebarPanel(api)
        prompts: list[str] = []
        monkeypatch.setattr(api, "open_cmdline", lambda initial="", prompt=":": prompts.append(initial))
        panel.set_query("beta")

        handled = panel._on_key("/", None)

        assert handled
        assert prompts == ["WorkspaceSymbolsPanel beta"]


class TestWorkspaceSymbolsHelpers:
    def test_build_symbol_nodes_formats_detail(self):
        nodes = _build_symbol_nodes(
            [
                {
                    "name": "beta",
                    "kind": "function",
                    "detail": "pkg.mod",
                    "path": "/tmp/other.py",
                    "line": 8,
                    "col": 1,
                }
            ]
        )

        assert nodes[0].label == "function     beta — pkg.mod"
