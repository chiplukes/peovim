"""tests.test_plugin_commentary — Phase 6 tail"""

from __future__ import annotations

from unittest.mock import MagicMock

from peovim.plugins.commentary import _comment_str, toggle_line_comment


def _make_buf(lines: list[str], filetype: str = "python") -> MagicMock:
    buf = MagicMock()
    buf.filetype = filetype
    buf.line_count.return_value = len(lines)
    _lines = list(lines)
    buf.get_line.side_effect = lambda i: _lines[i]

    def _replace(sl, sc, el, ec, text):
        _lines[sl] = text

    buf.replace.side_effect = _replace
    return buf, _lines


class TestCommentStr:
    def test_python(self):
        buf = MagicMock()
        buf.filetype = "python"
        assert _comment_str(buf) == "#"

    def test_javascript(self):
        buf = MagicMock()
        buf.filetype = "javascript"
        assert _comment_str(buf) == "//"

    def test_lua(self):
        buf = MagicMock()
        buf.filetype = "lua"
        assert _comment_str(buf) == "--"

    def test_unknown_defaults_to_hash(self):
        buf = MagicMock()
        buf.filetype = "unknownlang"
        assert _comment_str(buf) == "#"


class TestToggleLineComment:
    def test_adds_comment_to_plain_line(self):
        buf, lines = _make_buf(["x = 1"])
        toggle_line_comment(buf, 0)
        assert lines[0] == "# x = 1"

    def test_removes_comment_marker(self):
        buf, lines = _make_buf(["# x = 1"])
        toggle_line_comment(buf, 0)
        assert lines[0] == "x = 1"

    def test_removes_bare_comment_marker(self):
        buf, lines = _make_buf(["#x = 1"])
        toggle_line_comment(buf, 0)
        assert lines[0] == "x = 1"

    def test_preserves_indentation_when_adding(self):
        buf, lines = _make_buf(["    x = 1"])
        toggle_line_comment(buf, 0)
        assert lines[0] == "    # x = 1"

    def test_preserves_indentation_when_removing(self):
        buf, lines = _make_buf(["    # x = 1"])
        toggle_line_comment(buf, 0)
        assert lines[0] == "    x = 1"

    def test_javascript_adds_slash_slash(self):
        buf, lines = _make_buf(["const x = 1"], filetype="javascript")
        toggle_line_comment(buf, 0)
        assert lines[0] == "// const x = 1"

    def test_javascript_removes_slash_slash(self):
        buf, lines = _make_buf(["// const x = 1"], filetype="javascript")
        toggle_line_comment(buf, 0)
        assert lines[0] == "const x = 1"

    def test_empty_line_gets_commented(self):
        buf, lines = _make_buf([""])
        toggle_line_comment(buf, 0)
        assert lines[0].startswith("#")


class TestSetup:
    def test_registers_gcc(self):
        from peovim.plugins.commentary import setup

        api = MagicMock()
        setup(api)
        keys = [c.args[0] for c in api.keymap.nmap.call_args_list]
        assert "gcc" in keys

    def test_registers_visual_gc(self):
        from peovim.plugins.commentary import setup

        api = MagicMock()
        setup(api)
        assert api.keymap.vmap.called
        keys = [c.args[0] for c in api.keymap.vmap.call_args_list]
        assert "gc" in keys
