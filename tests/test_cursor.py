"""
Cursor movement, virtual column, clamp to buffer bounds.
"""

from peovim.core.buffer import PieceTable
from peovim.core.cursor import Cursor


def make_table(content: str) -> PieceTable:
    t = PieceTable()
    t.load(content.encode())
    return t


class TestCursorBasic:
    def test_initial_position(self):
        cur = Cursor()
        assert cur.line == 0
        assert cur.col == 0

    def test_set_position(self):
        cur = Cursor()
        cur.line = 3
        cur.col = 7
        assert cur.line == 3
        assert cur.col == 7

    def test_move_to(self):
        cur = Cursor()
        cur.move_to(5, 10)
        assert cur.line == 5
        assert cur.col == 10

    def test_move_to_updates_virtual_col(self):
        cur = Cursor()
        cur.move_to(0, 5)
        assert cur.virtual_col == 5


class TestCursorClamp:
    def test_clamp_line_to_buffer(self):
        buf = make_table("a\nb\nc")
        cur = Cursor()
        cur.move_to(10, 0)
        cur.clamp(buf)
        assert cur.line == 2  # last line

    def test_clamp_col_to_line_length(self):
        buf = make_table("hello\nhi")
        cur = Cursor()
        cur.move_to(1, 10)
        cur.clamp(buf)
        assert cur.col == 1  # "hi" is 2 chars, max normal-mode col = len-1 = 1

    def test_clamp_does_not_change_valid(self):
        buf = make_table("hello\nworld")
        cur = Cursor()
        cur.move_to(0, 3)
        cur.clamp(buf)
        assert cur.line == 0
        assert cur.col == 3

    def test_clamp_empty_line(self):
        buf = make_table("a\n\nb")
        cur = Cursor()
        cur.move_to(1, 5)
        cur.clamp(buf)
        assert cur.col == 0


class TestVirtualColumn:
    def test_virtual_col_preserved_on_j(self):
        # Moving down from col 5 to a line with only 2 chars
        # virtual_col stays at 5, actual col clamps to 2
        buf = make_table("hello world\nhi")
        cur = Cursor()
        cur.move_to(0, 10)  # col 10, virtual_col=10
        assert cur.virtual_col == 10
        # Move down — col clamps, virtual_col preserved
        cur.move_down(buf, preserve_virtual=True)
        assert cur.line == 1
        assert cur.col == 1  # "hi" has 2 chars, max normal-mode col = len-1 = 1
        assert cur.virtual_col == 10

    def test_virtual_col_reset_on_horizontal_move(self):
        cur = Cursor()
        cur.move_to(0, 10)
        assert cur.virtual_col == 10
        # Any explicit column change resets virtual_col
        cur.move_to(0, 3)
        assert cur.virtual_col == 3

    def test_virtual_col_restored_if_line_is_long_enough(self):
        buf = make_table("hi\nhello world")
        cur = Cursor()
        cur.move_to(0, 1)  # col=1 on short line, virtual_col=1
        cur.virtual_col = 5  # manually set as if we came from a longer line
        cur.move_down(buf, preserve_virtual=True)
        assert cur.line == 1
        assert cur.col == 5  # "hello world" has len>=6
        assert cur.virtual_col == 5


class TestMovement:
    def test_move_right(self):
        buf = make_table("hello")
        cur = Cursor()
        cur.move_right(buf)
        assert cur.col == 1

    def test_move_right_at_line_end_no_wrap(self):
        buf = make_table("hi")
        cur = Cursor()
        cur.move_to(0, 1)
        cur.move_right(buf)
        assert cur.col == 1  # clamped; no wrap in normal mode

    def test_move_left(self):
        buf = make_table("hello")
        cur = Cursor()
        cur.move_to(0, 3)
        cur.move_left(buf)
        assert cur.col == 2

    def test_move_left_at_col_0_stays(self):
        buf = make_table("hello")
        cur = Cursor()
        cur.move_left(buf)
        assert cur.col == 0

    def test_move_up_at_top_stays(self):
        buf = make_table("hello\nworld")
        cur = Cursor()
        cur.move_up(buf)
        assert cur.line == 0

    def test_move_down_at_bottom_stays(self):
        buf = make_table("hello\nworld")
        cur = Cursor()
        cur.move_to(1, 0)
        cur.move_down(buf)
        assert cur.line == 1


class TestDispatcherVerticalMove:
    def test_j_preserves_virtual_col_through_dispatcher(self):
        from peovim.core.document import Document
        from peovim.core.editor_state import EditorState
        from peovim.core.registers import RegisterStore
        from peovim.core.window import Window
        from peovim.core.workspace import Workspace
        from peovim.modal.actions import MoveCursor
        from peovim.modal.dispatcher import ActionDispatcher
        from peovim.modal.engine import ModalEngine

        doc = Document()
        doc.load_string("long line here\nshort\nanother long line here")
        window = Window(doc)
        engine = ModalEngine()
        engine.set_document(doc)
        engine.set_cursor(0, 0)
        engine.set_line_count(doc.line_count())
        dispatcher = ActionDispatcher(
            engine,
            window,
            RegisterStore(),
            editor_state=EditorState(),
            workspace=Workspace(window),
        )

        cur = window.cursor
        cur.move_to(0, 15)
        assert cur.virtual_col == 15

        dispatcher.dispatch([MoveCursor(1, 15)])
        assert cur.line == 1
        assert cur.col == 4
        assert cur.virtual_col == 15

        dispatcher.dispatch([MoveCursor(2, 4)])
        assert cur.line == 2
        assert cur.col == 15
        assert cur.virtual_col == 15

    def test_non_vertical_move_resets_virtual_col(self):
        from peovim.core.document import Document
        from peovim.core.editor_state import EditorState
        from peovim.core.registers import RegisterStore
        from peovim.core.window import Window
        from peovim.core.workspace import Workspace
        from peovim.modal.actions import MoveCursor
        from peovim.modal.dispatcher import ActionDispatcher
        from peovim.modal.engine import ModalEngine

        doc = Document()
        doc.load_string("long line here\nshort\nanother long line here")
        window = Window(doc)
        engine = ModalEngine()
        engine.set_document(doc)
        engine.set_cursor(0, 0)
        engine.set_line_count(doc.line_count())
        dispatcher = ActionDispatcher(
            engine,
            window,
            RegisterStore(),
            editor_state=EditorState(),
            workspace=Workspace(window),
        )

        cur = window.cursor
        cur.move_to(0, 15)
        assert cur.virtual_col == 15

        dispatcher.dispatch([MoveCursor(1, 15)])
        assert cur.virtual_col == 15

        dispatcher.dispatch([MoveCursor(1, 3)])
        assert cur.virtual_col == 3
