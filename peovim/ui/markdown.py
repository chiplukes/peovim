"""
ui.markdown — minimal markdown-to-plaintext renderer for hover floats.

Converts the most common LSP hover markdown constructs to clean terminal text:
  - Fenced code blocks (``` lang ... ```) → indented, with separator lines
  - Inline code (`foo`) → bare text
  - Bold / italic (**x**, *x*, __x__, _x_) → bare text
  - Horizontal rules (--- / ***) → ─── line
  - ATX headings (## Title) → bare title
  - Blank-line paragraph separation preserved
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from peovim.core.style import Style

if TYPE_CHECKING:
    from peovim.syntax.themes import Theme

RichSegment = tuple[str, Style]
RichLine = str | list[RichSegment]

_PYTHON_KEYWORDS = {
    "False",
    "None",
    "True",
    "and",
    "as",
    "assert",
    "async",
    "await",
    "break",
    "case",
    "class",
    "continue",
    "def",
    "del",
    "elif",
    "else",
    "except",
    "finally",
    "for",
    "from",
    "if",
    "import",
    "in",
    "is",
    "lambda",
    "match",
    "not",
    "or",
    "pass",
    "raise",
    "return",
    "try",
    "while",
    "with",
    "yield",
}
_JS_KEYWORDS = {
    "async",
    "await",
    "break",
    "case",
    "class",
    "const",
    "continue",
    "default",
    "else",
    "export",
    "extends",
    "false",
    "for",
    "function",
    "if",
    "import",
    "let",
    "new",
    "null",
    "return",
    "static",
    "switch",
    "this",
    "throw",
    "true",
    "try",
    "type",
    "typeof",
    "var",
    "while",
}


def render_markdown(text: str) -> list[str]:
    """Return a list of display lines suitable for a float content list."""
    return [_plain_text_from_rich_line(line) for line in render_rich_markdown(text)]


def render_rich_markdown(text: str, theme: Theme | None = None) -> list[RichLine]:
    """Return float lines, preserving styled segments for code blocks when possible."""
    if not text:
        return [""]

    xml_lines = _render_xml_doc(text, theme=theme)
    if xml_lines is not None:
        return xml_lines

    lines = text.splitlines()
    result: list[RichLine] = []
    in_code_block = False
    lang = ""

    for raw in lines:
        # -- Fenced code block boundaries --
        fence_match = re.match(r"^(`{3,}|~{3,})(.*)", raw)
        if fence_match:
            if not in_code_block:
                in_code_block = True
                lang = fence_match.group(2).strip()
                # Separator before code
                if result and result[-1] != "":
                    result.append("")
                if lang:
                    result.append([(f"[{lang}]", _style_for(theme, "comment"))])
            else:
                in_code_block = False
                lang = ""
                if result and result[-1] != "":
                    result.append("")
            continue

        if in_code_block:
            result.append(_render_code_line(raw, theme=theme, lang=lang))
            continue

        # -- Horizontal rule --
        if re.fullmatch(r"[-*_]{3,}\s*", raw):
            result.append("─" * 40)
            continue

        # -- ATX headings --
        heading_match = re.match(r"^(#{1,6})\s+(.*)", raw)
        if heading_match:
            raw = heading_match.group(2).strip()

        # -- Inline transformations --
        # Inline code (before bold/italic to avoid stripping backtick contents)
        raw = re.sub(r"`([^`]+)`", r"\1", raw)
        # Bold + italic together (***text***)
        raw = re.sub(r"\*{3}([^*]+)\*{3}", r"\1", raw)
        # Bold
        raw = re.sub(r"\*\*([^*]+)\*\*", r"\1", raw)
        raw = re.sub(r"__([^_]+)__", r"\1", raw)
        # Italic
        raw = re.sub(r"\*([^*]+)\*", r"\1", raw)
        raw = re.sub(r"_([^_]+)_", r"\1", raw)

        result.append(html.unescape(raw))

    return result or [""]


def render_rich_code_preview(
    lines: list[str],
    *,
    lang: str = "",
    theme: Theme | None = None,
    start_line: int = 1,
    highlight_line: int | None = None,
    show_line_numbers: bool = False,
) -> list[RichLine]:
    """Return syntax-colored preview lines for picker-like UIs."""
    if not lines:
        return []

    rendered: list[RichLine] = []
    number_width = max(4, len(str(start_line + len(lines) - 1)))
    for idx, raw in enumerate(lines):
        line_no = start_line + idx
        if show_line_numbers:
            marker = ">" if highlight_line == line_no else " "
            prefix_style = _style_for(theme, "text.title" if marker == ">" else "comment")
            segments: list[RichSegment] = [(f"{marker}{line_no:>{number_width}}: ", prefix_style)]
            if raw:
                segments.extend(_highlight_code(raw, lang, theme))
            rendered.append(segments)
            continue
        if not raw:
            rendered.append("")
            continue
        rendered.append(_highlight_code(raw, lang, theme))
    return rendered


def _render_xml_doc(text: str, theme: Theme | None = None) -> list[RichLine] | None:
    stripped = text.strip()
    if not stripped.startswith("<") or ">" not in stripped:
        return None
    try:
        root = ET.fromstring(f"<root>{stripped}</root>")
    except ET.ParseError:
        return None

    lines: list[RichLine] = []
    for child in root:
        lines.extend(_render_xml_node(child, theme=theme))
    cleaned = [_rstrip_rich_line(line) for line in lines]
    while cleaned and cleaned[0] == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return cleaned or [""]


def _render_xml_node(node: ET.Element, indent: int = 0, theme: Theme | None = None) -> list[RichLine]:
    tag = node.tag.split("}")[-1].lower()
    indent_str = "  " * indent
    lines: list[RichLine] = []

    if tag in {"summary", "remarks", "example", "value"}:
        body = _collect_xml_text(node, indent, theme=theme)
        if body:
            lines.extend(body)
            lines.append("")
        return lines

    if tag == "para":
        body = _collect_xml_text(node, indent, theme=theme)
        if body:
            lines.extend(body)
            lines.append("")
        return lines

    if tag in {"returns", "exception"}:
        label = "Returns:" if tag == "returns" else "Exception:"
        body_str = " ".join(
            _plain_text_from_rich_line(line).strip()
            for line in _collect_xml_text(node, 0, theme=theme)
            if _plain_text_from_rich_line(line).strip()
        )
        return [f"{label} {body_str}".rstrip(), ""]

    if tag == "paramref":
        name = node.attrib.get("name", "")
        return [name] if name else []

    if tag == "see":
        cref = node.attrib.get("cref") or node.attrib.get("langword") or node.attrib.get("href", "")
        cref = re.sub(r"^[A-Z]:", "", cref)
        return [cref] if cref else []

    if tag in {"c", "code"}:
        text = html.unescape("".join(node.itertext())).rstrip("\n")
        if tag == "c":
            return [[(text, _style_for(theme, "text.literal"))]]
        rendered: list[RichLine] = []
        for raw in text.splitlines() or [""]:
            rendered.append(_render_code_line(raw, indent=indent, theme=theme))
        rendered.append("")
        return rendered

    if tag in {"list", "item"}:
        for child in node:
            child_tag = child.tag.split("}")[-1].lower()
            if child_tag == "item":
                item_text = " ".join(
                    _plain_text_from_rich_line(line).strip()
                    for line in _collect_xml_text(child, 0, theme=theme)
                    if _plain_text_from_rich_line(line).strip()
                )
                if item_text:
                    lines.append(f"{indent_str}- {item_text}")
            else:
                lines.extend(_render_xml_node(child, indent + 1, theme=theme))
        if lines:
            lines.append("")
        return lines

    return _collect_xml_text(node, indent, theme=theme)


def _collect_xml_text(node: ET.Element, indent: int = 0, theme: Theme | None = None) -> list[RichLine]:
    tokens: list[str] = []
    if node.text and node.text.strip():
        tokens.append(html.unescape(node.text.strip()))
    for child in node:
        tokens.extend(
            _plain_text_from_rich_line(line)
            for line in _render_xml_node(
                child, indent + 1 if child.tag.split("}")[-1].lower() == "para" else indent, theme=theme
            )
        )
        if child.tail and child.tail.strip():
            tokens.append(html.unescape(child.tail.strip()))
    if not tokens:
        return []
    lines: list[RichLine] = []
    current = ""
    for token in tokens:
        if token == "":
            if current:
                lines.append(("  " * indent) + current.strip())
                current = ""
            lines.append("")
            continue
        if token.startswith("  ") or token.startswith("-"):
            if current:
                lines.append(("  " * indent) + current.strip())
                current = ""
            lines.append(token)
            continue
        current = f"{current} {token}".strip()
    if current:
        lines.append(("  " * indent) + _normalize_inline_spacing(current.strip()))
    return lines


def _render_code_line(raw: str, indent: int = 0, theme: Theme | None = None, lang: str = "") -> RichLine:
    prefix = ("  " * indent) + "  "
    text = html.unescape(raw)
    if not text:
        return prefix
    segments = [(prefix, _style_for(theme, "text"))]
    segments.extend(_highlight_code(text, lang, theme))
    return segments


def _normalize_inline_spacing(text: str) -> str:
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


def _highlight_code(text: str, lang: str, theme: Theme | None) -> list[RichSegment]:
    lang = (lang or "").lower()
    if lang in {"python", "py"}:
        return _highlight_regex_line(
            text,
            re.compile(
                r"(?P<comment>#.*$)|(?P<string>'[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\")|(?P<number>\b\d+(?:\.\d+)?\b)|(?P<keyword>\b(?:"
                + "|".join(sorted(_PYTHON_KEYWORDS))
                + r")\b)"
            ),
            theme,
        )
    if lang in {"typescript", "ts", "javascript", "js", "json", "tsx", "jsx"}:
        return _highlight_regex_line(
            text,
            re.compile(
                r"(?P<comment>//.*$)|(?P<string>'[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\")|(?P<number>\b\d+(?:\.\d+)?\b)|(?P<keyword>\b(?:"
                + "|".join(sorted(_JS_KEYWORDS))
                + r")\b)"
            ),
            theme,
        )
    if lang in {"xml", "html", "xaml"}:
        return _highlight_xml_line(text, theme)
    return [(text, _style_for(theme, "text"))]


def _highlight_regex_line(text: str, pattern: re.Pattern[str], theme: Theme | None) -> list[RichSegment]:
    segments: list[RichSegment] = []
    last = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        if start > last:
            segments.append((text[last:start], _style_for(theme, "text")))
        group = match.lastgroup or "text"
        style_group = {
            "comment": "comment",
            "string": "string",
            "number": "number",
            "keyword": "keyword",
        }.get(group, "text")
        segments.append((text[start:end], _style_for(theme, style_group)))
        last = end
    if last < len(text):
        segments.append((text[last:], _style_for(theme, "text")))
    return segments or [(text, _style_for(theme, "text"))]


def _highlight_xml_line(text: str, theme: Theme | None) -> list[RichSegment]:
    segments: list[RichSegment] = []
    token_re = re.compile(r"</?|/?>|\b[\w:-]+(?==)|\b[\w:-]+\b|\"[^\"]*\"")
    in_tag = False
    last = 0
    for match in token_re.finditer(text):
        start, end = match.span()
        if start > last:
            segments.append((text[last:start], _style_for(theme, "text")))
        token = text[start:end]
        style_group = "text"
        if token in {"<", "</", ">", "/>"}:
            style_group = "punctuation"
            in_tag = token in {"<", "</"} or (in_tag and token not in {">", "/>"})
            if token in {">", "/>"}:
                in_tag = False
        elif token.startswith('"'):
            style_group = "string"
        elif in_tag and segments and segments[-1][0] in {"<", "</"}:
            style_group = "tag"
        elif in_tag and "=" not in token:
            style_group = "tag.attribute"
        segments.append((token, _style_for(theme, style_group)))
        last = end
    if last < len(text):
        segments.append((text[last:], _style_for(theme, "text")))
    return segments or [(text, _style_for(theme, "text"))]


def _style_for(theme: Theme | None, group: str) -> Style:
    return theme.resolve(group) if theme is not None else Style()


def _plain_text_from_rich_line(line: RichLine) -> str:
    if isinstance(line, str):
        return line
    return "".join(text for text, _style in line)


def _rstrip_rich_line(line: RichLine) -> RichLine:
    if isinstance(line, str):
        return line.rstrip()
    text = _plain_text_from_rich_line(line).rstrip()
    if not text:
        return ""
    trimmed: list[RichSegment] = []
    remaining = len(text)
    for segment_text, style in line:
        if remaining <= 0:
            break
        piece = segment_text[:remaining]
        trimmed.append((piece, style))
        remaining -= len(piece)
    return trimmed
