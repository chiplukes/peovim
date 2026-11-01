"""Soft wrapping keeps source text, navigation and viewport coordinates aligned."""

from types import SimpleNamespace

import pytest

from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.window import Window
from peovim.modal.actions import MoveCursor
from peovim.modal.engine import ModalEngine, Mode
from peovim.ui.layout import Rect
from peovim.ui.text_layout import wrapped_cursor_offset, wrapped_ranges
from peovim.ui.window_render_controller import WindowRenderController
from peovim.ui.window_renderer import render_window


def setup(text, width=10, height=3, **options):
    doc = Document()
    doc.load_string(text)
    win = Window(doc, width=width, height=height)
    win.options.update(wrap=True, signcolumn="no", **options)
    engine = ModalEngine()
    engine.set_context_provider(lambda: win)
    host = SimpleNamespace(_editor_state=EditorState(), _engine=engine)
    controller = WindowRenderController(host)
    rect = Rect(0, 0, width, height)
    controller.sync_window_render_state(win, rect)
    return win, engine, controller, rect


def feed(win, engine, keys):
    actions = []
    for key in keys:
        actions = engine.feed_key(key)
        for action in actions:
            if isinstance(action, MoveCursor):
                win.cursor.move_to(action.line, action.col)
                win.cursor.clamp(win.document._table)
    return actions


def row(grid, index):
    return "".join(grid.read_cell(index, col)[0] for col in range(grid.width))


@pytest.mark.parametrize(
    "text,width",
    [("one two three four", 10), ("abcdefghijklm", 4), ("\talpha beta", 5), ("", 1), ("  indented text  ", 1)],
)
def test_wrap_preserves_all_source_characters(text, width):
    ranges = wrapped_ranges(text, width)
    expanded = text.expandtabs(4)
    assert "".join(expanded[start:end] for start, end in ranges) == expanded
    assert all(end - start <= width for start, end in ranges)


def test_render_word_boundaries_gutter_and_cursor():
    win, engine, controller, rect = setup("one two three four", width=14, number=True)
    win.cursor.move_to(0, 9)
    controller.sync_window_render_state(win, rect)
    grid = render_window(win.snapshot(), rect, True)
    assert row(grid, 0) == "  1 one two   "
    assert row(grid, 1) == "    three four"
    assert wrapped_cursor_offset(win, win.wrap_width) == (1, 1)
    assert grid.read_cell(1, 5)[2] == (80, 120, 200)
    assert win.document.get_line(0) == "one two three four"


def test_counted_screen_motions_and_file_line_motion():
    win, engine, _, _ = setup("abcdefghijABCDEFGHIJ0123456789\nnext")
    feed(win, engine, "2gj")
    assert (win.cursor.line, win.cursor.col) == (0, 20)
    feed(win, engine, "gk")
    assert win.cursor.col == 10
    feed(win, engine, "j")
    assert win.cursor.line == 1


def test_visual_screen_motion_preserves_count_and_selection():
    win, engine, _, _ = setup("abcdefghijABCDEFGHIJ0123456789")
    feed(win, engine, "v2gj")
    assert engine.mode == Mode.VISUAL_CHAR
    assert win.cursor.col == 20
    assert engine.visual_selection_regions(cursor=(0, 20)) == [(0, 0, 0, 21)]


def test_screen_column_survives_short_row():
    win, engine, _, _ = setup("abcdefghij\nx\nABCDEFGHIJ")
    win.cursor.move_to(0, 7)
    feed(win, engine, "gj")
    assert (win.cursor.line, win.cursor.col) == (1, 0)
    feed(win, engine, "gj")
    assert (win.cursor.line, win.cursor.col) == (2, 7)


def test_paragraph_taller_than_viewport_and_resize():
    win, engine, controller, rect = setup("x" * 100)
    feed(win, engine, "9gj")
    controller.sync_window_render_state(win, rect)
    assert win.scroll_line == 0
    assert win.scroll_wrap_row == 7
    assert wrapped_cursor_offset(win, 10) == (2, 0)
    grid = render_window(win.snapshot(), rect, True)
    assert row(grid, 0) == "x" * 10
    assert grid.read_cell(2, 0)[2] == (80, 120, 200)
    controller.sync_window_render_state(win, Rect(0, 0, 20, 3))
    assert 0 <= wrapped_cursor_offset(win, 20)[0] < 3
    win.options["wrap"] = False
    controller.sync_window_render_state(win, rect)
    assert win.scroll_wrap_row == 0
    assert win.scroll_col > 0


def test_insert_cursor_at_exact_width_has_visible_continuation():
    win, engine, controller, rect = setup("abcdefghij")
    engine.mode = Mode.INSERT
    win.cursor.move_to(0, 10)
    controller.sync_window_render_state(win, rect)
    assert wrapped_cursor_offset(win, 10) == (1, 0)
    grid = render_window(win.snapshot(), rect, True)
    assert row(grid, 1).strip() == ""
    assert grid.read_cell(1, 0)[2] == (80, 120, 200)


def test_wrapping_disabled_screen_motion_uses_file_lines():
    win, engine, controller, rect = setup("abcdefghijklmnop\nnext")
    win.options["wrap"] = False
    controller.sync_window_render_state(win, rect)
    feed(win, engine, "gj")
    assert (win.cursor.line, win.cursor.col) == (1, 0)


def test_scroll_inside_single_paragraph():
    from peovim.core.registers import RegisterStore
    from peovim.modal.actions import ScrollView
    from peovim.modal.dispatcher import ActionDispatcher

    win, engine, controller, rect = setup("x" * 100)
    dispatcher = ActionDispatcher(engine, win, RegisterStore())
    dispatcher.dispatch([ScrollView(4)])
    controller.sync_window_render_state(win, rect)
    assert win.scroll_wrap_row == 4
    assert win.cursor.col == 40


def test_operator_screen_motion_is_characterwise():
    from peovim.core.registers import RegisterStore
    from peovim.modal.dispatcher import ActionDispatcher

    win, engine, _, _ = setup("abcdefghijABCDEFGHIJ")
    dispatcher = ActionDispatcher(engine, win, RegisterStore())
    for key in "dgj":
        dispatcher.dispatch(engine.feed_key(key))
    assert win.document.get_line(0) == "ABCDEFGHIJ"


def test_virtual_padding_after_wrapped_line():
    from peovim.ui.decorations import Style, VirtualLine

    win, engine, controller, rect = setup("abcdefghijABCDEFGHIJ\nnext", height=4)
    padding = VirtualLine(0, Style(), count=2)
    controller._host._editor_state.decorations.add(id(win.document), "test", padding)
    feed(win, engine, "2gj")
    controller.sync_window_render_state(win, rect)
    assert (win.cursor.line, win.cursor.col) == (1, 0)
    assert wrapped_cursor_offset(win, 10) == (3, 0)
    grid = render_window(win.snapshot(), rect, True, extra_decorations=[padding])
    assert row(grid, 0) == "ABCDEFGHIJ"
    assert row(grid, 1).strip() == ""
    assert row(grid, 3).startswith("next")


def test_mouse_click_on_continuation():
    from test_mouse import _make_dispatcher

    from peovim.ui.backend import MouseEvent

    md, _, win, _, _, dispatcher = _make_dispatcher(
        "one two three four",
        layout_rect=Rect(0, 0, 10, 3),
        window_options={"wrap": True, "signcolumn": "no"},
    )
    controller = WindowRenderController(SimpleNamespace(_editor_state=dispatcher._editor_state))
    controller.sync_window_render_state(win, Rect(0, 0, 10, 3))
    md.handle(MouseEvent(row=1, col=2, button=0, pressed=True))
    assert (win.cursor.line, win.cursor.col) == (0, 10)


def test_terminal_cursor_uses_wrapped_coordinates():
    from test_event_loop_cmdline import _make_event_loop

    from peovim.ui.cell_grid import CellGrid

    loop, _, _, win = _make_event_loop("x" * 100)
    win.options.update(wrap=True, signcolumn="no", cursorblink=True)
    win.cursor.move_to(0, 90)
    rect = Rect(0, 0, 10, 3)
    loop._grid = CellGrid(10, 5)
    loop._current_layout = {loop._workspace.active_tab.root: rect}
    loop._window_render_controller.sync_window_render_state(win, rect)
    state = loop._resolve_terminal_cursor_state()
    assert state is not None
    assert state[:2] == (2, 0)


def test_scrollbar_uses_screen_rows_for_single_paragraph():
    from peovim.ui.scrollbar import SCROLLBAR_THUMB_CHAR

    win, _, controller, rect = setup("x" * 100, width=11, scrollbar=True)
    win.cursor.move_to(0, 90)
    controller.sync_window_render_state(win, rect)
    grid = render_window(win.snapshot(), rect, True)
    assert grid.read_cell(2, 10)[0] == SCROLLBAR_THUMB_CHAR
    assert grid.read_cell(0, 10)[0] != SCROLLBAR_THUMB_CHAR


def test_layout_cache_invalidates_after_edit():
    from peovim.ui.text_layout import window_wrap_rows

    win, _, _, _ = setup("abcdefghij")
    rows = window_wrap_rows(win, 10)
    assert window_wrap_rows(win, 10) is rows
    win.document.load_string("x" * 30)
    assert len(window_wrap_rows(win, 10)) == 3


def test_filetype_default_follows_buffer_switches_without_window_override():
    win, _, controller, rect = setup("x" * 100)
    del win.options["wrap"]
    store = controller._host._editor_state.options
    store.set("wrap", False)
    store.set("wrapfiletypes", "markdown,text")
    markdown = win.document
    markdown.filetype = "markdown"
    code = Document()
    code.load_string("x" * 100)
    code.filetype = "python"
    for document, expected in [(markdown, True), (code, False), (markdown, True)]:
        win.document = document
        controller.sync_window_render_state(win, rect)
        assert win.wrap_enabled is expected
        assert win.snapshot(store.global_as_dict()).options["wrap"] is expected
        assert "wrap" not in win.options


def test_filetype_defaults_apply_to_new_splits_and_filetype_changes():
    win, _, controller, rect = setup("x" * 100)
    del win.options["wrap"]
    store = controller._host._editor_state.options
    store.set("wrapfiletypes", "markdown")
    win.document.filetype = "markdown"
    split = Window(win.document, width=10, height=3)
    for window in (win, split):
        controller.sync_window_render_state(window, rect)
        assert window.wrap_enabled
        assert "wrap" not in window.options
    win.document.filetype = "python"
    for window in (win, split):
        controller.sync_window_render_state(window, rect)
        assert not window.wrap_enabled


@pytest.mark.parametrize(
    "global_wrap,local_wrap,filetype,expected",
    [
        (False, None, "markdown", True),
        (False, False, "markdown", False),
        (False, True, "python", True),
        (False, None, "python", False),
        (True, None, "python", True),
        (False, None, "", False),
    ],
)
def test_filetype_default_precedence(global_wrap, local_wrap, filetype, expected):
    win, _, controller, rect = setup("x" * 100)
    del win.options["wrap"]
    win.document.filetype = filetype
    store = controller._host._editor_state.options
    store.set("wrap", global_wrap)
    store.set("wrapfiletypes", " markdown, text, , ")
    if local_wrap is not None:
        win.options["wrap"] = local_wrap
    controller.sync_window_render_state(win, rect)
    assert win.wrap_enabled is expected
    assert win.snapshot(store.global_as_dict()).options["wrap"] is expected


def test_filetype_default_updates_when_configuration_changes():
    win, _, controller, rect = setup("x" * 100)
    del win.options["wrap"]
    win.document.filetype = "markdown"
    store = controller._host._editor_state.options
    for filetypes, expected in [("markdown", True), ("text", False), ("markdown,text", True), ("", False)]:
        store.set("wrapfiletypes", filetypes)
        controller.sync_window_render_state(win, rect)
        assert win.wrap_enabled is expected


def test_filetype_default_used_by_terminal_cursor_and_mouse():
    from test_event_loop_cmdline import _make_event_loop

    from peovim.ui.cell_grid import CellGrid

    loop, _, doc, win = _make_event_loop("x" * 100)
    doc.filetype = "markdown"
    loop._editor_state.options.set("wrapfiletypes", "markdown")
    win.options.update(signcolumn="no", cursorblink=True)
    win.cursor.move_to(0, 90)
    rect = Rect(0, 0, 10, 3)
    loop._grid = CellGrid(10, 5)
    loop._current_layout = {loop._workspace.active_tab.root: rect}
    loop._window_render_controller.sync_window_render_state(win, rect)
    state = loop._resolve_terminal_cursor_state()
    assert state is not None
    assert state[:2] == (2, 0)
    assert loop._mouse_dispatcher._buffer_position_for_point(win, rect, 1, 2) == (0, 82)
