"""
tests.test_config_loader — Tests for Phase 6e config loading

Covers: ConfigLoader, TrustStore, project root detection,
namespace injection, sync/async setup(), error isolation.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api():
    """Return a minimal EditorAPI-like mock."""
    from peovim.core.editor_state import EditorState

    api = MagicMock()
    api.keymap = MagicMock()
    api.commands = MagicMock()
    api.events = MagicMock()
    api.options = MagicMock()
    api.ui = MagicMock()
    api.git = MagicMock()
    api.store = MagicMock()
    api.lsp = MagicMock()
    api._editor_state = EditorState()
    return api


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


# ---------------------------------------------------------------------------
# TrustStore
# ---------------------------------------------------------------------------


class TestTrustStore:
    def test_unknown_project_prompts_and_persists_yes(self, tmp_path):
        from peovim.config.project import TrustStore
        from peovim.core.shada import ShadaStore

        shada = ShadaStore(path=tmp_path / "shada")
        ts = TrustStore(shada=shada, prompt_fn=lambda root, cfg: True)

        assert ts.is_trusted(tmp_path) is True
        assert shada.get_project_trust(str(tmp_path.resolve())) is True

    def test_set_trusted_persists_false(self, tmp_path):
        from peovim.config.project import TrustStore
        from peovim.core.shada import ShadaStore

        shada = ShadaStore(path=tmp_path / "shada")
        ts = TrustStore(shada=shada)
        ts.set_trusted(tmp_path, False)

        assert ts.get_decision(tmp_path) is False

    def test_is_trusted_uses_persisted_decision_without_prompt(self, tmp_path):
        from peovim.config.project import TrustStore
        from peovim.core.shada import ShadaStore

        shada = ShadaStore(path=tmp_path / "shada")
        shada.set_project_trust(str(tmp_path.resolve()), False)
        called = False

        def _prompt(_root, _cfg):
            nonlocal called
            called = True
            return True

        ts = TrustStore(shada=shada, prompt_fn=_prompt)

        assert ts.is_trusted(tmp_path) is False
        assert called is False


# ---------------------------------------------------------------------------
# find_project_root
# ---------------------------------------------------------------------------


class TestFindProjectRoot:
    def test_finds_git_dir(self, tmp_path):
        from peovim.config.project import find_project_root

        (tmp_path / ".git").mkdir()
        sub = tmp_path / "src" / "pkg"
        sub.mkdir(parents=True)
        assert find_project_root(sub) == tmp_path

    def test_finds_pyproject_toml(self, tmp_path):
        from peovim.config.project import find_project_root

        (tmp_path / "pyproject.toml").touch()
        assert find_project_root(tmp_path) == tmp_path

    def test_returns_none_when_no_marker(self, tmp_path):
        from peovim.config.project import find_project_root

        # tmp_path has no markers; walk all the way to filesystem root
        result = find_project_root(tmp_path)
        # On most machines the home dir or / has no peovim markers — result may
        # be None or some parent; just ensure it doesn't crash
        assert result is None or isinstance(result, Path)

    def test_custom_markers(self, tmp_path):
        from peovim.config.project import find_project_root

        (tmp_path / "MY_MARKER").touch()
        assert find_project_root(tmp_path, markers=["MY_MARKER"]) == tmp_path

    def test_start_is_file(self, tmp_path):
        from peovim.config.project import find_project_root

        (tmp_path / ".git").mkdir()
        f = tmp_path / "main.py"
        f.touch()
        assert find_project_root(f) == tmp_path


# ---------------------------------------------------------------------------
# find_project_config
# ---------------------------------------------------------------------------


class TestFindProjectConfig:
    def test_finds_ed_init_py(self, tmp_path):
        from peovim.config.project import find_project_config

        (tmp_path / ".git").mkdir()
        ed_dir = tmp_path / ".peovim"
        ed_dir.mkdir()
        cfg = ed_dir / "init.py"
        cfg.write_text("x = 1")
        assert find_project_config(tmp_path) == cfg

    def test_returns_none_when_no_init(self, tmp_path):
        from peovim.config.project import find_project_config

        (tmp_path / ".git").mkdir()
        # .peovim/ dir exists but no init.py
        (tmp_path / ".peovim").mkdir()
        assert find_project_config(tmp_path) is None

    def test_returns_none_no_root(self, tmp_path):
        from peovim.config.project import find_project_config

        # No markers in a fresh temp dir that has no ancestors with markers
        # (best-effort: just don't crash)
        result = find_project_config(tmp_path)
        assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# _build_namespace
# ---------------------------------------------------------------------------


class TestBuildNamespace:
    def test_injects_api_and_sub_apis(self):
        from peovim.config.loader import _build_namespace

        api = _make_api()
        ns = _build_namespace(api, plugin_manager=None)
        assert ns["api"] is api
        assert ns["editor"] is api
        assert ns["keymap"] is api.keymap
        assert ns["commands"] is api.commands
        assert ns["events"] is api.events
        assert ns["options"] is api.options
        assert ns["ui"] is api.ui
        assert ns["git"] is api.git
        assert ns["store"] is api.store

    def test_plugins_absent_when_not_provided(self):
        from peovim.config.loader import _build_namespace

        ns = _build_namespace(_make_api(), plugin_manager=None)
        assert "plugins" not in ns

    def test_plugins_injected_when_provided(self):
        from peovim.config.loader import _build_namespace

        pm = MagicMock()
        ns = _build_namespace(_make_api(), plugin_manager=pm)
        assert ns["plugins"] is pm


# ---------------------------------------------------------------------------
# ConfigLoader._exec_config
# ---------------------------------------------------------------------------


class TestExecConfig:
    def test_executes_module_level_code(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        cfg = tmp_path / "init.py"
        _write(
            cfg,
            """\
            executed = True
            api.keymap.nmap('<leader>w', ':w<CR>')
        """,
        )
        api = _make_api()
        ConfigLoader()._exec_config(cfg, api, None, "test")
        api.keymap.nmap.assert_called_once_with("<leader>w", ":w<CR>")

    def test_calls_sync_setup_function(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        cfg = tmp_path / "init.py"
        _write(
            cfg,
            """\
            def setup(api):
                api.keymap.nmap('<leader>q', ':q<CR>')
        """,
        )
        api = _make_api()
        ConfigLoader()._exec_config(cfg, api, None, "test")
        api.keymap.nmap.assert_called_once_with("<leader>q", ":q<CR>")

    def test_calls_async_setup_function(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        cfg = tmp_path / "init.py"
        _write(
            cfg,
            """\
            async def setup(api):
                api.options.set('tabstop', 4)
        """,
        )
        api = _make_api()
        ConfigLoader()._exec_config(cfg, api, None, "test")
        api.options.set.assert_called_once_with("tabstop", 4)

    def test_syntax_error_does_not_raise(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        cfg = tmp_path / "init.py"
        cfg.write_text("def broken(:\n    pass\n")
        # Should log error and return without raising
        ConfigLoader()._exec_config(cfg, _make_api(), None, "test")

    def test_runtime_error_does_not_raise(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        cfg = tmp_path / "init.py"
        _write(cfg, "raise RuntimeError('oops')")
        ConfigLoader()._exec_config(cfg, _make_api(), None, "test")  # must not raise

    def test_setup_error_does_not_raise(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        cfg = tmp_path / "init.py"
        _write(
            cfg,
            """\
            def setup(api):
                raise ValueError('bad config')
        """,
        )
        ConfigLoader()._exec_config(cfg, _make_api(), None, "test")  # must not raise

    def test_missing_file_does_not_raise(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        missing = tmp_path / "nonexistent.py"
        ConfigLoader()._exec_config(missing, _make_api(), None, "test")

    def test_plugins_accessible_in_namespace(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        cfg = tmp_path / "init.py"
        _write(cfg, "plugins.load('peovim.plugins.todo')")
        pm = MagicMock()
        ConfigLoader()._exec_config(cfg, _make_api(), pm, "test")
        pm.load.assert_called_once_with("peovim.plugins.todo")

    def test_flat_keymap_access(self, tmp_path):
        """keymap.nmap(...) without api. prefix."""
        from peovim.config.loader import ConfigLoader

        cfg = tmp_path / "init.py"
        _write(cfg, "keymap.nmap('jk', '<Esc>')")
        api = _make_api()
        ConfigLoader()._exec_config(cfg, api, None, "test")
        api.keymap.nmap.assert_called_once_with("jk", "<Esc>")


# ---------------------------------------------------------------------------
# ConfigLoader.load_user_config
# ---------------------------------------------------------------------------


class TestLoadUserConfig:
    def test_loads_user_config_when_present(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        cfg = tmp_path / "init.py"
        _write(cfg, "api.keymap.nmap('x', 'dd')")
        api = _make_api()
        loader = ConfigLoader()
        with patch.object(loader, "user_config_path", return_value=cfg):
            loader.load_user_config(api)
        api.keymap.nmap.assert_called_once_with("x", "dd")

    def test_skips_missing_user_config(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        api = _make_api()
        loader = ConfigLoader()
        missing = tmp_path / "init.py"
        with patch.object(loader, "user_config_candidates", return_value=[missing]):
            loader.load_user_config(api)  # must not raise
        api.keymap.nmap.assert_not_called()

    def test_loads_project_config_when_present(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        (tmp_path / ".git").mkdir()
        ed_dir = tmp_path / ".peovim"
        ed_dir.mkdir()
        proj_cfg = ed_dir / "init.py"
        _write(proj_cfg, "api.options.set('tabstop', 2)")
        api = _make_api()
        api.active_buffer.return_value.path = tmp_path / "main.py"

        loader = ConfigLoader()
        loader._trust._prompt_fn = lambda root, cfg: True
        # Patch candidates to a non-existent file so only project config runs
        with patch.object(loader, "user_config_candidates", return_value=[tmp_path / "nope.py"]):
            loader.load_user_config(api)

        api.options.set.assert_called_once_with("tabstop", 2)

    def test_both_user_and_project_loaded(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        user_cfg = tmp_path / "user_init.py"
        _write(user_cfg, "api.keymap.nmap('a', 'b')")

        (tmp_path / ".git").mkdir()
        proj_cfg = tmp_path / ".peovim" / "init.py"
        _write(proj_cfg, "api.keymap.nmap('c', 'd')")

        api = _make_api()
        api.active_buffer.return_value.path = tmp_path / "file.py"

        loader = ConfigLoader()
        loader._trust._prompt_fn = lambda root, cfg: True
        with patch.object(loader, "user_config_path", return_value=user_cfg):
            loader.load_user_config(api)

        assert api.keymap.nmap.call_count == 2

    def test_blocks_untrusted_project_config(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        (tmp_path / ".git").mkdir()
        proj_cfg = tmp_path / ".peovim" / "init.py"
        _write(proj_cfg, "api.options.set('tabstop', 2)")

        api = _make_api()
        api.active_buffer.return_value.path = tmp_path / "main.py"

        loader = ConfigLoader()
        loader._trust._prompt_fn = lambda root, cfg: False
        with patch.object(loader, "user_config_candidates", return_value=[tmp_path / "nope.py"]):
            loader.load_user_config(api)

        api.options.set.assert_not_called()
        assert loader._project_loaded_path is None
        assert loader._project_trust_status == "blocked"
        assert "Skipped untrusted project config" in api._editor_state.message

    def test_trusted_project_config_does_not_reprompt(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        (tmp_path / ".git").mkdir()
        proj_cfg = tmp_path / ".peovim" / "init.py"
        _write(proj_cfg, "api.options.set('tabstop', 2)")

        api = _make_api()
        api.active_buffer.return_value.path = tmp_path / "main.py"
        api._editor_state.shada.set_project_trust(str(tmp_path.resolve()), True)

        loader = ConfigLoader()
        loader._trust._prompt_fn = lambda root, cfg: (_ for _ in ()).throw(AssertionError("should not prompt"))
        with patch.object(loader, "user_config_candidates", return_value=[tmp_path / "nope.py"]):
            loader.load_user_config(api)

        api.options.set.assert_called_once_with("tabstop", 2)
        assert loader._project_trust_status == "trusted"
        assert loader._project_trust_prompted is False

    def test_project_config_error_does_not_stop_user_config(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        user_cfg = tmp_path / "init.py"
        _write(user_cfg, "api.keymap.nmap('x', 'y')")

        api = _make_api()
        loader = ConfigLoader()

        def _bad_project_config(_start):
            raise RuntimeError("simulated project config failure")

        with (
            patch.object(loader, "user_config_path", return_value=user_cfg),
            patch("peovim.config.loader.find_project_config", side_effect=_bad_project_config),
        ):
            loader.load_user_config(api)

        # User config still ran
        api.keymap.nmap.assert_called_once_with("x", "y")

    def test_config_runs_once_with_options_and_keymaps_together(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        user_cfg = tmp_path / "init.py"
        _write(
            user_cfg,
            """\
            options.set('leader', ' ')
            keymap.nmap('<leader>x', 'dd')
        """,
        )

        api = _make_api()
        loader = ConfigLoader()
        with patch.object(loader, "user_config_candidates", return_value=[user_cfg]):
            loader.load_user_config(api)

        api.options.set.assert_called_once_with("leader", " ")
        api.keymap.nmap.assert_called_once_with("<leader>x", "dd")

    def test_init_can_explicitly_load_selected_plugins(self, tmp_path):
        from peovim.config.loader import ConfigLoader

        user_cfg = tmp_path / "init.py"
        _write(
            user_cfg,
            """\
            plugins.load('peovim.plugins.picker')
            plugins.load('peovim.plugins.explorer')
            """,
        )

        api = _make_api()
        plugin_manager = MagicMock()
        loader = ConfigLoader()
        with patch.object(loader, "user_config_candidates", return_value=[user_cfg]):
            loader.load_user_config(api, plugin_manager=plugin_manager)

        plugin_manager.load.assert_any_call("peovim.plugins.picker")
        plugin_manager.load.assert_any_call("peovim.plugins.explorer")
        assert plugin_manager.load.call_count == 2


# ---------------------------------------------------------------------------
# user_config_path
# ---------------------------------------------------------------------------


class TestUserConfigPath:
    def test_returns_path_object(self):
        from peovim.config.loader import ConfigLoader

        p = ConfigLoader().user_config_path()
        assert isinstance(p, Path)
        assert p.name == "init.py"
        # Should be under an "peovim" config directory
        assert "peovim" in str(p).lower()
