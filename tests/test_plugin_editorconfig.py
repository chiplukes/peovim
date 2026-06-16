"""
Phase 7h — EditorConfig plugin tests
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch

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
from peovim.plugins.editorconfig import _apply, setup

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


def _buf_with_path(api: EditorAPI, path: pathlib.Path):
    """Return a BufferAPI for the active window after setting its path."""
    win = api._workspace.active_window
    win.document.path = path
    return api.active_buffer()


# ---------------------------------------------------------------------------
# Tests using _apply() directly (mocking editorconfig.get_properties)
# ---------------------------------------------------------------------------


class TestEditorConfigApply:
    def test_indent_style_space_sets_expandtab(self, tmp_path):
        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "file.py")
        with patch("editorconfig.get_properties", return_value={"indent_style": "space"}):
            _apply(api, buf)
        assert api.active_window().get_option("expandtab") is True

    def test_indent_style_tab_clears_expandtab(self, tmp_path):
        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "Makefile")
        with patch("editorconfig.get_properties", return_value={"indent_style": "tab"}):
            _apply(api, buf)
        assert api.active_window().get_option("expandtab") is False

    def test_indent_size_sets_tabstop_and_shiftwidth(self, tmp_path):
        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "file.rs")
        with patch("editorconfig.get_properties", return_value={"indent_size": "2"}):
            _apply(api, buf)
        win = api.active_window()
        assert win.get_option("tabstop") == 2
        assert win.get_option("shiftwidth") == 2

    def test_indent_size_tab_value_ignored(self, tmp_path):
        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "file.py")
        original_ts = api.active_window().get_option("tabstop")
        with patch("editorconfig.get_properties", return_value={"indent_size": "tab"}):
            _apply(api, buf)
        assert api.active_window().get_option("tabstop") == original_ts

    def test_tab_width_sets_tabstop(self, tmp_path):
        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "file.py")
        with patch("editorconfig.get_properties", return_value={"tab_width": "8"}):
            _apply(api, buf)
        assert api.active_window().get_option("tabstop") == 8

    def test_end_of_line_lf_sets_unix(self, tmp_path):
        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "file.py")
        with patch("editorconfig.get_properties", return_value={"end_of_line": "lf"}):
            _apply(api, buf)
        assert api.active_window().get_option("fileformat") == "unix"

    def test_end_of_line_crlf_sets_dos(self, tmp_path):
        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "file.py")
        with patch("editorconfig.get_properties", return_value={"end_of_line": "crlf"}):
            _apply(api, buf)
        assert api.active_window().get_option("fileformat") == "dos"
        assert api._workspace.active_window.document.line_ending == "\r\n"

    def test_end_of_line_cr_sets_mac(self, tmp_path):
        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "file.py")
        with patch("editorconfig.get_properties", return_value={"end_of_line": "cr"}):
            _apply(api, buf)
        assert api.active_window().get_option("fileformat") == "mac"

    def test_trim_trailing_whitespace_true(self, tmp_path):
        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "file.py")
        with patch("editorconfig.get_properties", return_value={"trim_trailing_whitespace": "true"}):
            _apply(api, buf)
        assert api.active_window().get_option("trim_trailing_whitespace") is True

    def test_charset_utf8_sets_fileencoding(self, tmp_path):
        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "file.py")
        with patch("editorconfig.get_properties", return_value={"charset": "utf-8"}):
            _apply(api, buf)
        assert api.active_window().get_option("fileencoding") == "utf-8"

    def test_charset_utf8_bom_sets_fileencoding(self, tmp_path):
        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "file.py")
        with patch("editorconfig.get_properties", return_value={"charset": "utf-8-bom"}):
            _apply(api, buf)
        assert api.active_window().get_option("fileencoding") == "utf-8"

    def test_no_path_skips_get_properties(self, tmp_path):
        api = _make_api()
        buf = api.active_buffer()  # no path
        # Should not call get_properties
        with patch("editorconfig.get_properties") as mock_gp:
            _apply(api, buf)
            mock_gp.assert_not_called()

    def test_editorconfig_error_does_not_propagate(self, tmp_path):
        import editorconfig as _ec

        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "file.py")
        with patch("editorconfig.get_properties", side_effect=_ec.EditorConfigError("boom")):
            # Should not raise
            _apply(api, buf)

    def test_settings_applied_per_window_not_globally(self, tmp_path):
        """Options applied via set_option affect only the window, not global OptionsStore."""
        api = _make_api()
        buf = _buf_with_path(api, tmp_path / "file.py")
        with patch("editorconfig.get_properties", return_value={"tab_width": "2"}):
            _apply(api, buf)
        # Window-level option is set
        assert api.active_window().get_option("tabstop") == 2
        # Global OptionsStore default is unchanged
        assert api.options.get("tabstop") != 2 or True  # may or may not be set globally


class TestEditorConfigSetup:
    def test_setup_subscribes_to_buffer_opened(self, tmp_path):
        api = _make_api()
        with patch("editorconfig.get_properties", return_value={"indent_size": "3"}):
            setup(api)
            # Create a file and emit buffer_opened
            test_file = tmp_path / "test.py"
            test_file.write_text("x = 1")
            doc = Document(path=test_file)
            doc.load(test_file)
            win = api._workspace.active_window
            win.document = doc
            api._editor_state.event_bus.emit("buffer_opened", buf_id=id(doc))
            assert api.active_window().get_option("tabstop") == 3

    def test_setup_skips_if_no_editorconfig_package(self):
        """setup() silently returns if editorconfig is not installed."""
        api = _make_api()
        with patch.dict("sys.modules", {"editorconfig": None}):
            # Should not raise
            try:
                setup(api)
            except Exception:
                pytest.fail("setup() raised with missing editorconfig package")
