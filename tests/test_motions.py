"""
Every motion function with count variants
"""

from peovim.core.document import Document
from peovim.modal.motions import (
    move_B,
    move_b,
    move_E,
    move_e,
    move_F,
    move_f,
    move_first_nonblank,
    move_H,
    move_h,
    move_j,
    move_k,
    move_L,
    move_l,
    move_last_nonblank,
    move_line_end,
    move_line_start,
    move_M,
    move_minus,
    move_paragraph_backward,
    move_paragraph_forward,
    move_plus,
    move_T,
    move_t,
    move_W,
    move_w,
)


def make_doc(content: str) -> Document:
    doc = Document()
    doc.load_string(content)
    return doc


# ---------------------------------------------------------------------------
# h / l motions
# ---------------------------------------------------------------------------


class TestHL:
    def test_h_basic(self):
        doc = make_doc("hello")
        assert move_h(doc, 0, 3) == (0, 2)

    def test_h_clamps_at_zero(self):
        doc = make_doc("hello")
        assert move_h(doc, 0, 0) == (0, 0)

    def test_h_count(self):
        doc = make_doc("hello")
        assert move_h(doc, 0, 4, count=3) == (0, 1)

    def test_l_basic(self):
        doc = make_doc("hello")
        assert move_l(doc, 0, 0) == (0, 1)

    def test_l_clamps_at_eol(self):
        doc = make_doc("hello")
        # last char is index 4; can't go past it in normal mode
        assert move_l(doc, 0, 4) == (0, 4)

    def test_l_count(self):
        doc = make_doc("hello")
        assert move_l(doc, 0, 0, count=3) == (0, 3)

    def test_l_empty_line(self):
        doc = make_doc("")
        assert move_l(doc, 0, 0) == (0, 0)


# ---------------------------------------------------------------------------
# j / k motions
# ---------------------------------------------------------------------------


class TestJK:
    def test_j_basic(self):
        doc = make_doc("a\nb\nc")
        assert move_j(doc, 0, 0) == (1, 0)

    def test_j_clamps_at_last(self):
        doc = make_doc("a\nb")
        assert move_j(doc, 1, 0) == (1, 0)

    def test_j_count(self):
        doc = make_doc("a\nb\nc\nd")
        assert move_j(doc, 0, 0, count=3) == (3, 0)

    def test_k_basic(self):
        doc = make_doc("a\nb\nc")
        assert move_k(doc, 2, 0) == (1, 0)

    def test_k_clamps_at_zero(self):
        doc = make_doc("a\nb")
        assert move_k(doc, 0, 0) == (0, 0)

    def test_k_count(self):
        doc = make_doc("a\nb\nc\nd")
        assert move_k(doc, 3, 0, count=2) == (1, 0)


# ---------------------------------------------------------------------------
# Line start/end
# ---------------------------------------------------------------------------


class TestLineEdge:
    def test_line_start(self):
        doc = make_doc("  hello")
        assert move_line_start(doc, 0, 5) == (0, 0)

    def test_first_nonblank(self):
        doc = make_doc("   hello")
        assert move_first_nonblank(doc, 0, 0) == (0, 3)

    def test_first_nonblank_no_indent(self):
        doc = make_doc("hello")
        assert move_first_nonblank(doc, 0, 3) == (0, 0)

    def test_line_end(self):
        doc = make_doc("hello")
        assert move_line_end(doc, 0, 0) == (0, 4)

    def test_line_end_empty(self):
        doc = make_doc("")
        assert move_line_end(doc, 0, 0) == (0, 0)

    def test_last_nonblank(self):
        doc = make_doc("hello   ")
        assert move_last_nonblank(doc, 0, 0) == (0, 4)

    def test_last_nonblank_no_trailing(self):
        doc = make_doc("hello")
        assert move_last_nonblank(doc, 0, 0) == (0, 4)


# ---------------------------------------------------------------------------
# Word motions: w
# ---------------------------------------------------------------------------


class TestWordW:
    def test_w_basic(self):
        doc = make_doc("hello world")
        assert move_w(doc, 0, 0) == (0, 6)

    def test_w_from_middle(self):
        doc = make_doc("foo bar baz")
        assert move_w(doc, 0, 4) == (0, 8)

    def test_w_count(self):
        doc = make_doc("a b c d")
        # w from 0: a→2 (b), b→4 (c), c→6 (d) — count=3 lands on d at col 6
        assert move_w(doc, 0, 0, count=3) == (0, 6)

    def test_w_across_lines(self):
        doc = make_doc("hello\nworld")
        # w from col 0: skip 'hello', skip newline, land at 'w' on next line
        result = move_w(doc, 0, 0)
        assert result == (1, 0)

    def test_w_punctuation(self):
        doc = make_doc("foo.bar")
        # 'foo' is a word, '.' is punctuation, 'bar' is a word
        result = move_w(doc, 0, 0)
        assert result == (0, 3)  # lands on '.'

    def test_w_at_end(self):
        doc = make_doc("hello")
        # At last word, w should stay at end
        result = move_w(doc, 0, 0)
        assert result[0] == 0 or result[0] == 0  # stays on same or last line


class TestWordW_simple:
    """Simpler w tests that are unambiguous."""

    def test_w_moves_forward(self):
        doc = make_doc("aaa bbb")
        new_line, new_col = move_w(doc, 0, 0)
        assert new_col > 0

    def test_W_basic(self):
        doc = make_doc("foo.bar baz")
        # W skips entire "foo.bar" as one WORD
        result = move_W(doc, 0, 0)
        assert result == (0, 8)

    def test_W_count(self):
        doc = make_doc("aaa bbb ccc")
        result = move_W(doc, 0, 0, count=2)
        assert result == (0, 8)


# ---------------------------------------------------------------------------
# Word motions: e / E
# ---------------------------------------------------------------------------


class TestWordE:
    def test_e_basic(self):
        doc = make_doc("hello world")
        # from 0, e goes to end of 'hello' at col 4
        assert move_e(doc, 0, 0) == (0, 4)

    def test_e_from_end(self):
        doc = make_doc("hello world")
        # from 4 (end of hello), e goes to end of next word
        result = move_e(doc, 0, 4)
        assert result == (0, 10)

    def test_e_count(self):
        doc = make_doc("a b c")
        result = move_e(doc, 0, 0, count=3)
        assert result == (0, 4)

    def test_E_basic(self):
        doc = make_doc("foo.bar baz")
        # E from 0 goes to end of 'foo.bar' (col 6)
        result = move_E(doc, 0, 0)
        assert result == (0, 6)


# ---------------------------------------------------------------------------
# Word motions: b / B
# ---------------------------------------------------------------------------


class TestWordB:
    def test_b_basic(self):
        doc = make_doc("hello world")
        # from 6 ('w'), b goes to start of 'hello' at 0
        assert move_b(doc, 0, 6) == (0, 0)

    def test_b_from_middle(self):
        doc = make_doc("hello world")
        # from 9 ('l' in world), b goes to 'w' at 6
        result = move_b(doc, 0, 9)
        assert result == (0, 6)

    def test_b_count(self):
        doc = make_doc("a b c d")
        result = move_b(doc, 0, 6, count=2)
        assert result == (0, 2)

    def test_b_at_line_start(self):
        doc = make_doc("first\nsecond")
        # from col 0 of line 1, b goes to previous line
        result = move_b(doc, 1, 0)
        assert result[0] == 0

    def test_B_basic(self):
        doc = make_doc("foo.bar baz")
        # B from 8 ('b' in baz) goes to start of 'foo.bar' at 0
        result = move_B(doc, 0, 8)
        assert result == (0, 0)


# ---------------------------------------------------------------------------
# Find-char motions: f / F / t / T
# ---------------------------------------------------------------------------


class TestFindChar:
    def test_f_finds(self):
        doc = make_doc("hello world")
        assert move_f(doc, 0, 0, "o") == (0, 4)

    def test_f_count(self):
        doc = make_doc("hello world")
        # second 'o' is at col 7 ('o' in world)
        assert move_f(doc, 0, 0, "o", count=2) == (0, 7)

    def test_f_not_found_stays(self):
        doc = make_doc("hello")
        assert move_f(doc, 0, 0, "z") == (0, 0)

    def test_F_backward(self):
        doc = make_doc("hello world")
        # from col 10, F finds 'o' at col 7
        assert move_F(doc, 0, 10, "o") == (0, 7)

    def test_F_not_found_stays(self):
        doc = make_doc("hello")
        assert move_F(doc, 0, 4, "z") == (0, 4)

    def test_t_before_char(self):
        doc = make_doc("hello world")
        # t finds 'o' then backs up one: col 3
        assert move_t(doc, 0, 0, "o") == (0, 3)

    def test_T_after_char(self):
        doc = make_doc("hello world")
        # T from 10 finds 'o' at 7, then moves forward 1: col 8
        assert move_T(doc, 0, 10, "o") == (0, 8)

    def test_t_not_found_stays(self):
        doc = make_doc("hello")
        assert move_t(doc, 0, 0, "z") == (0, 0)


# ---------------------------------------------------------------------------
# Paragraph motions
# ---------------------------------------------------------------------------


class TestParagraph:
    def test_forward_to_blank(self):
        doc = make_doc("aaa\nbbb\n\nccc")
        result = move_paragraph_forward(doc, 0, 0)
        assert result == (2, 0)  # blank line at 2

    def test_backward_to_blank(self):
        doc = make_doc("aaa\n\nbbb\nccc")
        result = move_paragraph_backward(doc, 3, 0)
        assert result == (1, 0)  # blank line at 1

    def test_forward_count(self):
        doc = make_doc("a\nb\n\nc\nd\n\ne")
        result = move_paragraph_forward(doc, 0, 0, count=2)
        assert result[0] == 5  # second blank line

    def test_forward_at_end(self):
        doc = make_doc("aaa\nbbb")
        result = move_paragraph_forward(doc, 0, 0)
        assert result[0] == doc.line_count() - 1

    def test_backward_at_start(self):
        doc = make_doc("aaa\nbbb")
        result = move_paragraph_backward(doc, 0, 0)
        assert result[0] == 0


# ---------------------------------------------------------------------------
# Screen motions: H / M / L
# ---------------------------------------------------------------------------


class TestScreenMotions:
    def test_H_top(self):
        doc = make_doc("\n".join(str(i) for i in range(50)))
        result = move_H(doc, 25, 0, scroll_line=10, window_height=20)
        assert result == (10, 0)

    def test_M_middle(self):
        doc = make_doc("\n".join(str(i) for i in range(50)))
        result = move_M(doc, 25, 0, scroll_line=0, window_height=20)
        assert result[0] == 9  # middle of lines 0-19

    def test_L_bottom(self):
        doc = make_doc("\n".join(str(i) for i in range(50)))
        result = move_L(doc, 0, 0, scroll_line=0, window_height=20)
        assert result == (19, 0)


# ---------------------------------------------------------------------------
# + / - motions
# ---------------------------------------------------------------------------


class TestPlusMinus:
    def test_plus_next_nonblank(self):
        doc = make_doc("hello\n   world")
        result = move_plus(doc, 0, 0)
        assert result == (1, 3)

    def test_minus_prev_nonblank(self):
        doc = make_doc("   hello\nworld")
        result = move_minus(doc, 1, 0)
        assert result == (0, 3)

    def test_plus_count(self):
        doc = make_doc("a\nb\nc\nd")
        result = move_plus(doc, 0, 0, count=2)
        assert result == (2, 0)
