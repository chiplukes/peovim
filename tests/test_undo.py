"""
Undo/redo, compound edits.
"""

from peovim.core.buffer import Edit, PieceTable
from peovim.core.history import UndoStack


def make_table(content: str = "") -> PieceTable:
    t = PieceTable()
    t.load(content.encode())
    return t


def table_text(t: PieceTable) -> str:
    return t.get_bytes(0, t.total_bytes()).decode()


# ---------------------------------------------------------------------------
# Basic undo / redo
# ---------------------------------------------------------------------------


class TestUndoRedo:
    def test_undo_single_insert(self):
        t = make_table("hello")
        undo = UndoStack()
        edit = t.insert(5, b" world")
        undo.push(edit)
        assert table_text(t) == "hello world"
        result = undo.undo(t)
        assert result is not None
        assert table_text(t) == "hello"

    def test_undo_empty_stack(self):
        t = make_table("hello")
        undo = UndoStack()
        assert undo.undo(t) is None

    def test_redo_after_undo(self):
        t = make_table("hello")
        undo = UndoStack()
        edit = t.insert(5, b" world")
        undo.push(edit)
        undo.undo(t)
        result = undo.redo(t)
        assert result is not None
        assert table_text(t) == "hello world"

    def test_redo_empty(self):
        t = make_table("hello")
        undo = UndoStack()
        assert undo.redo(t) is None

    def test_new_edit_clears_redo(self):
        t = make_table("hello")
        undo = UndoStack()
        e1 = t.insert(5, b" world")
        undo.push(e1)
        undo.undo(t)  # now redo has e1
        e2 = t.insert(5, b"!")
        undo.push(e2)
        assert undo.redo(t) is None  # redo cleared

    def test_undo_delete(self):
        t = make_table("hello world")
        undo = UndoStack()
        edit = t.delete(5, 6)
        undo.push(edit)
        assert table_text(t) == "hello"
        undo.undo(t)
        assert table_text(t) == "hello world"

    def test_multiple_undo_steps(self):
        t = make_table("")
        undo = UndoStack()
        e1 = t.insert(0, b"a")
        undo.push(e1)
        e2 = t.insert(1, b"b")
        undo.push(e2)
        e3 = t.insert(2, b"c")
        undo.push(e3)
        assert table_text(t) == "abc"
        undo.undo(t)
        assert table_text(t) == "ab"
        undo.undo(t)
        assert table_text(t) == "a"
        undo.undo(t)
        assert table_text(t) == ""
        # redo all three
        undo.redo(t)
        undo.redo(t)
        undo.redo(t)
        assert table_text(t) == "abc"

    def test_undo_past_beginning(self):
        t = make_table("hi")
        undo = UndoStack()
        e = t.insert(2, b"!")
        undo.push(e)
        undo.undo(t)
        assert undo.undo(t) is None  # nothing left

    def test_redo_past_end(self):
        t = make_table("hi")
        undo = UndoStack()
        e = t.insert(2, b"!")
        undo.push(e)
        undo.undo(t)
        undo.redo(t)
        assert undo.redo(t) is None  # nothing left


# ---------------------------------------------------------------------------
# Compound edits
# ---------------------------------------------------------------------------


class TestCompoundEdits:
    def test_compound_is_single_undo_step(self):
        t = make_table("hello")
        undo = UndoStack()
        undo.begin_compound()
        e1 = t.insert(5, b" world")
        undo.push(e1)
        e2 = t.insert(11, b"!")
        undo.push(e2)
        undo.end_compound()
        assert table_text(t) == "hello world!"
        undo.undo(t)
        # Both inserts reversed in one step
        assert table_text(t) == "hello"

    def test_compound_redo(self):
        t = make_table("hello")
        undo = UndoStack()
        undo.begin_compound()
        e1 = t.insert(5, b" world")
        undo.push(e1)
        e2 = t.insert(11, b"!")
        undo.push(e2)
        undo.end_compound()
        undo.undo(t)
        undo.redo(t)
        assert table_text(t) == "hello world!"

    def test_empty_compound_not_added(self):
        t = make_table("hello")
        undo = UndoStack()
        undo.begin_compound()
        undo.end_compound()
        assert undo.undo(t) is None  # nothing pushed

    def test_nested_compound_allowed(self):
        # Nested compound edits are now reentrant — inner begin/end are no-ops
        # so all edits roll into the outer group (needed for buf.batch() + doc.replace).
        t = make_table("hello")
        undo = UndoStack()
        undo.begin_compound()
        undo.begin_compound()  # reentrant — should not raise
        undo.push(Edit("insert", 0, b"X"))
        undo.end_compound()  # inner close — still open
        undo.push(Edit("insert", 1, b"Y"))
        undo.end_compound()  # outer close — commits
        assert undo.undo(t) is not None  # one undo step with both edits

    def test_compound_mixed_insert_delete(self):
        t = make_table("hello world")
        undo = UndoStack()
        undo.begin_compound()
        e1 = t.delete(5, 6)  # delete " world" → "hello"
        undo.push(e1)
        e2 = t.insert(5, b"!")  # "hello!"
        undo.push(e2)
        undo.end_compound()
        assert table_text(t) == "hello!"
        undo.undo(t)
        assert table_text(t) == "hello world"


# ---------------------------------------------------------------------------
# Max depth
# ---------------------------------------------------------------------------


class TestMaxDepth:
    def test_max_depth_eviction(self):
        t = make_table("")
        undo = UndoStack(max_depth=3)
        for _i in range(5):
            e = t.insert(t.total_bytes(), b"x")
            undo.push(e)
        # Can only undo 3 times (oldest 2 evicted)
        assert undo.undo(t) is not None
        assert undo.undo(t) is not None
        assert undo.undo(t) is not None
        assert undo.undo(t) is None
