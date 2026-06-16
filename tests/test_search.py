"""
Tests for peovim.core.search (pure search utilities) and integration of
SetSearchPattern / SearchNext / SearchWordUnderCursor / ClearSearchHighlight
actions through the ActionDispatcher.
"""

from __future__ import annotations

import re

from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.search import (
    build_word_pattern,
    compile_pattern,
    search_all_in_line,
    search_next,
)
from peovim.core.window import Window
from peovim.modal.actions import (
    ClearSearchHighlight,
    SearchNext,
    SearchWordUnderCursor,
    SetSearchPattern,
)
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc(content: str) -> Document:
    doc = Document()
    doc.load_string(content)
    return doc


def _session(content: str = "", editor_state: EditorState | None = None):
    """Return (doc, window, dispatcher) wired together."""
    doc = _make_doc(content)
    window = Window(doc)
    registers = RegisterStore()
    engine = ModalEngine()
    engine.set_document(doc)
    engine.set_cursor(0, 0)
    engine.set_line_count(doc.line_count())
    disp = ActionDispatcher(engine, window, registers, editor_state=editor_state)
    return doc, window, disp


# ---------------------------------------------------------------------------
# compile_pattern
# ---------------------------------------------------------------------------


class TestCompilePattern:
    def test_basic_case_sensitive(self):
        p = compile_pattern("Hello")
        assert p.search("Hello world") is not None
        assert p.search("hello world") is None

    def test_ignorecase(self):
        p = compile_pattern("hello", ignorecase=True)
        assert p.search("HELLO") is not None
        assert p.search("Hello") is not None

    def test_smartcase_no_upper_uses_ignorecase(self):
        p = compile_pattern("hello", ignorecase=True, smartcase=True)
        assert p.search("HELLO") is not None

    def test_smartcase_with_upper_forces_case_sensitive(self):
        p = compile_pattern("Hello", ignorecase=True, smartcase=True)
        assert p.search("hello") is None
        assert p.search("Hello") is not None

    def test_smartcase_overrides_ignorecase(self):
        # Any uppercase in pattern → case-sensitive even with ignorecase=True
        p = compile_pattern("FOO", ignorecase=True, smartcase=True)
        assert p.search("foo") is None
        assert p.search("FOO") is not None

    def test_returns_re_pattern(self):
        p = compile_pattern("abc")
        assert isinstance(p, re.Pattern)


# ---------------------------------------------------------------------------
# build_word_pattern
# ---------------------------------------------------------------------------


class TestBuildWordPattern:
    def test_whole_word(self):
        pat = build_word_pattern("foo")
        assert pat == r"\bfoo\b"
        p = re.compile(pat)
        assert p.search("foo bar") is not None
        assert p.search("foobar") is None

    def test_no_word_boundary(self):
        pat = build_word_pattern("foo", whole_word=False)
        p = re.compile(pat)
        assert p.search("foobar") is not None

    def test_special_chars_escaped(self):
        pat = build_word_pattern("a.b", whole_word=False)
        p = re.compile(pat)
        # literal dot, not wildcard
        assert p.search("a.b") is not None
        assert p.search("axb") is None


# ---------------------------------------------------------------------------
# search_next — forward
# ---------------------------------------------------------------------------


class TestSearchNextForward:
    def test_finds_match_on_same_line_after_cursor(self):
        doc = _make_doc("foo foo foo")
        p = re.compile("foo")
        result = search_next(doc, p, 0, 0, "forward")
        assert result == (0, 4)  # second "foo" starts at col 4

    def test_finds_match_on_next_line(self):
        doc = _make_doc("hello\nfoo bar")
        p = re.compile("foo")
        result = search_next(doc, p, 0, 0, "forward")
        assert result == (1, 0)

    def test_wraps_around_to_beginning(self):
        doc = _make_doc("foo\nno match")
        p = re.compile("foo")
        # Start past the only match; should wrap
        result = search_next(doc, p, 0, 0, "forward", wrapscan=True)
        assert result == (0, 0)

    def test_no_wrap_returns_none(self):
        doc = _make_doc("hello\nfoo")
        p = re.compile("foo")
        # cursor already past the match on line 1
        result = search_next(doc, p, 1, 3, "forward", wrapscan=False)
        assert result is None

    def test_returns_none_no_match(self):
        doc = _make_doc("hello\nworld")
        p = re.compile("xyz")
        assert search_next(doc, p, 0, 0, "forward") is None

    def test_wraps_to_first_line_match(self):
        doc = _make_doc("foo\nbar\nbaz")
        p = re.compile("foo")
        # Start past "foo"; it should wrap and find it on line 0
        result = search_next(doc, p, 2, 0, "forward", wrapscan=True)
        assert result == (0, 0)

    def test_multiline_finds_correct_line(self):
        doc = _make_doc("aaa\nbbb\nccc\nddd")
        p = re.compile("ccc")
        result = search_next(doc, p, 0, 0, "forward")
        assert result == (2, 0)


# ---------------------------------------------------------------------------
# search_next — backward
# ---------------------------------------------------------------------------


class TestSearchNextBackward:
    def test_finds_match_before_cursor_on_same_line(self):
        doc = _make_doc("foo foo foo")
        p = re.compile("foo")
        result = search_next(doc, p, 0, 8, "backward")
        assert result == (0, 4)

    def test_finds_match_on_previous_line(self):
        doc = _make_doc("foo bar\nhello")
        p = re.compile("foo")
        result = search_next(doc, p, 1, 0, "backward")
        assert result == (0, 0)

    def test_wraps_to_last_line(self):
        doc = _make_doc("bar\nfoo")
        p = re.compile("foo")
        result = search_next(doc, p, 0, 0, "backward", wrapscan=True)
        assert result == (1, 0)

    def test_no_wrap_returns_none(self):
        doc = _make_doc("foo\nbar")
        p = re.compile("foo")
        # cursor at start of line 0 — nothing before
        result = search_next(doc, p, 0, 0, "backward", wrapscan=False)
        assert result is None

    def test_returns_none_no_match(self):
        doc = _make_doc("hello\nworld")
        p = re.compile("xyz")
        assert search_next(doc, p, 1, 5, "backward") is None


# ---------------------------------------------------------------------------
# search_all_in_line
# ---------------------------------------------------------------------------


class TestSearchAllInLine:
    def test_finds_all_matches(self):
        p = re.compile("ab")
        result = search_all_in_line("ab cd ab ef ab", p)
        assert result == [(0, 2), (6, 8), (12, 14)]

    def test_no_matches_returns_empty(self):
        p = re.compile("xyz")
        assert search_all_in_line("hello world", p) == []

    def test_single_match(self):
        p = re.compile("world")
        result = search_all_in_line("hello world", p)
        assert result == [(6, 11)]

    def test_overlapping_not_returned_twice(self):
        # re.finditer does not overlap by default
        p = re.compile("aa")
        result = search_all_in_line("aaaa", p)
        assert result == [(0, 2), (2, 4)]


# ---------------------------------------------------------------------------
# EditorState / SearchState
# ---------------------------------------------------------------------------


class TestSearchState:
    def test_set_pattern_compiles(self):
        es = EditorState()
        es.search.set_pattern("foo", "forward")
        assert es.search.compiled is not None
        assert es.search.hlsearch_active is True
        assert es.search.pattern == "foo"

    def test_set_empty_pattern_clears(self):
        es = EditorState()
        es.search.set_pattern("foo", "forward")
        es.search.set_pattern("", "forward")
        assert es.search.compiled is None
        assert es.search.hlsearch_active is False

    def test_direction_stored(self):
        es = EditorState()
        es.search.set_pattern("x", "backward")
        assert es.search.direction == "backward"


# ---------------------------------------------------------------------------
# Integration: SetSearchPattern via dispatcher
# ---------------------------------------------------------------------------


class TestSetSearchPatternAction:
    def test_set_search_jumps_to_first_match(self):
        es = EditorState()
        doc, window, disp = _session("hello\nworld\nfoo", es)
        disp.dispatch([SetSearchPattern("world", "forward")])
        assert window.cursor.line == 1
        assert window.cursor.col == 0

    def test_set_search_updates_editor_state(self):
        es = EditorState()
        doc, window, disp = _session("hello world", es)
        disp.dispatch([SetSearchPattern("world", "forward")])
        assert es.search.pattern == "world"
        assert es.search.hlsearch_active is True

    def test_set_search_no_match_does_not_move(self):
        es = EditorState()
        doc, window, disp = _session("hello", es)
        disp.dispatch([SetSearchPattern("xyz", "forward")])
        assert window.cursor.line == 0
        assert window.cursor.col == 0

    def test_set_search_without_editor_state_no_error(self):
        doc, window, disp = _session("hello world")
        # Should not raise
        disp.dispatch([SetSearchPattern("world", "forward")])


# ---------------------------------------------------------------------------
# Integration: SearchNext via dispatcher
# ---------------------------------------------------------------------------


class TestSearchNextAction:
    def test_n_jumps_to_next_match(self):
        es = EditorState()
        doc, window, disp = _session("foo bar foo baz foo", es)
        disp.dispatch([SetSearchPattern("foo", "forward")])
        # cursor is now at col 4 (second "foo")
        first_pos = (window.cursor.line, window.cursor.col)
        disp.dispatch([SearchNext(reverse=False, count=1)])
        second_pos = (window.cursor.line, window.cursor.col)
        assert second_pos != first_pos

    def test_N_reverses_direction(self):
        es = EditorState()
        doc, window, disp = _session("foo\nfoo\nfoo", es)
        disp.dispatch([SetSearchPattern("foo", "forward")])
        # Move forward once
        disp.dispatch([SearchNext(reverse=False)])
        after_n = (window.cursor.line, window.cursor.col)
        # Reverse (N)
        disp.dispatch([SearchNext(reverse=True)])
        after_N = (window.cursor.line, window.cursor.col)
        assert after_N != after_n

    def test_search_next_without_editor_state_no_error(self):
        doc, window, disp = _session("foo bar")
        disp.dispatch([SearchNext(reverse=False)])

    def test_search_next_count(self):
        es = EditorState()
        doc, window, disp = _session("a\na\na\na\na", es)
        disp.dispatch([SetSearchPattern("a", "forward")])
        start_line = window.cursor.line
        disp.dispatch([SearchNext(reverse=False, count=2)])
        assert window.cursor.line > start_line


# ---------------------------------------------------------------------------
# Integration: SearchWordUnderCursor via dispatcher
# ---------------------------------------------------------------------------


class TestSearchWordUnderCursorAction:
    def test_star_finds_word(self):
        es = EditorState()
        doc, window, disp = _session("foo bar foo", es)
        # cursor at col 0 → word "foo"
        disp.dispatch([SearchWordUnderCursor(whole_word=True, reverse=False)])
        assert es.search.pattern != ""
        assert "foo" in es.search.pattern
        assert window.cursor.col == 8  # second "foo" at col 8

    def test_hash_finds_word_backward(self):
        es = EditorState()
        doc, window, disp = _session("foo bar foo", es)
        # Move to second "foo"
        window.cursor.move_to(0, 8)
        disp.dispatch([SearchWordUnderCursor(whole_word=True, reverse=True)])
        # should jump back to first "foo"
        assert window.cursor.col == 0

    def test_no_word_at_cursor_is_harmless(self):
        es = EditorState()
        doc, window, disp = _session("   ", es)
        # No crash even if no word under cursor
        disp.dispatch([SearchWordUnderCursor(whole_word=True, reverse=False)])


# ---------------------------------------------------------------------------
# Integration: ClearSearchHighlight
# ---------------------------------------------------------------------------


class TestClearSearchHighlight:
    def test_clear_turns_off_hlsearch(self):
        es = EditorState()
        es.search.set_pattern("foo", "forward")
        assert es.search.hlsearch_active is True
        doc, window, disp = _session("foo bar", es)
        disp.dispatch([ClearSearchHighlight()])
        assert es.search.hlsearch_active is False

    def test_clear_without_editor_state_no_error(self):
        doc, window, disp = _session("foo bar")
        disp.dispatch([ClearSearchHighlight()])


# ---------------------------------------------------------------------------
# Integration: :nohlsearch command
# ---------------------------------------------------------------------------


class TestNohlsearchCommand:
    def test_nohlsearch_clears_highlight(self):
        from peovim.commands.builtin import register_builtins
        from peovim.commands.parser import parse_ex_command
        from peovim.commands.registry import CommandRegistry

        es = EditorState()
        es.search.set_pattern("foo", "forward")
        assert es.search.hlsearch_active is True

        # Simulate what the dispatcher does when running :nohlsearch
        registry = CommandRegistry()
        register_builtins(registry)
        parsed = parse_ex_command("nohlsearch")
        handler = registry.get(parsed.cmd)
        assert handler is not None

        class FakeCtx:
            editor_state = es

        handler(parsed, FakeCtx())
        assert es.search.hlsearch_active is False

    def test_noh_alias_works(self):
        from peovim.commands.builtin import register_builtins
        from peovim.commands.parser import parse_ex_command
        from peovim.commands.registry import CommandRegistry

        es = EditorState()
        es.search.set_pattern("bar", "backward")
        assert es.search.hlsearch_active is True

        registry = CommandRegistry()
        register_builtins(registry)
        parsed = parse_ex_command("noh")
        handler = registry.get(parsed.cmd)
        assert handler is not None

        class FakeCtx:
            editor_state = es

        handler(parsed, FakeCtx())
        assert es.search.hlsearch_active is False
