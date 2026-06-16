"""Tests for the persistent references sidebar plugin."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import peovim.plugins.references_panel as _mod
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
from peovim.plugins.references_panel import (
    _build_preview_content,
    _build_reference_nodes,
    _ReferencesSidebarPanel,
    configure,
    setup,
)


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


class TestReferencesPanelPlugin:
    def test_setup_registers_panel_and_command(self):
        api = _make_api()

        setup(api)

        assert "references" in api.ui.list_sidebar_panels()
        assert api.commands._registry.get("ReferencesPanel") is not None
        assert api.ui.get_sidebar_panel("references") is not None

    def test_toggle_shows_and_hides_sidebar(self):
        api = _make_api()
        api.lsp = SimpleNamespace(references_search=lambda cb: cb([]))

        setup(api)
        api.commands.execute("ReferencesPanel")
        assert api.ui.is_sidebar_visible("references")

        api.commands.execute("ReferencesPanel")
        assert not api.ui.is_sidebar_visible("references")

    def test_refresh_without_lsp_shows_placeholder(self):
        api = _make_api()

        setup(api)
        panel = api.ui.get_sidebar_panel("references")
        assert isinstance(panel, _ReferencesSidebarPanel)

        panel.refresh()

        assert panel._tree._roots[0].label == "LSP unavailable"

    def test_refresh_builds_nodes_from_references(self):
        api = _make_api()
        api.lsp = SimpleNamespace(
            references_search=lambda cb: cb(
                [
                    {"path": "/tmp/other.py", "line": 8, "col": 1},
                ]
            )
        )
        api._workspace.active_window.cursor.move_to(0, 7)

        setup(api)
        panel = api.ui.get_sidebar_panel("references")
        assert isinstance(panel, _ReferencesSidebarPanel)

        panel.refresh()

        assert panel._tree._title == "References [beta]"
        assert panel._tree._roots[0].label == "other.py:9:2"

    def test_selecting_reference_closes_preview_and_jumps(self, monkeypatch):
        api = _make_api()
        panel = _ReferencesSidebarPanel(api)
        opened: list[tuple[Path, int, int]] = []
        blurred: list[bool] = []
        closed: list = []
        monkeypatch.setattr(api, "goto_location", lambda path, line=0, col=0: opened.append((Path(path), line, col)))
        monkeypatch.setattr(api.ui, "blur_sidebar", lambda: blurred.append(True))
        fake_float = SimpleNamespace(is_open=True, close=lambda: closed.append(True))
        panel._preview_float = fake_float
        node = SimpleNamespace(value=(str(Path("/tmp/other.py")), 8, 1))

        panel._on_select(node)

        assert closed == [True]
        assert opened == [(Path("/tmp/other.py"), 8, 1)]
        assert blurred == [True]

    def test_cursor_moved_event_refreshes_visible_references_panel(self, monkeypatch):
        api = _make_api()
        api.lsp = SimpleNamespace(references_search=lambda cb: cb([]))

        setup(api)
        panel = api.ui.get_sidebar_panel("references")
        assert isinstance(panel, _ReferencesSidebarPanel)
        api.ui.show_sidebar_panel("references", panel, focus=False)
        scheduled: list[int] = []
        monkeypatch.setattr(panel, "schedule_refresh", lambda delay_ms=200: scheduled.append(delay_ms))

        api.events.emit("cursor_moved", buf_id=id(api._workspace.active_window.document), line=1, col=0)

        assert scheduled == [200]

    def test_cursor_moved_skipped_while_previewing(self, monkeypatch):
        api = _make_api()
        api.lsp = SimpleNamespace(references_search=lambda cb: cb([]))

        setup(api)
        panel = api.ui.get_sidebar_panel("references")
        assert isinstance(panel, _ReferencesSidebarPanel)
        api.ui.show_sidebar_panel("references", panel, focus=False)
        scheduled: list = []
        monkeypatch.setattr(panel, "schedule_refresh", lambda delay_ms=200: scheduled.append(delay_ms))

        panel._is_previewing = True
        api.events.emit("cursor_moved", buf_id=id(api._workspace.active_window.document), line=1, col=0)

        assert scheduled == []

    def test_schedule_refresh_captures_label_at_schedule_time_not_execution_time(self, monkeypatch):
        """_label should reflect the word when schedule_refresh was called, not when refresh fires."""
        api = _make_api()
        api._workspace.active_window.cursor.move_to(0, 7)
        api.lsp = SimpleNamespace(references_search=lambda cb: cb([]))

        setup(api)
        panel = api.ui.get_sidebar_panel("references")
        assert isinstance(panel, _ReferencesSidebarPanel)

        deferred: list = []
        monkeypatch.setattr(
            "asyncio.get_event_loop", lambda: SimpleNamespace(call_later=lambda delay, fn: deferred.append(fn))
        )

        panel.schedule_refresh()
        assert panel._pending_label == "beta"

        api._workspace.active_window.document.load_string("if foo:\n    pass\n")
        api._workspace.active_window.cursor.move_to(0, 0)

        assert len(deferred) == 1
        deferred[0]()

        assert panel._label == "beta"
        assert panel._tree._title == "References [beta]"

    # ------------------------------------------------------------------
    # Float preview
    # ------------------------------------------------------------------

    def test_cursor_move_opens_preview_float(self, monkeypatch):
        api = _make_api()
        panel = _ReferencesSidebarPanel(api)
        opened: list[dict] = []
        fake_handle = SimpleNamespace(is_open=True, set_content=lambda c: None, set_title=lambda t: None)
        monkeypatch.setattr(api.ui, "open_float", lambda content, **kw: opened.append(kw) or fake_handle)
        monkeypatch.setitem(_mod._config, "preview_mode", "float")
        node = SimpleNamespace(value=(str(Path("/tmp/sample.py")), 0, 0))

        panel._on_cursor_move(node)

        assert len(opened) == 1
        assert opened[0]["title"] == "sample.py:1"
        assert panel._preview_float is fake_handle

    def test_cursor_move_updates_existing_float_instead_of_reopening(self, monkeypatch):
        api = _make_api()
        panel = _ReferencesSidebarPanel(api)
        open_calls: list = []
        content_updates: list = []
        fake_handle = SimpleNamespace(
            is_open=True,
            set_content=lambda c: content_updates.append(c),
            set_title=lambda t: None,
        )
        monkeypatch.setattr(api.ui, "open_float", lambda content, **kw: open_calls.append(1) or fake_handle)
        monkeypatch.setitem(_mod._config, "preview_mode", "float")
        node = SimpleNamespace(value=(str(Path("/tmp/sample.py")), 0, 0))

        panel._on_cursor_move(node)
        panel._on_cursor_move(node)

        assert len(open_calls) == 1
        assert len(content_updates) == 1

    def test_on_blur_closes_preview_float(self):
        api = _make_api()
        panel = _ReferencesSidebarPanel(api)
        closed: list = []
        fake_handle = SimpleNamespace(is_open=True, close=lambda: closed.append(True))
        panel._preview_float = fake_handle

        panel.on_blur()

        assert closed == [True]
        assert panel._preview_float is None

    # ------------------------------------------------------------------
    # Cursor mode
    # ------------------------------------------------------------------

    def test_cursor_mode_same_file_moves_cursor(self, monkeypatch):
        api = _make_api()
        panel = _ReferencesSidebarPanel(api)
        set_cursor_calls: list = []
        scroll_calls: list = []
        from peovim.api import window_api

        monkeypatch.setattr(window_api.WindowAPI, "set_cursor", lambda self, ln, c: set_cursor_calls.append((ln, c)))
        monkeypatch.setattr(window_api.WindowAPI, "scroll_to_cursor", lambda self: scroll_calls.append(True))
        monkeypatch.setitem(_mod._config, "preview_mode", "cursor")
        node = SimpleNamespace(value=(str(Path("/tmp/sample.py")), 3, 5))

        panel._on_cursor_move(node)

        assert set_cursor_calls == [(3, 5)]
        assert scroll_calls == [True]

    def test_cursor_mode_suppresses_refresh(self, monkeypatch):
        api = _make_api()
        api.lsp = SimpleNamespace(references_search=lambda cb: cb([]))

        setup(api)
        panel = api.ui.get_sidebar_panel("references")
        assert isinstance(panel, _ReferencesSidebarPanel)
        api.ui.show_sidebar_panel("references", panel, focus=False)
        scheduled: list = []
        monkeypatch.setattr(panel, "schedule_refresh", lambda delay_ms=200: scheduled.append(delay_ms))

        panel._is_previewing = True
        api.events.emit("cursor_moved", buf_id=id(api._workspace.active_window.document), line=1, col=0)

        assert scheduled == []

    # ------------------------------------------------------------------
    # configure()
    # ------------------------------------------------------------------

    def test_configure_updates_config(self, monkeypatch):
        monkeypatch.setitem(_mod._config, "preview_mode", "float")
        monkeypatch.setitem(_mod._config, "preview_syntax", True)

        configure(preview_mode="cursor", preview_syntax=False)

        assert _mod._config["preview_mode"] == "cursor"
        assert _mod._config["preview_syntax"] is False

    def test_configure_after_load_closes_float(self, monkeypatch):
        api = _make_api()
        panel = _ReferencesSidebarPanel(api)
        closed: list = []
        fake_handle = SimpleNamespace(is_open=True, close=lambda: closed.append(True))
        panel._preview_float = fake_handle
        monkeypatch.setattr(_mod, "_panel", panel)

        configure(preview_mode="cursor")

        assert closed == [True]


class TestReferencesPanelHelpers:
    def test_build_reference_nodes_formats_locations(self):
        nodes = _build_reference_nodes(
            [
                {"path": "/tmp/other.py", "line": 8, "col": 1},
            ]
        )

        assert nodes[0].label == "other.py:9:2"

    def test_build_preview_content_highlights_target_line(self, tmp_path):
        src = tmp_path / "foo.py"
        src.write_text("line0\nline1\nline2\nline3\nline4\n")

        content = _build_preview_content(str(src), 2, 0)

        plain = [c for c in content if isinstance(c, str)]
        styled = [c for c in content if isinstance(c, list)]
        assert len(styled) == 1
        assert "line2" in styled[0][0][0]
        assert all("line2" not in p for p in plain)

    def test_build_preview_content_with_syntax(self, tmp_path):
        src = tmp_path / "foo.py"
        src.write_text("x = 1\ny = 2\nz = 3\n")
        from peovim.syntax.themes import get_theme

        theme = get_theme("catppuccin")

        content = _build_preview_content(str(src), 1, 0, theme=theme)

        # Target line should be a list of (text, Style) segments
        assert isinstance(content[1], list)
        # All segments on the target line should have the highlight bg
        from peovim.plugins.references_panel import _HIGHLIGHT_BG

        for _text, style in content[1]:
            assert style.bg == _HIGHLIGHT_BG

    def test_build_preview_content_returns_error_for_missing_file(self):
        content = _build_preview_content("/nonexistent/path/file.py", 0, 0)

        assert content == ["(unable to read file)"]
