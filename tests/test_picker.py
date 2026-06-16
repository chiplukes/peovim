"""
Phase 6d — FloatManager, NotifyManager, PickerWidget, UIAPI tests.
"""

from __future__ import annotations

import time

from peovim.core.style import Style
from peovim.ui.cell_grid import CellGrid
from peovim.ui.float_manager import (
    Absolute,
    Centered,
    CursorRelative,
    FloatManager,
    draw_border,
)
from peovim.ui.notify import NotifyManager
from peovim.ui.picker import PickerWidget, _filter
from peovim.ui.which_key_panel import WhichKeyPanel


def _grid(w: int = 80, h: int = 24) -> CellGrid:
    return CellGrid(w, h)


def _text(grid: CellGrid, row: int) -> str:
    return "".join(c[0] for c in grid._current[row]).rstrip()


# ---------------------------------------------------------------------------
# draw_border helper
# ---------------------------------------------------------------------------


class TestDrawBorder:
    def test_corners(self):
        g = _grid(20, 5)
        draw_border(g, 0, 0, 20, 5)
        assert g._current[0][0][0] == "┌"
        assert g._current[0][19][0] == "┐"
        assert g._current[4][0][0] == "└"
        assert g._current[4][19][0] == "┘"

    def test_sides(self):
        g = _grid(20, 5)
        draw_border(g, 0, 0, 20, 5)
        for row in range(1, 4):
            assert g._current[row][0][0] == "│"
            assert g._current[row][19][0] == "│"

    def test_title_in_top_border(self):
        g = _grid(30, 5)
        draw_border(g, 0, 0, 30, 5, title="Hello")
        top = "".join(c[0] for c in g._current[0])
        assert "Hello" in top

    def test_offset_position(self):
        g = _grid(40, 10)
        draw_border(g, 5, 2, 10, 6)
        assert g._current[2][5][0] == "┌"
        assert g._current[2][14][0] == "┐"


# ---------------------------------------------------------------------------
# FloatManager
# ---------------------------------------------------------------------------


class TestFloatManager:
    def test_open_float_returns_handle(self):
        fm = FloatManager()
        handle = fm.open_float(["hello"], width=20, height=5)
        assert handle is not None
        assert handle.is_open

    def test_close_float(self):
        fm = FloatManager()
        handle = fm.open_float(["hi"], width=20, height=5)
        handle.close()
        assert not handle.is_open

    def test_on_close_called(self):
        fm = FloatManager()
        fired = []
        handle = fm.open_float(["hi"], width=20, height=5, on_close=lambda: fired.append(1))
        handle.close()
        assert fired == [1]

    def test_set_content(self):
        fm = FloatManager()
        handle = fm.open_float(["old"], width=20, height=5)
        handle.set_content(["new line"])
        assert handle._float.content == ["new line"]

    def test_set_title(self):
        fm = FloatManager()
        handle = fm.open_float(["x"], width=20, height=5, title="Old")
        handle.set_title("New")
        assert handle._float.title == "New"

    def test_render_absolute(self):
        fm = FloatManager()
        fm.open_float(["hello world"], anchor=Absolute(2, 1), width=20, height=5, border=False)
        g = _grid(40, 10)
        fm.render(g)
        row1 = "".join(c[0] for c in g._current[1][2:13])
        assert "hello world" in row1

    def test_render_centered(self):
        fm = FloatManager()
        fm.open_float(["center"], anchor=Centered(), width=20, height=5, border=False)
        g = _grid(40, 20)
        fm.render(g)
        # Should not raise and should write something
        assert fm.has_visible

    def test_render_cursor_relative(self):
        fm = FloatManager()
        fm.open_float(["rel"], anchor=CursorRelative(1, 0), width=15, height=4, border=False)
        g = _grid(40, 20)
        fm.render(g, cursor_x=5, cursor_y=3)
        assert fm.has_visible

    def test_render_expands_tabs_in_float_content(self):
        fm = FloatManager()
        fm.open_float(["\talpha"], anchor=Absolute(0, 0), width=15, height=4, border=False)
        g = _grid(20, 6)
        fm.render(g)
        row = "".join(cell[0] for cell in g._current[0][:8])
        assert "\t" not in row
        assert row[:4] == "    "
        assert row[4] == "a"

    def test_z_order_sorting(self):
        fm = FloatManager()
        fm.open_float(["low"], width=20, height=5, z_order=10)
        fm.open_float(["high"], width=20, height=5, z_order=1)
        assert fm._floats[0].z_order == 1
        assert fm._floats[1].z_order == 10

    def test_render_with_border(self):
        fm = FloatManager()
        fm.open_float(["content line"], anchor=Absolute(0, 0), width=20, height=5, border=True, title="T")
        g = _grid(40, 10)
        fm.render(g)
        assert g._current[0][0][0] == "┌"

    def test_close_all(self):
        fm = FloatManager()
        fm.open_float(["a"], width=10, height=3)
        fm.open_float(["b"], width=10, height=3)
        fm.close_all()
        assert not fm.has_visible

    def test_string_content_split(self):
        fm = FloatManager()
        handle = fm.open_float("line1\nline2", width=20, height=5, border=False)
        assert len(handle._float.content) == 2

    def test_render_styled_segments_applies_foreground_colors(self):
        fm = FloatManager()
        fm.open_float(
            [[("kw", Style(fg=(1, 2, 3))), (" txt", Style(fg=(4, 5, 6)))]],
            anchor=Absolute(0, 0),
            width=10,
            height=3,
            border=False,
        )
        g = _grid(12, 4)
        fm.render(g)
        assert g._current[0][0][1] == (1, 2, 3)
        assert g._current[0][3][1] == (4, 5, 6)


# ---------------------------------------------------------------------------
# NotifyManager
# ---------------------------------------------------------------------------


class TestNotifyManager:
    def test_notify_returns_handle(self):
        nm = NotifyManager()
        h = nm.notify("test message")
        assert h is not None

    def test_notify_queued(self):
        nm = NotifyManager()
        nm.notify("hello")
        assert len(nm._queue) == 1

    def test_notify_dismiss(self):
        nm = NotifyManager()
        h = nm.notify("bye")
        h.dismiss()
        assert len(nm._queue) == 0

    def test_notify_renders_message(self):
        nm = NotifyManager()
        nm.notify("hello world", timeout=60)
        g = _grid(80, 24)
        nm.render(g)
        # Check top-right area for the message
        found = any("hello world" in "".join(c[0] for c in g._current[row]) for row in range(5))
        assert found

    def test_notify_renders_multiline_message(self):
        nm = NotifyManager()
        nm.notify("line one\nline two", timeout=60)
        g = _grid(80, 24)

        nm.render(g)

        rendered = ["".join(c[0] for c in g._current[row]) for row in range(6)]
        assert any("line one" in row for row in rendered)
        assert any("line two" in row for row in rendered)

    def test_notify_expires(self):
        nm = NotifyManager()
        # Create notification with 0.001s timeout
        nm.notify("expire me", timeout=0.001)
        time.sleep(0.05)
        g = _grid(80, 24)
        nm.render(g)  # triggers pruning
        assert len(nm._queue) == 0

    def test_notify_persistent(self):
        nm = NotifyManager()
        nm.notify("persistent", timeout=0)
        time.sleep(0.02)
        g = _grid(80, 24)
        nm.render(g)
        assert len(nm._queue) == 1

    def test_notify_levels(self):
        nm = NotifyManager()
        for level in ("info", "warn", "error", "debug"):
            nm.notify(f"{level} msg", level=level, timeout=60)
        assert len(nm._queue) == 4

    def test_max_stack_trimmed(self):
        nm = NotifyManager()
        from peovim.ui.notify import _NOTIFY_MAX_STACK

        for i in range(_NOTIFY_MAX_STACK + 3):
            nm.notify(f"msg {i}", timeout=60)
        assert len(nm._queue) <= _NOTIFY_MAX_STACK

    def test_notification_wrap_cache_is_reused_across_height_and_render(self):
        nm = NotifyManager()
        handle = nm.notify("line one\nline two", title="T", timeout=60)
        notif = handle._notif
        width = 40

        first_height = notif.height(width)
        first_lines = notif.message_lines(width)
        second_height = notif.height(width)
        second_lines = notif.message_lines(width)

        assert first_height == second_height
        assert first_lines == second_lines
        assert notif._cached_wrap_width == width
        assert notif._cached_message_lines == first_lines


# ---------------------------------------------------------------------------
# PickerWidget — state
# ---------------------------------------------------------------------------


class TestPickerState:
    def test_opens_correctly(self):
        p = PickerWidget()
        assert not p.is_open
        p.open("Files", ["a.py", "b.py", "c.py"])
        assert p.is_open

    def test_close_via_esc(self):
        p = PickerWidget()
        p.open("T", ["a", "b"])
        p.feed_key("<Esc>")
        assert not p.is_open

    def test_close_callback(self):
        closed = []
        p = PickerWidget()
        p.open("T", ["a"], on_close=lambda: closed.append(True))
        p.feed_key("<Esc>")
        assert closed == [True]

    def test_confirm_calls_on_confirm(self):
        results = []
        p = PickerWidget()
        p.open("T", ["alpha", "beta"], on_confirm=lambda s: results.append(s))
        p.feed_key("<Enter>")
        assert results == ["alpha"]
        assert not p.is_open

    def test_navigation_down(self):
        p = PickerWidget()
        p.open("T", ["a", "b", "c"])
        assert p._sel == 0
        p.feed_key("<Down>")
        assert p._sel == 1

    def test_navigation_up(self):
        p = PickerWidget()
        p.open("T", ["a", "b", "c"])
        p.feed_key("<Down>")
        p.feed_key("<Down>")
        p.feed_key("<Up>")
        assert p._sel == 1

    def test_navigation_clamps_top(self):
        p = PickerWidget()
        p.open("T", ["a", "b"])
        p.feed_key("<Up>")
        assert p._sel == 0

    def test_navigation_clamps_bottom(self):
        p = PickerWidget()
        p.open("T", ["a", "b"])
        p.feed_key("<Down>")
        p.feed_key("<Down>")
        p.feed_key("<Down>")
        assert p._sel == 1

    def test_typing_filters(self):
        p = PickerWidget()
        p.open("T", ["alpha", "beta", "gamma"])
        p.feed_key("a")
        assert all("a" in item for item in p._filtered)

    def test_backspace_removes_char(self):
        p = PickerWidget()
        p.open("T", ["alpha", "beta"])
        p.feed_key("a")
        p.feed_key("<Backspace>")
        assert p._query == ""
        assert len(p._filtered) == 2

    def test_callable_source(self):
        p = PickerWidget()
        p.open("T", lambda q: [f for f in ["foo", "bar", "baz"] if q in f])
        assert p._filtered == ["foo", "bar", "baz"]
        p.feed_key("b")
        assert all("b" in item for item in p._filtered)


# ---------------------------------------------------------------------------
# PickerWidget — render
# ---------------------------------------------------------------------------


class TestPickerRender:
    def test_render_draws_to_grid(self):
        p = PickerWidget()
        p.open("MyPicker", ["item1", "item2", "item3"])
        g = _grid(80, 24)
        p.render(g)
        # Bottom portion should have picker content
        full_text = "\n".join("".join(c[0] for c in g._current[r]) for r in range(24))
        assert "MyPicker" in full_text or "item" in full_text

    def test_render_nothing_when_closed(self):
        p = PickerWidget()
        g = _grid(80, 24)
        p.render(g)  # should be no-op — grid stays blank
        text = "\n".join("".join(c[0] for c in g._current[r]).strip() for r in range(24))
        assert text.strip() == ""

    def test_render_with_preview(self):
        p = PickerWidget()
        p.open("T", ["file.py"], preview=lambda f: f"Preview of {f}")
        g = _grid(100, 30)
        p.render(g)
        full = "\n".join("".join(c[0] for c in g._current[r]) for r in range(30))
        assert "Preview" in full or "file.py" in full

    def test_render_with_styled_preview_segments(self):
        p = PickerWidget()
        p.open("T", ["file.py"], preview=lambda _f: [[("def", Style(fg=(1, 2, 3))), (" main", Style(fg=(4, 5, 6)))]])
        g = _grid(100, 30)

        p.render(g)

        full = "\n".join("".join(c[0] for c in g._current[r]) for r in range(30))
        assert "def main" in full
        preview_row = next(row for row in range(30) if "def main" in "".join(c[0] for c in g._current[row]))
        text_row = "".join(c[0] for c in g._current[preview_row])
        start = text_row.index("def main")
        assert g._current[preview_row][start][1] == (1, 2, 3)
        assert g._current[preview_row][start + 3][1] == (4, 5, 6)


# ---------------------------------------------------------------------------
# WhichKeyPanel — render
# ---------------------------------------------------------------------------


class TestWhichKeyPanel:
    def test_render_draws_title_and_binding(self):
        panel = WhichKeyPanel()
        panel.show([("<leader>f", "find file")], title="Which Key")
        g = _grid(80, 12)

        panel.render(g, start_row=6, terminal_width=80)

        assert "Which Key" in _text(g, 6)
        assert "<leader>f" in _text(g, 7)
        assert "find file" in _text(g, 7)


# ---------------------------------------------------------------------------
# Fuzzy filter function
# ---------------------------------------------------------------------------


class TestFuzzyFilter:
    def test_empty_query_returns_all(self):
        items = ["a", "b", "c"]
        assert _filter("", items) == items

    def test_substring_match(self):
        items = ["hello", "world", "help"]
        result = _filter("hel", items)
        # Items clearly matching "hel" must be present
        assert "hello" in result
        assert "help" in result

    def test_case_insensitive(self):
        items = ["Hello", "WORLD"]
        result = _filter("hello", items)
        assert "Hello" in result

    def test_no_match_returns_empty(self):
        items = ["foo", "bar"]
        result = _filter("zzz", items)
        assert result == []


# ---------------------------------------------------------------------------
# UIAPI integration
# ---------------------------------------------------------------------------


class TestUIAPI:
    def _make_ui(self):
        from peovim.api.ui_api import UIAPI

        fm = FloatManager()
        nm = NotifyManager()
        p = PickerWidget()
        return UIAPI(fm, nm, p), fm, nm, p

    def test_open_float_delegates(self):
        ui, fm, nm, p = self._make_ui()
        handle = ui.open_float(["hi"], width=20, height=5)
        assert handle is not None
        assert fm.has_visible

    def test_notify_delegates(self):
        ui, fm, nm, p = self._make_ui()
        ui.notify("hello", timeout=60)
        assert len(nm._queue) == 1

    def test_open_picker_delegates(self):
        ui, fm, nm, p = self._make_ui()
        ui.open_picker("Files", ["a", "b"])
        assert p.is_open

    def test_close_picker_delegates(self):
        ui, fm, nm, p = self._make_ui()
        ui.open_picker("T", ["x"])
        ui.close_picker()
        assert not p.is_open

    def test_open_float_no_manager_returns_none(self):
        from peovim.api.ui_api import UIAPI

        ui = UIAPI()
        assert ui.open_float(["x"]) is None

    def test_notify_no_manager_returns_none(self):
        from peovim.api.ui_api import UIAPI

        ui = UIAPI()
        assert ui.notify("x") is None

    def test_set_sidebar_style_updates_sidebar_host(self):
        from peovim.api.ui_api import UIAPI

        ui = UIAPI()

        style = ui.set_sidebar_style(background="#252526", header_active_bg="#50648C")

        assert style.background == (37, 37, 38)
        assert style.header_active_bg == (80, 100, 140)
