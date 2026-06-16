"""
tests.test_plugin_todo — Tests for the peovim.plugins.todo built-in plugin

Covers: keyword scanning, debounced rescans, namespace clearing,
:TodoList command registration, keymap registration, and error isolation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api(lines: list[str] | None = None, buf_id: int = 1) -> MagicMock:
    """Build a minimal EditorAPI mock with a single buffer."""
    api = MagicMock()
    buf = _make_buf(lines or [], buf_id=buf_id)
    api.active_buffer.return_value = buf
    api.active_window.return_value = MagicMock()
    api.list_buffers.return_value = [buf]
    api.options.get.return_value = None  # default: no overrides
    return api, buf


def _make_buf(lines: list[str], buf_id: int = 1) -> MagicMock:
    buf = MagicMock()
    buf.buf_id = buf_id
    buf.path = None
    buf.line_count.return_value = len(lines)
    buf.get_line.side_effect = lambda i: lines[i] if 0 <= i < len(lines) else ""
    return buf


# ---------------------------------------------------------------------------
# setup() bootstrap
# ---------------------------------------------------------------------------


class TestSetup:
    def test_setup_subscribes_to_events(self):
        from peovim.plugins.todo import setup

        api, _ = _make_api()
        setup(api)
        events = [c.args[0] for c in api.events.on.call_args_list]
        assert "buffer_opened" in events
        assert "buffer_changed" in events

    def test_setup_registers_todo_list_command(self):
        from peovim.plugins.todo import setup

        api, _ = _make_api()
        setup(api)
        assert api.commands.register.called
        cmd_name = api.commands.register.call_args_list[0].args[0]
        assert cmd_name == "TodoList"

    def test_setup_registers_keymap(self):
        from peovim.plugins.todo import setup

        api, _ = _make_api()
        setup(api)
        assert api.keymap.nmap.called
        key = api.keymap.nmap.call_args_list[0].args[0]
        assert key == "<leader>xt"

    def test_setup_scans_existing_buffers(self):
        """setup() should scan already-open buffers immediately."""
        from peovim.plugins.todo import setup

        api, buf = _make_api(["# TODO: fix this"])
        setup(api)
        buf.add_highlight.assert_called()
        buf.clear_namespace.assert_called_with("todo")


# ---------------------------------------------------------------------------
# _scan_buffer
# ---------------------------------------------------------------------------


class TestScanBuffer:
    def test_highlights_todo(self):
        from peovim.plugins.todo import _scan_buffer

        api, buf = _make_api(["# TODO: something"])
        _scan_buffer(api, buf)
        assert buf.add_highlight.called

    def test_highlights_fixme(self):
        from peovim.plugins.todo import _scan_buffer

        api, buf = _make_api(["# FIXME broken"])
        _scan_buffer(api, buf)
        assert buf.add_highlight.called

    def test_does_not_add_signs_for_todo(self):
        from peovim.plugins.todo import _scan_buffer

        api, buf = _make_api(["# TODO: highlight me"])
        _scan_buffer(api, buf)
        buf.add_sign.assert_not_called()

    def test_clears_namespace_before_scan(self):
        from peovim.plugins.todo import _scan_buffer

        api, buf = _make_api(["# TODO: x"])
        _scan_buffer(api, buf)
        buf.clear_namespace.assert_called_with("todo")

    def test_no_highlights_on_plain_line(self):
        from peovim.plugins.todo import _scan_buffer

        api, buf = _make_api(["x = 1 + 2"])
        _scan_buffer(api, buf)
        buf.add_highlight.assert_not_called()

    def test_multiple_keywords_on_same_line(self):
        from peovim.plugins.todo import _scan_buffer

        api, buf = _make_api(["# TODO: x  FIXME: y"])
        _scan_buffer(api, buf)
        assert buf.add_highlight.call_count == 2

    def test_keyword_correct_col_positions(self):
        from peovim.plugins.todo import _scan_buffer

        api, buf = _make_api(["x = 1  # FIXME: bug"])
        _scan_buffer(api, buf)
        # The highlight should start at column 10 (where FIXME starts)
        call_args = buf.add_highlight.call_args_list[0].args
        # (ns, start_line, start_col, end_line, end_col, style)
        assert call_args[1] == 0  # start_line
        assert call_args[2] == 9  # start_col

    def test_custom_keywords_option(self):
        from peovim.plugins.todo import _scan_buffer

        api, buf = _make_api(["# MYTODO: custom"])
        api.options.get.side_effect = lambda name: ["MYTODO"] if name == "todo_keywords" else None
        _scan_buffer(api, buf)
        buf.add_highlight.assert_called()

    def test_empty_buffer_no_crash(self):
        from peovim.plugins.todo import _scan_buffer

        api, buf = _make_api([])
        _scan_buffer(api, buf)  # must not raise


class TestDebounce:
    def test_buffer_changed_debounces_rescan(self, monkeypatch):
        from peovim.plugins import todo as todo_mod

        api, buf = _make_api(["# TODO: later"])
        handles: list[object] = []

        class FakeHandle:
            def __init__(self, callback):
                self.callback = callback
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        class FakeLoop:
            def call_later(self, delay, callback):
                handle = FakeHandle(callback)
                handles.append((delay, handle))
                return handle

        monkeypatch.setattr(todo_mod.asyncio, "get_event_loop", lambda: FakeLoop())

        todo_mod._on_debounced(api, buf_id=buf.buf_id)
        todo_mod._on_debounced(api, buf_id=buf.buf_id)

        assert handles[0][0] == todo_mod._DEBOUNCE_DELAY_SECONDS
        assert handles[0][1].cancelled is True
        assert handles[1][1].cancelled is False
        buf.add_highlight.assert_not_called()

        handles[1][1].callback()

        assert buf.add_highlight.called

    def test_teardown_cancels_pending_handles(self):
        from peovim.plugins import todo as todo_mod

        class FakeHandle:
            def __init__(self):
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        handle = FakeHandle()
        todo_mod._debounce_timers[1] = handle

        todo_mod.teardown()

        assert handle.cancelled is True
        assert todo_mod._debounce_timers == {}


# ---------------------------------------------------------------------------
# TodoList command / picker
# ---------------------------------------------------------------------------


class TestTodoList:
    def test_todo_list_opens_picker(self):
        from peovim.plugins.todo import _todo_list

        api, _ = _make_api(["# TODO: open me"])
        _todo_list(api)
        api.ui.open_picker.assert_called_once()
        title = api.ui.open_picker.call_args.args[0]
        assert "Todo" in title

    def test_todo_list_passes_items_to_picker(self):
        from peovim.plugins.todo import _todo_list

        api, _ = _make_api(["# TODO: item 1", "x = 1", "# FIXME: item 2"])
        _todo_list(api)
        items = api.ui.open_picker.call_args.args[1]
        assert len(items) == 2

    def test_todo_list_empty_buffer(self):
        from peovim.plugins.todo import _todo_list

        api, _ = _make_api(["no todos here"])
        _todo_list(api)
        items = api.ui.open_picker.call_args.args[1]
        assert items == []
