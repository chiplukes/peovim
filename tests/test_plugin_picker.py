"""
tests.test_plugin_picker — Tests for peovim.plugins.picker (Phase 6h)

Covers: setup keybindings and commands, find_files, find_buffers,
live grep, preview helper, error isolation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api(
    files: list[Path] | None = None,
    buffers: list[MagicMock] | None = None,
    grep_hits: list[tuple] | None = None,
    recent_files: list[Path] | None = None,
    diagnostics: list[dict] | None = None,
    commands: list[str] | None = None,
) -> MagicMock:
    api = MagicMock()
    api.find_root.return_value = Path("/fake/root")
    api.find_files.return_value = files or []
    api.list_buffers.return_value = buffers or []
    api.grep.return_value = grep_hits or []
    api.recent_files.return_value = recent_files or []
    api.list_diagnostics.return_value = diagnostics or []
    api.active_buffer.return_value = MagicMock()
    api.active_window.return_value = MagicMock()
    api.commands.list_commands.return_value = commands or ["write", "quit"]
    return api


def _make_buf(path: Path | None = None, buf_id: int = 1) -> MagicMock:
    buf = MagicMock()
    buf.buf_id = buf_id
    buf.path = path
    buf.line_count.return_value = 0
    buf.get_lines.return_value = []
    return buf


# ---------------------------------------------------------------------------
# setup()
# ---------------------------------------------------------------------------


class TestSetup:
    def test_registers_keybindings(self):
        from peovim.plugins.picker import setup

        api = _make_api()
        setup(api)
        keys = [c.args[0] for c in api.keymap.nmap.call_args_list]
        assert "<leader>ff" in keys
        assert "<leader>fb" in keys
        assert "<leader>fg" in keys
        assert "<leader>sf" in keys
        assert "<leader>sr" in keys
        assert "<leader>sg" in keys
        assert "<leader>sw" in keys
        assert "<leader>s/" in keys
        assert "<leader>sb" in keys
        assert "<leader>sd" in keys
        assert "<leader>sp" in keys

    def test_registers_search_group(self):
        from peovim.plugins.picker import setup

        api = _make_api()
        setup(api)
        api.keymap.ngroup.assert_called_once_with("<leader>s", "Search")

    def test_registers_find_command(self):
        from peovim.plugins.picker import setup

        api = _make_api()
        setup(api)
        cmd_names = [c.args[0] for c in api.commands.register.call_args_list]
        assert "find" in cmd_names

    def test_registers_grep_command(self):
        from peovim.plugins.picker import setup

        api = _make_api()
        setup(api)
        cmd_names = [c.args[0] for c in api.commands.register.call_args_list]
        assert "grep" in cmd_names


# ---------------------------------------------------------------------------
# _find_files
# ---------------------------------------------------------------------------


class TestFindFiles:
    def test_opens_picker_with_file_list(self):
        from peovim.plugins.picker import _find_files

        root = Path("/fake/root")
        files = [root / "a.py", root / "b.py"]
        api = _make_api(files=files)
        _find_files(api)
        api.ui.open_picker.assert_called_once()
        title = api.ui.open_picker.call_args.args[0]
        assert "File" in title

    def test_file_items_are_relative_to_root(self):
        from peovim.plugins.picker import _find_files

        root = Path("/fake/root")
        files = [root / "src" / "main.py"]
        api = _make_api(files=files)
        _find_files(api)
        items = api.ui.open_picker.call_args.args[1]
        assert str(items[0]).endswith("src\\main.py") or str(items[0]) == "src/main.py"

    def test_empty_file_list(self):
        from peovim.plugins.picker import _find_files

        api = _make_api(files=[])
        _find_files(api)
        items = api.ui.open_picker.call_args.args[1]
        assert items == []

    def test_find_files_exception_is_swallowed(self):
        from peovim.plugins.picker import _find_files

        api = _make_api()
        api.find_files.side_effect = RuntimeError("no root")
        _find_files(api)  # must not raise
        api.ui.open_picker.assert_called_once()

    def test_picker_has_preview_callback(self):
        from peovim.plugins.picker import _find_files

        root = Path("/fake/root")
        api = _make_api(files=[root / "x.py"])
        _find_files(api)
        kwargs = api.ui.open_picker.call_args.kwargs
        assert "preview" in kwargs and callable(kwargs["preview"])

    def test_confirm_opens_selected_file_via_api(self):
        from peovim.plugins.picker import _find_files

        root = Path("/fake/root")
        api = _make_api(files=[root / "x.py"])
        _find_files(api)
        item = api.ui.open_picker.call_args.args[1][0]
        on_confirm = api.ui.open_picker.call_args.kwargs["on_confirm"]

        on_confirm(item)

        api.open_buffer.assert_called_once_with(root / "x.py")


class TestRecentFiles:
    def test_opens_picker_with_recent_files(self):
        from peovim.plugins.picker import _find_recent_files

        files = [Path("/fake/root/a.py"), Path("/fake/root/b.py")]
        api = _make_api(recent_files=files)

        _find_recent_files(api)

        api.ui.open_picker.assert_called_once()
        items = api.ui.open_picker.call_args.args[1]
        assert len(items) == 2


# ---------------------------------------------------------------------------
# _find_buffers
# ---------------------------------------------------------------------------


class TestFindBuffers:
    def test_opens_picker_with_buffer_paths(self):
        from peovim.plugins.picker import _find_buffers

        bufs = [_make_buf(Path("/fake/root/a.py"), 1), _make_buf(Path("/fake/root/b.py"), 2)]
        api = _make_api(buffers=bufs)
        _find_buffers(api)
        api.ui.open_picker.assert_called_once()
        items = api.ui.open_picker.call_args.args[1]
        assert len(items) == 2

    def test_buffer_without_path_gets_label(self):
        from peovim.plugins.picker import _find_buffers

        bufs = [_make_buf(None, 42)]
        api = _make_api(buffers=bufs)
        _find_buffers(api)
        items = api.ui.open_picker.call_args.args[1]
        assert "42" in str(items[0]) or str(items[0]).startswith("<buffer")

    def test_empty_buffer_list(self):
        from peovim.plugins.picker import _find_buffers

        api = _make_api(buffers=[])
        _find_buffers(api)
        items = api.ui.open_picker.call_args.args[1]
        assert items == []

    def test_confirm_opens_buffer_path(self):
        from peovim.plugins.picker import _find_buffers

        bufs = [_make_buf(Path("/fake/root/a.py"), 1)]
        bufs[0].get_lines.return_value = ["alpha"]
        bufs[0].line_count.return_value = 1
        api = _make_api(buffers=bufs)

        _find_buffers(api)
        item = api.ui.open_picker.call_args.args[1][0]
        on_confirm = api.ui.open_picker.call_args.kwargs["on_confirm"]

        on_confirm(item)

        api.open_buffer.assert_called_once_with(Path("/fake/root/a.py"))


# ---------------------------------------------------------------------------
# _live_grep / _grep_with_args
# ---------------------------------------------------------------------------


class TestLiveGrep:
    def test_opens_picker(self):
        from peovim.plugins.picker import _live_grep

        api = _make_api()
        _live_grep(api)
        api.ui.open_picker.assert_called_once()

    def test_grep_with_query_populates_items(self):
        from peovim.plugins.picker import _grep_with_args

        hits = [(Path("/fake/root/a.py"), 3, "TODO: fix")]
        api = _make_api(grep_hits=hits)
        _grep_with_args(api, "TODO")
        items = api.ui.open_picker.call_args.args[1]
        assert len(items) == 1
        assert "TODO" in str(items[0])

    def test_grep_empty_query_returns_empty_items(self):
        from peovim.plugins.picker import _grep_with_args

        api = _make_api()
        _grep_with_args(api, "")
        items = api.ui.open_picker.call_args.args[1]
        assert callable(items)

    def test_grep_exception_is_swallowed(self):
        from peovim.plugins.picker import _grep_with_args

        api = _make_api()
        api.grep.side_effect = RuntimeError("grep broke")
        _grep_with_args(api, "pattern")  # must not raise

    def test_live_grep_uses_dynamic_source(self):
        from peovim.plugins.picker import _live_grep

        api = _make_api()

        _live_grep(api)

        source = api.ui.open_picker.call_args.args[1]
        assert callable(source)

    def test_grep_word_under_cursor_uses_current_word(self):
        from peovim.plugins.picker import _grep_word_under_cursor

        hits = [(Path("/fake/root/a.py"), 0, "foo bar")]
        api = _make_api(grep_hits=hits)
        api.active_window.return_value.cursor = (0, 2)
        api.active_buffer.return_value.line_count.return_value = 1
        api.active_buffer.return_value.get_line.return_value = "foo bar"

        _grep_word_under_cursor(api)

        items = api.ui.open_picker.call_args.args[1]
        assert len(items) == 1
        assert "foo bar" in str(items[0])

    def test_grep_word_reports_missing_word(self):
        from peovim.plugins.picker import _grep_word_under_cursor

        api = _make_api()
        api.active_window.return_value.cursor = (0, 0)
        api.active_buffer.return_value.line_count.return_value = 1
        api.active_buffer.return_value.get_line.return_value = "   "

        _grep_word_under_cursor(api)

        api.ui.open_picker.assert_not_called()
        api.ui.notify.assert_called_once()

    def test_grep_with_query_reports_no_matches(self):
        from peovim.plugins.picker import _grep_with_args

        api = _make_api(grep_hits=[])

        _grep_with_args(api, "TODO", label="TODO")

        api.ui.open_picker.assert_not_called()
        api.ui.notify.assert_called_once()

    def test_grep_preview_centers_selected_match(self, tmp_path):
        from peovim.plugins.picker import _grep_with_args

        preview_file = tmp_path / "sample.py"
        preview_file.write_text("\n".join(f"line{i}" for i in range(80)), encoding="utf-8")
        api = _make_api(grep_hits=[(preview_file, 40, "line40")])

        _grep_with_args(api, "line40")

        item = api.ui.open_picker.call_args.args[1][0]
        preview = api.ui.open_picker.call_args.kwargs["preview"](item)
        plain = [line if isinstance(line, str) else "".join(text for text, _style in line) for line in preview]

        assert any(line.startswith(">  41: line40") for line in plain)
        assert not plain[0].startswith("    1:")


class TestBufferLines:
    def test_search_buffer_lines_opens_picker(self):
        from peovim.plugins.picker import _search_buffer_lines

        api = _make_api()
        api.active_buffer.return_value.line_count.return_value = 2
        api.active_buffer.return_value.get_line.side_effect = ["alpha", "beta"]

        _search_buffer_lines(api)

        api.ui.open_picker.assert_called_once()
        items = api.ui.open_picker.call_args.args[1]
        assert len(items) == 2


class TestDiagnostics:
    def test_find_diagnostics_opens_picker(self):
        from peovim.plugins.picker import _find_diagnostics

        api = _make_api(
            diagnostics=[{"path": Path("/fake/root/a.py"), "line": 2, "col": 0, "severity": "E", "message": "broken"}]
        )

        _find_diagnostics(api)

        api.ui.open_picker.assert_called_once()
        items = api.ui.open_picker.call_args.args[1]
        assert len(items) == 1


class TestCommands:
    def test_find_commands_opens_picker(self):
        from peovim.plugins.picker import _find_commands

        api = _make_api(commands=["write", "quit"])

        _find_commands(api)

        api.ui.open_picker.assert_called_once()
        assert api.ui.open_picker.call_args.args[0] == "Commands (executes on Enter)"
        items = api.ui.open_picker.call_args.args[1]
        assert len(items) == 2

    def test_confirm_executes_selected_command(self):
        from peovim.plugins.picker import _find_commands

        api = _make_api(commands=["write"])

        _find_commands(api)
        item = api.ui.open_picker.call_args.args[1][0]
        on_confirm = api.ui.open_picker.call_args.kwargs["on_confirm"]

        on_confirm(item)

        api.commands.execute.assert_called_once_with("write")


# ---------------------------------------------------------------------------
# _preview_file
# ---------------------------------------------------------------------------


class TestPreviewFile:
    def test_returns_lines_for_existing_file(self, tmp_path):
        from peovim.plugins.picker import _preview_file

        f = tmp_path / "hello.py"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")
        lines = _preview_file(f)
        assert ["".join(text for text, _style in line) for line in lines] == ["line1", "line2", "line3"]

    def test_returns_empty_for_missing_file(self, tmp_path):
        from peovim.plugins.picker import _preview_file

        lines = _preview_file(tmp_path / "nonexistent.py")
        assert lines == []

    def test_returns_empty_for_none(self):
        from peovim.plugins.picker import _preview_file

        assert _preview_file(None) == []

    def test_limits_to_preview_lines(self, tmp_path):
        from peovim.plugins import picker as picker_mod
        from peovim.plugins.picker import _preview_file

        f = tmp_path / "big.py"
        f.write_text("\n".join(f"line{i}" for i in range(200)), encoding="utf-8")
        lines = _preview_file(f)
        assert len(lines) == picker_mod._PREVIEW_LINES

    def test_centered_preview_marks_selected_line(self, tmp_path):
        from peovim.plugins.picker import _preview_file

        f = tmp_path / "focus.py"
        f.write_text("\n".join(f"line{i}" for i in range(80)), encoding="utf-8")

        lines = _preview_file(f, center_line=40)

        plain = ["".join(text for text, _style in line) for line in lines]
        assert any(line.startswith(">  41: line40") for line in plain)
        assert not plain[0].startswith("    1:")

    def test_python_preview_returns_styled_segments(self, tmp_path):
        from peovim.plugins.picker import _preview_file

        f = tmp_path / "styled.py"
        f.write_text("def answer():\n    return 42\n", encoding="utf-8")

        lines = _preview_file(f)

        assert isinstance(lines[0], list)
        assert any(text == "def" and style.fg is not None for text, style in lines[0])
