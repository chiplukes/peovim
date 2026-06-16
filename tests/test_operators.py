"""
Operator+motion combinations and register effects.
Tests d/y/c with various motions via EditorSession integration.
"""

from peovim.core.document import Document
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine, Mode

# ---------------------------------------------------------------------------
# Session helper (same as test_integration.py)
# ---------------------------------------------------------------------------


class EditorSession:
    def __init__(self, content: str = "", cols: int = 80, rows: int = 24) -> None:
        self.doc = Document()
        self.doc.load_string(content)
        self.window = Window(self.doc, width=cols, height=rows)
        self.registers = RegisterStore()
        self.engine = ModalEngine()
        self.engine.set_cursor(0, 0)
        self.engine.set_line_count(self.doc.line_count())
        self.engine.set_document(self.doc)
        self.dispatcher = ActionDispatcher(self.engine, self.window, self.registers)

    def type(self, keys: str) -> None:
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

    def reg(self, name: str = '"') -> str:
        text, _ = self.registers.get(name)
        return text


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
# d + motions
# ---------------------------------------------------------------------------


class TestDeleteMotion:
    def test_dh_deletes_left(self):
        s = EditorSession("hello")
        s.engine.set_cursor(0, 3)
        s.window.cursor.move_to(0, 3)
        s.type("dh")
        # Should delete char to left (col 2 'l')
        assert "l" not in s.line(0) or len(s.line(0)) < 5

    def test_dl_deletes_right(self):
        s = EditorSession("hello")
        s.type("dl")
        # dl deletes from col 0 to col 1 exclusive
        assert s.line(0) == "ello"

    def test_dj_deletes_two_lines(self):
        s = EditorSession("line1\nline2\nline3")
        s.type("dj")
        # dj with range_type='line' deletes current + next line
        # result depends on implementation but line count should decrease
        assert s.line_count() == 1

    def test_dk_deletes_current_and_above(self):
        s = EditorSession("line1\nline2\nline3")
        s.engine.set_cursor(1, 0)
        s.window.cursor.move_to(1, 0)
        s.type("dk")
        # dk from line1 (index 1) deletes lines 0 and 1, leaving "line3"
        assert s.line_count() == 1
        assert s.line(0) == "line3"


class TestYankMotion:
    def test_yw_yanks_word(self):
        s = EditorSession("hello world")
        s.type("yw")
        text = s.reg()
        assert "hello" in text

    def test_ye_yanks_word_end_inclusively(self):
        s = EditorSession("hello world")
        s.type("ye")

        assert s.reg() == "hello"

    def test_yE_yanks_word_end_inclusively(self):
        s = EditorSession("foo.bar baz")
        s.type("yE")

        assert s.reg() == "foo.bar"

    def test_yw_yanks_full_last_hex_token(self):
        s = EditorSession("1E1E1E")
        s.type("yw")

        assert s.reg() == "1E1E1E"

    def test_yW_yanks_hash_prefixed_hex_token(self):
        s = EditorSession("#1E1E1E")
        s.type("yW")

        assert s.reg() == "#1E1E1E"

    def test_yl_yanks_char(self):
        s = EditorSession("abc")
        s.type("yl")
        # yl yanks from cursor to cursor+1
        text = s.reg()
        assert len(text) == 1

    def test_register_yank(self):
        s = EditorSession("hello")
        s.type('"ayy')  # yank line to register a
        text, kind = s.registers.get("a")
        assert text == "hello"
        assert kind == "line"

    def test_yank_default_register(self):
        s = EditorSession("world")
        s.type("yy")
        text = s.reg('"')
        assert text == "world"

    def test_visual_char_yank_then_put_preserves_last_character(self):
        s = EditorSession("hello")

        s.type("vlllly")

        assert s.reg() == "hello"

        s.engine.set_cursor(0, 0)
        s.window.cursor.move_to(0, 0)
        s.type("P")

        assert s.line(0) == "hellohello"


class TestChangeMotion:
    def test_cl_change_char(self):
        s = EditorSession("hello")
        s.type("clX<Esc>")
        assert s.line(0) == "Xello"

    def test_cw_change_word(self):
        s = EditorSession("hello world")
        s.type("cwbye<Esc>")
        assert s.line(0).startswith("bye")

    def test_change_enters_insert(self):
        s = EditorSession("hello")
        s.type("c")  # operator pending
        s.type("l")  # cl
        assert s.mode() == Mode.INSERT


class TestVisualBlockOperators:
    def test_visual_block_yank_stores_block_register(self):
        s = EditorSession("alpha\nbeta\ngamma")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("y")

        text, kind = s.registers.get('"')
        assert kind == "block"
        assert text == "lph\neta\namm"

    def test_visual_block_delete_removes_same_columns_per_line(self):
        s = EditorSession("alpha\nbeta\ngamma")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("d")

        assert s.text() == "aa\nb\nga"
        assert s.mode() == Mode.NORMAL

    def test_visual_block_delete_stores_block_for_later_put(self):
        s = EditorSession("alpha\nbeta\ngamma")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("d")

        text, kind = s.registers.get('"')
        assert kind == "block"
        assert text == "lph\neta\namm"

    def test_visual_block_delete_then_put_pastes_deleted_block(self):
        s = EditorSession("alpha\nbeta\ngamma")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("d")
        s.engine.set_cursor(0, 0)
        s.window.cursor.move_to(0, 0)
        s.type("p")

        assert s.text() == "alpha\nbeta\ngamma"

    def test_visual_block_change_deletes_block_and_enters_insert(self):
        s = EditorSession("alpha\nbeta\ngamma")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("c")

        assert s.text() == "aa\nb\nga"
        assert s.mode() == Mode.INSERT

    def test_visual_block_I_replays_insert_across_rows(self):
        s = EditorSession("alpha\nbeta\ngamma")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("IZZ<Esc>")

        assert s.text() == "aZZlpha\nbZZeta\ngZZamma"
        assert s.mode() == Mode.NORMAL

    def test_visual_block_A_pads_short_lines_and_replays_insert(self):
        s = EditorSession("abcd\nx\ndefg")
        s.engine.set_cursor(0, 0)
        s.window.cursor.move_to(0, 0)
        s.type("<C-v>")
        s.engine.set_cursor(2, 1)
        s.window.cursor.move_to(2, 1)

        s.type("A!<Esc>")

        assert s.text() == "ab!cd\nx !\nde!fg"
        assert s.mode() == Mode.NORMAL

    def test_visual_block_insert_is_single_undo_step(self):
        original = "alpha\nbeta\ngamma"
        s = EditorSession(original)
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("IZZ<Esc>")
        s.type("u")

        assert s.text() == original

    def test_visual_block_insert_dot_repeat_reapplies_block_text(self):
        s = EditorSession("alpha\nbeta\ngamma\ndelta")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(1, 3)
        s.window.cursor.move_to(1, 3)

        s.type("IZZ<Esc>")
        s.engine.set_cursor(2, 1)
        s.window.cursor.move_to(2, 1)
        s.type(".")

        assert s.text() == "aZZlpha\nbZZeta\ngZZamma\ndZZelta"

    def test_visual_block_append_dot_repeat_reapplies_block_text(self):
        s = EditorSession("abcd\nxy\nijkl\nmn")
        s.engine.set_cursor(0, 0)
        s.window.cursor.move_to(0, 0)
        s.type("<C-v>")
        s.engine.set_cursor(1, 1)
        s.window.cursor.move_to(1, 1)

        s.type("A!<Esc>")
        s.engine.set_cursor(2, 2)
        s.window.cursor.move_to(2, 2)
        s.type(".")

        assert s.text() == "ab!cd\nxy!\nij!kl\nmn!"

    def test_visual_block_replace_replaces_each_intersection_char(self):
        s = EditorSession("alpha\nbeta\ngamma")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("rZ")

        assert s.text() == "aZZZa\nbZZZ\ngZZZa"
        assert s.mode() == Mode.NORMAL

    def test_visual_block_tilde_toggles_case_per_row(self):
        s = EditorSession("aBcDe\nFgHi\njKlMn")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("~")

        assert s.text() == "abCde\nFGhI\njkLmn"

    def test_visual_block_gu_lowers_case_per_row(self):
        s = EditorSession("aBcDe\nFgHi\njKlMn")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("gu")

        assert s.text() == "abcde\nFghi\njklmn"

    def test_visual_block_gU_uppers_case_per_row(self):
        s = EditorSession("aBcDe\nFgHi\njKlMn")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("gU")

        assert s.text() == "aBCDe\nFGHI\njKLMn"

    def test_visual_block_case_change_is_single_undo_step(self):
        original = "aBcDe\nFgHi\njKlMn"
        s = EditorSession(original)
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("gU")
        s.type("u")

        assert s.text() == original

    def test_visual_block_indent_shifts_all_covered_lines(self):
        s = EditorSession("one\ntwo\nthree")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type(">")

        assert s.line(0).startswith("    one") or s.line(0).startswith("\tone")
        assert s.line(1).startswith("    two") or s.line(1).startswith("\ttwo")
        assert s.line(2).startswith("    three") or s.line(2).startswith("\tthree")
        assert s.mode() == Mode.VISUAL_BLOCK

    def test_visual_block_outdent_shifts_all_covered_lines(self):
        s = EditorSession("    one\n    two\n    three")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("<")

        assert s.line(0) == "one"
        assert s.line(1) == "two"
        assert s.line(2) == "three"
        assert s.mode() == Mode.VISUAL_BLOCK

    def test_visual_block_delete_dot_repeat_reapplies_same_block_at_cursor(self):
        s = EditorSession("alpha\nbeta\ngamma")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("d")
        s.type(".")

        assert s.text() == "a\nb\ng"

    def test_visual_block_paste_dot_repeat_reapplies_block_register(self):
        s = EditorSession("one\ntwo\nthree\nfour")
        s.registers.set('"', "BC\nDE", "block")

        s.type("p")
        s.engine.set_cursor(2, 0)
        s.window.cursor.move_to(2, 0)
        s.type(".")

        assert s.text() == "oBCne\ntDEwo\ntBChree\nfDEour"

    def test_visual_block_O_switches_to_other_corner_same_row(self):
        s = EditorSession("alpha\nbeta\ngamma")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("O")

        assert s.cursor() == (2, 1)

    def test_gv_reselects_last_visual_block_after_operation(self):
        s = EditorSession("alpha\nbeta\ngamma")
        s.engine.set_cursor(0, 1)
        s.window.cursor.move_to(0, 1)
        s.type("<C-v>")
        s.engine.set_cursor(2, 3)
        s.window.cursor.move_to(2, 3)

        s.type("d")
        s.type("gv")

        assert s.mode() == Mode.VISUAL_BLOCK
        assert s.cursor() == (2, 1)
        assert s.engine.visual_selection_regions() == [(0, 1, 0, 2), (1, 1, 1, 2), (2, 1, 2, 2)]


# ---------------------------------------------------------------------------
# Indent operators
# ---------------------------------------------------------------------------


class TestIndentOperator:
    def test_double_greater_indents(self):
        s = EditorSession("hello")
        s.type(">>")
        assert s.line(0).startswith(" " * 4) or s.line(0).startswith("\t")

    def test_double_less_dedents(self):
        s = EditorSession("    hello")
        s.type("<<")
        assert not s.line(0).startswith("    ")


# ---------------------------------------------------------------------------
# Dot repeat with motions
# ---------------------------------------------------------------------------


class TestDotRepeatMotion:
    def test_dot_repeats_dl(self):
        s = EditorSession("hello")
        s.type("dl")  # delete 'h' -> "ello"
        assert s.line(0) == "ello"
        s.type(".")  # repeat -> "llo"
        assert s.line(0) == "llo"
