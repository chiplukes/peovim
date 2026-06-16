"""
Document: load/save, encoding, CRLF round-trip, event emission, undo integration.
"""

from pathlib import Path

import pytest

from peovim.core import persistence as persistence_mod
from peovim.core.document import Document

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_doc(content: str = "", path: Path | None = None) -> Document:
    doc = Document(path=path)
    doc.load_string(content)
    return doc


# ---------------------------------------------------------------------------
# Basic construction
# ---------------------------------------------------------------------------


class TestDocumentBasic:
    def test_empty_document(self):
        doc = make_doc("")
        assert doc.line_count() == 1
        assert doc.get_line(0) == ""
        assert not doc.dirty

    def test_single_line(self):
        doc = make_doc("hello")
        assert doc.line_count() == 1
        assert doc.get_line(0) == "hello"

    def test_multiline(self):
        doc = make_doc("a\nb\nc")
        assert doc.line_count() == 3
        assert doc.get_line(0) == "a"
        assert doc.get_line(1) == "b"
        assert doc.get_line(2) == "c"

    def test_version_starts_at_zero(self):
        doc = make_doc("hello")
        assert doc.version == 0


# ---------------------------------------------------------------------------
# CRLF normalization
# ---------------------------------------------------------------------------


class TestCRLF:
    def test_crlf_detected(self):
        doc = Document()
        doc.load_bytes(b"line1\r\nline2\r\n")
        assert doc.line_ending == "\r\n"
        assert doc.line_count() == 3  # line1, line2, ""

    def test_crlf_content_normalized(self):
        doc = Document()
        doc.load_bytes(b"line1\r\nline2")
        assert doc.get_line(0) == "line1"
        assert doc.get_line(1) == "line2"

    def test_cr_only(self):
        doc = Document()
        doc.load_bytes(b"a\rb\rc")
        assert doc.line_ending == "\r"
        assert doc.line_count() == 3

    def test_lf_only(self):
        doc = Document()
        doc.load_bytes(b"a\nb\nc")
        assert doc.line_ending == "\n"

    def test_save_restores_crlf(self, tmp_path):
        doc = Document()
        doc.load_bytes(b"line1\r\nline2\r\n")
        out = tmp_path / "out.txt"
        doc.save(out)
        assert b"\r\n" in out.read_bytes()

    def test_save_preserves_lf(self, tmp_path):
        doc = Document()
        doc.load_bytes(b"line1\nline2\n")
        out = tmp_path / "out.txt"
        doc.save(out)
        data = out.read_bytes()
        assert b"\r\n" not in data
        assert b"\n" in data

    def test_mixed_line_endings_detected_and_prefer_dominant_style(self):
        doc = Document()
        doc.load_bytes(b"line1\r\nline2\nline3\r\n")

        assert doc.had_mixed_line_endings is True
        assert doc.line_ending == "\r\n"
        assert doc.fileformat == "dos"

    def test_insert_normalizes_crlf_and_cr_to_internal_lf(self):
        doc = make_doc("alpha")

        doc.insert(0, 5, "\r\nbeta\rgamma")

        assert doc.get_text() == "alpha\nbeta\ngamma"

    def test_replace_normalizes_crlf_and_cr_to_internal_lf(self):
        doc = make_doc("alpha")

        doc.replace(0, 0, 0, 5, "one\r\ntwo\rthree")

        assert doc.get_text() == "one\ntwo\nthree"


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


class TestFileIO:
    def test_load_from_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello\nworld\n", encoding="utf-8")
        doc = Document()
        doc.load(f)
        assert doc.path == f
        assert doc.line_count() == 3
        assert doc.get_line(0) == "hello"

    def test_save_and_reload(self, tmp_path):
        f = tmp_path / "test.txt"
        doc = Document()
        doc.load_string("hello\nworld")
        doc.save(f)
        doc2 = Document()
        doc2.load(f)
        assert doc2.get_line(0) == "hello"
        assert doc2.get_line(1) == "world"

    def test_dirty_flag(self):
        doc = make_doc("hello")
        assert not doc.dirty
        doc.insert(0, 0, "X")
        assert doc.dirty

    def test_save_clears_dirty(self, tmp_path):
        f = tmp_path / "test.txt"
        doc = make_doc("hello")
        doc.insert(0, 5, "!")
        doc.save(f)
        assert not doc.dirty

    def test_has_external_changes_detects_modified_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello\n", encoding="utf-8")
        doc = Document()
        doc.load(f)

        f.write_text("goodbye\n", encoding="utf-8")

        assert doc.has_external_changes() is True

    def test_reload_clears_external_change_state(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello\n", encoding="utf-8")
        doc = Document()
        doc.load(f)
        f.write_text("goodbye\n", encoding="utf-8")

        doc.reload()

        assert doc.has_external_changes() is False
        assert doc.get_text() == "goodbye\n"

    def test_save_preserves_existing_file_when_replace_fails(self, tmp_path, monkeypatch):
        f = tmp_path / "test.txt"
        f.write_text("original\n", encoding="utf-8")
        doc = make_doc("updated\n", path=f)

        def _boom(src, dst) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(persistence_mod.os, "replace", _boom)

        with pytest.raises(OSError, match="replace failed"):
            doc.save()

        assert f.read_text(encoding="utf-8") == "original\n"
        assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# String-level insert / delete
# ---------------------------------------------------------------------------


class TestInsertDelete:
    def test_insert_char(self):
        doc = make_doc("helo")
        doc.insert(0, 3, "l")
        assert doc.get_line(0) == "hello"

    def test_insert_at_end_of_line(self):
        doc = make_doc("hi")
        doc.insert(0, 2, "!")
        assert doc.get_line(0) == "hi!"

    def test_insert_newline(self):
        doc = make_doc("ab")
        doc.insert(0, 1, "\n")
        assert doc.line_count() == 2
        assert doc.get_line(0) == "a"
        assert doc.get_line(1) == "b"

    def test_delete_char(self):
        doc = make_doc("hello")
        doc.delete(0, 2, 0, 3)  # delete 'l' at col 2
        assert doc.get_line(0) == "helo"

    def test_delete_range(self):
        doc = make_doc("hello world")
        doc.delete(0, 5, 0, 11)
        assert doc.get_line(0) == "hello"

    def test_delete_across_lines(self):
        doc = make_doc("abc\ndef")
        doc.delete(0, 2, 1, 1)  # delete from "c\nd"
        assert doc.line_count() == 1
        assert doc.get_line(0) == "abef"

    def test_replace(self):
        doc = make_doc("hello world")
        doc.replace(0, 6, 0, 11, "python")
        assert doc.get_line(0) == "hello python"

    def test_version_increments(self):
        doc = make_doc("hi")
        doc.insert(0, 0, "X")
        assert doc.version == 1
        doc.insert(0, 0, "Y")
        assert doc.version == 2


# ---------------------------------------------------------------------------
# Column conversion (char ↔ byte)
# ---------------------------------------------------------------------------


class TestColumnConversion:
    def test_ascii_char_byte_equal(self):
        doc = make_doc("hello")
        assert doc.char_to_byte(0, 3) == 3
        assert doc.byte_to_char(0, 3) == 3

    def test_unicode_char_to_byte(self):
        # "café" — 'é' is 2 bytes in UTF-8
        doc = make_doc("café")
        # char col 4 (after 'é') = byte col 5
        assert doc.char_to_byte(0, 4) == 5

    def test_unicode_byte_to_char(self):
        doc = make_doc("café")
        assert doc.byte_to_char(0, 5) == 4


# ---------------------------------------------------------------------------
# Undo / redo via Document
# ---------------------------------------------------------------------------


class TestDocumentUndo:
    def test_undo_insert(self):
        doc = make_doc("hello")
        doc.insert(0, 5, " world")
        assert doc.get_line(0) == "hello world"
        doc.undo()
        assert doc.get_line(0) == "hello"

    def test_redo_insert(self):
        doc = make_doc("hello")
        doc.insert(0, 5, " world")
        doc.undo()
        doc.redo()
        assert doc.get_line(0) == "hello world"

    def test_undo_sets_dirty(self):
        doc = make_doc("hello")
        doc.insert(0, 5, "!")
        doc.undo()
        # After undoing all changes, dirty should be False
        assert not doc.dirty

    def test_compound_edit_via_context_manager(self):
        doc = make_doc("hello world")
        with doc.compound_edit():
            doc.delete(0, 5, 0, 11)
            doc.insert(0, 5, "!")
        assert doc.get_line(0) == "hello!"
        doc.undo()
        assert doc.get_line(0) == "hello world"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class TestDocumentEvents:
    def test_buffer_changed_fires_on_insert(self):
        doc = make_doc("hello")
        fired = []
        doc.on_changed(lambda d: fired.append(True))
        doc.insert(0, 5, "!")
        assert fired == [True]

    def test_buffer_changed_fires_on_delete(self):
        doc = make_doc("hello")
        fired = []
        doc.on_changed(lambda d: fired.append(True))
        doc.delete(0, 0, 0, 1)
        assert fired == [True]

    def test_multiple_handlers(self):
        doc = make_doc("hi")
        results = []
        doc.on_changed(lambda d: results.append("a"))
        doc.on_changed(lambda d: results.append("b"))
        doc.insert(0, 2, "!")
        assert results == ["a", "b"]

    def test_reload_fires_changed_handler(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("original\n", encoding="utf-8")
        doc = Document()
        doc.load(f)
        fired = []
        doc.on_changed(lambda d: fired.append(d.get_text()))
        f.write_text("updated\n", encoding="utf-8")
        doc.reload()
        assert fired == ["updated\n"]


# ---------------------------------------------------------------------------
# Undo cursor position (§14)
# ---------------------------------------------------------------------------


class TestUndoCursorPosition:
    def test_undo_returns_change_position(self):
        doc = make_doc("hello\nworld\n")
        doc.insert(1, 0, "XYZ")
        pos = doc.undo()
        assert pos is not None
        # Cursor should be at line 1, col 0 (where the insert happened)
        assert pos == (1, 0)

    def test_undo_nothing_returns_none(self):
        doc = make_doc("hello")
        assert doc.undo() is None

    def test_redo_returns_change_position(self):
        doc = make_doc("hello\nworld\n")
        doc.insert(1, 3, "!")
        doc.undo()
        pos = doc.redo()
        assert pos is not None
        assert pos == (1, 3)

    def test_redo_nothing_returns_none(self):
        doc = make_doc("hello")
        assert doc.redo() is None

    def test_undo_positions_within_line(self):
        doc = make_doc("hello world")
        doc.insert(0, 5, "!")
        pos = doc.undo()
        assert pos is not None
        line, col = pos
        assert line == 0
        assert col == 5

    def test_undo_multiline_insert(self):
        doc = make_doc("abc")
        doc.insert(0, 0, "x\ny\n")
        pos = doc.undo()
        assert pos is not None
        assert pos == (0, 0)

    def test_undo_is_truthy(self):
        # Existing callers use `if doc.undo():` — tuple is always truthy.
        doc = make_doc("hello")
        doc.insert(0, 0, "x")
        result = doc.undo()
        assert result  # tuple is truthy

    def test_undo_none_is_falsy(self):
        doc = make_doc("hello")
        result = doc.undo()
        assert not result  # None is falsy
