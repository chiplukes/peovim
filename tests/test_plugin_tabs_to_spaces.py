"""tests.test_plugin_tabs_to_spaces - tests for peovim.plugins.tabs_to_spaces"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


def _make_api(content: str = "\ta\n", *, tabstop: int = 4) -> tuple[MagicMock, MagicMock, MagicMock]:
    api = MagicMock()
    buf = MagicMock()
    win = MagicMock()

    lines = content.split("\n")
    buf.buf_id = 1
    buf.path = Path("/fake/file.py")
    buf.line_count.return_value = len(lines)
    buf.get_line.side_effect = lambda i: lines[i]
    buf.get_text.return_value = content

    win.cursor = (0, 1)
    win.get_option.side_effect = lambda name: tabstop if name == "tabstop" else None

    api.active_buffer.return_value = buf
    api.active_window.return_value = win
    api.list_buffers.return_value = [buf]
    api.options.get.side_effect = lambda name: tabstop if name == "tabstop" else None
    return api, buf, win


class TestNormalizeBuffer:
    def test_expands_tabs_using_tabstop(self):
        from peovim.plugins.tabs_to_spaces import _normalize_buffer

        api, buf, _win = _make_api("a\tb\n", tabstop=4)

        changed = _normalize_buffer(api, buf)

        assert changed is True
        buf.replace.assert_called_once_with(0, 0, 1, 0, "a   b\n")

    def test_no_replace_when_no_tabs_present(self):
        from peovim.plugins.tabs_to_spaces import _normalize_buffer

        api, buf, _win = _make_api("alpha\n", tabstop=4)

        changed = _normalize_buffer(api, buf)

        assert changed is False
        buf.replace.assert_not_called()

    def test_preserves_active_cursor_display_column(self):
        from peovim.plugins.tabs_to_spaces import _normalize_buffer

        api, buf, win = _make_api("\tabc\n", tabstop=4)

        _normalize_buffer(api, buf)

        win.set_cursor.assert_called_once_with(0, 4)


class TestSetup:
    def test_subscribes_to_open_and_pre_save(self):
        from peovim.plugins.tabs_to_spaces import setup

        api, _buf, _win = _make_api("alpha\n")

        setup(api)

        events = [call.args[0] for call in api.events.on.call_args_list]
        assert "buffer_opened" in events
        assert "buffer_pre_save" in events

    def test_normalizes_existing_buffers_on_setup(self):
        from peovim.plugins.tabs_to_spaces import setup

        api, buf, _win = _make_api("\talpha\n")

        setup(api)

        buf.replace.assert_called_once()


class TestEventHandlers:
    def test_open_handler_defers_normalization(self):
        from peovim.plugins.tabs_to_spaces import _on_buffer_opened

        api, buf, _win = _make_api("\talpha\n")

        _on_buffer_opened(api, buf_id=1)

        api.defer.assert_called_once()
        callback = api.defer.call_args.args[0]
        callback()
        buf.replace.assert_called_once()
