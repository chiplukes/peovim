"""tests.test_plugin_guess_indent — Phase 6 tail"""

from __future__ import annotations

from unittest.mock import MagicMock

from peovim.plugins.guess_indent import _guess_size, detect_indent


def _make_buf(lines: list[str]) -> MagicMock:
    buf = MagicMock()
    buf.line_count.return_value = len(lines)
    buf.get_line.side_effect = lambda i: lines[i]
    return buf


class TestDetectIndent:
    def test_spaces_2(self):
        buf = _make_buf(["def f():", "  x = 1", "  y = 2"])
        assert detect_indent(buf) == (True, 2)

    def test_spaces_4(self):
        buf = _make_buf(["def f():", "    x = 1", "    y = 2"])
        assert detect_indent(buf) == (True, 4)

    def test_tabs_detected(self):
        buf = _make_buf(["def f():", "\tx = 1", "\ty = 2"])
        r = detect_indent(buf)
        assert r is not None
        assert r[0] is False  # use_spaces=False

    def test_empty_buffer_returns_none(self):
        buf = _make_buf([])
        assert detect_indent(buf) is None

    def test_no_indented_lines_returns_none(self):
        buf = _make_buf(["x = 1", "y = 2", "z = 3"])
        assert detect_indent(buf) is None

    def test_mixed_prefers_majority(self):
        # 3 space-indented, 1 tab-indented → spaces win
        buf = _make_buf(["  a", "  b", "  c", "\td"])
        r = detect_indent(buf)
        assert r is not None
        assert r[0] is True

    def test_spaces_8_only_lines_defaults_to_4(self):
        # Only 8-space lines → ambiguous (likely 2 levels of 4-space), prefer 4
        buf = _make_buf(["        x"] * 5)
        r = detect_indent(buf)
        assert r == (True, 4)

    def test_spaces_8_with_4_space_lines(self):
        # Mix of 4-space and 8-space lines → base unit is clearly 4
        buf = _make_buf(["    a"] * 3 + ["        b"] * 3)
        r = detect_indent(buf)
        assert r == (True, 4)

    def test_block_comment_noise_does_not_force_two_space_indent(self):
        buf = _make_buf(
            [
                "/*",
                " * header",
                " * more header",
                " */",
                "module demo",
                "    always @(posedge clk)",
                "        begin",
                "        end",
                "    /*",
                "     * nested comment",
                "     */",
                "    assign x = y;",
            ]
        )
        assert detect_indent(buf) == (True, 4)

    def test_block_comment_noise_does_not_override_tab_indentation(self):
        buf = _make_buf(
            [
                "/*",
                " * header",
                " */",
                "\tif (ready) begin",
                "\t\tvalue <= next_value;",
                "\tend",
            ]
        )
        r = detect_indent(buf)
        assert r is not None
        assert r[0] is False


class TestGuessSize:
    def test_size_2(self):
        assert _guess_size({2: 10, 4: 3}) == 2

    def test_size_4(self):
        assert _guess_size({4: 10, 8: 2}) == 4

    def test_empty(self):
        assert _guess_size({}) == 4


class TestSetup:
    def test_subscribes_to_event(self):
        from peovim.plugins.guess_indent import setup

        api = MagicMock()
        api.list_buffers.return_value = []
        setup(api)
        events = [c.args[0] for c in api.events.on.call_args_list]
        assert "buffer_opened" in events

    def test_applies_to_existing_buffers(self):
        from peovim.plugins.guess_indent import setup

        api = MagicMock()
        buf = _make_buf(["    x = 1"])
        buf.buf_id = 1
        api.list_buffers.return_value = [buf]
        setup(api)
        api.active_window().set_option.assert_called()
