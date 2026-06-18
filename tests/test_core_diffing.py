"""Tests for peovim.core.diffing.parse_hunks."""

from __future__ import annotations

from peovim.core.diffing import parse_hunks


def _hunk(type_: str, start: int, end: int) -> dict:
    return {"type": type_, "start": start, "end": end}


class TestParseHunksEmpty:
    def test_empty_string(self):
        assert parse_hunks("") == []

    def test_no_hunk_headers(self):
        assert parse_hunks("index abc..def\n--- a/f\n+++ b/f\n") == []


class TestParseHunksAdd:
    def test_add_at_end_of_file(self):
        # @@ -5,0 +6,3 @@ — old_count=0, lines added at end
        diff = "@@ -5,0 +6,3 @@\n+line1\n+line2\n+line3\n"
        result = parse_hunks(diff)
        assert result == [_hunk("add", 5, 7)]

    def test_add_in_middle_of_file(self):
        # Adds surrounded by context lines — line-by-line correctly yields "add"
        diff = "@@ -10,3 +10,6 @@\n context\n context\n context\n+added1\n+added2\n+added3\n"
        result = parse_hunks(diff)
        assert result == [_hunk("add", 12, 14)]

    def test_add_single_line(self):
        diff = "@@ -1,0 +2,1 @@\n+new line\n"
        result = parse_hunks(diff)
        assert result == [_hunk("add", 1, 1)]


class TestParseHunksDelete:
    def test_delete_places_sign_on_preceding_line(self):
        diff = "@@ -3,3 +3,0 @@\n-removed1\n-removed2\n-removed3\n"
        result = parse_hunks(diff)
        # dels start at line 2 (0-based), sign goes to line 1
        assert result == [_hunk("delete", 1, 1)]

    def test_delete_at_start_of_file_clamps_to_zero(self):
        diff = "@@ -1,2 +1,0 @@\n-first\n-second\n"
        result = parse_hunks(diff)
        assert result == [_hunk("delete", 0, 0)]

    def test_delete_with_context(self):
        diff = "@@ -5,5 +5,3 @@\n context\n context\n-removed\n-removed2\n context\n"
        result = parse_hunks(diff)
        assert result == [_hunk("delete", 5, 5)]


class TestParseHunksChange:
    def test_change_mixed_add_and_delete(self):
        diff = "@@ -1,3 +1,3 @@\n context\n-old line\n+new line\n context\n"
        result = parse_hunks(diff)
        assert result == [_hunk("change", 1, 1)]

    def test_change_spans_multiple_lines(self):
        diff = "@@ -1,4 +1,4 @@\n-old1\n-old2\n+new1\n+new2\n context\n context\n"
        result = parse_hunks(diff)
        assert result == [_hunk("change", 0, 1)]


class TestParseHunksMultiple:
    def test_two_hunks(self):
        diff = (
            "@@ -1,3 +1,4 @@\n context\n+added\n context\n context\n@@ -10,3 +11,2 @@\n context\n-removed\n context\n"
        )
        result = parse_hunks(diff)
        assert len(result) == 2
        assert result[0]["type"] == "add"
        assert result[1]["type"] == "delete"

    def test_hunk_boundary_resets_tracking(self):
        # Two separate adds — each should be its own hunk
        diff = "@@ -1,0 +1,1 @@\n+first\n@@ -5,0 +6,1 @@\n+second\n"
        result = parse_hunks(diff)
        assert result == [_hunk("add", 0, 0), _hunk("add", 5, 5)]


class TestParseHunksGitFormat:
    def test_git_diff_header_lines_ignored(self):
        # Lines before the first @@ are skipped (diff --git, index, ---, +++)
        diff = (
            "diff --git a/file.py b/file.py\n"
            "index abc123..def456 100644\n"
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1,3 +1,4 @@\n"
            " context\n"
            "+added\n"
            " context\n"
            " context\n"
        )
        result = parse_hunks(diff)
        assert result == [_hunk("add", 1, 1)]

    def test_git_diff_multi_file(self):
        # Two files in one diff output
        diff = "diff --git a/a.py b/a.py\n@@ -1,0 +1,1 @@\n+in a\ndiff --git a/b.py b/b.py\n@@ -5,0 +6,1 @@\n+in b\n"
        result = parse_hunks(diff)
        assert result == [_hunk("add", 0, 0), _hunk("add", 5, 5)]

    def test_malformed_hunk_header_skipped(self):
        diff = "@@ bad header @@\n+line\n@@ -1,0 +1,1 @@\n+good\n"
        result = parse_hunks(diff)
        assert result == [_hunk("add", 0, 0)]
