"""tests.test_plugin_surround — Phase 6 tail"""

from __future__ import annotations

from unittest.mock import MagicMock

from peovim.plugins.surround import change_surround, delete_surround, surround_word


def _make_buf(line: str) -> tuple:
    lines = [line]
    buf = MagicMock()
    buf.get_line.side_effect = lambda i: lines[i]

    def _replace(sl, sc, el, ec, text):
        lines[sl] = text

    buf.replace.side_effect = _replace
    return buf, lines


class TestSurroundWord:
    def test_surround_word_with_parens(self):
        buf, lines = _make_buf("hello world")
        surround_word(buf, 0, 0, "(", ")")
        assert lines[0] == "(hello) world"

    def test_surround_word_with_spaces(self):
        buf, lines = _make_buf("hello")
        surround_word(buf, 0, 0, "( ", " )")
        assert lines[0] == "( hello )"

    def test_surround_word_in_middle(self):
        buf, lines = _make_buf("foo bar baz")
        surround_word(buf, 0, 4, "[", "]")
        assert lines[0] == "foo [bar] baz"

    def test_surround_with_quotes(self):
        buf, lines = _make_buf("hello")
        surround_word(buf, 0, 0, '"', '"')
        assert lines[0] == '"hello"'

    def test_surround_single_char(self):
        buf, lines = _make_buf("x")
        surround_word(buf, 0, 0, "(", ")")
        assert lines[0] == "(x)"


class TestChangeSurround:
    def test_change_parens_to_brackets(self):
        buf, lines = _make_buf("(hello)")
        result = change_surround(buf, 0, "(", ")", "[", "]")
        assert result is True
        assert lines[0] == "[hello]"

    def test_change_quotes(self):
        buf, lines = _make_buf('"hello"')
        result = change_surround(buf, 0, '"', '"', "'", "'")
        assert result is True
        assert lines[0] == "'hello'"

    def test_returns_false_if_not_found(self):
        buf, lines = _make_buf("hello")
        result = change_surround(buf, 0, "(", ")", "[", "]")
        assert result is False

    def test_change_spaced_pair(self):
        buf, lines = _make_buf("( hello )")
        result = change_surround(buf, 0, "( ", " )", "[", "]")
        assert result is True
        assert lines[0] == "[hello]"


class TestDeleteSurround:
    def test_delete_parens(self):
        buf, lines = _make_buf("(hello)")
        result = delete_surround(buf, 0, "(", ")")
        assert result is True
        assert lines[0] == "hello"

    def test_delete_brackets(self):
        buf, lines = _make_buf("[foo, bar]")
        result = delete_surround(buf, 0, "[", "]")
        assert result is True
        assert lines[0] == "foo, bar"

    def test_delete_quotes(self):
        buf, lines = _make_buf('"text"')
        result = delete_surround(buf, 0, '"', '"')
        assert result is True
        assert lines[0] == "text"

    def test_returns_false_if_not_found(self):
        buf, lines = _make_buf("hello")
        result = delete_surround(buf, 0, "(", ")")
        assert result is False

    def test_preserves_context_outside_pair(self):
        buf, lines = _make_buf("x = (hello) + 1")
        result = delete_surround(buf, 0, "(", ")")
        assert result is True
        assert lines[0] == "x = hello + 1"


class TestSetup:
    def test_registers_ysiw_bindings(self):
        from peovim.plugins.surround import setup

        api = MagicMock()
        setup(api)
        keys = [c.args[0] for c in api.keymap.nmap.call_args_list]
        assert any(k.startswith("ysiw") for k in keys)

    def test_registers_ds_bindings(self):
        from peovim.plugins.surround import setup

        api = MagicMock()
        setup(api)
        keys = [c.args[0] for c in api.keymap.nmap.call_args_list]
        assert any(k.startswith("ds") for k in keys)

    def test_registers_cs_bindings(self):
        from peovim.plugins.surround import setup

        api = MagicMock()
        setup(api)
        keys = [c.args[0] for c in api.keymap.nmap.call_args_list]
        assert any(k.startswith("cs") for k in keys)
