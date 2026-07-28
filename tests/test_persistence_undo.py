"""Tests for persistent per-file undo storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from peovim.core.buffer import Edit
from peovim.core.document import Document
from peovim.core.persistence_undo import (
    delete_undo_file,
    read_undo_file,
    undo_directory_size,
    write_undo_file,
)


class TestUndoFileIO:
    def test_write_and_read_undo_file(self, tmp_path: Path) -> None:
        filepath = tmp_path / "test.py"
        filepath.write_text("hello")

        stack: list[list[Edit]] = [[Edit(kind="insert", pos=5, text=b" world")]]
        redo: list[list[Edit]] = []

        write_undo_file(filepath, stack, redo, dirty_count=1)

        result = read_undo_file(filepath)
        assert result is not None
        loaded_stack, loaded_redo, loaded_dc = result
        assert loaded_dc == 1
        assert len(loaded_stack) == 1
        assert len(loaded_stack[0]) == 1
        assert loaded_stack[0][0].kind == "insert"
        assert loaded_stack[0][0].pos == 5
        assert loaded_stack[0][0].text == b" world"
        assert loaded_redo == []

    def test_read_missing_undo_file(self, tmp_path: Path) -> None:
        result = read_undo_file(tmp_path / "nonexistent.py")
        assert result is None

    def test_read_stale_undo_file(self, tmp_path: Path) -> None:
        """Undo file should be discarded when the file on disk has changed."""
        filepath = tmp_path / "test.py"
        filepath.write_text("original")

        stack: list[list[Edit]] = [[Edit(kind="insert", pos=8, text=b"!")]]
        write_undo_file(filepath, stack, [], dirty_count=1)

        filepath.write_text("modified externally")

        result = read_undo_file(filepath)
        assert result is None

    def test_delete_undo_file(self, tmp_path: Path) -> None:
        filepath = tmp_path / "test.py"
        filepath.write_text("hello")
        write_undo_file(filepath, [], [], dirty_count=0)
        assert read_undo_file(filepath) is not None
        delete_undo_file(filepath)
        assert read_undo_file(filepath) is None

    def test_delete_nonexistent_undo_file(self, tmp_path: Path) -> None:
        delete_undo_file(tmp_path / "nonexistent.py")  # must not raise

    def test_write_undo_file_clean_state(self, tmp_path: Path) -> None:
        filepath = tmp_path / "test.py"
        filepath.write_text("hello")
        stack: list[list[Edit]] = [[Edit(kind="insert", pos=5, text=b" world")]]
        write_undo_file(filepath, stack, [], dirty_count=0)  # clean

        filepath.unlink()
        filepath.write_text("hello")  # same content

        result = read_undo_file(filepath)
        assert result is not None
        _, _, loaded_dc = result
        assert loaded_dc == 0

    def test_undo_directory_size(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

        undo_tmp = tmp_path / "undo_test"
        undo_tmp.mkdir()

        monkeypatch.setattr(
            "peovim.core.persistence_undo._undo_dir",
            lambda: undo_tmp,
        )

        file1 = tmp_path / "a.py"
        file1.write_text("a")
        write_undo_file(file1, [], [], dirty_count=0)

        file2 = tmp_path / "b.py"
        file2.write_text("b")
        write_undo_file(file2, [], [], dirty_count=0)

        count, total = undo_directory_size()
        assert count == 2
        assert total > 0

    def test_undo_directory_size_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        empty_dir = tmp_path / "empty_undo"
        empty_dir.mkdir(parents=True)
        monkeypatch.setattr("peovim.core.persistence_undo._undo_dir", lambda: empty_dir)
        count, total = undo_directory_size()
        assert count == 0
        assert total == 0


class TestDocumentRestoreUndo:
    def test_restore_undo_clean_state(self, tmp_path: Path) -> None:
        """After a save (clean state), no dirty entries are replayed."""
        filepath = tmp_path / "doc.py"
        filepath.write_text("hello")

        doc = Document()
        doc.load(filepath)
        assert doc.get_text() == "hello"
        assert doc.dirty is False

        doc.insert(line=0, col=5, text=" world")
        doc.save()  # clean state: save writes changes to disk
        doc.flush_undo()

        doc2 = Document()
        doc2.load(filepath)
        assert doc2.get_text() == "hello world"
        assert doc2.dirty is False

    def test_restore_undo_dirty_state(self, tmp_path: Path) -> None:
        """Unsaved edits are replayed on reload."""
        filepath = tmp_path / "doc.py"
        filepath.write_text("hello")

        doc = Document()
        doc.load(filepath)
        doc.insert(line=0, col=5, text=" world")
        assert doc.get_text() == "hello world"
        doc.flush_undo()

        # Reload — dirty edits should be restored
        doc2 = Document()
        doc2.load(filepath)
        assert doc2.get_text() == "hello world"
        assert doc2.dirty is True

        # Undo should work
        result = doc2.undo()
        assert result is not None
        assert doc2.get_text() == "hello"

    def test_restore_undo_dirty_then_save(self, tmp_path: Path) -> None:
        """Dirty edits replayed, then save makes them clean."""
        filepath = tmp_path / "doc.py"
        filepath.write_text("hello")

        doc = Document()
        doc.load(filepath)
        doc.insert(line=0, col=5, text=" world")
        doc.flush_undo()

        doc2 = Document()
        doc2.load(filepath)
        assert doc2.get_text() == "hello world"
        assert doc2.dirty is True

        doc2.save()
        assert doc2.dirty is False

        doc3 = Document()
        doc3.load(filepath)
        assert doc3.get_text() == "hello world"
        assert doc3.dirty is False

    def test_restore_stale_discarded(self, tmp_path: Path) -> None:
        """Undo is discarded when file changed externally."""
        filepath = tmp_path / "doc.py"
        filepath.write_text("hello")

        doc = Document()
        doc.load(filepath)
        doc.insert(line=0, col=5, text=" world")
        doc.flush_undo()

        filepath.write_text("completely different content")

        doc2 = Document()
        doc2.load(filepath)
        assert doc2.get_text() == "completely different content"
        assert doc2.dirty is False
        assert doc2._change_counter == 0

    def test_restore_undo_multiple_edits(self, tmp_path: Path) -> None:
        """Multiple edits with save points restore correctly."""
        filepath = tmp_path / "doc.py"
        filepath.write_text("abc")

        doc = Document()
        doc.load(filepath)
        doc.insert(line=0, col=3, text="d")
        doc.flush_undo()

        doc2 = Document()
        doc2.load(filepath)
        assert doc2.get_text() == "abcd"
        doc2.insert(line=0, col=4, text="e")
        doc2.flush_undo()

        doc3 = Document()
        doc3.load(filepath)
        assert doc3.get_text() == "abcde"

        # Undo twice to get back to "abc"
        doc3.undo()
        assert doc3.get_text() == "abcd"
        doc3.undo()
        assert doc3.get_text() == "abc"
        result = doc3.undo()
        assert result is None  # no more undo

    def test_no_path_document_skip_undo(self) -> None:
        """Documents without a path skip undo persistence."""
        doc = Document()
        doc.load_string("scratch content")
        doc.insert(line=0, col=16, text="!")
        doc.flush_undo()  # must not raise

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "a",
            "hello\nworld\n",
            "x" * 10000,
        ],
    )
    def test_restore_various_content(self, tmp_path: Path, text: str) -> None:
        filepath = tmp_path / "doc.py"
        filepath.write_text(text)

        doc = Document()
        doc.load(filepath)
        append = "ADDED"
        last_line = max(0, doc.line_count() - 1)
        last_col = len(doc.get_line(last_line)) if doc.line_count() > 0 else 0
        doc.insert(line=last_line, col=last_col, text=append)
        doc.flush_undo()

        doc2 = Document()
        doc2.load(filepath)
        expected = text + append
        assert doc2.get_text() == expected

    def test_restore_with_redo_entries(self, tmp_path: Path) -> None:
        """Undo entries with redo entries restore correctly."""
        filepath = tmp_path / "doc.py"
        filepath.write_text("abc")

        doc = Document()
        doc.load(filepath)
        doc.insert(line=0, col=3, text="def")
        doc.undo()
        doc.flush_undo()

        doc2 = Document()
        doc2.load(filepath)
        assert doc2.get_text() == "abc"

        result = doc2.redo()
        assert result is not None
        assert doc2.get_text() == "abcdef"
