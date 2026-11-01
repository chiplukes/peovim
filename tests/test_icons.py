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


def test_diagnostic_icons_preserve_locations_and_text_fallbacks():
    from peovim.plugins.diagnostics_panel import _build_diagnostic_nodes
    from peovim.ui.icons import diagnostic_icon

    diagnostics = [
        {"path": "file.py", "line": i, "col": 2, "severity": severity, "message": "message"}
        for i, severity in enumerate("EWIH")
    ]
    decorated = _build_diagnostic_nodes(diagnostics, icons="on")[0].get_children()
    plain = _build_diagnostic_nodes(diagnostics, icons="off")[0].get_children()
    assert [n.icon for n in plain] == ["err", "warn", "info", "hint"]
    assert [n.icon for n in decorated] == [diagnostic_icon(s, mode="on") for s in "EWIH"]
    assert [n.value for n in decorated] == [n.value for n in plain]
    assert [n.label for n in decorated] == [n.label for n in plain]
    assert all(n.icon_fg is not None for n in decorated)


def test_outline_icons_propagate_to_children_and_preserve_unknown_kinds():
    from peovim.plugins.outline import _build_outline_nodes
    from peovim.ui.icons import symbol_icon

    symbols = [
        {
            "name": "Example",
            "kind": "class",
            "line": 0,
            "col": 0,
            "children": [{"name": "run", "kind": "method", "line": 1, "col": 4}],
        }
    ]
    decorated = _build_outline_nodes(symbols, icons="on")[0]
    plain = _build_outline_nodes(symbols, icons="off")[0]
    assert plain.icon == "cls"
    assert decorated.icon == symbol_icon("class", mode="on")
    assert decorated.get_children()[0].icon == symbol_icon("method", mode="on")
    assert plain.get_children()[0].icon == "meth"
    assert decorated.value == plain.value
    assert decorated.get_children()[0].value == plain.get_children()[0].value
    assert _build_outline_nodes([{"name": "x", "kind": "unknown"}], icons="on")[0].icon == "unk"


@pytest.mark.parametrize(
    "kind,name", [(20, "enum"), (21, "const"), (22, "struct"), (23, "event"), (24, "operator"), (25, "type")]
)
def test_completion_icons_use_lsp_kind_numbers(kind, name):
    from peovim.ui.icons import completion_icon, symbol_icon

    assert completion_icon(kind, mode="on") == symbol_icon(name, mode="on")
    assert completion_icon(kind, mode="off") == ""
    assert completion_icon(999, mode="on") == ""


def test_completion_icons_keep_label_position_and_insert_text():
    from peovim.ui.cell_grid import CellGrid
    from peovim.ui.completion import CompletionPopup
    from peovim.ui.icons import completion_icon

    rendered = []
    for mode in ("on", "off"):
        popup = CompletionPopup()
        popup.icons = mode
        popup.open([{"label": "calculate", "kind": 3, "insertText": "calculate()"}], 0, 0)
        grid = CellGrid(50, 10)
        popup.render(grid, 0, 0)
        text = "".join(grid.read_cell(2, c)[0] for c in range(grid.width))
        rendered.append(text)
        assert popup.accept() == "calculate()"
    assert completion_icon(3, mode="on") in rendered[0]
    assert "fn " in rendered[1]
    assert rendered[0].index("calculate") == rendered[1].index("calculate")


def test_status_line_icons_preserve_dirty_marker_and_coordinates(tmp_path):
    from peovim.core.document import Document
    from peovim.core.editor_state import EditorState
    from peovim.core.window import Window
    from peovim.modal.engine import Mode
    from peovim.ui.cell_grid import CellGrid
    from peovim.ui.layout import Rect
    from peovim.ui.status_bar import render_status_bar

    doc = Document()
    doc.load_string("hello")
    doc.path = tmp_path / "example.py"
    doc.insert(0, 0, "x")
    win = Window(doc)
    state = EditorState()
    for mode in ("on", "off"):
        state.options.set("icons", mode)
        grid = CellGrid(60, 1)
        render_status_bar(win, Mode.NORMAL, Rect(0, 0, 60, 1), grid, editor_state=state)
        text = "".join(grid.read_cell(0, c)[0] for c in range(grid.width))
        assert "example.py [+]" in text
        assert "1:1" in text
        assert (path_icon(doc.path, mode="on") in text) is (mode == "on")


def test_tree_icon_color_does_not_color_message_or_selection():
    from peovim.ui.cell_grid import CellGrid
    from peovim.ui.tree_view import TreeNode, TreeView

    color = (244, 71, 71)
    nodes = [TreeNode("selected"), TreeNode("message", icon="!", icon_fg=color)]
    tree = TreeView(nodes, width=30)
    grid = CellGrid(30, 3)
    tree.render(grid)
    text = "".join(grid.read_cell(1, c)[0] for c in range(grid.width))
    icon_col = text.index("!")
    assert grid.read_cell(1, icon_col)[1] == color
    assert grid.read_cell(1, text.index("message"))[1] != color
    tree._selected_idx = 1
    tree.render(grid)
    assert grid.read_cell(1, icon_col)[1] != color


def test_status_line_drops_icon_before_hiding_filename(tmp_path):
    from peovim.core.document import Document
    from peovim.core.editor_state import EditorState
    from peovim.core.window import Window
    from peovim.modal.engine import Mode
    from peovim.ui.cell_grid import CellGrid
    from peovim.ui.layout import Rect
    from peovim.ui.status_bar import render_status_bar

    doc = Document()
    doc.path = tmp_path / "example.py"
    state = EditorState()
    state.options.set("icons", "on")
    grid = CellGrid(15, 1)
    render_status_bar(Window(doc), Mode.NORMAL, Rect(0, 0, 15, 1), grid, editor_state=state)
    text = "".join(grid.read_cell(0, c)[0] for c in range(grid.width))
    assert "example.py" in text
    assert path_icon(doc.path, mode="on") not in text
