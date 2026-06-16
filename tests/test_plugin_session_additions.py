"""Tests for peovim.plugins.session_additions."""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_api(lines: list[str] | None = None, buf_id: int = 1):
    current_lines = list(lines or [])
    api = MagicMock()
    buf = MagicMock()
    buf.buf_id = buf_id
    buf.path = None
    buf.line_count.side_effect = lambda: len(current_lines)
    buf.get_line.side_effect = lambda i: current_lines[i] if 0 <= i < len(current_lines) else ""
    api.active_buffer.return_value = buf
    api.list_buffers.return_value = [buf]
    api.options.get.return_value = None
    return api, buf, current_lines


class TestSetup:
    def test_setup_defines_options_and_registers_sign(self):
        from peovim.plugins.session_additions import setup

        api, _buf, _lines = _make_api(["one"])
        setup(api)

        option_names = [call.args[0] for call in api.options.define.call_args_list]
        assert "session_additions_enabled" in option_names
        assert "session_additions_sign_char" in option_names
        assert "session_additions_sign_color" in option_names
        assert api.register_sign_type.call_args.args[0] == "session_additions.add"

    def test_setup_subscribes_to_buffer_events(self):
        from peovim.plugins.session_additions import setup

        api, _buf, _lines = _make_api(["one"])
        setup(api)

        events = [call.args[0] for call in api.events.on.call_args_list]
        assert "buffer_opened" in events
        assert "buffer_changed" in events

    def test_setup_scans_existing_buffers(self):
        from peovim.plugins.session_additions import setup

        api, buf, _lines = _make_api(["one"])
        setup(api)
        buf.clear_namespace.assert_called_with("session_additions")


class TestHelpers:
    def test_added_line_numbers_marks_inserted_lines(self):
        from peovim.plugins.session_additions import _added_line_numbers

        assert _added_line_numbers(["one", "three"], ["one", "two", "three"]) == [1]

    def test_added_line_numbers_marks_split_lines_as_added(self):
        from peovim.plugins.session_additions import _added_line_numbers

        added = _added_line_numbers(["alpha beta"], ["alpha", "beta"])
        assert added == [0, 1]

    def test_sign_color_parses_hex(self):
        from peovim.plugins.session_additions import _sign_color

        api, _buf, _lines = _make_api(["one"])
        api.options.get.side_effect = lambda name: "#50c850" if name == "session_additions_sign_color" else None
        assert _sign_color(api) == (80, 200, 80)


class TestUpdateSigns:
    def test_update_signs_marks_inserted_lines_since_open(self):
        from peovim.plugins.session_additions import _baseline_by_buf_id, _update_signs

        api, buf, lines = _make_api(["one", "three"])
        _baseline_by_buf_id.clear()
        _baseline_by_buf_id[buf.buf_id] = list(lines)
        lines.insert(1, "two")

        _update_signs(api, buf)

        buf.add_sign.assert_called_once_with("session_additions", 1, "session_additions.add")

    def test_update_signs_respects_disabled_option(self):
        from peovim.plugins.session_additions import _baseline_by_buf_id, _update_signs

        api, buf, lines = _make_api(["one"])
        api.options.get.side_effect = lambda name: False if name == "session_additions_enabled" else None
        _baseline_by_buf_id.clear()
        _baseline_by_buf_id[buf.buf_id] = list(lines)
        lines.append("two")

        _update_signs(api, buf)

        buf.add_sign.assert_not_called()

    def test_update_signs_uses_custom_sign_char(self):
        from peovim.plugins.session_additions import _baseline_by_buf_id, _update_signs

        api, buf, lines = _make_api(["one"])
        api.options.get.side_effect = lambda name: {
            "session_additions_sign_char": ">",
            "session_additions_sign_color": "1,2,3",
        }.get(name)
        _baseline_by_buf_id.clear()
        _baseline_by_buf_id[buf.buf_id] = list(lines)
        lines.append("two")

        _update_signs(api, buf)

        assert api.register_sign_type.call_args.args[0] == "session_additions.add"
        assert api.register_sign_type.call_args.args[1] == ">"
        assert api.register_sign_type.call_args.args[2].fg == (1, 2, 3)

    def test_buffer_opened_resets_baseline(self):
        from peovim.plugins.session_additions import _baseline_by_buf_id, _on_buffer_opened

        api, buf, lines = _make_api(["one"])
        _baseline_by_buf_id.clear()
        lines.append("two")

        _on_buffer_opened(api, buf_id=buf.buf_id)

        assert _baseline_by_buf_id[buf.buf_id] == ["one", "two"]
