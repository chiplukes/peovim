"""
tests.test_syntax — Tests for Phase 5 syntax highlighting

Covers: filetype detection, language registry, theme system, SyntaxEngine
parse task, and renderer integration.

Grammar-dependent tests are skipped when tree-sitter-python is not installed.
"""

from __future__ import annotations

import importlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _python_grammar_available() -> bool:
    try:
        import tree_sitter_python  # noqa: F401
        from tree_sitter import Parser  # noqa: F401

        return True
    except ImportError:
        return False


def _verilog_grammar_available() -> bool:
    try:
        import tree_sitter_verilog  # noqa: F401
        from tree_sitter import Parser  # noqa: F401

        return True
    except ImportError:
        return False


def _markdown_grammar_available() -> bool:
    try:
        import tree_sitter_markdown  # noqa: F401
        from tree_sitter import Parser  # noqa: F401

        return True
    except ImportError:
        return False


def _c_grammar_available() -> bool:
    try:
        import tree_sitter_c  # noqa: F401
        from tree_sitter import Parser  # noqa: F401

        return True
    except ImportError:
        return False


def _cpp_grammar_available() -> bool:
    try:
        import tree_sitter_cpp  # noqa: F401
        from tree_sitter import Parser  # noqa: F401

        return True
    except ImportError:
        return False


def _make_python_snapshot(text: str):
    from peovim.core.document import Document

    doc = Document()
    doc.load_string(text)
    doc.filetype = "python"
    return doc.snapshot()


def _make_verilog_snapshot(text: str):
    from peovim.core.document import Document

    doc = Document()
    doc.load_string(text)
    doc.filetype = "verilog"
    return doc.snapshot()


def _make_markdown_snapshot(text: str):
    from peovim.core.document import Document

    doc = Document()
    doc.load_string(text)
    doc.filetype = "markdown"
    return doc.snapshot()


# ---------------------------------------------------------------------------
# 5b — Filetype detection
# ---------------------------------------------------------------------------


class TestFiletypeDetection:
    def test_python_extension(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("foo.py") == "python"

    def test_pyi_extension(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("stubs.pyi") == "python"

    def test_javascript(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("app.js") == "javascript"

    def test_typescript(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("app.ts") == "typescript"

    def test_tsx(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("App.tsx") == "tsx"

    def test_rust(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("main.rs") == "rust"

    def test_verilog(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("top.v") == "verilog"
        assert detect_filetype("alu.sv") == "verilog"

    def test_json(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("config.json") == "json"

    def test_yaml(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("config.yml") == "yaml"
        assert detect_filetype("config.yaml") == "yaml"

    def test_toml(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("pyproject.toml") == "toml"

    def test_markdown(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("README.md") == "markdown"

    def test_makefile_basename(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("Makefile") == "make"
        assert detect_filetype("GNUmakefile") == "make"

    def test_dockerfile_basename(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("Dockerfile") == "dockerfile"

    def test_unknown_extension(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype("foo.xyz") == ""

    def test_none_path_no_shebang(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype(None) == ""

    def test_shebang_python(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype(None, "#!/usr/bin/env python3") == "python"
        assert detect_filetype(None, "#!/usr/bin/python") == "python"

    def test_shebang_node(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype(None, "#!/usr/bin/env node") == "javascript"

    def test_shebang_bash(self):
        from peovim.core.filetype import detect_filetype

        assert detect_filetype(None, "#!/bin/bash") == "bash"
        assert detect_filetype(None, "#!/bin/sh") == "bash"

    def test_extension_wins_over_no_shebang(self):
        from peovim.core.filetype import detect_filetype

        # Path has .py extension; no shebang → python from extension
        assert detect_filetype("script.py", "# just a comment") == "python"


# ---------------------------------------------------------------------------
# 5b — Document.filetype propagation
# ---------------------------------------------------------------------------


class TestDocumentFiletype:
    def test_filetype_set_on_load_string(self):
        from peovim.core.document import Document

        doc = Document()
        doc.load_string("x = 1\n")
        # No path → filetype defaults to "" (no shebang)
        assert doc.filetype == ""

    def test_filetype_from_shebang(self):
        from peovim.core.document import Document

        doc = Document()
        doc.load_string("#!/usr/bin/env python3\nx = 1\n")
        assert doc.filetype == "python"

    def test_filetype_in_snapshot(self):
        from peovim.core.document import Document

        doc = Document()
        doc.load_string("#!/usr/bin/env python3\nx = 1\n")
        snap = doc.snapshot()
        assert snap.filetype == "python"

    def test_manual_filetype_in_snapshot(self):
        from peovim.core.document import Document

        doc = Document()
        doc.load_string("x = 1\n")
        doc.filetype = "python"
        snap = doc.snapshot()
        assert snap.filetype == "python"


# ---------------------------------------------------------------------------
# 5c — Language registry
# ---------------------------------------------------------------------------


class TestLanguageRegistry:
    def test_known_filetypes(self):
        from peovim.syntax.languages import get_language_info

        for ft in (
            "python",
            "javascript",
            "typescript",
            "rust",
            "c",
            "go",
            "lua",
            "json",
            "yaml",
            "toml",
            "markdown",
            "bash",
            "verilog",
        ):
            assert get_language_info(ft) is not None, f"{ft} not registered"

    def test_unknown_filetype_returns_none(self):
        from peovim.syntax.languages import get_language_info

        assert get_language_info("") is None
        assert get_language_info("cobol") is None

    def test_register_custom(self):
        from peovim.syntax.languages import LanguageInfo, get_language_info, register_language

        info = LanguageInfo(filetype="custom_test", module_name="nonexistent_pkg")
        register_language("custom_test", info)
        assert get_language_info("custom_test") is info
        # Clean up
        from peovim.syntax.languages import _REGISTRY

        del _REGISTRY["custom_test"]

    def test_tsx_uses_typescript_module(self):
        from peovim.syntax.languages import get_language_info

        info = get_language_info("tsx")
        assert info is not None
        assert "typescript" in info.module_name

    def test_verilog_loads_query_from_repo_file(self):
        from peovim.syntax.languages import get_language_info

        info = get_language_info("verilog")

        assert info is not None
        query = info.get_highlights_query()
        assert query is not None
        assert "module_keyword" in query

    @pytest.mark.skipif(not _markdown_grammar_available(), reason="tree-sitter-markdown not installed")
    def test_markdown_loads_query_from_repo_file(self):
        from peovim.syntax.languages import get_language_info

        info = get_language_info("markdown")

        assert info is not None
        query = info.get_highlights_query()
        assert query is not None
        assert "atx_h1_marker" in query

    def test_c_loads_query_from_repo_file(self):
        from peovim.syntax.languages import get_language_info

        info = get_language_info("c")

        assert info is not None
        query = info.get_highlights_query()
        assert query is not None
        assert "#include" in query
        assert "(comment) @comment" in query

    def test_cpp_loads_query_from_repo_file(self):
        from peovim.syntax.languages import get_language_info

        info = get_language_info("cpp")

        assert info is not None
        query = info.get_highlights_query()
        assert query is not None
        assert "#include" in query
        assert "raw_string_literal" in query

    def test_loads_generic_package_query_path_when_attr_missing(self, tmp_path, monkeypatch):
        from peovim.syntax.languages import LanguageInfo

        package_dir = tmp_path / "fake_tree_sitter_c"
        queries_dir = package_dir / "queries"
        queries_dir.mkdir(parents=True)
        (package_dir / "__init__.py").write_text("def language():\n    return None\n", encoding="utf-8")
        (queries_dir / "highlights.scm").write_text("(identifier) @variable\n", encoding="utf-8")

        monkeypatch.syspath_prepend(str(tmp_path))
        importlib.invalidate_caches()
        sys.modules.pop("fake_tree_sitter_c", None)

        info = LanguageInfo(
            filetype="fake_c",
            module_name="fake_tree_sitter_c",
            highlights_attr="MISSING_QUERY",
        )

        assert info.get_highlights_query() == "(identifier) @variable\n"


# ---------------------------------------------------------------------------
# 5d — Theme system
# ---------------------------------------------------------------------------


class TestThemes:
    def test_style_accepts_hex_colors(self):
        from peovim.core.style import Style

        style = Style(fg="#1A2B3C", bg="#DDEEFF")

        assert style.fg == (26, 43, 60)
        assert style.bg == (221, 238, 255)

    def test_builtin_themes_registered(self):
        from peovim.syntax.themes import get_theme

        for name in ("catppuccin", "gruvbox", "onedark"):
            assert get_theme(name) is not None, f"{name} not registered"

    def test_unknown_theme_returns_none(self):
        from peovim.syntax.themes import get_theme

        assert get_theme("nonexistent") is None

    def test_keyword_has_fg_colour(self):
        from peovim.syntax.themes import get_theme

        for name in ("catppuccin", "gruvbox", "onedark"):
            theme = get_theme(name)
            style = theme.resolve("keyword")
            assert style.fg is not None, f"{name}: keyword.fg is None"

    def test_fallback_to_parent_group(self):
        from peovim.syntax.themes import get_theme

        theme = get_theme("catppuccin")
        # 'keyword.return' falls back to 'keyword' if not explicitly set,
        # or returns its own style — either way should have a fg colour
        style = theme.resolve("keyword.return")
        assert style.fg is not None

    def test_unknown_group_returns_empty_style(self):
        from peovim.core.style import Style
        from peovim.syntax.themes import get_theme

        theme = get_theme("catppuccin")
        style = theme.resolve("totally_unknown_group_xyz")
        assert style == Style()

    def test_register_custom_theme(self):
        from peovim.core.style import Style
        from peovim.syntax.themes import Theme, get_theme, register_theme

        custom = Theme(name="test_custom", groups={"keyword": Style(fg=(1, 2, 3))})
        register_theme("test_custom", custom)
        assert get_theme("test_custom") is custom
        assert get_theme("test_custom").resolve("keyword").fg == (1, 2, 3)

    def test_register_custom_theme_accepts_hex_colors(self):
        from peovim.core.style import Style
        from peovim.syntax.themes import Theme, get_theme, register_theme

        custom = Theme(
            name="test_custom_hex",
            groups={"keyword": Style(fg="#112233")},
            default_fg="#ABCDEF",
        )
        register_theme("test_custom_hex", custom)

        assert get_theme("test_custom_hex") is custom
        assert custom.default_fg == (171, 205, 239)
        assert get_theme("test_custom_hex").resolve("keyword").fg == (17, 34, 51)

    def test_builtin_themes_cover_verilog_specific_groups(self):
        from peovim.syntax.themes import get_theme

        for name in ("catppuccin", "gruvbox", "onedark"):
            theme = get_theme(name)
            for group in (
                "module",
                "parameter",
                "field",
                "function.builtin",
                "constant.macro",
                "keyword.control",
                "keyword.conditional",
            ):
                assert theme.resolve(group).fg is not None, f"{name}: {group}.fg is None"

    def test_gruvbox_titles_use_blue_accent(self):
        from peovim.syntax.themes import get_theme

        theme = get_theme("gruvbox")

        assert theme.resolve("text.title").fg == theme.resolve("function").fg


# ---------------------------------------------------------------------------
# 5e — SyntaxEngine._parse_task
# ---------------------------------------------------------------------------


class TestParseTask:
    @pytest.mark.skipif(not _python_grammar_available(), reason="tree-sitter-python not installed")
    def test_returns_spans_for_python(self):
        from peovim.syntax.engine import _parse_task

        snap = _make_python_snapshot("def foo(x: int) -> None:\n    return x\n")
        spans = _parse_task(snap)
        assert len(spans) > 0
        groups = {s.group for s in spans}
        assert "keyword" in groups or "keyword.function" in groups

    @pytest.mark.skipif(not _python_grammar_available(), reason="tree-sitter-python not installed")
    def test_spans_sorted_by_position(self):
        from peovim.syntax.engine import _parse_task

        snap = _make_python_snapshot("x = 1\ny = 2\n")
        spans = _parse_task(snap)
        for a, b in zip(spans, spans[1:], strict=False):
            assert (a.start_line, a.start_col) <= (b.start_line, b.start_col)

    @pytest.mark.skipif(not _python_grammar_available(), reason="tree-sitter-python not installed")
    def test_keyword_span_on_line_0(self):
        from peovim.syntax.engine import _parse_task

        snap = _make_python_snapshot("def foo(): pass\n")
        spans = _parse_task(snap)
        keyword_spans = [s for s in spans if s.group in ("keyword", "keyword.function")]
        assert any(s.start_line == 0 for s in keyword_spans)

    def test_returns_empty_for_unknown_filetype(self):
        from peovim.core.document import Document
        from peovim.syntax.engine import _parse_task

        doc = Document()
        doc.load_string("hello world\n")
        doc.filetype = ""
        snap = doc.snapshot()
        assert _parse_task(snap) == []

    def test_returns_empty_for_missing_grammar(self):
        from peovim.core.document import Document
        from peovim.syntax.engine import _parse_task

        doc = Document()
        doc.load_string("fn main() {}\n")
        doc.filetype = "cobol"  # not registered
        snap = doc.snapshot()
        assert _parse_task(snap) == []

    @pytest.mark.skipif(not _verilog_grammar_available(), reason="tree-sitter-verilog not installed")
    def test_returns_spans_for_verilog(self):
        from peovim.syntax.engine import _parse_task

        snap = _make_verilog_snapshot("module top(input logic clk); endmodule\n")
        spans = _parse_task(snap)

        assert len(spans) > 0
        groups = {s.group for s in spans}
        assert "keyword" in groups or "type" in groups or "variable" in groups

    @pytest.mark.skipif(not _verilog_grammar_available(), reason="tree-sitter-verilog not installed")
    def test_returns_enriched_spans_for_verilog(self):
        from peovim.syntax.engine import _parse_task

        snap = _make_verilog_snapshot(
            "`define WIDTH 8\n"
            "module top #(parameter WIDTH = 8) (input logic clk, output logic q);\n"
            "  my_mod #(.WIDTH(WIDTH)) u_mod (.clk(clk), .q(q));\n"
            "  always_ff @(posedge clk) begin\n"
            '    $display("hi");\n'
            "  end\n"
            "endmodule\n"
        )

        spans = _parse_task(snap)
        groups = {s.group for s in spans}

        assert "module" in groups
        assert "parameter" in groups
        assert "field" in groups
        assert "function.builtin" in groups
        assert "constant.macro" in groups

    @pytest.mark.skipif(not _c_grammar_available(), reason="tree-sitter-c not installed")
    def test_returns_spans_for_c(self):
        from peovim.core.document import Document
        from peovim.syntax.engine import _parse_task

        doc = Document()
        doc.load_string("int main(void) { return 0; }\n")
        doc.filetype = "c"

        spans = _parse_task(doc.snapshot())

        assert len(spans) > 0
        groups = {s.group for s in spans}
        assert "type" in groups
        assert "keyword" in groups

    @pytest.mark.skipif(not _cpp_grammar_available(), reason="tree-sitter-cpp not installed")
    def test_returns_spans_for_cpp(self):
        from peovim.core.document import Document
        from peovim.syntax.engine import _parse_task

        doc = Document()
        doc.load_string("#include <vector>\nclass Foo { public: int value; };\n")
        doc.filetype = "cpp"

        spans = _parse_task(doc.snapshot())

        assert len(spans) > 0
        groups = {s.group for s in spans}
        assert "keyword" in groups

    @pytest.mark.skipif(not _cpp_grammar_available(), reason="tree-sitter-cpp not installed")
    def test_returns_enriched_spans_for_cpp(self):
        from peovim.core.document import Document
        from peovim.syntax.engine import _parse_task

        doc = Document()
        doc.load_string('#include "foo.h"\n// note\n#define VALUE 1\nclass Foo { public: int value; };\n')
        doc.filetype = "cpp"

        spans = _parse_task(doc.snapshot())

        groups = {s.group for s in spans}
        assert "keyword" in groups
        assert "string" in groups
        assert "comment" in groups
        assert "type" in groups

    @pytest.mark.skipif(not _verilog_grammar_available(), reason="tree-sitter-verilog not installed")
    def test_returns_function_spans_for_verilog(self):
        from peovim.syntax.engine import _parse_task

        snap = _make_verilog_snapshot(
            "module top;\n"
            "  function automatic logic calc(input logic a);\n"
            "    calc = '0;\n"
            "  endfunction\n"
            "  task do_work(input logic b);\n"
            "  endtask\n"
            "endmodule\n"
        )

        spans = _parse_task(snap)
        groups = {s.group for s in spans}

        assert "function" in groups

    @pytest.mark.skipif(not _verilog_grammar_available(), reason="tree-sitter-verilog not installed")
    def test_returns_control_and_type_spans_for_verilog(self):
        from peovim.syntax.engine import _parse_task

        snap = _make_verilog_snapshot(
            "module top(input logic clk, input logic rst_n, output logic q);\n"
            "  wire ready;\n"
            "  always_ff @(posedge clk) begin\n"
            "    if (!rst_n) begin\n"
            "      q <= '0;\n"
            "    end else begin\n"
            "      q <= ready;\n"
            "    end\n"
            "  end\n"
            "endmodule\n"
        )

        spans = _parse_task(snap)
        groups = {s.group for s in spans}

        assert "keyword.control" in groups
        assert "keyword.conditional" in groups
        assert "type.builtin" in groups
        assert "variable" in groups

    @pytest.mark.skipif(not _verilog_grammar_available(), reason="tree-sitter-verilog not installed")
    def test_returns_comment_number_and_bracket_spans_for_verilog(self):
        from peovim.syntax.engine import _parse_task

        snap = _make_verilog_snapshot(
            "module top(input wire [3:0] data_in);\n  /* block comment */\n  assign ready = 1'b1;\nendmodule\n"
        )

        spans = _parse_task(snap)
        groups = {s.group for s in spans}

        assert "comment" in groups
        assert "number" in groups
        assert "punctuation.bracket" in groups
        assert "type.builtin" in groups

    @pytest.mark.skipif(not _markdown_grammar_available(), reason="tree-sitter-markdown not installed")
    def test_returns_title_and_literal_spans_for_markdown(self):
        from peovim.syntax.engine import _parse_task

        snap = _make_markdown_snapshot("# Title\n\n```python\nprint('x')\n```\n")
        spans = _parse_task(snap)
        groups = {s.group for s in spans}

        assert "text.title" in groups
        assert "text.literal" in groups or "punctuation.delimiter" in groups


# ---------------------------------------------------------------------------
# 5g — Renderer integration: syntax spans applied to cells
# ---------------------------------------------------------------------------


class TestRendererSyntax:
    @pytest.mark.skipif(not _python_grammar_available(), reason="tree-sitter-python not installed")
    def test_keyword_cells_coloured(self):
        """Cells that cover 'def' should get the keyword colour from the theme."""
        from peovim.core.document import Document
        from peovim.core.window import Window
        from peovim.syntax.engine import _parse_task
        from peovim.syntax.themes import get_theme
        from peovim.ui.layout import Rect
        from peovim.ui.window_renderer import render_window

        doc = Document()
        doc.load_string("def foo(): pass\n")
        doc.filetype = "python"
        win = Window(doc, width=40, height=5)
        snap = win.snapshot()

        spans = _parse_task(snap.buffer_snapshot)
        theme = get_theme("catppuccin")
        rect = Rect(0, 0, 40, 5)

        grid = render_window(snap, rect, is_active=True, highlight_spans=spans, theme=theme)

        # 'def' is at col 0-2 on row 0; gutter_w=0 (no number option)
        # col 0 is the cursor position and gets overwritten with cursor style;
        # check col 1 ('e' in 'def') which should still carry the keyword fg.
        keyword_fg = theme.resolve("keyword").fg
        cell_e = grid._current[0][1]  # 'e' of 'def'
        assert cell_e[1] == keyword_fg, f"Expected keyword fg {keyword_fg}, got {cell_e[1]}"

    def test_render_without_spans_unchanged(self):
        """render_window without spans must produce plain text (no crash)."""
        from peovim.core.document import Document
        from peovim.core.window import Window
        from peovim.ui.layout import Rect
        from peovim.ui.window_renderer import render_window

        doc = Document()
        doc.load_string("hello world\n")
        win = Window(doc, width=20, height=3)
        snap = win.snapshot()
        rect = Rect(0, 0, 20, 3)
        grid = render_window(snap, rect, is_active=True)
        # First cell should be 'h'
        assert grid._current[0][0][0] == "h"

    @pytest.mark.skipif(not _verilog_grammar_available(), reason="tree-sitter-verilog not installed")
    def test_verilog_control_keywords_and_identifiers_use_different_colours(self):
        from peovim.core.document import Document
        from peovim.core.window import Window
        from peovim.syntax.engine import _parse_task
        from peovim.syntax.themes import get_theme
        from peovim.ui.layout import Rect
        from peovim.ui.window_renderer import render_window

        doc = Document()
        doc.load_string(
            "module top(input logic clk, input logic rst_n, output logic q);\n"
            "  wire ready;\n"
            "  always_ff @(posedge clk) begin\n"
            "    if (!rst_n) begin\n"
            "      q <= '0;\n"
            "    end else begin\n"
            "      q <= ready;\n"
            "    end\n"
            "  end\n"
            "endmodule\n"
        )
        doc.filetype = "verilog"

        win = Window(doc, width=80, height=12)
        snap = win.snapshot()
        spans = _parse_task(snap.buffer_snapshot)
        theme = get_theme("catppuccin")
        rect = Rect(0, 0, 80, 12)

        grid = render_window(snap, rect, is_active=True, highlight_spans=spans, theme=theme)

        control_fg = theme.resolve("keyword.control").fg
        conditional_fg = theme.resolve("keyword.conditional").fg
        type_fg = theme.resolve("type.builtin").fg
        variable_fg = theme.resolve("variable").fg

        cell_wire = grid._current[1][3]  # 'i' in "wire"
        cell_ready = grid._current[1][8]  # 'e' in "ready"
        cell_begin = grid._current[2][28]  # 'e' in "begin"
        cell_if = grid._current[3][4]  # 'i' in "if"

        assert cell_wire[1] == type_fg
        assert cell_ready[1] == variable_fg
        assert cell_begin[1] == control_fg
        assert cell_if[1] == conditional_fg
        assert cell_begin[1] != cell_ready[1]
        assert cell_if[1] != cell_ready[1]

    @pytest.mark.skipif(not _verilog_grammar_available(), reason="tree-sitter-verilog not installed")
    def test_verilog_comments_brackets_and_numbers_are_coloured(self):
        from peovim.core.document import Document
        from peovim.core.window import Window
        from peovim.syntax.engine import _parse_task
        from peovim.syntax.themes import get_theme
        from peovim.ui.layout import Rect
        from peovim.ui.window_renderer import render_window

        doc = Document()
        doc.load_string(
            "module top(input wire [3:0] data_in);\n  /* block comment */\n  assign ready = 1'b1;\nendmodule\n"
        )
        doc.filetype = "verilog"

        win = Window(doc, width=80, height=6)
        snap = win.snapshot()
        spans = _parse_task(snap.buffer_snapshot)
        theme = get_theme("catppuccin")
        rect = Rect(0, 0, 80, 6)

        grid = render_window(snap, rect, is_active=True, highlight_spans=spans, theme=theme)

        bracket_fg = theme.resolve("punctuation.bracket").fg
        comment_fg = theme.resolve("comment").fg
        number_fg = theme.resolve("number").fg

        cell_bracket = grid._current[0][22]  # '[' in "[3:0]"
        cell_comment = grid._current[1][5]  # '*' in "/* block comment */"
        cell_number = grid._current[2][17]  # '1' in "1'b1"

        assert cell_bracket[1] == bracket_fg
        assert cell_comment[1] == comment_fg
        assert cell_number[1] == number_fg

    @pytest.mark.skipif(not _markdown_grammar_available(), reason="tree-sitter-markdown not installed")
    def test_markdown_heading_cells_use_title_colour(self):
        from peovim.core.document import Document
        from peovim.core.window import Window
        from peovim.syntax.engine import _parse_task
        from peovim.syntax.themes import get_theme
        from peovim.ui.layout import Rect
        from peovim.ui.window_renderer import render_window

        doc = Document()
        doc.load_string("# Title\n")
        doc.filetype = "markdown"

        win = Window(doc, width=40, height=4)
        win.cursor.move_to(0, 6)
        snap = win.snapshot()
        spans = _parse_task(snap.buffer_snapshot)
        theme = get_theme("gruvbox")
        rect = Rect(0, 0, 40, 4)

        grid = render_window(snap, rect, is_active=False, highlight_spans=spans, theme=theme)

        title_fg = theme.resolve("text.title").fg
        cell_marker = grid._current[0][0]  # '#'
        cell_title = grid._current[0][2]  # 'T' in '# Title'

        assert cell_marker[1] == title_fg
        assert cell_title[1] == title_fg


# ---------------------------------------------------------------------------
# 5h — :colorscheme command
# ---------------------------------------------------------------------------


class TestColorschemeCommand:
    def _make_ctx(self, theme="catppuccin"):
        from peovim.core.editor_state import EditorState

        state = EditorState()
        state.active_theme = theme

        class Ctx:
            editor_state = state

        return Ctx()

    def test_colorscheme_switches_theme(self):
        from peovim.commands.builtin import _cmd_colorscheme
        from peovim.commands.parser import ParsedCommand

        ctx = self._make_ctx("catppuccin")
        cmd = ParsedCommand(cmd="colorscheme", args="gruvbox")
        _cmd_colorscheme(cmd, ctx)
        assert ctx.editor_state.active_theme == "gruvbox"

    def test_colorscheme_unknown_sets_error_message(self):
        from peovim.commands.builtin import _cmd_colorscheme
        from peovim.commands.parser import ParsedCommand

        ctx = self._make_ctx("catppuccin")
        cmd = ParsedCommand(cmd="colorscheme", args="nonexistent")
        _cmd_colorscheme(cmd, ctx)
        assert ctx.editor_state.active_theme == "catppuccin"  # unchanged
        assert "nonexistent" in ctx.editor_state.message
