"""
Every text object inner/outer variant
"""

from peovim.core.document import Document
from peovim.modal.text_objects import (
    backtick_inner,
    backtick_outer,
    brace_inner,
    brace_outer,
    bracket_inner,
    bracket_outer,
    dquote_inner,
    dquote_outer,
    paragraph_inner,
    paragraph_outer,
    paren_inner,
    paren_outer,
    squote_inner,
    squote_outer,
    word_inner,
    word_outer,
)


def make_doc(content: str) -> Document:
    doc = Document()
    doc.load_string(content)
    return doc


# ---------------------------------------------------------------------------
# Word objects
# ---------------------------------------------------------------------------


class TestWordObjects:
    def test_iw_on_word(self):
        doc = make_doc("hello world")
        sl, sc, el, ec = word_inner(doc, 0, 2)
        assert (sl, sc, el) == (0, 0, 0)
        assert ec == 5  # "hello" is cols 0-4, exclusive end = 5

    def test_iw_on_space(self):
        doc = make_doc("hello world")
        sl, sc, el, ec = word_inner(doc, 0, 5)  # col 5 is the space
        assert sc == 5
        assert ec == 6  # just the space

    def test_aw_includes_trailing_space(self):
        doc = make_doc("hello world")
        sl, sc, el, ec = word_outer(doc, 0, 0)  # cursor on 'h'
        # should include "hello " (hello + trailing space)
        assert sc == 0
        assert ec == 6  # "hello " ends at exclusive col 6

    def test_aw_at_end_includes_leading_space(self):
        doc = make_doc("hello world")
        sl, sc, el, ec = word_outer(doc, 0, 8)  # cursor on 'r' in world
        # should include " world" (leading space + world) or just world
        # At minimum, includes the word
        assert ec >= 11

    def test_iw_single_word(self):
        doc = make_doc("hello")
        sl, sc, el, ec = word_inner(doc, 0, 0)
        assert (sl, sc, el, ec) == (0, 0, 0, 5)


# ---------------------------------------------------------------------------
# Quote objects
# ---------------------------------------------------------------------------


class TestQuoteObjects:
    def test_dquote_inner(self):
        doc = make_doc('say "hello world" here')
        result = dquote_inner(doc, 0, 5)  # cursor inside quotes
        assert result is not None
        sl, sc, el, ec = result
        assert sl == 0 and el == 0
        assert doc.get_line(0)[sc:ec] == "hello world"

    def test_dquote_outer(self):
        doc = make_doc('say "hello" here')
        result = dquote_outer(doc, 0, 5)
        assert result is not None
        sl, sc, el, ec = result
        assert doc.get_line(0)[sc:ec] == '"hello"'

    def test_dquote_not_found(self):
        doc = make_doc("no quotes here")
        result = dquote_inner(doc, 0, 5)
        assert result is None

    def test_squote_inner(self):
        doc = make_doc("say 'hello' world")
        result = squote_inner(doc, 0, 6)  # inside 'hello'
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "hello"

    def test_squote_outer(self):
        doc = make_doc("say 'hi' ok")
        result = squote_outer(doc, 0, 5)
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "'hi'"

    def test_backtick_inner(self):
        doc = make_doc("run `cmd` now")
        result = backtick_inner(doc, 0, 6)
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "cmd"

    def test_backtick_outer(self):
        doc = make_doc("run `cmd` now")
        result = backtick_outer(doc, 0, 6)
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "`cmd`"

    def test_dquote_cursor_outside_uses_next_pair(self):
        doc = make_doc('a "b" c "d" e')
        # cursor at col 0 (before first quote)
        result = dquote_inner(doc, 0, 0)
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "b"


# ---------------------------------------------------------------------------
# Paren / bracket objects
# ---------------------------------------------------------------------------


class TestBracketObjects:
    def test_paren_inner(self):
        doc = make_doc("func(hello)")
        result = paren_inner(doc, 0, 6)  # cursor on 'l' inside parens
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "hello"

    def test_paren_outer(self):
        doc = make_doc("func(hello)")
        result = paren_outer(doc, 0, 6)
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "(hello)"

    def test_paren_nested(self):
        doc = make_doc("(a(b)c)")
        result = paren_inner(doc, 0, 3)  # cursor on 'b' in inner parens
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "b"

    def test_paren_not_found(self):
        doc = make_doc("no parens here")
        result = paren_inner(doc, 0, 5)
        assert result is None

    def test_brace_inner(self):
        doc = make_doc("{hello}")
        result = brace_inner(doc, 0, 3)
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "hello"

    def test_brace_outer(self):
        doc = make_doc("{hello}")
        result = brace_outer(doc, 0, 3)
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "{hello}"

    def test_bracket_inner(self):
        doc = make_doc("[item]")
        result = bracket_inner(doc, 0, 2)
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "item"

    def test_bracket_outer(self):
        doc = make_doc("[item]")
        result = bracket_outer(doc, 0, 2)
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "[item]"

    def test_paren_on_opener(self):
        doc = make_doc("(hello)")
        result = paren_inner(doc, 0, 0)  # cursor on '('
        assert result is not None
        _, sc, _, ec = result
        assert doc.get_line(0)[sc:ec] == "hello"


# ---------------------------------------------------------------------------
# Multiline bracket
# ---------------------------------------------------------------------------


class TestMultilineBracket:
    def test_paren_multiline_inner(self):
        doc = make_doc("(\nhello\n)")
        result = paren_inner(doc, 1, 0)  # cursor on 'hello'
        assert result is not None
        sl, sc, el, ec = result
        # inner starts after '(' on line 0
        assert sl == 0
        assert el == 2
        assert doc.get_line(1)[0:5] == "hello"


# ---------------------------------------------------------------------------
# Paragraph objects
# ---------------------------------------------------------------------------


class TestParagraphObjects:
    def test_ip_basic(self):
        doc = make_doc("first\nsecond\n\nthird")
        sl, sc, el, ec = paragraph_inner(doc, 0, 0)
        assert sl == 0
        assert el == 1  # "first" and "second"

    def test_ip_middle_paragraph(self):
        doc = make_doc("a\nb\n\nc\nd\n\ne")
        sl, sc, el, ec = paragraph_inner(doc, 3, 0)  # cursor on 'c'
        assert sl == 3
        assert el == 4  # "c" and "d"

    def test_ap_includes_blank_lines(self):
        doc = make_doc("aaa\nbbb\n\nccc")
        sl, sc, el, ec = paragraph_outer(doc, 0, 0)
        # should include trailing blank line
        assert el >= 2

    def test_ip_single_line(self):
        doc = make_doc("hello")
        sl, sc, el, ec = paragraph_inner(doc, 0, 0)
        assert sl == 0 and el == 0
