"""tests.test_plugin_which_key — Phase 6 tail"""

from __future__ import annotations

from unittest.mock import MagicMock

from peovim.plugins.which_key import _show_bindings, get_bindings_for_prefix

_LEADER = "\\"  # default leader key used by tests


def _make_api(bindings=None) -> MagicMock:
    api = MagicMock()
    api.keymap.leader = _LEADER
    if bindings is None:
        bindings = []
    api.keymap.get_bindings.return_value = bindings
    api.keymap.get_group_name.return_value = ""
    return api


def _binding(keys: str, desc: str = "") -> MagicMock:
    binding = MagicMock()
    binding.keys = keys
    binding.desc = desc
    return binding


class TestGetBindingsForPrefix:
    def test_returns_matching_bindings(self):
        api = _make_api(
            [
                _binding("<leader>ff", "Find files"),
                _binding("<leader>fb", "Find buffers"),
                _binding("gd", "Go to def"),
            ]
        )
        result = get_bindings_for_prefix(api, "<leader>")
        assert len(result) == 2
        keys = {row["keys"] for row in result}
        assert "<leader>ff" in keys
        assert "<leader>fb" in keys

    def test_excludes_exact_prefix(self):
        api = _make_api([_binding("<leader>", "leader key")])
        result = get_bindings_for_prefix(api, "<leader>")
        assert result == []

    def test_empty_when_no_matches(self):
        api = _make_api([_binding("gd", "Go to def")])
        result = get_bindings_for_prefix(api, "<leader>")
        assert result == []

    def test_returns_desc(self):
        api = _make_api([_binding("<leader>ff", "Find files")])
        result = get_bindings_for_prefix(api, "<leader>")
        assert result[0]["desc"] == "Find files"

    def test_handles_registry_exception(self):
        api = MagicMock()
        api.keymap.get_bindings.side_effect = RuntimeError("broken")
        result = get_bindings_for_prefix(api, "<leader>")
        assert result == []


class TestShowBindings:
    def test_opens_panel_when_matches_found(self):
        api = _make_api(
            [
                _binding("<leader>ff", "Find files"),
                _binding("<leader>fb", "Find buffers"),
            ]
        )

        _show_bindings(api, _LEADER, "normal")

        api.ui.show_which_key.assert_called_once()

    def test_no_panel_when_no_matches(self):
        api = _make_api([_binding("gd", "Go to def")])

        _show_bindings(api, _LEADER, "normal")

        api.ui.show_which_key.assert_not_called()

    def test_panel_title_contains_prefix(self):
        api = _make_api([_binding("<leader>ff", "Find files")])

        _show_bindings(api, _LEADER, "normal")

        call_kwargs = api.ui.show_which_key.call_args
        title = call_kwargs.kwargs.get("title", call_kwargs.args[1] if len(call_kwargs.args) > 1 else "")
        assert "Which Key" in title

    def test_visual_mode_uses_visual_bindings(self):
        api = _make_api(
            [
                _binding("gc", "Commentary: toggle comments on selection"),
                _binding("gb", "Some visual binding"),
            ]
        )

        _show_bindings(api, "g", "visual")

        api.keymap.get_bindings.assert_called_with("visual")
        call_args = api.ui.show_which_key.call_args
        pairs = call_args.args[0]
        assert ("c", "Commentary: toggle comments on selection") in pairs
        assert ("b", "Some visual binding") in pairs


class TestSetup:
    def test_registers_which_key_command(self):
        from peovim.plugins.which_key import setup

        api = MagicMock()
        setup(api)
        cmd_names = [call.args[0] for call in api.commands.register.call_args_list]
        assert "whichkey" in cmd_names

    def test_registers_leader_question(self):
        from peovim.plugins.which_key import setup

        api = MagicMock()
        setup(api)
        keys = [call.args[0] for call in api.keymap.nmap.call_args_list]
        assert "<leader>?" in keys
