"""Tests for peovim.ui.markdown — render_markdown()."""

from peovim.syntax.themes import get_theme
from peovim.ui.markdown import render_markdown, render_rich_markdown


def test_empty():
    assert render_markdown("") == [""]


def test_plain_text():
    result = render_markdown("hello world")
    assert result == ["hello world"]


def test_fenced_code_block():
    md = "```python\nx = 1\n```"
    result = render_markdown(md)
    assert "[python]" in result
    assert "  x = 1" in result


def test_fenced_code_block_no_lang():
    md = "```\nsome code\n```"
    result = render_markdown(md)
    assert "  some code" in result


def test_inline_code():
    result = render_markdown("Use `foo()` here")
    assert result == ["Use foo() here"]


def test_bold_stars():
    result = render_markdown("**hello** world")
    assert result == ["hello world"]


def test_bold_underscores():
    result = render_markdown("__hello__ world")
    assert result == ["hello world"]


def test_italic_stars():
    result = render_markdown("*hello* world")
    assert result == ["hello world"]


def test_italic_underscores():
    result = render_markdown("_hello_ world")
    assert result == ["hello world"]


def test_heading():
    result = render_markdown("## My Title")
    assert result == ["My Title"]


def test_horizontal_rule():
    result = render_markdown("---")
    assert result[0].startswith("─")


def test_multi_paragraph():
    md = "first line\n\nsecond line"
    result = render_markdown(md)
    assert "first line" in result
    assert "" in result
    assert "second line" in result


def test_code_block_preserves_content():
    md = "before\n```\nif x:\n    pass\n```\nafter"
    result = render_markdown(md)
    assert "  if x:" in result
    assert "      pass" in result
    assert "before" in result
    assert "after" in result


def test_bold_italic_combined():
    result = render_markdown("***bold italic***")
    assert result == ["bold italic"]


def test_xml_summary_is_rendered_to_plain_text():
    result = render_markdown("<summary>Returns <c>value</c> text.</summary>")
    assert result == ["Returns value text."]


def test_xml_code_block_is_rendered_readably():
    md = "<summary>Example:</summary><code>let x = 1\nreturn x;</code>"
    result = render_markdown(md)
    assert "Example:" in result
    assert "  let x = 1" in result
    assert "  return x;" in result


def test_rich_markdown_styles_python_code_block_keywords_and_strings():
    theme = get_theme("catppuccin")
    result = render_rich_markdown('```python\nreturn "x"\n```', theme=theme)
    code_line = result[1]
    assert isinstance(code_line, list)
    assert any(text == "return" and style == theme.resolve("keyword") for text, style in code_line)
    assert any(text == '"x"' and style == theme.resolve("string") for text, style in code_line)


def test_rich_markdown_styles_xml_code_block_tags():
    theme = get_theme("catppuccin")
    result = render_rich_markdown('```xml\n<tag attr="x">\n```', theme=theme)
    code_line = result[1]
    assert isinstance(code_line, list)
    assert any(text == "tag" and style == theme.resolve("tag") for text, style in code_line)
    assert any(text == "attr" and style == theme.resolve("tag.attribute") for text, style in code_line)
