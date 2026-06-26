"""
End-to-end editing sessions using ModalEngine + ActionDispatcher + Document.
No real terminal required — HeadlessBackend captures render ops.

These tests simulate what a real user would do and verify the combined
behavior of the modal engine, dispatcher, and document.
"""

from peovim.core.document import Document
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine, Mode
from peovim.ui.backends.headless import HeadlessBackend

# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------


class EditorSession:
    """
    Minimal editing session for integration tests.
    Wires together ModalEngine + ActionDispatcher + Document + Window.
    """

    def __init__(self, content: str = "", cols: int = 80, rows: int = 24) -> None:
        self.doc = Document()
        self.doc.load_string(content)
        self.window = Window(self.doc, width=cols, height=rows)
        self.registers = RegisterStore()
        self.engine = ModalEngine()
        self.engine.set_cursor(0, 0)
        self.engine.set_line_count(self.doc.line_count())
        self.dispatcher = ActionDispatcher(self.engine, self.window, self.registers)
        self.backend = HeadlessBackend(cols=cols, rows=rows)

    def type(self, keys: str) -> None:
        """Type a sequence of keys (parsed by HeadlessBackend.feed_keys notation)."""
        for key in _parse_keys(keys):
            actions = self.engine.feed_key(key)
            self.dispatcher.dispatch(actions)

    def text(self) -> str:
        return self.doc.get_text()

    def line(self, n: int = 0) -> str:
        return self.doc.get_line(n)

    def cursor(self) -> tuple[int, int]:
        return (self.window.cursor.line, self.window.cursor.col)

    def mode(self) -> Mode:
        return self.engine.mode

    def line_count(self) -> int:
        return self.doc.line_count()


def _parse_keys(seq: str) -> list[str]:
    keys = []
    i = 0
    while i < len(seq):
        if seq[i] == "<":
            end = seq.find(">", i)
            if end != -1:
                keys.append(seq[i : end + 1])
                i = end + 1
                continue
        keys.append(seq[i])
        i += 1
    return keys


# ---------------------------------------------------------------------------
# Basic insert mode editing
# ---------------------------------------------------------------------------


class TestInsertEditing:
    def test_insert_text(self):
        s = EditorSession()
        s.type("ihello<Esc>")
        assert s.line(0) == "hello"

    def test_insert_then_normal(self):
        s = EditorSession()
        s.type("ihello<Esc>")
        assert s.mode() == Mode.NORMAL

    def test_append_text(self):
        s = EditorSession("hello")
        s.engine.set_cursor(0, 4)
        s.window.cursor.move_to(0, 4)
        s.type("a world<Esc>")
        assert s.line(0) == "hello world"

    def test_insert_newline(self):
        s = EditorSession()
        s.type("iline1<CR>line2<Esc>")
        assert s.line_count() == 2
        assert s.line(0) == "line1"
        assert s.line(1) == "line2"

    def test_insert_open_line_below(self):
        s = EditorSession("first")
        s.type("osecond<Esc>")
        assert s.line_count() == 2
        assert s.line(1) == "second"

    def test_insert_open_line_above(self):
        s = EditorSession("second")
        s.type("Ofirst<Esc>")
        assert s.line_count() == 2
        assert s.line(0) == "first"
        assert s.line(1) == "second"

    def test_backspace_deletes(self):
        s = EditorSession()
        s.type("ihello<BS><Esc>")
        assert s.line(0) == "hell"

    def test_backspace_at_end_of_line_keeps_insert_cursor_at_eol(self):
        s = EditorSession()
        s.type("ihello<BS>")
        assert s.line(0) == "hell"
        assert s.cursor() == (0, 4)
        assert s.mode() == Mode.INSERT

    def test_repeated_backspace_deletes_characters_in_order(self):
        s = EditorSession()
        s.type("ihello<BS><BS><BS><BS><BS>")
        assert s.line(0) == ""
        assert s.cursor() == (0, 0)

    def test_backspace_at_start_of_line_joins_previous_line(self):
        s = EditorSession("ab\ncd")
        s.window.cursor.move_to(1, 0)
        s.engine.set_cursor(1, 0)
        s.type("i<BS>")
        assert s.line_count() == 1
        assert s.line(0) == "abcd"
        assert s.cursor() == (0, 2)

    def test_insert_mode_arrow_keys_move_cursor(self):
        s = EditorSession()
        s.type("ihello<Left><Left><Right>!")
        assert s.line(0) == "hell!o"
        assert s.cursor() == (0, 5)


# ---------------------------------------------------------------------------
# Normal mode operations
# ---------------------------------------------------------------------------


class TestNormalMode:
    def test_dd_deletes_line(self):
        s = EditorSession("hello\nworld")
        s.type("dd")
        assert s.line_count() == 1
        assert s.line(0) == "world"

    def test_dd_updates_clipboard_backed_unnamed_register_for_paste(self):
        from peovim.core.editor_state import EditorState

        doc = Document()
        doc.load_string("one\ntwo\nthree")
        window = Window(doc, width=80, height=24)
        registers = RegisterStore()
        registers.set("+", "stale", "line")
        engine = ModalEngine()
        engine.set_document(doc)
        engine.set_cursor(0, 0)
        engine.set_line_count(doc.line_count())
        editor_state = EditorState()
        editor_state.options.set_global("clipboard", "unnamedplus")
        dispatcher = ActionDispatcher(engine, window, registers, editor_state=editor_state)

        for key in _parse_keys("ddp"):
            actions = engine.feed_key(key)
            dispatcher.dispatch(actions)

        assert doc.get_text() == "two\none\nthree"
        assert registers.get("+") == ("one", "line")

    def test_yy_pp_duplicates_line(self):
        s = EditorSession("hello")
        s.type("yyp")
        assert s.line_count() == 2
        assert s.line(0) == "hello"
        assert s.line(1) == "hello"

    def test_x_deletes_char(self):
        s = EditorSession("hello")
        s.type("x")
        assert s.line(0) == "ello"

    def test_undo_redo(self):
        s = EditorSession("hello")
        s.type("x")
        assert s.line(0) == "ello"
        s.type("u")
        assert s.line(0) == "hello"
        s.type("<C-r>")
        assert s.line(0) == "ello"

    def test_cursor_j_k(self):
        s = EditorSession("line1\nline2\nline3")
        s.type("j")
        assert s.cursor()[0] == 1
        s.type("k")
        assert s.cursor()[0] == 0

    def test_cursor_G_goes_to_last(self):
        s = EditorSession("a\nb\nc")
        s.type("G")
        assert s.cursor()[0] == 2

    def test_cursor_gg_goes_to_first(self):
        s = EditorSession("a\nb\nc")
        s.type("G")  # go to bottom
        s.type("gg")  # go back to top
        assert s.cursor()[0] == 0

    def test_count_j(self):
        s = EditorSession("a\nb\nc\nd\ne")
        s.type("3j")
        assert s.cursor()[0] == 3


# ---------------------------------------------------------------------------
# Undo / redo (deeper)
# ---------------------------------------------------------------------------


class TestUndoRedo:
    def test_undo_insert_text(self):
        s = EditorSession()
        s.type("ihello<Esc>")
        assert s.line(0) == "hello"
        # One undo undoes the entire insert session (Vim-compatible)
        s.type("u")
        assert s.line(0) == ""

    def test_redo_chain(self):
        s = EditorSession()
        s.type("iabc<Esc>")
        # One undo undoes the whole insert session
        s.type("u")
        assert s.line(0) == ""
        # One redo restores it
        s.type("<C-r>")
        assert s.line(0) == "abc"

    def test_multiple_lines_undo(self):
        s = EditorSession()
        s.type("iline1<CR>line2<Esc>")
        assert s.line_count() == 2
        # One undo undoes the entire insert session
        s.type("u")
        assert s.line(0) == ""
        assert s.line_count() == 1


# ---------------------------------------------------------------------------
# Yank and paste
# ---------------------------------------------------------------------------


class TestYankPaste:
    def test_yy_paste_below(self):
        s = EditorSession("hello")
        s.type("yyp")
        assert s.line(0) == "hello"
        assert s.line(1) == "hello"

    def test_P_paste_above(self):
        s = EditorSession("hello\nworld")
        # Move to line 1 ("world"), yank it, paste before (above) it
        s.type("j")
        s.type("yyP")
        # "world" inserted before line 1 → hello, world(pasted), world(original)
        assert s.line(0) == "hello"
        assert s.line(1) == "world"
        assert s.line(2) == "world"
        assert s.line_count() == 3

    def test_block_register_p_pastes_rectangle_after_cursor(self):
        s = EditorSession("one\ntwo\nthree")
        s.registers.set('"', "BC\nDE", "block")

        s.type("p")

        assert s.line(0) == "oBCne"
        assert s.line(1) == "tDEwo"
        assert s.line(2) == "three"

    def test_block_register_P_pastes_rectangle_before_cursor(self):
        s = EditorSession("one\ntwo\nthree")
        s.registers.set('"', "BC\nDE", "block")

        s.type("P")

        assert s.line(0) == "BCone"
        assert s.line(1) == "DEtwo"
        assert s.line(2) == "three"

    def test_block_register_p_pads_short_lines_and_extends_document(self):
        s = EditorSession("ab\nx")
        s.registers.set('"', "!\n?\n#", "block")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)

        s.type("p")

        assert s.line(0) == "ab!"
        assert s.line(1) == "x ?"
        assert s.line(2) == "  #"

    def test_block_register_paste_is_single_undo_step(self):
        original = "one\ntwo\nthree"
        s = EditorSession(original)
        s.registers.set('"', "BC\nDE", "block")

        s.type("p")
        s.type("u")

        assert s.text() == original

    def test_char_p_cursor_lands_on_last_pasted_char(self):
        # word2 yanked, cursor on 'w' of word1, p → word1word2 but cursor on '2'
        s = EditorSession("word1")
        s.registers.set('"', "word2", "char")
        s.engine.set_cursor(0, 0)
        s.window.cursor.move_to(0, 0)

        s.type("p")

        assert s.line(0) == "wword2ord1"
        assert s.window.cursor.col == 5  # on '2', last char of 'word2'

    def test_char_P_cursor_lands_on_last_pasted_char(self):
        # word2 yanked, cursor on 'w' of word1, P → word2word1, cursor on '2'
        s = EditorSession("word1")
        s.registers.set('"', "word2", "char")
        s.engine.set_cursor(0, 0)
        s.window.cursor.move_to(0, 0)

        s.type("P")

        assert s.line(0) == "word2word1"
        assert s.window.cursor.col == 4  # on '2', last char of 'word2'


# ---------------------------------------------------------------------------
# Mode transitions
# ---------------------------------------------------------------------------


class TestModeTransitions:
    def test_i_then_esc(self):
        s = EditorSession()
        s.type("i")
        assert s.mode() == Mode.INSERT
        s.type("<Esc>")
        assert s.mode() == Mode.NORMAL

    def test_v_enters_visual(self):
        s = EditorSession("hello")
        s.type("v")
        assert s.mode() == Mode.VISUAL_CHAR

    def test_visual_esc(self):
        s = EditorSession("hello")
        s.type("v")
        s.type("<Esc>")
        assert s.mode() == Mode.NORMAL

    def test_visual_ctrl_d_extends_selection_half_page(self):
        s = EditorSession("\n".join(f"line {i}" for i in range(40)))
        s.window.cursor.move_to(5, 0)
        s.engine.set_cursor(5, 0)

        s.type("v<C-d>")

        assert s.mode() == Mode.VISUAL_CHAR
        assert s.cursor() == (15, 0)
        assert s.window.scroll_line == 10
        assert s.engine.visual_selection_regions() == [(5, 0, 15, 1)]

    def test_visual_ctrl_u_extends_selection_half_page_up(self):
        s = EditorSession("\n".join(f"line {i}" for i in range(40)))
        s.window.cursor.move_to(15, 0)
        s.engine.set_cursor(15, 0)
        s.window.scroll_line = 10
        s.engine.set_scroll(10)

        s.type("v<C-u>")

        assert s.mode() == Mode.VISUAL_CHAR
        assert s.cursor() == (5, 0)
        assert s.window.scroll_line == 0
        assert s.engine.visual_selection_regions() == [(5, 0, 15, 1)]

    def test_colon_enters_command(self):
        s = EditorSession()
        s.type(":")
        assert s.mode() == Mode.COMMAND


# ---------------------------------------------------------------------------
# Dot repeat
# ---------------------------------------------------------------------------


class TestDotRepeat:
    def test_dot_repeats_x(self):
        s = EditorSession("hello world")
        s.type("x")  # delete 'h'
        assert s.line(0) == "ello world"
        s.type(".")  # repeat delete
        assert s.line(0) == "llo world"

    def test_dot_repeats_x_at_new_cursor_position(self):
        s = EditorSession("abcde")
        s.type("x")
        s.type("ll")
        s.type(".")

        assert s.line(0) == "bce"

    def test_dot_repeats_replace_char_at_new_cursor_position(self):
        s = EditorSession("abcde")
        s.type("rx")
        s.type("ll")
        s.type(".")

        assert s.line(0) == "xbxde"

    def test_dot_repeats_dd(self):
        s = EditorSession("line1\nline2\nline3")
        s.type("dd")
        assert s.line(0) == "line2"
        s.type(".")
        assert s.line(0) == "line3"

    def test_dot_repeats_dw_re_evaluates_motion(self):
        # dw on "foo bar baz": deletes "foo ", cursor on "bar"
        # . should delete "bar " (re-evaluate word motion), not a fixed 4-char width
        s = EditorSession("foo bar baz")
        s.type("dw")
        assert s.line(0) == "bar baz"
        s.type(".")
        assert s.line(0) == "baz"

    def test_dot_repeats_de_re_evaluates_motion(self):
        # de on "abc defgh": deletes "abc", cursor on " "
        # . should delete "defgh" (next word end), not fixed 3-char width
        s = EditorSession("abc defgh")
        s.type("de")
        assert s.line(0) == " defgh"
        s.type("l")  # move to 'd'
        s.type(".")
        assert s.line(0) == " "


# ---------------------------------------------------------------------------
# Quit with unsaved changes
# ---------------------------------------------------------------------------


class TestUnsavedQuit:
    def _make_dispatcher(self, content: str = "hello"):
        from peovim.core.document import Document
        from peovim.core.editor_state import EditorState
        from peovim.core.registers import RegisterStore
        from peovim.core.window import Window
        from peovim.modal.dispatcher import ActionDispatcher
        from peovim.modal.engine import ModalEngine

        doc = Document()
        doc.load_string(content)
        window = Window(doc)
        registers = RegisterStore()
        engine = ModalEngine()
        editor_state = EditorState()
        disp = ActionDispatcher(engine, window, registers, editor_state=editor_state)
        return disp, doc, editor_state

    def test_quit_clean_doc_succeeds(self):
        from peovim.modal.actions import QuitEditor

        disp, doc, state = self._make_dispatcher()
        assert not doc.dirty
        disp.dispatch([QuitEditor()])
        assert disp.quit_requested
        assert state.message == ""

    def test_quit_dirty_doc_blocked(self):
        from peovim.modal.actions import InsertText, QuitEditor

        disp, doc, state = self._make_dispatcher()
        disp.dispatch([InsertText(0, 0, "x")])  # make dirty
        assert doc.dirty
        disp.dispatch([QuitEditor(force=False)])
        assert not disp.quit_requested
        assert "No write since last change" in state.message

    def test_quit_force_dirty_succeeds(self):
        from peovim.modal.actions import InsertText, QuitEditor

        disp, doc, state = self._make_dispatcher()
        disp.dispatch([InsertText(0, 0, "x")])
        assert doc.dirty
        disp.dispatch([QuitEditor(force=True)])
        assert disp.quit_requested

    def test_message_is_set_on_blocked_quit(self):
        from peovim.modal.actions import InsertText, QuitEditor

        disp, doc, state = self._make_dispatcher()
        disp.dispatch([InsertText(0, 0, "x")])
        disp.dispatch([QuitEditor()])
        assert state.message != ""

    def test_save_no_path_sets_message(self):
        from peovim.modal.actions import SaveBuffer

        disp, doc, state = self._make_dispatcher()
        assert doc.path is None
        disp.dispatch([SaveBuffer()])
        assert "No file name" in state.message
