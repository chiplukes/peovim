"""
Phase 6b — Options system: OptionsStore, EditorState integration, :set wiring.
"""

import pytest

from peovim.core.event_bus import EventBus
from peovim.core.options import OptionError, OptionsStore

# ---------------------------------------------------------------------------
# Basic get / default
# ---------------------------------------------------------------------------


class TestOptionsStoreDefaults:
    def test_get_known_bool_default(self):
        s = OptionsStore()
        assert s.get("number") is False

    def test_get_known_int_default(self):
        s = OptionsStore()
        assert s.get("shiftwidth") == 4

    def test_get_known_str_default(self):
        s = OptionsStore()
        assert s.get("signcolumn") == "yes"

    def test_get_unknown_returns_none(self):
        s = OptionsStore()
        assert s.get("nonexistent_option_xyz") is None

    def test_default_method(self):
        s = OptionsStore()
        assert s.default("tabstop") == 4
        assert s.default("unknown") is None

    def test_is_known(self):
        s = OptionsStore()
        assert s.is_known("number")
        assert s.is_known("signcolumn")
        assert not s.is_known("nonexistent_xyz")


# ---------------------------------------------------------------------------
# Set global
# ---------------------------------------------------------------------------


class TestOptionsStoreSetGlobal:
    def test_set_and_get_bool(self):
        s = OptionsStore()
        s.set_global("number", True)
        assert s.get("number") is True

    def test_set_and_get_int(self):
        s = OptionsStore()
        s.set_global("shiftwidth", 2)
        assert s.get("shiftwidth") == 2

    def test_set_and_get_str(self):
        s = OptionsStore()
        s.set_global("signcolumn", "yes")
        assert s.get("signcolumn") == "yes"

    def test_set_coerces_bool_string(self):
        s = OptionsStore()
        s.set_global("number", "true")
        assert s.get("number") is True
        s.set_global("number", "no")
        assert s.get("number") is False

    def test_set_coerces_int_string(self):
        s = OptionsStore()
        s.set_global("shiftwidth", "2")
        assert s.get("shiftwidth") == 2

    def test_set_unknown_stored_untyped(self):
        # Unknown options are stored without validation so plugin options set
        # before define() don't abort the user config file.
        s = OptionsStore()
        s.set_global("nonexistent_xyz", True)
        assert s.get("nonexistent_xyz") is True

    def test_define_promotes_untyped_value(self):
        # Value set before define() is promoted when the option is formally defined.
        s = OptionsStore()
        s.set_global("myplugin_opt", "hello")
        assert s.get("myplugin_opt") == "hello"
        s.define("myplugin_opt", str, "default")
        assert s.get("myplugin_opt") == "hello"  # pre-set value preserved

    def test_set_invalid_value_raises(self):
        s = OptionsStore()
        with pytest.raises(OptionError):
            s.set_global("signcolumn", "invalid_value")

    def test_set_invalid_parallel_render_value_raises(self):
        s = OptionsStore()
        with pytest.raises(OptionError):
            s.set_global("parallelrender", "invalid_value")

    def test_set_invalid_parallel_render_workers_raises(self):
        s = OptionsStore()
        with pytest.raises(OptionError):
            s.set_global("parallelrenderworkers", -1)

    def test_set_invalid_insert_cursor_value_raises(self):
        s = OptionsStore()
        with pytest.raises(OptionError):
            s.set_global("insertcursor", "triangle")

    def test_set_buffer_option_in_global_scope_raises(self):
        s = OptionsStore()
        with pytest.raises(OptionError):
            s.set("filetype", "python", scope="global")

    def test_set_window_option_in_buffer_scope_raises(self):
        s = OptionsStore()
        with pytest.raises(OptionError):
            s.set("number", True, scope="buffer", buf_id=1)


# ---------------------------------------------------------------------------
# Scope chain
# ---------------------------------------------------------------------------


class TestOptionsScopeChain:
    def test_window_override_beats_global(self):
        s = OptionsStore()
        s.set_global("shiftwidth", 4)
        s.set_window(win_id=1, name="shiftwidth", value=2)
        assert s.get("shiftwidth", win_id=1) == 2
        assert s.get("shiftwidth", win_id=2) == 4  # other window sees global

    def test_buffer_override_beats_window(self):
        s = OptionsStore()
        s.set_global("shiftwidth", 4)
        s.set_window(win_id=1, name="shiftwidth", value=2)
        s.set_buffer(buf_id=10, name="shiftwidth", value=8)
        assert s.get("shiftwidth", win_id=1, buf_id=10) == 8

    def test_buffer_override_beats_global(self):
        s = OptionsStore()
        s.set_global("expandtab", True)
        s.set_buffer(buf_id=5, name="expandtab", value=False)
        assert s.get("expandtab", buf_id=5) is False
        assert s.get("expandtab") is True  # global unchanged

    def test_no_override_falls_back_to_default(self):
        s = OptionsStore()
        assert s.get("wrapscan") is True  # default

    def test_set_buffer_option_globally_raises(self):
        s = OptionsStore()
        with pytest.raises(OptionError):
            s.set("filetype", "python", scope="global")

    def test_buffer_option_set_buffer_scope(self):
        s = OptionsStore()
        s.set_buffer(buf_id=7, name="filetype", value="python")
        assert s.get("filetype", buf_id=7) == "python"
        assert s.get("filetype", buf_id=8) == ""  # other buf sees default


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


class TestOptionsEvents:
    def test_option_changed_event_emitted(self):
        bus = EventBus()
        s = OptionsStore(event_bus=bus)
        received = []
        bus.on("option_changed", lambda **kw: received.append(kw))
        s.set_global("number", True)
        assert len(received) == 1
        assert received[0]["name"] == "number"
        assert received[0]["value"] is True
        assert received[0]["scope"] == "global"

    def test_no_event_without_bus(self):
        s = OptionsStore()
        s.set_global("number", True)  # should not raise


# ---------------------------------------------------------------------------
# Plugin-defined options
# ---------------------------------------------------------------------------


class TestPluginDefinedOptions:
    def test_define_new_option(self):
        s = OptionsStore()
        s.define("myplugin_enabled", bool, False, ("global",))
        assert s.is_known("myplugin_enabled")
        assert s.get("myplugin_enabled") is False

    def test_define_duplicate_is_noop(self):
        s = OptionsStore()
        s.define("number", str, "bad", ("global",))
        # Should not change the existing definition
        assert s.default("number") is False  # still bool default

    def test_define_and_set(self):
        s = OptionsStore()
        s.define("myplugin_width", int, 80, ("global",))
        s.set_global("myplugin_width", 120)
        assert s.get("myplugin_width") == 120


# ---------------------------------------------------------------------------
# Key built-in options exist with correct types/defaults
# ---------------------------------------------------------------------------


class TestBuiltinOptionCoverage:
    @pytest.mark.parametrize(
        "name,default",
        [
            ("number", False),
            ("relativenumber", False),
            ("wrap", False),
            ("signcolumn", "yes"),
            ("tabstop", 4),
            ("shiftwidth", 4),
            ("expandtab", True),
            ("scrolloff", 0),
            ("hlsearch", True),
            ("ignorecase", False),
            ("smartcase", False),
            ("wrapscan", True),
            ("leader", "\\"),
            ("parallelrender", "auto"),
            ("parallelrenderworkers", 0),
            ("cursorblink", False),
            ("insertcursor", "block"),
            ("colorcolumn", ""),
            ("filetype", ""),
            ("fileformat", "unix"),
        ],
    )
    def test_default(self, name, default):
        s = OptionsStore()
        assert s.get(name) == default or s.default(name) == default


# ---------------------------------------------------------------------------
# :set command wires to OptionsStore
# ---------------------------------------------------------------------------


class TestSetCommandWiring:
    def _make_ctx(self):
        from peovim.core.document import Document
        from peovim.core.editor_state import EditorState
        from peovim.core.window import Window

        doc = Document()
        doc.load_string("hello\nworld")
        w = Window(doc)
        es = EditorState()

        class Ctx:
            window = w
            editor_state = es

        return Ctx(), w, es

    def test_set_bool_true(self):
        from peovim.commands.builtin import register_builtins
        from peovim.commands.parser import parse_ex_command
        from peovim.commands.registry import CommandRegistry

        ctx, w, es = self._make_ctx()
        reg = CommandRegistry()
        register_builtins(reg)
        cmd = parse_ex_command("set number")
        reg.execute(cmd, ctx)
        assert w.options["number"] is True
        assert es.options.get("number") is True

    def test_set_bool_false(self):
        from peovim.commands.builtin import register_builtins
        from peovim.commands.parser import parse_ex_command
        from peovim.commands.registry import CommandRegistry

        ctx, w, es = self._make_ctx()
        reg = CommandRegistry()
        register_builtins(reg)
        # First enable, then disable
        cmd = parse_ex_command("set number")
        reg.execute(cmd, ctx)
        cmd = parse_ex_command("set nonumber")
        reg.execute(cmd, ctx)
        assert w.options["number"] is False
        assert es.options.get("number") is False

    def test_set_int_option(self):
        from peovim.commands.builtin import register_builtins
        from peovim.commands.parser import parse_ex_command
        from peovim.commands.registry import CommandRegistry

        ctx, w, es = self._make_ctx()
        reg = CommandRegistry()
        register_builtins(reg)
        cmd = parse_ex_command("set shiftwidth=2")
        reg.execute(cmd, ctx)
        assert w.options["shiftwidth"] == 2
        assert es.options.get("shiftwidth") == 2

    def test_set_unknown_option_still_writes_window_options(self):
        from peovim.commands.builtin import register_builtins
        from peovim.commands.parser import parse_ex_command
        from peovim.commands.registry import CommandRegistry

        ctx, w, es = self._make_ctx()
        reg = CommandRegistry()
        register_builtins(reg)
        # An option not in OptionsStore should still work via window.options
        cmd = parse_ex_command("set customthing=foo")
        reg.execute(cmd, ctx)
        assert w.options["customthing"] == "foo"
