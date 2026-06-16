from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock


class FakeBuffer:
    def __init__(self, lines: list[str], buf_id: int = 1) -> None:
        self.lines = list(lines)
        self.buf_id = buf_id

    def get_line(self, line: int) -> str:
        return self.lines[line]

    def replace(self, start_line: int, start_col: int, end_line: int, end_col: int, text: str) -> None:
        assert start_line == end_line
        self.lines[start_line] = text

    @contextmanager
    def batch(self):
        yield


def _make_api(lines: list[str]) -> tuple[MagicMock, FakeBuffer]:
    api = MagicMock()
    buf = FakeBuffer(lines)
    api.active_buffer.return_value = buf
    api.active_window.return_value = SimpleNamespace(cursor=(0, 0))
    api._editor_state = SimpleNamespace(message="")

    def _set_status(
        message: str, *, notify: bool = True, level: str = "info", title: str = "", timeout: float = 3.0
    ) -> None:
        api._editor_state.message = message
        if notify:
            api.ui.notify(message, level=level, title=title, timeout=timeout)

    api.set_status.side_effect = _set_status
    return api, buf


class TestSetup:
    def test_setup_registers_visual_mappings_and_commands(self):
        from peovim.plugins.align import setup

        api, _buf = _make_api(["a"])

        setup(api)

        visual_keys = [call.args[0] for call in api.keymap.vmap.call_args_list]
        command_names = [call.args[0] for call in api.commands.register.call_args_list]

        assert "ga" in visual_keys
        assert "gA" in visual_keys
        assert "AlignChar" in command_names
        assert "AlignRegex" in command_names


class TestPrompting:
    def test_prompt_align_char_opens_cmdline_and_remembers_selection(self):
        from peovim.plugins import align as align_mod

        api, buf = _make_api(["a = 1", "bbb = 2"])
        ctx = SimpleNamespace(visual_range=(0, 1), cursor=(1, 0))

        align_mod._prompt_align_char(ctx, api)

        api.open_cmdline.assert_called_once_with("AlignChar ")
        assert align_mod._pending_visual_range == (0, 1)
        assert align_mod._pending_buf_id == buf.buf_id
        align_mod.teardown()

    def test_prompt_align_regex_opens_cmdline(self):
        from peovim.plugins import align as align_mod

        api, _buf = _make_api(["x"])
        ctx = SimpleNamespace(visual_range=(0, 0), cursor=(0, 0))

        align_mod._prompt_align_regex(ctx, api)

        api.open_cmdline.assert_called_once_with("AlignRegex ")
        align_mod.teardown()


class TestCommands:
    def test_align_char_aligns_selected_lines(self):
        from peovim.plugins import align as align_mod

        api, buf = _make_api(["a = 1", "long_name = 2", "xx = 3"])
        align_mod._pending_visual_range = (0, 2)
        align_mod._pending_buf_id = buf.buf_id

        align_mod._cmd_align_char(api, "=")

        positions = [line.index("=") for line in buf.lines]
        assert positions == [positions[0]] * len(positions)

    def test_align_regex_aligns_match_start_columns(self):
        from peovim.plugins import align as align_mod

        api, buf = _make_api(["let x -> 1", "name_here -> 2"])
        align_mod._pending_visual_range = (0, 1)
        align_mod._pending_buf_id = buf.buf_id

        align_mod._cmd_align_regex(api, r"->")

        positions = [line.index("->") for line in buf.lines]
        assert positions == [positions[0], positions[0]]

    def test_align_regex_reports_invalid_pattern(self):
        from peovim.plugins import align as align_mod

        api, buf = _make_api(["a = 1"])
        align_mod._pending_visual_range = (0, 0)
        align_mod._pending_buf_id = buf.buf_id

        align_mod._cmd_align_regex(api, "[")

        api.ui.notify.assert_called_once()
        assert align_mod._pending_visual_range is None
        assert buf.lines == ["a = 1"]

    def test_align_char_uses_current_line_without_pending_selection(self):
        from peovim.plugins import align as align_mod

        api, buf = _make_api(["x=1", "longer = 2"])
        api.active_window.return_value = SimpleNamespace(cursor=(0, 0))
        align_mod.teardown()

        align_mod._cmd_align_char(api, "=")

        assert buf.lines == ["x=1", "longer = 2"]
        api.ui.notify.assert_not_called()
