from types import SimpleNamespace

import pytest

from peovim.core.options import OptionError, OptionsStore
from peovim.ui.icons import nerd_icons_enabled, path_icon


@pytest.mark.parametrize(
    "environment,expected",
    [
        ({"TERM_PROGRAM": "WezTerm", "TERM": "xterm-256color"}, True),
        ({"TERM_PROGRAM": "wezterm"}, True),
        ({"TERM_PROGRAM": "WezTerm", "TERM": "dumb"}, False),
        ({"TERM_PROGRAM": "tmux", "WEZTERM_PANE": "1"}, False),
        ({"WEZTERM_PANE": "1"}, False),
        ({"TERM": "xterm-256color"}, False),
        ({}, False),
    ],
)
def test_auto_icons_uses_positive_terminal_identity(environment, expected):
    assert nerd_icons_enabled("auto", environment) is expected


def test_manual_override_and_option_validation():
    assert nerd_icons_enabled("on", {})
    assert not nerd_icons_enabled("off", {"TERM_PROGRAM": "WezTerm"})
    with pytest.raises(OptionError):
        OptionsStore().set("icons", "sometimes")


def test_explorer_uses_one_icon_without_changing_labels_or_paths(tmp_path, monkeypatch):
    from peovim.plugins.explorer import _make_nodes

    monkeypatch.setenv("TERM_PROGRAM", "WezTerm")
    monkeypatch.setenv("TERM", "xterm-256color")
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "inside.md").write_text("# Heading")
    (tmp_path / "example.py").write_text("pass")
    nodes = _make_nodes(tmp_path)
    directory = next(n for n in nodes if n.label == "folder")
    file = next(n for n in nodes if n.label == "example.py")
    assert directory.icon == path_icon(folder, directory=True)
    assert len(file.icon) == 1
    assert file.value == str(tmp_path / "example.py")
    assert next(n for n in directory.get_children() if n.label == "inside.md").icon
    assert all(not n.icon for n in _make_nodes(tmp_path, icons="off"))


def test_picker_preserves_file_identity_with_icons(tmp_path):
    from peovim.plugins.picker import _path_item

    path = tmp_path / "README.md"
    plain = _path_item(path, tmp_path, icons="off")
    decorated = _path_item(path, tmp_path, icons="on")
    assert decorated.path == plain.path == path
    assert plain.label == "README.md"
    assert decorated.label == path_icon(path, mode="on") + " README.md"


def markdown_fixture(text):
    pytest.importorskip("tree_sitter_markdown")
    from peovim.core.document import Document
    from peovim.core.window import Window
    from peovim.modal.engine import ModalEngine
    from peovim.syntax.engine import _parse_task
    from peovim.ui.window_render_controller import WindowRenderController

    doc = Document()
    doc.load_string(text)
    doc.filetype = "markdown"
    win = Window(doc, width=30, height=20)
    win.options["signcolumn"] = "no"
    engine = ModalEngine()
    spans = _parse_task(doc.snapshot(), buffer_id=id(doc))
    host = SimpleNamespace(_syntax_cache={id(doc): (doc.version, spans)}, _engine=engine)
    return win, WindowRenderController(host)


def test_markdown_bullets_only_decorate_parsed_lists_and_preserve_source():
    text = "# Heading\n\n- first\n  * nested\n+ last\n\n---\n\n```md\n- code\n```\n\n    * indented code\n"
    win, controller = markdown_fixture(text)
    decorations = controller._build_markdown_bullets(win, win.snapshot())
    assert [(d.line, d.col, d.display_char) for d in decorations] == [(2, 0, "•"), (3, 2, "•"), (4, 0, "•")]
    assert win.document.get_line(2) == "- first"
    from peovim.ui.layout import Rect
    from peovim.ui.window_renderer import render_window

    grid = render_window(win.snapshot(), Rect(0, 0, 30, 20), True, extra_decorations=decorations)
    assert grid.read_cell(2, 0)[0] == "•"
    assert grid.read_cell(9, 0)[0] == "-"


def test_markdown_bullets_reveal_cursor_line_and_visual_selection():
    from peovim.modal.engine import Mode

    win, controller = markdown_fixture("- first\n- second\n")
    assert [d.line for d in controller._build_markdown_bullets(win, win.snapshot())] == [1]
    win.cursor.move_to(1, 0)
    assert [d.line for d in controller._build_markdown_bullets(win, win.snapshot())] == [0]
    controller._host._engine.mode = Mode.VISUAL_CHAR
    assert controller._build_markdown_bullets(win, win.snapshot()) == []


def test_markdown_bullets_respect_disable_and_stale_syntax():
    win, controller = markdown_fixture("heading\n- item\n")
    assert controller._build_markdown_bullets(win, win.snapshot())
    win.options["markdownbullets"] = False
    assert controller._build_markdown_bullets(win, win.snapshot()) == []
    win.options["markdownbullets"] = True
    controller._host._syntax_cache[id(win.document)] = (-1, [])
    assert controller._build_markdown_bullets(win, win.snapshot()) == []


def test_markdown_bullet_renders_red_and_theme_can_override():
    from peovim.core.style import Style
    from peovim.syntax.themes import Theme
    from peovim.ui.layout import Rect
    from peovim.ui.window_renderer import render_window

    win, controller = markdown_fixture("heading\n- item\n")
    theme = Theme("custom", {"text.strong": Style(fg="#569CD6")})
    decorations = controller._build_markdown_bullets(win, win.snapshot(), theme=theme)
    grid = render_window(win.snapshot(), Rect(0, 0, 30, 20), True, extra_decorations=decorations, theme=theme)
    assert grid.read_cell(1, 0)[:2] == ("•", (244, 71, 71))
    theme.groups["markdown.bullet"] = Style(fg="#FF0000")
    assert controller._build_markdown_bullets(win, win.snapshot(), theme=theme)[0].style.fg == (255, 0, 0)


def test_markdown_strong_is_light_purple_with_visible_delimiters():
    from peovim.core.style import Style
    from peovim.syntax.themes import Theme
    from peovim.ui.layout import Rect
    from peovim.ui.window_renderer import render_window

    win, controller = markdown_fixture("heading\n**word** and __other__\n")
    theme = Theme("custom", {"text.strong": Style(fg="#569CD6")})
    spans = controller._host._syntax_cache[id(win.document)][1]
    strong = [s for s in spans if s.group == "markdown.strong"]
    assert [(s.start_line, s.start_col, s.end_col) for s in strong] == [(1, 0, 8), (1, 13, 22)]
    grid = render_window(win.snapshot(), Rect(0, 0, 30, 20), True, highlight_spans=spans, theme=theme)
    assert "".join(grid.read_cell(1, c)[0] for c in range(8)) == "**word**"
    assert all(grid.read_cell(1, c)[1] == (203, 166, 247) for c in range(8))
    assert win.document.get_line(1) == "**word** and __other__"


def test_markdown_strong_excludes_code_and_handles_utf8_and_multiline():
    win, controller = markdown_fixture(
        "é **bold**\n\n**two\nlines**\n\n`**inline code**`\n\n```\n**fenced code**\n```\n\n    **indented code**\n\n\\*\\*escaped\\*\\*\n"
    )
    spans = controller._host._syntax_cache[id(win.document)][1]
    strong = [s for s in spans if s.group == "markdown.strong"]
    assert [(s.start_line, s.start_col, s.end_line, s.end_col) for s in strong] == [(0, 3, 0, 11), (2, 0, 3, 7)]
