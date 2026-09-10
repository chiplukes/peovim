"""Tests for CLI positional-argument parsing in peovim.main."""

from __future__ import annotations

from peovim.main import _parse_file_arg


def test_no_args_returns_nothing() -> None:
    assert _parse_file_arg([]) == (None, None, None)


def test_plain_file_no_line() -> None:
    assert _parse_file_arg(["foo.py"]) == ("foo.py", None, None)


def test_vim_style_plus_line_before_file(tmp_path) -> None:
    f = tmp_path / "foo.py"
    f.write_text("a\nb\nc\n")
    assert _parse_file_arg(["+3", str(f)]) == (str(f), 2, None)


def test_vim_style_plus_line_after_file(tmp_path) -> None:
    f = tmp_path / "foo.py"
    f.write_text("a\nb\nc\n")
    assert _parse_file_arg([str(f), "+3"]) == (str(f), 2, None)


def test_bare_plus_line_with_no_file() -> None:
    assert _parse_file_arg(["+7"]) == (None, 6, None)


def test_colon_line_suffix_only_applies_when_bare_path_exists(tmp_path) -> None:
    f = tmp_path / "foo.py"
    f.write_text("a\nb\nc\n")
    assert _parse_file_arg([f"{f}:3"]) == (str(f), 2, None)


def test_colon_line_col_suffix(tmp_path) -> None:
    f = tmp_path / "foo.py"
    f.write_text("a\nb\nc\n")
    assert _parse_file_arg([f"{f}:3:2"]) == (str(f), 2, 1)


def test_colon_suffix_left_alone_when_file_does_not_exist() -> None:
    # Avoid misparsing a nonexistent path that merely looks like file:line.
    assert _parse_file_arg(["nonexistent.py:3"]) == ("nonexistent.py:3", None, None)


def test_literal_path_with_colon_that_exists_is_not_reinterpreted(tmp_path) -> None:
    # A real file whose name contains a colon must win over line/col parsing.
    f = tmp_path / "weird:42.py"
    f.write_text("a\n")
    assert _parse_file_arg([str(f)]) == (str(f), None, None)


def test_plus_line_takes_precedence_over_colon_suffix_in_same_invocation(tmp_path) -> None:
    f = tmp_path / "foo.py"
    f.write_text("a\nb\nc\nd\n")
    # +N always wins if both were somehow supplied; colon form only fills in
    # goto_line when +N wasn't given.
    assert _parse_file_arg(["+1", f"{f}:3"]) == (str(f), 0, None)
