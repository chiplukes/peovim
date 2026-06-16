"""
PieceTable unit tests: insert, delete, undo, line index, edge cases.

All tests are synchronous and need no real terminal.
"""

import pytest

from peovim.core.buffer import Edit, PieceTable

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_table(content: str = "") -> PieceTable:
    t = PieceTable()
    t.load(content.encode())
    return t


def table_text(t: PieceTable) -> str:
    return t.get_bytes(0, t.total_bytes()).decode()


# ---------------------------------------------------------------------------
# Construction / load / clear
# ---------------------------------------------------------------------------


class TestInit:
    def test_empty_clear(self):
        t = PieceTable()
        t.clear()
        assert t.total_bytes() == 0
        assert t.line_count() == 1
        assert t.version == 0

    def test_load_empty(self):
        t = make_table("")
        assert t.total_bytes() == 0
        assert t.line_count() == 1

    def test_load_simple(self):
        t = make_table("hello")
        assert t.total_bytes() == 5
        assert t.line_count() == 1
        assert table_text(t) == "hello"

    def test_load_multiline(self):
        t = make_table("a\nb\nc")
        assert t.total_bytes() == 5
        assert t.line_count() == 3

    def test_load_trailing_newline(self):
        # "hello\n" → 2 logical lines (line 0="hello", line 1="")
        t = make_table("hello\n")
        assert t.line_count() == 2

    def test_version_zero_after_load(self):
        t = make_table("hello")
        assert t.version == 0


# ---------------------------------------------------------------------------
# get_bytes
# ---------------------------------------------------------------------------


class TestGetBytes:
    def test_whole(self):
        t = make_table("abcde")
        assert t.get_bytes(0, 5) == b"abcde"

    def test_slice(self):
        t = make_table("abcde")
        assert t.get_bytes(1, 4) == b"bcd"

    def test_empty_range(self):
        t = make_table("abcde")
        assert t.get_bytes(2, 2) == b""

    def test_across_pieces(self):
        t = make_table("hello")
        t.insert(5, b" world")
        # pieces: [original 'hello'] [add ' world']
        assert t.get_bytes(3, 8) == b"lo wo"


# ---------------------------------------------------------------------------
# get_line_bytes
# ---------------------------------------------------------------------------


class TestGetLineBytes:
    def test_single_line_no_newline(self):
        t = make_table("hello")
        assert t.get_line_bytes(0) == b"hello"

    def test_single_line_with_newline(self):
        t = make_table("hello\n")
        assert t.get_line_bytes(0) == b"hello"
        assert t.get_line_bytes(1) == b""

    def test_multiline(self):
        t = make_table("abc\ndef\nghi")
        assert t.get_line_bytes(0) == b"abc"
        assert t.get_line_bytes(1) == b"def"
        assert t.get_line_bytes(2) == b"ghi"

    def test_out_of_range(self):
        t = make_table("hello")
        with pytest.raises((AssertionError, IndexError)):
            t.get_line_bytes(5)


# ---------------------------------------------------------------------------
# line_offsets / byte_offset_of / line_col_of
# ---------------------------------------------------------------------------


class TestLineIndex:
    def test_empty(self):
        t = make_table("")
        assert t.byte_offset_of(0, 0) == 0
        assert t.line_col_of(0) == (0, 0)

    def test_single_line(self):
        t = make_table("hello")
        assert t.byte_offset_of(0, 3) == 3
        assert t.line_col_of(3) == (0, 3)

    def test_multiline_offsets(self):
        # "abc\ndef\n" → line 0 at 0, line 1 at 4, line 2 at 8
        t = make_table("abc\ndef\n")
        assert t.byte_offset_of(0, 0) == 0
        assert t.byte_offset_of(1, 0) == 4
        assert t.byte_offset_of(2, 0) == 8

    def test_round_trip(self):
        t = make_table("abc\ndef\nghi")
        for line in range(3):
            for col in range(3):
                pos = t.byte_offset_of(line, col)
                assert t.line_col_of(pos) == (line, col)

    def test_line_col_of_end(self):
        t = make_table("abc")
        # pos == total_bytes() is valid
        assert t.line_col_of(3) == (0, 3)


# ---------------------------------------------------------------------------
# insert
# ---------------------------------------------------------------------------


class TestInsert:
    def test_insert_at_start(self):
        t = make_table("world")
        t.insert(0, b"hello ")
        assert table_text(t) == "hello world"

    def test_insert_at_end(self):
        t = make_table("hello")
        t.insert(5, b" world")
        assert table_text(t) == "hello world"

    def test_insert_in_middle(self):
        t = make_table("helo")
        t.insert(3, b"l")
        assert table_text(t) == "hello"

    def test_insert_newline(self):
        t = make_table("ab")
        t.insert(1, b"\n")
        assert t.line_count() == 2
        assert t.get_line_bytes(0) == b"a"
        assert t.get_line_bytes(1) == b"b"

    def test_insert_increments_version(self):
        t = make_table("hi")
        t.insert(2, b"!")
        assert t.version == 1

    def test_insert_returns_edit(self):
        t = make_table("hi")
        edit = t.insert(2, b"!")
        assert isinstance(edit, Edit)
        assert edit.kind == "insert"
        assert edit.pos == 2
        assert edit.text == b"!"

    def test_multiple_inserts(self):
        t = make_table("")
        t.insert(0, b"a")
        t.insert(1, b"b")
        t.insert(2, b"c")
        assert table_text(t) == "abc"
        assert t.version == 3

    def test_insert_into_multiline(self):
        t = make_table("line1\nline2")
        t.insert(5, b"\nnewline")
        assert t.line_count() == 3
        assert t.get_line_bytes(1) == b"newline"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_from_start(self):
        t = make_table("hello world")
        t.delete(0, 6)
        assert table_text(t) == "world"

    def test_delete_from_end(self):
        t = make_table("hello world")
        t.delete(5, 6)
        assert table_text(t) == "hello"

    def test_delete_middle(self):
        t = make_table("hello world")
        t.delete(5, 1)  # delete space
        assert table_text(t) == "helloworld"

    def test_delete_all(self):
        t = make_table("hello")
        t.delete(0, 5)
        assert t.total_bytes() == 0
        assert t.line_count() == 1

    def test_delete_newline(self):
        t = make_table("a\nb")
        t.delete(1, 1)  # delete \n
        assert t.line_count() == 1
        assert table_text(t) == "ab"

    def test_delete_across_pieces(self):
        t = make_table("hello")
        t.insert(5, b" world")
        # now: "hello world" across 2 pieces
        t.delete(3, 5)  # delete "lo wo"
        assert table_text(t) == "helrld"

    def test_delete_returns_edit(self):
        t = make_table("hello")
        edit = t.delete(0, 3)
        assert isinstance(edit, Edit)
        assert edit.kind == "delete"
        assert edit.pos == 0
        assert edit.text == b"hel"

    def test_delete_increments_version(self):
        t = make_table("hello")
        t.delete(0, 1)
        assert t.version == 1

    def test_delete_spanning_three_pieces(self):
        t = make_table("abc")
        t.insert(1, b"X")  # "aXbc" — 3 pieces: [a][X][bc]
        t.insert(3, b"Y")  # "aXbYc" — 4 pieces
        t.delete(1, 3)  # delete "XbY" → "ac"
        assert table_text(t) == "ac"


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_snapshot_immutable(self):
        t = make_table("hello")
        snap = t.snapshot()
        t.insert(5, b" world")
        # snapshot should still reflect old state
        assert snap.version == 0
        assert len(snap.pieces) == 1  # only original piece

    def test_snapshot_version(self):
        t = make_table("hi")
        t.insert(2, b"!")
        snap = t.snapshot()
        assert snap.version == 1

    def test_snapshot_line_offsets(self):
        t = make_table("a\nb\nc")
        snap = t.snapshot()
        assert snap.line_offsets == (0, 2, 4)

    def test_snapshot_is_frozen(self):
        t = make_table("hello")
        snap = t.snapshot()
        with pytest.raises((AttributeError, TypeError)):
            snap.version = 99  # type: ignore[misc]

    def test_snapshot_add_buffer_copied(self):
        t = make_table("hi")
        t.insert(2, b"!")
        snap = t.snapshot()
        # further mutation should not affect snap.add
        t.insert(3, b"?")
        assert snap.add == b"!?"[:1] or len(snap.add) == 1


# ---------------------------------------------------------------------------
# Edge cases from spec
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_insert_at_boundary_of_pieces(self):
        t = make_table("ab")
        t.insert(2, b"c")  # end of original
        t.insert(2, b"X")  # between original and first add
        assert table_text(t) == "abXc"

    def test_empty_buffer_line_count(self):
        t = make_table("")
        assert t.line_count() == 1

    def test_file_no_trailing_newline(self):
        t = make_table("hello")
        assert t.line_count() == 1
        assert t.get_line_bytes(0) == b"hello"

    def test_file_with_trailing_newline(self):
        t = make_table("hello\n")
        assert t.line_count() == 2
        assert t.get_line_bytes(1) == b""

    def test_delete_everything_then_insert(self):
        t = make_table("hello")
        t.delete(0, 5)
        t.insert(0, b"world")
        assert table_text(t) == "world"

    def test_line_count_after_insert_newlines(self):
        t = make_table("")
        t.insert(0, b"a\nb\nc")
        assert t.line_count() == 3

    def test_get_bytes_full_after_multiple_ops(self):
        t = make_table("the quick brown fox")
        t.delete(4, 6)  # "the brown fox"
        t.insert(4, b"lazy ")  # "the lazy brown fox"
        assert table_text(t) == "the lazy brown fox"


# ---------------------------------------------------------------------------
# Piece coalescing (§11.2)
# ---------------------------------------------------------------------------


class TestPieceCoalescing:
    def test_sequential_end_of_buffer_stays_one_piece(self):
        # Typing one char at a time at the end should not grow the piece list.
        t = make_table("")
        for ch in b"hello":
            t.insert(t.total_bytes(), bytes([ch]))
        assert len(t._pieces) == 1
        assert table_text(t) == "hello"

    def test_sequential_midfile_stays_compact(self):
        # Typing sequentially at a midfile position should coalesce.
        t = make_table("abcd")  # 1 original piece
        t.insert(2, b"X")      # split → [ab] [X] [cd]
        t.insert(3, b"Y")      # extend [X] → [XY], still 3 pieces
        t.insert(4, b"Z")      # extend [XY] → [XYZ], still 3 pieces
        assert len(t._pieces) == 3
        assert table_text(t) == "abXYZcd"

    def test_nonsequential_inserts_create_new_pieces(self):
        # Jumping around should not coalesce.
        t = make_table("abcd")
        t.insert(2, b"X")  # [ab][X][cd]
        t.insert(1, b"Y")  # [a][Y][b][X][cd] — 5 pieces
        assert table_text(t) == "aYbXcd"

    def test_coalescing_preserves_line_index(self):
        t = make_table("line1\nline2\n")
        for ch in b"new":
            t.insert(t.total_bytes(), bytes([ch]))
        assert table_text(t) == "line1\nline2\nnew"
        assert t.line_count() == 3

    def test_coalescing_text_correct_after_many_inserts(self):
        t = make_table("")
        word = b"the quick brown fox"
        for i, ch in enumerate(word):
            t.insert(i, bytes([ch]))
        assert table_text(t) == word.decode()

    def test_undo_after_coalesced_inserts(self):
        # Undo must still reverse each individual edit correctly even when
        # the piece table coalesced their storage.
        from peovim.core.history import UndoStack

        t = make_table("")
        undo = UndoStack()
        for ch in b"abc":
            edit = t.insert(t.total_bytes(), bytes([ch]))
            undo.push(edit)
        assert table_text(t) == "abc"
        undo.undo(t)
        assert table_text(t) == "ab"
        undo.undo(t)
        assert table_text(t) == "a"
        undo.undo(t)
        assert table_text(t) == ""


# ---------------------------------------------------------------------------
# Incremental line index (§11.1)
# ---------------------------------------------------------------------------


def _offsets(t: PieceTable) -> list[int]:
    return list(t._line_offsets)


def _reference_offsets(content: str) -> list[int]:
    """Ground-truth line offsets from content string."""
    data = content.encode()
    offsets = [0]
    for i, byte in enumerate(data):
        if byte == 0x0A:
            offsets.append(i + 1)
    return offsets


class TestIncrementalLineIndex:
    def test_insert_single_char_no_newline(self):
        t = make_table("hello\nworld")
        t.insert(5, b"!")
        assert table_text(t) == "hello!\nworld"
        assert _offsets(t) == _reference_offsets("hello!\nworld")

    def test_insert_newline_splits_line(self):
        t = make_table("helloworld")
        t.insert(5, b"\n")
        assert _offsets(t) == _reference_offsets("hello\nworld")

    def test_insert_multi_newline(self):
        t = make_table("ab")
        t.insert(1, b"\nx\ny\n")
        assert table_text(t) == "a\nx\ny\nb"
        assert _offsets(t) == _reference_offsets("a\nx\ny\nb")

    def test_insert_at_start(self):
        t = make_table("hello")
        t.insert(0, b"say ")
        assert _offsets(t) == _reference_offsets("say hello")

    def test_insert_newline_at_start(self):
        t = make_table("hello")
        t.insert(0, b"\n")
        assert _offsets(t) == _reference_offsets("\nhello")

    def test_insert_at_end(self):
        t = make_table("hello")
        t.insert(5, b"\nworld")
        assert _offsets(t) == _reference_offsets("hello\nworld")

    def test_delete_no_newline(self):
        t = make_table("hello\nworld")
        t.delete(1, 3)
        assert table_text(t) == "ho\nworld"
        assert _offsets(t) == _reference_offsets("ho\nworld")

    def test_delete_spanning_newline(self):
        t = make_table("a\nb\nc\n")
        t.delete(1, 3)  # deletes "\nb\n"
        assert table_text(t) == "ac\n"
        assert _offsets(t) == _reference_offsets("ac\n")

    def test_delete_entire_line(self):
        t = make_table("line1\nline2\nline3\n")
        t.delete(6, 6)  # remove "line2\n"
        assert table_text(t) == "line1\nline3\n"
        assert _offsets(t) == _reference_offsets("line1\nline3\n")

    def test_delete_from_start(self):
        t = make_table("hello\nworld")
        t.delete(0, 6)  # remove "hello\n"
        assert table_text(t) == "world"
        assert _offsets(t) == _reference_offsets("world")

    def test_round_trip_many_edits(self):
        # Interleaved inserts and deletes on a multi-line buffer
        t = make_table("aaa\nbbb\nccc\n")
        t.insert(4, b"XXX")
        t.delete(0, 3)
        t.insert(t.total_bytes(), b"\nddd")
        t.delete(7, 1)
        text = table_text(t)
        assert _offsets(t) == _reference_offsets(text)

    def test_line_count_consistent_with_offsets(self):
        t = make_table("one\ntwo\nthree")
        assert t.line_count() == len(t._line_offsets)
        t.insert(3, b"\nmid")
        assert t.line_count() == len(t._line_offsets)
        t.delete(0, 4)
        assert t.line_count() == len(t._line_offsets)

    def test_get_line_bytes_correct_after_incremental_updates(self):
        t = make_table("foo\nbar\nbaz")
        t.insert(3, b"!")       # "foo!\nbar\nbaz"  (! at 3, \n at 4, r at 7)
        t.delete(7, 1)          # "foo!\nba\nbaz"   (delete r at 7)
        t.insert(t.total_bytes(), b"\nqux")  # "foo!\nba\nbaz\nqux"
        assert t.get_line_bytes(0) == b"foo!"
        assert t.get_line_bytes(1) == b"ba"
        assert t.get_line_bytes(2) == b"baz"
        assert t.get_line_bytes(3) == b"qux"


# ---------------------------------------------------------------------------
# Pending edit geometry (§12 tree-sitter incremental parse plumbing)
# ---------------------------------------------------------------------------


class TestPendingEdits:
    def test_insert_records_pending_edit(self):
        t = make_table("hello\nworld")
        t.insert(5, b"!")
        assert len(t._pending_edits) == 1
        e = t._pending_edits[0]
        assert e.start_byte == 5
        assert e.old_end_byte == 5
        assert e.new_end_byte == 6

    def test_insert_records_correct_points_no_newline(self):
        t = make_table("hello\nworld")
        t.insert(5, b"!")
        e = t._pending_edits[0]
        assert (e.start_row, e.start_col) == (0, 5)
        assert (e.old_end_row, e.old_end_col) == (0, 5)
        assert (e.new_end_row, e.new_end_col) == (0, 6)

    def test_insert_records_correct_points_with_newline(self):
        t = make_table("ab\ncd")
        t.insert(2, b"\nXY")  # inserts 3 bytes at pos 2
        e = t._pending_edits[0]
        assert e.start_byte == 2
        assert e.new_end_byte == 5  # 2 + 3
        assert (e.start_row, e.start_col) == (0, 2)
        assert (e.new_end_row, e.new_end_col) == (1, 2)  # \n at index 0 → 1 newline, trailing "XY"

    def test_delete_records_pending_edit(self):
        t = make_table("hello\nworld")
        t.delete(3, 4)  # delete "lo\nw"
        assert len(t._pending_edits) == 1
        e = t._pending_edits[0]
        assert e.start_byte == 3
        assert e.old_end_byte == 7
        assert e.new_end_byte == 3

    def test_delete_records_correct_points(self):
        t = make_table("hello\nworld")
        t.delete(3, 4)  # delete "lo\nw" — crosses line boundary
        e = t._pending_edits[0]
        assert (e.start_row, e.start_col) == (0, 3)
        assert (e.old_end_row, e.old_end_col) == (1, 1)
        assert (e.new_end_row, e.new_end_col) == (0, 3)

    def test_snapshot_drains_pending_edits(self):
        t = make_table("hello")
        t.insert(5, b"!")
        assert len(t._pending_edits) == 1
        snap = t.snapshot()
        assert len(t._pending_edits) == 0
        assert len(snap.pending_edits) == 1

    def test_multiple_edits_accumulate(self):
        t = make_table("abc")
        t.insert(3, b"D")
        t.delete(0, 1)
        assert len(t._pending_edits) == 2

    def test_load_resets_pending_edits(self):
        t = make_table("hello")
        t.insert(5, b"!")
        t.load(b"new content")
        assert len(t._pending_edits) == 0

    def test_clear_resets_pending_edits(self):
        t = make_table("hello")
        t.insert(5, b"!")
        t.clear()
        assert len(t._pending_edits) == 0

    def test_snapshot_pending_edits_version_consistency(self):
        t = make_table("abc")
        t.insert(3, b"D")
        t.insert(4, b"E")
        snap = t.snapshot()
        assert len(snap.pending_edits) == 2
        # version is 2, and pending_edits has 2 entries → base was version 0
        assert snap.version == 2
        assert snap.version - len(snap.pending_edits) == 0


def _check_piece_offsets(t: "PieceTable") -> None:
    """Assert _piece_offsets is in sync with _pieces."""
    expected = []
    pos = 0
    for p in t._pieces:
        expected.append(pos)
        pos += p.length
    assert t._piece_offsets == expected, f"piece_offsets={t._piece_offsets!r} expected={expected!r}"


class TestPieceOffsets:
    def test_empty_buffer(self):
        t = make_table("")
        _check_piece_offsets(t)
        assert t._piece_offsets == []

    def test_after_load(self):
        t = make_table("hello\nworld")
        _check_piece_offsets(t)
        assert t._piece_offsets == [0]

    def test_after_insert_coalesce(self):
        t = make_table("hello")
        t.insert(5, b" world")
        _check_piece_offsets(t)

    def test_after_insert_non_coalesce(self):
        t = make_table("hello world")
        t.insert(5, b"!")
        _check_piece_offsets(t)

    def test_after_delete(self):
        t = make_table("hello world")
        t.delete(5, 6)
        _check_piece_offsets(t)

    def test_after_insert_then_delete(self):
        t = make_table("hello world")
        t.insert(5, b"!!!")
        t.delete(0, 3)
        _check_piece_offsets(t)

    def test_after_multiple_inserts(self):
        t = make_table("abcdef")
        t.insert(3, b"XY")
        t.insert(0, b"Z")
        t.insert(9, b"W")
        _check_piece_offsets(t)

    def test_get_bytes_uses_correct_offsets(self):
        t = make_table("hello world")
        t.insert(5, b"!!!")
        assert t.get_bytes(0, 3) == b"hel"
        assert t.get_bytes(5, 8) == b"!!!"
        assert t.get_bytes(8, 14) == b" world"
        _check_piece_offsets(t)

    def test_clear_resets_offsets(self):
        t = make_table("hello")
        t.clear()
        assert t._piece_offsets == []

    def test_load_resets_offsets(self):
        t = make_table("hello")
        t.insert(5, b" world")
        t.load(b"fresh")
        _check_piece_offsets(t)
        assert t._piece_offsets == [0]

    def test_find_piece_bisect_matches_scan(self):
        """Bisect _find_piece must return same results as the old linear scan."""
        t = make_table("abcde fghij")
        t.insert(5, b"!!!")  # creates new piece
        t.delete(2, 2)  # fragments further

        def find_piece_linear(table, pos):
            byte_pos = 0
            for i, piece in enumerate(table._pieces):
                if byte_pos + piece.length > pos:
                    return (i, pos - byte_pos)
                byte_pos += piece.length
            return (len(table._pieces), 0)

        total = t.total_bytes()
        for pos in range(total + 1):
            bisect_result = t._find_piece(pos)
            linear_result = find_piece_linear(t, pos)
            # Both should identify the same piece start and produce same offset
            assert bisect_result == linear_result or (
                # Equivalent: bisect may return (i, piece.length) when linear returns (i+1, 0)
                bisect_result[0] < len(t._pieces)
                and bisect_result[1] == t._pieces[bisect_result[0]].length
                and linear_result == (bisect_result[0] + 1, 0)
            ), f"pos={pos}: bisect={bisect_result} linear={linear_result}"
