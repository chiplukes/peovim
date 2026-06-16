"""
Phase 7d — PluginManager lazy loading tests
"""

from __future__ import annotations

import sys
import types

from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine
from peovim.plugins.manager import PluginManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api() -> EditorAPI:
    doc = Document()
    doc.load_string("hello")
    window = Window(doc)
    workspace = Workspace(window)
    registers = RegisterStore()
    editor_state = EditorState()
    command_registry = CommandRegistry()
    register_builtins(command_registry)
    engine = ModalEngine()
    engine.set_document(doc)
    dispatcher = ActionDispatcher(engine, window, registers, editor_state=editor_state)
    dispatcher._command_registry = command_registry
    return EditorAPI(workspace, engine, dispatcher, editor_state, command_registry)


def _make_fake_module(name: str, setup_fn=None) -> types.ModuleType:
    """Create a fake plugin module in sys.modules."""
    mod = types.ModuleType(name)
    calls = []
    if setup_fn:
        mod.setup = setup_fn
    else:

        def _default_setup(api):
            calls.append("setup")

        mod.setup = _default_setup
        mod._calls = calls
    sys.modules[name] = mod
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPluginManagerLazy:
    def setup_method(self):
        self._api = _make_api()
        self._pm = PluginManager(self._api)
        # Track registered fake modules so we can clean up
        self._fake_modules: list[str] = []

    def teardown_method(self):
        for name in self._fake_modules:
            sys.modules.pop(name, None)

    def _register_fake(self, name: str):
        mod = _make_fake_module(name)
        self._fake_modules.append(name)
        return mod

    def test_eager_load_calls_setup_immediately(self):
        calls = []
        _make_fake_module("fake_eager", lambda api: calls.append("setup"))
        self._fake_modules.append("fake_eager")
        self._pm.load("fake_eager")
        assert calls == ["setup"]
        assert "fake_eager" in self._pm.list_loaded()

    def test_on_filetype_not_loaded_until_event_fires(self):
        calls = []
        _make_fake_module("fake_ft", lambda api: calls.append("setup"))
        self._fake_modules.append("fake_ft")
        self._pm.load("fake_ft", on_filetype=["python"])
        assert calls == []
        assert "fake_ft" in self._pm.list_pending()
        assert "fake_ft" not in self._pm.list_loaded()

    def test_on_filetype_loaded_on_matching_filetype(self):
        calls = []
        _make_fake_module("fake_ft2", lambda api: calls.append("setup"))
        self._fake_modules.append("fake_ft2")
        self._pm.load("fake_ft2", on_filetype=["python"])
        self._api.events.emit("filetype_detected", filetype="python")
        assert calls == ["setup"]
        assert "fake_ft2" in self._pm.list_loaded()
        assert "fake_ft2" not in self._pm.list_pending()

    def test_on_filetype_not_loaded_for_wrong_filetype(self):
        calls = []
        _make_fake_module("fake_ft3", lambda api: calls.append("setup"))
        self._fake_modules.append("fake_ft3")
        self._pm.load("fake_ft3", on_filetype=["python"])
        self._api.events.emit("filetype_detected", filetype="rust")
        assert calls == []
        assert "fake_ft3" in self._pm.list_pending()

    def test_on_filetype_loaded_only_once(self):
        calls = []
        _make_fake_module("fake_ft4", lambda api: calls.append("setup"))
        self._fake_modules.append("fake_ft4")
        self._pm.load("fake_ft4", on_filetype=["python"])
        self._api.events.emit("filetype_detected", filetype="python")
        self._api.events.emit("filetype_detected", filetype="python")
        assert len(calls) == 1

    def test_on_event_loaded_on_first_matching_event(self):
        calls = []
        _make_fake_module("fake_ev", lambda api: calls.append("setup"))
        self._fake_modules.append("fake_ev")
        self._pm.load("fake_ev", on_event="editor_ready")
        assert calls == []
        self._api.events.emit("editor_ready")
        assert calls == ["setup"]
        assert "fake_ev" in self._pm.list_loaded()

    def test_on_event_not_loaded_before_event(self):
        calls = []
        _make_fake_module("fake_ev2", lambda api: calls.append("setup"))
        self._fake_modules.append("fake_ev2")
        self._pm.load("fake_ev2", on_event="editor_ready")
        assert "fake_ev2" in self._pm.list_pending()
        assert "fake_ev2" not in self._pm.list_loaded()

    def test_on_command_stub_registered_immediately(self):
        _make_fake_module("fake_cmd", lambda api: None)
        self._fake_modules.append("fake_cmd")
        self._pm.load("fake_cmd", on_command=["FakeCmd"])
        # Stub should be registered in command registry
        registry = self._api.commands._registry
        assert registry.get("FakeCmd") is not None

    def test_on_command_plugin_loaded_on_invocation(self):
        calls = []
        _make_fake_module("fake_cmd2", lambda api: calls.append("setup"))
        self._fake_modules.append("fake_cmd2")
        self._pm.load("fake_cmd2", on_command=["FakeCmd2"])
        assert calls == []
        # Invoke the stub
        self._api.commands.execute("FakeCmd2")
        assert calls == ["setup"]

    def test_list_pending_shows_unloaded(self):
        _make_fake_module("fake_pend", lambda api: None)
        self._fake_modules.append("fake_pend")
        self._pm.load("fake_pend", on_event="some_event")
        assert "fake_pend" in self._pm.list_pending()
        assert "fake_pend" not in self._pm.list_loaded()

    def test_list_loaded_shows_loaded(self):
        calls = []
        _make_fake_module("fake_load", lambda api: calls.append("setup"))
        self._fake_modules.append("fake_load")
        self._pm.load("fake_load")
        assert "fake_load" in self._pm.list_loaded()
        assert "fake_load" not in self._pm.list_pending()

    def test_error_in_deferred_setup_does_not_crash(self):
        def bad_setup(api):
            raise RuntimeError("intentional error")

        _make_fake_module("fake_bad", bad_setup)
        self._fake_modules.append("fake_bad")
        self._pm.load("fake_bad", on_event="some_other_event")
        # Should not raise
        self._api.events.emit("some_other_event")
        # Plugin should be in errors, not loaded
        assert "fake_bad" not in self._pm.list_loaded()
