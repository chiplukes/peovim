"""
Mark set/jump, global marks, special marks
"""

from peovim.core.marks import MarkStore


class TestLocalMarks:
    def test_set_and_get(self):
        ms = MarkStore()
        ms.set("a", 5, 10)
        assert ms.get("a") == (5, 10)

    def test_overwrite(self):
        ms = MarkStore()
        ms.set("a", 1, 0)
        ms.set("a", 3, 7)
        assert ms.get("a") == (3, 7)

    def test_get_nonexistent(self):
        ms = MarkStore()
        assert ms.get("a") is None

    def test_all_lowercase_marks(self):
        ms = MarkStore()
        for ch in "abcdefghijklmnopqrstuvwxyz":
            ms.set(ch, ord(ch), 0)
        for ch in "abcdefghijklmnopqrstuvwxyz":
            assert ms.get(ch) == (ord(ch), 0)

    def test_delete_mark(self):
        ms = MarkStore()
        ms.set("b", 2, 3)
        ms.delete("b")
        assert ms.get("b") is None

    def test_delete_nonexistent_no_error(self):
        ms = MarkStore()
        ms.delete("z")  # should not raise

    def test_clear_local(self):
        ms = MarkStore()
        ms.set("a", 1, 0)
        ms.set("b", 2, 0)
        ms.clear_local()
        assert ms.get("a") is None
        assert ms.get("b") is None


class TestGlobalMarks:
    def test_set_and_get_global(self):
        ms = MarkStore()
        ms.set_global("A", "/foo/bar.py", 10, 5)
        result = ms.get_global("A")
        assert result == ("/foo/bar.py", 10, 5)

    def test_get_global_position(self):
        ms = MarkStore()
        ms.set_global("Z", "/path/file.py", 3, 0)
        pos = ms.get("Z")
        assert pos == (3, 0)

    def test_set_global_via_set(self):
        ms = MarkStore()
        ms.set("B", 5, 2)  # uppercase -> global (no path)
        pos = ms.get("B")
        assert pos == (5, 2)

    def test_global_nonexistent(self):
        ms = MarkStore()
        assert ms.get_global("A") is None

    def test_all_uppercase_marks(self):
        ms = MarkStore()
        for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            ms.set(ch, 1, 0)
        for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            assert ms.get(ch) == (1, 0)


class TestSpecialMarks:
    def test_last_jump(self):
        ms = MarkStore()
        ms.set("`", 7, 3)
        assert ms.get("`") == (7, 3)

    def test_last_change(self):
        ms = MarkStore()
        ms.set(".", 4, 2)
        assert ms.get(".") == (4, 2)

    def test_visual_start_end(self):
        ms = MarkStore()
        ms.set("<", 1, 0)
        ms.set(">", 1, 5)
        assert ms.get("<") == (1, 0)
        assert ms.get(">") == (1, 5)

    def test_yank_range(self):
        ms = MarkStore()
        ms.set("[", 2, 0)
        ms.set("]", 2, 10)
        assert ms.get("[") == (2, 0)
        assert ms.get("]") == (2, 10)

    def test_unknown_mark_ignored(self):
        ms = MarkStore()
        ms.set("!", 0, 0)  # not a valid mark
        assert ms.get("!") is None


class TestListMarks:
    def test_list_returns_local_and_special(self):
        ms = MarkStore()
        ms.set("a", 1, 0)
        ms.set("b", 2, 0)
        ms.set(".", 3, 0)
        result = ms.list_marks()
        assert "a" in result
        assert "b" in result
        assert "." in result

    def test_list_excludes_unset(self):
        ms = MarkStore()
        ms.set("a", 0, 0)
        result = ms.list_marks()
        assert "b" not in result

    def test_list_excludes_global(self):
        ms = MarkStore()
        ms.set("A", 1, 0)
        result = ms.list_marks()
        # Global marks not in list_marks (they belong to files)
        assert "A" not in result
