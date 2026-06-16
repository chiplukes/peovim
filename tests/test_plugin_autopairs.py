"""tests.test_plugin_autopairs — Phase 6 tail"""

from __future__ import annotations

from unittest.mock import MagicMock

from peovim.plugins.autopairs import _backspace, _insert_pair, _skip_or_insert


def _make_api(line_text: str = "", col: int = 0) -> tuple:
    api = MagicMock()
    win = MagicMock()
    buf = MagicMock()
    win.cursor = (0, col)
    buf.get_line.return_value = line_text
    buf.line_count.return_value = 1
    api.active_window.return_value = win
    api.active_buffer.return_value = buf
    return api, win, buf


class TestInsertPair:
    def test_inserts_both_chars(self):
        api, win, buf = _make_api("", 0)
        _insert_pair(api, "(", ")")
        buf.insert.assert_called_once_with(0, 0, "()")

    def test_cursor_placed_between_pair(self):
        api, win, buf = _make_api("", 0)
        _insert_pair(api, "(", ")")
        win.set_cursor.assert_called_with(0, 1)

    def test_insert_in_middle_of_line(self):
        api, win, buf = _make_api("ab", 1)
        _insert_pair(api, "[", "]")
        buf.insert.assert_called_once_with(0, 1, "[]")
        win.set_cursor.assert_called_with(0, 2)

    def test_quote_pair(self):
        api, win, buf = _make_api("", 0)
        _insert_pair(api, '"', '"')
        buf.insert.assert_called_once_with(0, 0, '""')


class TestSkipOrInsert:
    def test_skips_over_existing_closer(self):
        api, win, buf = _make_api("()", 1)
        _skip_or_insert(api, ")")
        win.set_cursor.assert_called_with(0, 2)
        buf.insert.assert_not_called()

    def test_inserts_if_no_closer_ahead(self):
        api, win, buf = _make_api("(x", 1)
        _skip_or_insert(api, ")")
        buf.insert.assert_called_once_with(0, 1, ")")

    def test_skips_bracket_closer(self):
        api, win, buf = _make_api("[]", 1)
        _skip_or_insert(api, "]")
        win.set_cursor.assert_called_with(0, 2)


class TestBackspace:
    def test_deletes_both_inside_empty_pair(self):
        api, win, buf = _make_api("()", 1)
        _backspace(api)
        buf.delete.assert_called_once_with(0, 0, 0, 2)
        win.set_cursor.assert_called_with(0, 0)

    def test_normal_backspace_outside_pair(self):
        api, win, buf = _make_api("abc", 2)
        _backspace(api)
        buf.delete.assert_called_once_with(0, 1, 0, 2)

    def test_no_delete_at_col_zero(self):
        api, win, buf = _make_api("x", 0)
        _backspace(api)
        buf.delete.assert_not_called()

    def test_does_not_delete_pair_with_content_inside(self):
        api, win, buf = _make_api("(x)", 2)
        _backspace(api)
        # 'x' is at col 1, ')' at col 2 — not an empty pair
        buf.delete.assert_called_once_with(0, 1, 0, 2)


class TestSetup:
    def test_registers_insert_bindings(self):
        from peovim.plugins.autopairs import setup

        api = MagicMock()
        setup(api)
        keys = [c.args[0] for c in api.keymap.imap.call_args_list]
        assert "(" in keys
        assert "[" in keys
        assert "{" in keys
        assert '"' in keys

    def test_registers_backspace(self):
        from peovim.plugins.autopairs import setup

        api = MagicMock()
        setup(api)
        keys = [c.args[0] for c in api.keymap.imap.call_args_list]
        assert "<BS>" in keys
