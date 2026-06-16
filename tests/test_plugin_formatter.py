"""tests.test_plugin_formatter — Phase 6 tail"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_api(filetype: str = "python", content: str = "x = 1\n") -> tuple:
    api = MagicMock()
    buf = MagicMock()
    buf.filetype = filetype
    buf.path = Path("/fake/file.py")
    buf.buf_id = 1
    buf.line_count.return_value = content.count("\n") + 1
    buf.get_line.side_effect = lambda i: content.split("\n")[i] if i < len(content.split("\n")) else ""
    buf.get_text.return_value = content
    api.active_buffer.return_value = buf
    api.list_buffers.return_value = [buf]
    api.options.get.return_value = None
    return api, buf


class TestFormatBuffer:
    def test_formats_and_replaces_content(self):
        from peovim.plugins.formatter import _format_buffer

        api, buf = _make_api(filetype="python", content="x=1\n")
        formatted = "x = 1\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=formatted)
            result = _format_buffer(api, buf)
        assert result is True
        buf.replace.assert_called_once()

    def test_no_replace_when_unchanged(self):
        from peovim.plugins.formatter import _format_buffer

        content = "x = 1\n"
        api, buf = _make_api(content=content)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=content)
            result = _format_buffer(api, buf)
        assert result is True
        buf.replace.assert_not_called()

    def test_returns_false_on_formatter_error(self):
        from peovim.plugins.formatter import _format_buffer

        api, buf = _make_api()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            result = _format_buffer(api, buf)
        assert result is False

    def test_returns_false_when_no_formatter(self):
        from peovim.plugins.formatter import _format_buffer

        api, buf = _make_api(filetype="cobol")
        result = _format_buffer(api, buf)
        assert result is False

    def test_handles_missing_formatter_binary(self):
        from peovim.plugins.formatter import _format_buffer

        api, buf = _make_api()
        with patch("subprocess.run", side_effect=FileNotFoundError("ruff not found")):
            result = _format_buffer(api, buf)
        assert result is False

    def test_uses_utf8_for_unicode_buffer_content(self):
        from peovim.plugins.formatter import _format_buffer

        content = "# ── Options ───────────────────────────────────────────────────────────\n"
        api, buf = _make_api(content=content)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=content)

            result = _format_buffer(api, buf)

        assert result is True
        kwargs = mock_run.call_args.kwargs
        assert kwargs["text"] is True
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["input"] == content

    def test_user_formatter_overrides_default(self):
        from peovim.plugins.formatter import _get_formatter_cmd

        api, buf = _make_api(filetype="python")
        api.options.get.side_effect = lambda name: {"python": ["black", "-"]} if name == "formatters" else None
        cmd = _get_formatter_cmd(api, "python")
        assert cmd == ["black", "-"]


class TestGetFormatterCmd:
    def test_returns_default_for_python(self):
        from peovim.plugins.formatter import _get_formatter_cmd

        api = MagicMock()
        api.options.get.return_value = None
        cmd = _get_formatter_cmd(api, "python")
        assert cmd is not None
        assert "ruff" in cmd[0]

    def test_returns_none_for_unknown(self):
        from peovim.plugins.formatter import _get_formatter_cmd

        api = MagicMock()
        api.options.get.return_value = None
        assert _get_formatter_cmd(api, "cobol") is None


class TestSetup:
    def test_registers_format_command(self):
        from peovim.plugins.formatter import setup

        api = MagicMock()
        setup(api)
        cmd_names = [c.args[0] for c in api.commands.register.call_args_list]
        assert "format" in cmd_names

    def test_subscribes_to_pre_save(self):
        from peovim.plugins.formatter import setup

        api = MagicMock()
        setup(api)
        events = [c.args[0] for c in api.events.on.call_args_list]
        assert "buffer_pre_save" in events

    def test_registers_keymap(self):
        from peovim.plugins.formatter import setup

        api = MagicMock()
        setup(api)
        assert api.keymap.nmap.called
