"""Ex command parsing and execution"""

from pathlib import Path
from types import SimpleNamespace

from peovim.commands.builtin import register_builtins
from peovim.commands.parser import parse_ex_command
from peovim.commands.registry import CommandRegistry

# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_simple_cmd(self):
        pc = parse_ex_command("w")
        assert pc.cmd == "w"
        assert not pc.bang
        assert pc.args == ""

    def test_cmd_with_bang(self):
        pc = parse_ex_command("q!")
        assert pc.cmd == "q"
        assert pc.bang

    def test_cmd_with_args(self):
        pc = parse_ex_command("edit foo.py")
        assert pc.cmd == "edit"
        assert pc.args == "foo.py"

    def test_line_range(self):
        pc = parse_ex_command("1,5d")
        assert pc.range_start is not None
        assert pc.range_start.kind == "line"
        assert pc.range_start.value == 1
        assert pc.range_end is not None
        assert pc.range_end.kind == "line"
        assert pc.range_end.value == 5
        assert pc.cmd == "d"

    def test_percent_range(self):
        pc = parse_ex_command("%s/foo/bar/g")
        assert pc.all_lines
        assert pc.cmd == "s"
        assert "foo" in pc.args

    def test_dot_range(self):
        pc = parse_ex_command(".d")
        assert pc.range_start is not None
        assert pc.range_start.kind == "dot"

    def test_dollar_range(self):
        pc = parse_ex_command("$y")
        assert pc.range_start is not None
        assert pc.range_start.kind == "dollar"

    def test_mark_range(self):
        pc = parse_ex_command("'a,'bd")
        assert pc.range_start is not None
        assert pc.range_start.kind == "mark"
        assert pc.range_end is not None
        assert pc.range_end.kind == "mark"

    def test_empty_cmd(self):
        pc = parse_ex_command("")
        assert pc.cmd == ""

    def test_set_cmd(self):
        pc = parse_ex_command("set number")
        assert pc.cmd == "set"
        assert pc.args == "number"

    def test_substitute_args(self):
        pc = parse_ex_command("s/foo/bar/g")
        assert pc.cmd == "s"
        assert "foo" in pc.args

    def test_raw_preserved(self):
        raw = "1,5d"
        pc = parse_ex_command(raw)
        assert pc.raw == raw


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_register_and_get(self):
        reg = CommandRegistry()

        def handler(cmd, ctx):
            return "ok"

        reg.register("write", handler)
        assert reg.get("write") is handler

    def test_abbreviation_match(self):
        reg = CommandRegistry()

        def handler(cmd, ctx):
            return "ok"

        reg.register("write", handler, min_abbrev=1)
        assert reg.get("w") is handler

    def test_abbreviation_min_length(self):
        reg = CommandRegistry()

        def handler(cmd, ctx):
            return None

        reg.register("set", handler, min_abbrev=2)
        assert reg.get("se") is handler
        assert reg.get("s") is None  # too short

    def test_not_found(self):
        reg = CommandRegistry()
        assert reg.get("xyz") is None

    def test_ambiguous_returns_none(self):
        reg = CommandRegistry()
        reg.register("write", lambda c, x: None, min_abbrev=1)
        reg.register("wq", lambda c, x: None, min_abbrev=2)
        # 'w' could be 'write' but 'wq' starts with 'w' too
        # Only 'write' should match since 'wq' needs min 2
        result = reg.get("w")
        assert result is not None  # 'write' matches

    def test_execute(self):
        reg = CommandRegistry()
        results = []
        reg.register("echo", lambda c, x: results.append(c.args))
        pc = parse_ex_command("echo hello")
        reg.execute(pc, None)
        assert results == ["hello"]

    def test_list_commands(self):
        reg = CommandRegistry()
        reg.register("write", lambda c, x: None)
        reg.register("quit", lambda c, x: None)
        commands = reg.list_commands()
        assert "write" in commands
        assert "quit" in commands
        assert commands == sorted(commands)


# ---------------------------------------------------------------------------
# Builtin commands via integration
# ---------------------------------------------------------------------------


class MockContext:
    """Minimal context for testing builtin commands."""

    def __init__(self, content: str = ""):
        from peovim.core.document import Document
        from peovim.core.editor_state import EditorState
        from peovim.core.registers import RegisterStore
        from peovim.core.window import Window
        from peovim.core.workspace import Workspace
        from peovim.modal.dispatcher import ActionDispatcher
        from peovim.modal.engine import ModalEngine

        self.doc = Document()
        self.doc.load_string(content)
        self.window = Window(self.doc)
        self.window.cursor.move_to(0, 0)
        self.registers = RegisterStore()
        self.editor_state = EditorState()
        self.engine = ModalEngine()
        self.engine.set_cursor(0, 0)
        self.engine.set_line_count(self.doc.line_count())
        self.engine.set_document(self.doc)
        self.workspace = Workspace(self.window)
        self.dispatcher = ActionDispatcher(
            self.engine,
            self.window,
            self.registers,
            editor_state=self.editor_state,
            workspace=self.workspace,
        )
        # Minimal API mock so builtin commands that call api.window_count() work.
        self.editor_state._api = SimpleNamespace(
            window_count=lambda: len(self.workspace.active_tab.all_windows()),
        )


class TestBuiltinCommands:
    def _make_registry(self):
        reg = CommandRegistry()
        register_builtins(reg)
        return reg

    def _prepare_split_context(self, content: str = "alpha\n"):
        from peovim.modal.actions import SplitWindow

        ctx = MockContext(content)
        reg = self._make_registry()

        ctx.dispatcher.dispatch([SplitWindow("v")])
        scratch_window = ctx.workspace.active_window
        other_window = next(win for win in ctx.workspace.active_tab.all_windows() if win is not scratch_window)
        ctx.window = scratch_window
        ctx.dispatcher.window = scratch_window
        ctx.engine.set_document(scratch_window.document)
        ctx.engine.set_line_count(scratch_window.document.line_count())
        ctx.engine.set_cursor(scratch_window.cursor.line, scratch_window.cursor.col)
        ctx.engine.set_scroll(scratch_window.scroll_line)
        return ctx, reg, scratch_window, other_window

    def test_delete_lines(self):
        ctx = MockContext("line1\nline2\nline3")
        reg = self._make_registry()
        pc = parse_ex_command("1,2d")
        reg.execute(pc, ctx)
        assert ctx.doc.line_count() == 1
        assert ctx.doc.get_line(0) == "line3"

    def test_yank_lines(self):
        ctx = MockContext("hello\nworld")
        reg = self._make_registry()
        pc = parse_ex_command("1y a")
        reg.execute(pc, ctx)
        text, kind = ctx.registers.get("a")
        assert text == "hello"

    def test_substitute_basic(self):
        ctx = MockContext("hello world")
        reg = self._make_registry()
        pc = parse_ex_command("s/world/there")
        reg.execute(pc, ctx)
        assert ctx.doc.get_line(0) == "hello there"

    def test_substitute_global(self):
        ctx = MockContext("aaa aaa aaa")
        reg = self._make_registry()
        pc = parse_ex_command("s/aaa/bbb/g")
        reg.execute(pc, ctx)
        assert ctx.doc.get_line(0) == "bbb bbb bbb"

    def test_substitute_percent(self):
        ctx = MockContext("foo\nfoo\nfoo")
        reg = self._make_registry()
        pc = parse_ex_command("%s/foo/bar/g")
        reg.execute(pc, ctx)
        for i in range(3):
            assert ctx.doc.get_line(i) == "bar"

    def test_set_option(self):
        ctx = MockContext()
        reg = self._make_registry()
        pc = parse_ex_command("set number")
        reg.execute(pc, ctx)
        assert ctx.window.options.get("number") is True

    def test_set_option_value(self):
        ctx = MockContext()
        reg = self._make_registry()
        pc = parse_ex_command("set tabstop=4")
        reg.execute(pc, ctx)
        assert ctx.window.options.get("tabstop") == 4

    def test_set_nooption(self):
        ctx = MockContext()
        ctx.window.options["wrap"] = True
        reg = self._make_registry()
        pc = parse_ex_command("set nowrap")
        reg.execute(pc, ctx)
        assert ctx.window.options.get("wrap") is False

    def test_abbreviations(self):
        reg = self._make_registry()
        assert reg.get("w") is not None  # write
        assert reg.get("q") is not None  # quit
        assert reg.get("e") is not None  # edit
        assert reg.get("d") is not None  # delete
        assert reg.get("y") is not None  # yank
        assert reg.get("s") is not None  # substitute

    def test_echo(self):
        reg = self._make_registry()
        pc = parse_ex_command("echo hello world")
        result = reg.execute(pc, None)
        assert result == "hello world"

    def test_config_command_opens_user_init(self, tmp_path, monkeypatch):
        ctx = MockContext("")
        reg = self._make_registry()
        config_path = tmp_path / "peovim" / "init.py"
        monkeypatch.setattr("peovim.commands.builtin.preferred_user_config_path", lambda: config_path)

        reg.execute(parse_ex_command("config"), ctx)

        assert config_path.exists()
        assert ctx.window.document.path == Path(config_path).resolve()

    def test_init_command_alias_opens_user_init(self, tmp_path, monkeypatch):
        ctx = MockContext("")
        reg = self._make_registry()
        config_path = tmp_path / "peovim" / "init.py"
        monkeypatch.setattr("peovim.commands.builtin.preferred_user_config_path", lambda: config_path)

        reg.execute(parse_ex_command("init"), ctx)

        assert config_path.exists()
        assert ctx.window.document.path == Path(config_path).resolve()

    def test_edit_without_args_reloads_current_file(self, tmp_path):
        ctx = MockContext("")
        reg = self._make_registry()
        target = tmp_path / "sample.txt"
        target.write_text("one\ntwo\n", encoding="utf-8")
        ctx.doc.load(target)
        ctx.window.cursor.move_to(1, 1)
        target.write_text("three\nfour\n", encoding="utf-8")

        reg.execute(parse_ex_command("edit"), ctx)

        assert ctx.doc.get_text() == "three\nfour\n"
        assert ctx.window.cursor.line == 1
        assert ctx.window.cursor.col == 1
        assert ctx.editor_state.message == f"Reloaded: {target}"

    def test_edit_without_args_blocks_dirty_reload_without_bang(self, tmp_path):
        ctx = MockContext("")
        reg = self._make_registry()
        target = tmp_path / "sample.txt"
        target.write_text("one\n", encoding="utf-8")
        ctx.doc.load(target)
        ctx.doc.insert(0, 3, "!")

        reg.execute(parse_ex_command("edit"), ctx)

        assert ctx.doc.get_text() == "one!\n"
        assert ctx.editor_state.message == "E37: No write since last change (add ! to override)"

    def test_edit_bang_reloads_current_file_and_discards_dirty_changes(self, tmp_path):
        ctx = MockContext("")
        reg = self._make_registry()
        target = tmp_path / "sample.txt"
        target.write_text("one\n", encoding="utf-8")
        ctx.doc.load(target)
        ctx.doc.insert(0, 3, "!")
        target.write_text("disk\n", encoding="utf-8")

        reg.execute(parse_ex_command("edit!"), ctx)

        assert ctx.doc.get_text() == "disk\n"
        assert not ctx.doc.dirty
        assert ctx.editor_state.message == f"Reloaded: {target}"

    def test_messages_command_opens_message_history(self):
        ctx = MockContext("")
        ctx.editor_state.message = "first"
        ctx.editor_state.message = "second"
        reg = self._make_registry()

        reg.execute(parse_ex_command("messages"), ctx)

        assert ctx.window.document.get_text() == "first\nsecond"
        assert ctx.window.document.path is None

    def test_palette_command_opens_swatch_view(self):
        ctx = MockContext("")
        reg = self._make_registry()

        reg.execute(parse_ex_command("palette"), ctx)

        assert "Palette preview for theme:" in ctx.window.document.get_text()
        assert "Theme default_bg:" in ctx.window.document.get_text()
        assert (
            "Terminal default bg appears only when a theme/group background is None." in ctx.window.document.get_text()
        )
        assert "Reference palette" in ctx.window.document.get_text()
        assert "Pure hues" in ctx.window.document.get_text()
        assert "Muted tones" in ctx.window.document.get_text()
        assert "Saturated tones" in ctx.window.document.get_text()
        assert "pure red" in ctx.window.document.get_text()
        assert "crimson" in ctx.window.document.get_text()
        assert "steel blue" in ctx.window.document.get_text()
        assert ctx.window.document.path is None
        assert ctx.editor_state.message == "Palette: catppuccin"
        assert ctx.editor_state.decorations.get_for_namespace(id(ctx.window.document), "cmd:palette")

    def test_palette_command_accepts_theme_name(self):
        ctx = MockContext("")
        reg = self._make_registry()

        reg.execute(parse_ex_command("palette gruvbox"), ctx)

        assert "Palette preview for theme: gruvbox" in ctx.window.document.get_text()
        assert ctx.editor_state.message == "Palette: gruvbox"

    def test_palette_command_is_window_local_in_split_views(self):
        from peovim.modal.actions import SplitWindow

        ctx = MockContext("alpha\n")
        reg = self._make_registry()
        original_doc = ctx.window.document

        ctx.dispatcher.dispatch([SplitWindow("v")])
        palette_window = ctx.workspace.active_window
        other_window = next(win for win in ctx.workspace.active_tab.all_windows() if win is not palette_window)
        assert palette_window.document is original_doc
        assert other_window.document is original_doc

        ctx.window = palette_window
        ctx.dispatcher.window = palette_window
        ctx.engine.set_document(palette_window.document)
        ctx.engine.set_line_count(palette_window.document.line_count())
        ctx.engine.set_cursor(palette_window.cursor.line, palette_window.cursor.col)
        ctx.engine.set_scroll(palette_window.scroll_line)

        reg.execute(parse_ex_command("palette"), ctx)

        assert palette_window.document is not original_doc
        assert "Palette preview for theme:" in palette_window.document.get_text()
        assert other_window.document is original_doc
        assert other_window.document.get_text() == "alpha\n"

    def test_bdelete_closes_scratch_split_window(self):
        ctx, reg, scratch_window, other_window = self._prepare_split_context()

        reg.execute(parse_ex_command("palette"), ctx)
        assert len(ctx.workspace.active_tab.all_windows()) == 2
        assert scratch_window.document.path is None

        reg.execute(parse_ex_command("bd"), ctx)

        assert len(ctx.workspace.active_tab.all_windows()) == 1
        assert ctx.workspace.active_window is other_window
        assert other_window.document.get_text() == "alpha\n"

    def test_bdelete_closes_messages_scratch_split_window(self):
        ctx, reg, scratch_window, other_window = self._prepare_split_context()
        ctx.editor_state.message = "first"
        ctx.editor_state.message = "second"

        reg.execute(parse_ex_command("messages"), ctx)
        assert len(ctx.workspace.active_tab.all_windows()) == 2
        assert scratch_window.document.path is None

        reg.execute(parse_ex_command("bd"), ctx)

        assert len(ctx.workspace.active_tab.all_windows()) == 1
        assert ctx.workspace.active_window is other_window
        assert other_window.document.get_text() == "alpha\n"

    def test_bdelete_closes_checkhealth_scratch_split_window(self):
        from types import SimpleNamespace

        ctx, reg, scratch_window, other_window = self._prepare_split_context()
        ctx.editor_state._api = SimpleNamespace(
            health=SimpleNamespace(run=lambda _api: "health ok"),
            window_count=lambda: len(ctx.workspace.active_tab.all_windows()),
        )

        reg.execute(parse_ex_command("checkhealth"), ctx)
        assert len(ctx.workspace.active_tab.all_windows()) == 2
        assert scratch_window.document.path is None

        reg.execute(parse_ex_command("bd"), ctx)

        assert len(ctx.workspace.active_tab.all_windows()) == 1
        assert ctx.workspace.active_window is other_window
        assert other_window.document.get_text() == "alpha\n"

    def test_palette_command_errors_for_unknown_theme(self):
        ctx = MockContext("")
        reg = self._make_registry()

        reg.execute(parse_ex_command("palette no_such_theme"), ctx)

        assert ctx.editor_state.message == "E185: Cannot find color scheme 'no_such_theme'"

    def test_set_fileformat_updates_document_line_ending(self):
        ctx = MockContext("hello\n")
        reg = self._make_registry()

        reg.execute(parse_ex_command("set fileformat=dos"), ctx)

        assert ctx.window.options.get("fileformat") == "dos"
        assert ctx.doc.line_ending == "\r\n"

    def test_dos2unix_sets_document_fileformat(self):
        ctx = MockContext("hello\n")
        ctx.doc.set_fileformat("dos")
        reg = self._make_registry()

        reg.execute(parse_ex_command("dos2unix"), ctx)

        assert ctx.window.options.get("fileformat") == "unix"
        assert ctx.doc.line_ending == "\n"
        assert "dos2unix" in ctx.editor_state.message

    def test_unix2dos_sets_document_fileformat(self):
        ctx = MockContext("hello\n")
        reg = self._make_registry()

        reg.execute(parse_ex_command("unix2dos"), ctx)

        assert ctx.window.options.get("fileformat") == "dos"
        assert ctx.doc.line_ending == "\r\n"
        assert "unix2dos" in ctx.editor_state.message


class TestParserLineEndingCommands:
    def test_parses_dos2unix_as_full_command_name(self):
        pc = parse_ex_command("dos2unix")

        assert pc.cmd == "dos2unix"
        assert pc.args == ""

    def test_parses_unix2dos_as_full_command_name(self):
        pc = parse_ex_command("unix2dos")

        assert pc.cmd == "unix2dos"
        assert pc.args == ""

    def test_substitute_via_run_ex_command(self):
        """Substitute must apply when dispatched via RunExCommand (live editor path)."""
        from peovim.modal.actions import RunExCommand

        ctx = MockContext("hello world")
        ctx.dispatcher.dispatch([RunExCommand("s/world/there")])
        assert ctx.doc.get_line(0) == "hello there"

    def test_substitute_global_via_run_ex_command(self):
        """Global substitute via RunExCommand."""
        from peovim.modal.actions import RunExCommand

        ctx = MockContext("foo foo foo")
        ctx.dispatcher.dispatch([RunExCommand("s/foo/bar/g")])
        assert ctx.doc.get_line(0) == "bar bar bar"

    def test_substitute_percent_via_run_ex_command(self):
        """%s/pat/rep/g via RunExCommand applies to all lines."""
        from peovim.modal.actions import RunExCommand

        ctx = MockContext("foo\nfoo\nfoo")
        ctx.dispatcher.dispatch([RunExCommand("%s/foo/bar/g")])
        for i in range(3):
            assert ctx.doc.get_line(i) == "bar"

    def test_substitute_visual_mark_range(self):
        """'<,'>s/pat/rep applies only to the visual selection lines."""
        from peovim.modal.actions import RunExCommand
        from peovim.modal.engine import Mode

        ctx = MockContext("foo\nfoo\nfoo\nfoo")
        # Simulate a visual selection of lines 1-2 (0-indexed)
        ctx.engine._last_visual_selection = (Mode.VISUAL_LINE, (1, 0), (2, 0))
        ctx.dispatcher.dispatch([RunExCommand("'<,'>s/foo/bar")])
        assert ctx.doc.get_line(0) == "foo"   # untouched
        assert ctx.doc.get_line(1) == "bar"   # substituted
        assert ctx.doc.get_line(2) == "bar"   # substituted
        assert ctx.doc.get_line(3) == "foo"   # untouched

    def test_substitute_visual_mark_range_global(self):
        """'<,'>s/pat/rep/g applies to all occurrences within the selection."""
        from peovim.modal.actions import RunExCommand
        from peovim.modal.engine import Mode

        ctx = MockContext("aaa\naaa\naaa")
        ctx.engine._last_visual_selection = (Mode.VISUAL_LINE, (0, 0), (1, 0))
        ctx.dispatcher.dispatch([RunExCommand("'<,'>s/a/b/g")])
        assert ctx.doc.get_line(0) == "bbb"
        assert ctx.doc.get_line(1) == "bbb"
        assert ctx.doc.get_line(2) == "aaa"  # outside selection
