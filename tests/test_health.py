"""tests/test_health.py — Phase 6.5 health check tests"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from peovim.core.health import HealthItem, HealthRegistry, build_registry, format_report, highlight_report
from peovim.core.health_checks import (
    check_config,
    check_data_dirs,
    check_editor_version,
    check_lsp,
    check_optional_deps,
    check_persistence,
    check_plugins,
    check_python_env,
    check_render_runtime,
    check_syntax,
    check_terminal,
)
from peovim.ui.render_jobs import RenderRuntimeDiagnostics

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api():
    """Minimal mock EditorAPI with a working keymap.leader."""
    from peovim.core.editor_state import EditorState

    api = MagicMock()
    api.keymap.leader = "\\"
    api._editor_state = EditorState()
    return api


def _statuses(items: list[HealthItem]) -> list[str]:
    return [i.status for i in items]


def _messages(items: list[HealthItem]) -> list[str]:
    return [i.message for i in items]


# ---------------------------------------------------------------------------
# HealthItem
# ---------------------------------------------------------------------------


class TestHealthItem:
    def test_defaults(self):
        item = HealthItem("ok", "All good")
        assert item.status == "ok"
        assert item.message == "All good"
        assert item.detail == ""

    def test_with_detail(self):
        item = HealthItem("warn", "Missing", detail="Run: uv sync")
        assert item.detail == "Run: uv sync"


# ---------------------------------------------------------------------------
# HealthRegistry
# ---------------------------------------------------------------------------


class TestHealthRegistry:
    def test_register_and_run(self):
        reg = HealthRegistry()
        reg.register("test", lambda api, pm, cl: [HealthItem("ok", "pass")], label="Test")
        results = reg.run_all(None, None, None)
        assert "test" in results
        label, items = results["test"]
        assert label == "Test"
        assert items[0].status == "ok"

    def test_label_defaults_to_name(self):
        reg = HealthRegistry()
        reg.register("mycheck", lambda api, pm, cl: [])
        results = reg.run_all(None, None, None)
        label, _ = results["mycheck"]
        assert label == "mycheck"

    def test_crashed_checker_returns_error(self):
        reg = HealthRegistry()

        def bad(api, pm, cl):
            raise RuntimeError("boom")

        reg.register("bad", bad, label="Bad")
        results = reg.run_all(None, None, None)
        _, items = results["bad"]
        assert items[0].status == "error"
        assert "boom" in items[0].message

    def test_multiple_checkers_ordered(self):
        reg = HealthRegistry()
        reg.register("a", lambda *_: [HealthItem("ok", "a")])
        reg.register("b", lambda *_: [HealthItem("ok", "b")])
        keys = list(reg.run_all(None, None, None).keys())
        assert keys == ["a", "b"]


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


class TestFormatReport:
    def test_section_headings(self):
        reg = HealthRegistry()
        reg.register("py", lambda *_: [HealthItem("ok", "Python 3.12")], label="Python Environment")
        results = reg.run_all(None, None, None)
        report = format_report(results)
        assert "## Python Environment" in report

    def test_status_icons(self):
        reg = HealthRegistry()
        reg.register(
            "x",
            lambda *_: [
                HealthItem("ok", "good"),
                HealthItem("warn", "careful"),
                HealthItem("error", "broken"),
                HealthItem("info", "fyi"),
            ],
        )
        report = format_report(reg.run_all(None, None, None))
        assert "✓" in report
        assert "⚠" in report
        assert "✗" in report
        assert "·" in report

    def test_detail_indented(self):
        reg = HealthRegistry()
        reg.register("x", lambda *_: [HealthItem("warn", "missing", detail="Run: uv sync")])
        report = format_report(reg.run_all(None, None, None))
        assert "Run: uv sync" in report
        # detail should be indented beyond the status icon
        for line in report.splitlines():
            if "Run: uv sync" in line:
                assert line.startswith(" ")

    def test_build_registry_has_builtin_sections(self):
        reg = build_registry()
        results = reg.run_all(MagicMock(keymap=MagicMock(leader="\\")), None, None)
        assert "version" in results
        assert "python" in results
        assert "syntax" in results
        assert "optional" in results
        assert "render" in results
        assert "terminal" in results
        assert "data_dirs" in results
        assert "persistence" in results
        assert "config" in results
        assert "plugins" in results
        assert "lsp" in results


# ---------------------------------------------------------------------------
# highlight_report
# ---------------------------------------------------------------------------


class TestHighlightReport:
    def _make_report(self) -> str:
        reg = HealthRegistry()
        reg.register(
            "x",
            lambda *_: [
                HealthItem("ok", "All good"),
                HealthItem("warn", "Watch out", detail="More info here"),
                HealthItem("error", "Broken"),
                HealthItem("info", "FYI"),
            ],
            label="Test Section",
        )
        return format_report(reg.run_all(None, None, None))

    def test_heading_spans_emitted(self):
        report = self._make_report()
        spans = highlight_report(report)
        # Find the heading line
        heading_line = next(i for i, ln in enumerate(report.splitlines()) if ln.startswith("## "))
        heading_spans = [s for s in spans if s[0] == heading_line]
        assert heading_spans
        # Should cover col 0 through end of line
        assert heading_spans[0][1] == 0

    def test_ok_line_gets_green_span(self):
        from peovim.core.health import _C_OK

        report = self._make_report()
        spans = highlight_report(report)
        # Find "ok" line index
        ok_line = next(i for i, ln in enumerate(report.splitlines()) if "All good" in ln)
        ok_spans = [s for s in spans if s[0] == ok_line]
        assert ok_spans
        assert any(s[3].fg == _C_OK for s in ok_spans)

    def test_warn_line_gets_yellow_span(self):
        from peovim.core.health import _C_WARN

        report = self._make_report()
        spans = highlight_report(report)
        warn_line = next(i for i, ln in enumerate(report.splitlines()) if "Watch out" in ln)
        warn_spans = [s for s in spans if s[0] == warn_line]
        assert any(s[3].fg == _C_WARN for s in warn_spans)

    def test_error_line_gets_red_span(self):
        from peovim.core.health import _C_ERROR

        report = self._make_report()
        spans = highlight_report(report)
        err_line = next(i for i, ln in enumerate(report.splitlines()) if "Broken" in ln)
        err_spans = [s for s in spans if s[0] == err_line]
        assert any(s[3].fg == _C_ERROR for s in err_spans)

    def test_info_line_gets_muted_span(self):
        from peovim.core.health import _C_INFO

        report = self._make_report()
        spans = highlight_report(report)
        info_line = next(i for i, ln in enumerate(report.splitlines()) if "FYI" in ln)
        info_spans = [s for s in spans if s[0] == info_line]
        assert any(s[3].fg == _C_INFO for s in info_spans)

    def test_detail_line_gets_dim_span(self):
        from peovim.core.health import _C_DETAIL

        report = self._make_report()
        spans = highlight_report(report)
        detail_line = next(i for i, ln in enumerate(report.splitlines()) if "More info here" in ln)
        detail_spans = [s for s in spans if s[0] == detail_line]
        assert detail_spans
        assert any(s[3].fg == _C_DETAIL for s in detail_spans)

    def test_heading_uses_bold_attr(self):
        from peovim.ui.backend import ATTR_BOLD

        report = self._make_report()
        spans = highlight_report(report)
        heading_line = next(i for i, ln in enumerate(report.splitlines()) if ln.startswith("## "))
        heading_spans = [s for s in spans if s[0] == heading_line]
        assert any(s[3].attrs & ATTR_BOLD for s in heading_spans)

    def test_empty_report_produces_no_spans(self):
        spans = highlight_report("")
        assert spans == []

    def test_blank_lines_produce_no_spans(self):
        spans = highlight_report("\n\n\n")
        assert spans == []


# ---------------------------------------------------------------------------
# check_python_env
# ---------------------------------------------------------------------------


class TestCheckPythonEnv:
    def test_current_python_ok(self):
        items = check_python_env(_make_api(), None, None)
        py_items = [i for i in items if "Python" in i.message]
        assert py_items
        # Current interpreter should be >= 3.11 in the dev env
        assert py_items[0].status in ("ok", "warn")

    def test_old_python_warns(self):
        with patch.object(sys, "version_info", (3, 9, 0)):
            items = check_python_env(_make_api(), None, None)
        py_items = [i for i in items if "Python" in i.message]
        assert py_items[0].status == "warn"

    def test_platformdirs_present(self):
        items = check_python_env(_make_api(), None, None)
        msgs = _messages(items)
        assert any("platformdirs" in m for m in msgs)

    def test_missing_package_is_error(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "platformdirs":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            items = check_python_env(_make_api(), None, None)

        err = [i for i in items if i.status == "error" and "platformdirs" in i.message]
        assert err


# ---------------------------------------------------------------------------
# check_syntax
# ---------------------------------------------------------------------------


class TestCheckSyntax:
    def test_tree_sitter_present(self):
        items = check_syntax(_make_api(), None, None)
        ts_items = [i for i in items if "tree-sitter" in i.message.lower() and "not installed" not in i.message]
        assert ts_items, f"Expected tree-sitter ok item, got: {items}"

    def test_missing_tree_sitter_returns_early(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "tree_sitter":
                raise ImportError("no tree_sitter")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            items = check_syntax(_make_api(), None, None)

        assert len(items) == 1
        assert items[0].status == "error"

    def test_missing_grammar_is_warn_or_info(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "tree_sitter_python":
                raise ImportError("no grammar")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            items = check_syntax(_make_api(), None, None)

        missing = [i for i in items if "python" in i.message and "not installed" in i.message]
        assert missing
        assert missing[0].status in ("warn", "info")

    def test_install_hint_when_missing(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "tree_sitter_python":
                raise ImportError
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            items = check_syntax(_make_api(), None, None)

        hint = [i for i in items if "uv sync" in i.detail]
        assert hint

    def test_c_cpp_markdown_grammars_checked(self):
        items = check_syntax(_make_api(), None, None)
        messages = _messages(items)
        assert any("tree-sitter-c" in m for m in messages)
        assert any("tree-sitter-cpp" in m for m in messages)
        assert any("tree-sitter-markdown" in m for m in messages)


# ---------------------------------------------------------------------------
# check_optional_deps
# ---------------------------------------------------------------------------


class TestCheckOptionalDeps:
    def test_git_found_or_not(self):
        items = check_optional_deps(_make_api(), None, None)
        git_items = [i for i in items if "git" in i.message.lower()]
        assert git_items

    def test_missing_rapidfuzz_is_info(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "rapidfuzz":
                raise ImportError("no rapidfuzz")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            items = check_optional_deps(_make_api(), None, None)

        rf = [i for i in items if "rapidfuzz" in i.message]
        assert rf
        assert rf[0].status == "info"

    def test_missing_git_is_warn(self):
        with patch("shutil.which", return_value=None):
            items = check_optional_deps(_make_api(), None, None)
        git_items = [i for i in items if "git" in i.message.lower() and i.status == "warn"]
        assert git_items

    def test_missing_rg_is_info(self):
        with patch("shutil.which", side_effect=lambda x: None if x == "rg" else "/usr/bin/git"):
            items = check_optional_deps(_make_api(), None, None)
        rg = [i for i in items if "ripgrep" in i.message and i.status == "info"]
        assert rg

    def test_missing_pygit2_is_info(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "pygit2":
                raise ImportError("no pygit2")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            items = check_optional_deps(_make_api(), None, None)

        pg = [i for i in items if "pygit2" in i.message]
        assert pg
        assert pg[0].status == "info"

    def test_missing_jedi_is_info(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "jedi":
                raise ImportError("no jedi")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            items = check_optional_deps(_make_api(), None, None)

        jd = [i for i in items if "jedi" in i.message]
        assert jd
        assert jd[0].status == "info"

    def test_missing_ed_crossterm_is_info(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "ed_crossterm":
                raise ImportError("no ed_crossterm")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            items = check_optional_deps(_make_api(), None, None)

        ct = [i for i in items if "ed_crossterm" in i.message]
        assert ct
        assert ct[0].status == "info"


# ---------------------------------------------------------------------------
# check_render_runtime
# ---------------------------------------------------------------------------


class TestCheckRenderRuntime:
    def test_default_state_reports_not_requested(self):
        items = check_render_runtime(_make_api(), None, None)
        msgs = _messages(items)
        assert "parallelrender=auto" in msgs
        assert any("effective render workers=" in i.message for i in items)
        assert any("free-threaded runtime=" in i.message for i in items)
        assert any(i.status == "info" and "not requested" in i.message for i in items)

    def test_parallelrender_on_warns_when_runtime_lacks_capability(self, monkeypatch):
        api = _make_api()
        api._editor_state.options.set_global("parallelrender", "on")
        monkeypatch.setattr(
            "peovim.core.health_checks.render_runtime_diagnostics",
            lambda policy: RenderRuntimeDiagnostics(
                requested=True,
                runtime_supported=False,
                effective_parallelism=False,
                free_threaded=False,
                gil_disabled_value=0,
                worker_count=8,
                worker_source="cpu",
                reason="Python build is not free-threaded (Py_GIL_DISABLED=0)",
            ),
        )

        items = check_render_runtime(api, None, None)

        warn_items = [i for i in items if i.status == "warn"]
        assert warn_items
        assert "requested but unavailable" in warn_items[0].message
        assert "Py_GIL_DISABLED=0" in warn_items[0].detail

    def test_parallelrender_on_reports_ok_when_supported(self, monkeypatch):
        api = _make_api()
        api._editor_state.options.set_global("parallelrender", "on")
        api._editor_state.options.set_global("parallelrenderworkers", 3)
        monkeypatch.setattr(
            "peovim.core.health_checks.render_runtime_diagnostics",
            lambda policy: RenderRuntimeDiagnostics(
                requested=True,
                runtime_supported=True,
                effective_parallelism=True,
                free_threaded=True,
                gil_disabled_value=1,
                worker_count=3,
                worker_source="policy",
                reason="parallel rendering available",
            ),
        )

        items = check_render_runtime(api, None, None)

        assert any(i.message == "parallelrenderworkers=3" for i in items)
        assert any(i.message == "effective render workers=3 (policy)" for i in items)
        assert any(i.status == "ok" and "available" in i.message for i in items)

    def test_parallelrender_on_reports_sequential_effective_worker_one(self, monkeypatch):
        api = _make_api()
        api._editor_state.options.set_global("parallelrender", "on")
        api._editor_state.options.set_global("parallelrenderworkers", 1)
        monkeypatch.setattr(
            "peovim.core.health_checks.render_runtime_diagnostics",
            lambda policy: RenderRuntimeDiagnostics(
                requested=True,
                runtime_supported=True,
                effective_parallelism=False,
                free_threaded=True,
                gil_disabled_value=1,
                worker_count=1,
                worker_source="policy",
                reason="effective worker count is 1, so rendering remains sequential",
            ),
        )

        items = check_render_runtime(api, None, None)

        assert any(i.status == "info" and "still sequential in practice" in i.message for i in items)


# ---------------------------------------------------------------------------
# check_persistence
# ---------------------------------------------------------------------------


class TestCheckPersistence:
    def test_reports_multi_instance_warning(self):
        items = check_persistence(_make_api(), None, None)

        assert items[0].status == "warn"
        assert "not fully coordinated" in items[0].message

    def test_lists_known_persistence_surfaces(self):
        items = check_persistence(_make_api(), None, None)
        messages = _messages(items)

        assert any(m.startswith("File saves:") for m in messages)
        assert any(m.startswith("shada:") for m in messages)
        assert any(m.startswith("sessions:") for m in messages)
        assert any(m.startswith("plugin stores:") for m in messages)
        assert any(m.startswith("project markers:") for m in messages)
        assert any(m.startswith("git scratch snapshots:") for m in messages)

    def test_health_report_includes_persistence_section(self):
        reg = build_registry()
        report = format_report(reg.run_all(_make_api(), None, None))

        assert "## Persistence" in report
        assert "File saves: save-time external-change detection; no merge" in report


# ---------------------------------------------------------------------------
# check_config
# ---------------------------------------------------------------------------


class TestCheckConfig:
    def test_no_loader_returns_info(self):
        items = check_config(_make_api(), None, None)
        assert any(i.status == "info" for i in items)

    def test_loaded_path_shown(self):
        from pathlib import Path

        loader = MagicMock()
        loader._loaded_path = Path("/home/user/.config/peovim/init.py")
        loader._load_error = ""
        loader._project_loaded_path = None
        loader.user_config_candidates.return_value = [loader._loaded_path]
        items = check_config(_make_api(), None, loader)
        ok_items = [i for i in items if i.status == "ok" and "Loaded" in i.message]
        assert ok_items

    def test_no_init_shows_candidates(self):
        from pathlib import Path

        loader = MagicMock()
        loader._loaded_path = None
        loader._load_error = ""
        loader._project_loaded_path = None
        loader.user_config_candidates.return_value = [
            Path("C:/Users/user/AppData/Roaming/peovim/init.py"),
            Path("/home/user/.config/peovim/init.py"),
        ]
        items = check_config(_make_api(), None, loader)
        no_cfg = [i for i in items if "No user init.py" in i.message]
        assert no_cfg

    def test_load_error_shown(self):
        from pathlib import Path

        loader = MagicMock()
        loader._loaded_path = Path("/home/user/.config/peovim/init.py")
        loader._load_error = "SyntaxError: invalid syntax"
        loader._project_loaded_path = None
        loader.user_config_candidates.return_value = [loader._loaded_path]
        items = check_config(_make_api(), None, loader)
        err = [i for i in items if i.status == "error"]
        assert err

    def test_blocked_project_config_reported(self):
        from pathlib import Path

        loader = MagicMock()
        loader._loaded_path = None
        loader._load_error = ""
        loader._project_loaded_path = None
        loader._project_trust_status = "blocked"
        loader._project_trust_prompted = True
        loader.user_config_candidates.return_value = [Path("/home/user/.config/peovim/init.py")]

        items = check_config(_make_api(), None, loader)

        assert any(i.status == "warn" and "Project config blocked" in i.message for i in items)

    def test_project_config_trust_info_reported_when_loaded(self):
        from pathlib import Path

        loader = MagicMock()
        loader._loaded_path = Path("/home/user/.config/peovim/init.py")
        loader._load_error = ""
        loader._project_loaded_path = Path("/repo/.peovim/init.py")
        loader._project_trust_status = "trusted"
        loader._project_trust_prompted = False
        loader.user_config_candidates.return_value = [loader._loaded_path]

        items = check_config(_make_api(), None, loader)

        assert any(i.message == "Project config trust: trusted" for i in items)

    def test_leader_key_shown(self):
        items = check_config(_make_api(), None, None)
        leader_items = [i for i in items if "Leader" in i.message]
        assert leader_items


# ---------------------------------------------------------------------------
# check_plugins
# ---------------------------------------------------------------------------


class TestCheckPlugins:
    def test_no_manager_returns_info(self):
        items = check_plugins(_make_api(), None, None)
        assert items[0].status == "info"

    def test_loaded_plugins_shown_as_ok(self):
        pm = MagicMock()
        pm.list_loaded.return_value = ["peovim.plugins.picker", "peovim.plugins.todo"]
        pm._load_errors = {}
        items = check_plugins(_make_api(), pm, None)
        ok = [i for i in items if i.status == "ok"]
        assert len(ok) == 2

    def test_failed_plugins_shown_as_error(self):
        pm = MagicMock()
        pm.list_loaded.return_value = ["peovim.plugins.picker"]
        pm._load_errors = {"peovim.plugins.gitsigns": "ImportError: no pygit2"}
        items = check_plugins(_make_api(), pm, None)
        err = [i for i in items if i.status == "error"]
        assert err
        assert "gitsigns" in err[0].message

    def test_empty_manager_shows_info(self):
        pm = MagicMock()
        pm.list_loaded.return_value = []
        pm._load_errors = {}
        items = check_plugins(_make_api(), pm, None)
        assert items[0].status == "info"


# ---------------------------------------------------------------------------
# Integration — EditorAPI.health
# ---------------------------------------------------------------------------


class TestHealthAPIIntegration:
    def _make_full_api(self):
        from peovim.api.editor import EditorAPI
        from peovim.commands.builtin import register_builtins
        from peovim.commands.registry import CommandRegistry
        from peovim.core.document import Document
        from peovim.core.editor_state import EditorState
        from peovim.core.jumplist import JumpList
        from peovim.core.marks import MarkStore
        from peovim.core.registers import RegisterStore
        from peovim.core.window import Window
        from peovim.core.workspace import Workspace
        from peovim.modal.dispatcher import ActionDispatcher
        from peovim.modal.engine import ModalEngine

        doc = Document()
        window = Window(doc)
        workspace = Workspace(window)
        registers = RegisterStore()
        marks = MarkStore()
        jumplist = JumpList()
        es = EditorState()
        reg = CommandRegistry()
        register_builtins(reg)
        engine = ModalEngine()
        engine.set_document(doc)
        disp = ActionDispatcher(engine, window, registers, marks=marks, jumplist=jumplist, editor_state=es)
        disp._command_registry = reg
        return EditorAPI(workspace, engine, disp, es, reg)

    def test_health_api_exists(self):
        api = self._make_full_api()
        assert hasattr(api, "health")

    def test_run_returns_string(self):
        api = self._make_full_api()
        report = api.health.run(api)
        assert isinstance(report, str)
        assert "## Editor Version" in report
        assert "## Python Environment" in report
        assert "## Render Runtime" in report
        assert "## Terminal Environment" in report
        assert "## Data Directories" in report
        assert "## Language Server Protocol" in report
        assert "## Plugins" in report

    def test_custom_checker_registered(self):
        api = self._make_full_api()
        api.health.register("custom", lambda a, pm, cl: [HealthItem("ok", "custom ok")], label="My Plugin")
        report = api.health.run(api)
        assert "## My Plugin" in report
        assert "custom ok" in report

    def test_set_context_wires_plugin_manager(self):
        api = self._make_full_api()
        pm = MagicMock()
        pm.list_loaded.return_value = ["peovim.plugins.test"]
        pm._load_errors = {}
        api.health.set_context(plugin_manager=pm)
        report = api.health.run(api)
        assert "peovim.plugins.test" in report

    def test_checkhealth_command_writes_render_warning_to_buffer(self, monkeypatch):
        from peovim.commands.parser import parse_ex_command

        api = self._make_full_api()
        api._editor_state.options.set_global("parallelrender", "on")
        monkeypatch.setattr(
            "peovim.core.health_checks.render_runtime_diagnostics",
            lambda policy: RenderRuntimeDiagnostics(
                requested=True,
                runtime_supported=False,
                effective_parallelism=False,
                free_threaded=False,
                gil_disabled_value=0,
                worker_count=8,
                worker_source="cpu",
                reason="Python build is not free-threaded (Py_GIL_DISABLED=0)",
            ),
        )

        class Ctx:
            window = api._workspace.active_window
            dispatcher = api._dispatcher
            editor_state = api._editor_state

        cmd = parse_ex_command("checkhealth")
        api._command_registry.execute(cmd, Ctx())

        report = api._workspace.active_window.document.get_text()
        assert "## Render Runtime" in report
        assert "Parallel rendering requested but unavailable" in report
        assert "Py_GIL_DISABLED=0" in report


# ---------------------------------------------------------------------------
# check_editor_version
# ---------------------------------------------------------------------------


class TestCheckEditorVersion:
    def test_returns_version_info(self):
        items = check_editor_version(_make_api(), None, None)
        msgs = _messages(items)
        assert any("peovim" in m for m in msgs)
        assert any("API version" in m for m in msgs)

    def test_api_namespaces_listed(self):
        items = check_editor_version(_make_api(), None, None)
        msgs = _messages(items)
        assert any("api.editor" in m for m in msgs)
        assert any("api.buffer" in m for m in msgs)
        assert any("api.lsp" in m for m in msgs)

    def test_implemented_namespaces_are_ok(self):
        items = check_editor_version(_make_api(), None, None)
        editor_ns = [i for i in items if "api.editor:" in i.message]
        assert editor_ns
        assert editor_ns[0].status == "ok"

    def test_experimental_namespaces_are_warn(self):
        items = check_editor_version(_make_api(), None, None)
        lsp_ns = [i for i in items if "api.lsp:" in i.message]
        assert lsp_ns
        assert lsp_ns[0].status == "warn"

    def test_planned_namespaces_are_info(self):
        items = check_editor_version(_make_api(), None, None)
        debug_ns = [i for i in items if "api.debug:" in i.message]
        assert debug_ns
        assert debug_ns[0].status == "info"


# ---------------------------------------------------------------------------
# check_terminal
# ---------------------------------------------------------------------------


class TestCheckTerminal:
    def test_returns_os_info(self):
        items = check_terminal(_make_api(), None, None)
        os_items = [i for i in items if "OS:" in i.message]
        assert os_items

    def test_returns_term_info(self):
        items = check_terminal(_make_api(), None, None)
        term_items = [i for i in items if "TERM=" in i.message]
        assert term_items

    def test_truecolor_colorterm_is_ok(self):
        with patch.dict("os.environ", {"COLORTERM": "truecolor"}, clear=False):
            items = check_terminal(_make_api(), None, None)
        ct = [i for i in items if "True color" in i.message]
        assert ct
        assert ct[0].status == "ok"

    def test_missing_colorterm_warns(self):
        env = {k: v for k, v in __import__("os").environ.items() if k not in ("COLORTERM", "TERM")}
        env["TERM"] = "xterm"
        with patch.dict("os.environ", env, clear=True):
            items = check_terminal(_make_api(), None, None)
        warn = [i for i in items if i.status == "warn" and "color" in i.message.lower()]
        assert warn

    def test_utf8_encoding_is_ok(self):
        mock_stdout = MagicMock()
        mock_stdout.encoding = "utf-8"
        with patch("sys.stdout", mock_stdout):
            items = check_terminal(_make_api(), None, None)
        enc = [i for i in items if "stdout encoding" in i.message]
        assert enc
        assert enc[0].status == "ok"

    def test_non_utf_encoding_is_warn(self):
        mock_stdout = MagicMock()
        mock_stdout.encoding = "ascii"
        with patch("sys.stdout", mock_stdout):
            items = check_terminal(_make_api(), None, None)
        enc = [i for i in items if "stdout encoding" in i.message]
        assert enc
        assert enc[0].status == "warn"


# ---------------------------------------------------------------------------
# check_data_dirs
# ---------------------------------------------------------------------------


class TestCheckDataDirs:
    def test_returns_items_for_standard_dirs(self):
        items = check_data_dirs(_make_api(), None, None)
        msgs = _messages(items)
        assert any("data dir" in m for m in msgs)
        assert any("config dir" in m for m in msgs)
        assert any("log dir" in m for m in msgs)
        assert any("cache dir" in m for m in msgs)

    def test_writable_existing_dir_is_ok(self, tmp_path):
        import platformdirs

        with (
            patch.object(platformdirs, "user_data_dir", return_value=str(tmp_path)),
            patch.object(platformdirs, "user_config_dir", return_value=str(tmp_path)),
            patch.object(platformdirs, "user_log_dir", return_value=str(tmp_path)),
            patch.object(platformdirs, "user_cache_dir", return_value=str(tmp_path)),
        ):
            items = check_data_dirs(_make_api(), None, None)
        ok_items = [i for i in items if i.status == "ok" and "dir" in i.message]
        assert ok_items

    def test_missing_dir_is_info(self, tmp_path):
        import platformdirs

        missing = str(tmp_path / "nonexistent")
        with (
            patch.object(platformdirs, "user_data_dir", return_value=missing),
            patch.object(platformdirs, "user_config_dir", return_value=missing),
            patch.object(platformdirs, "user_log_dir", return_value=missing),
            patch.object(platformdirs, "user_cache_dir", return_value=missing),
        ):
            items = check_data_dirs(_make_api(), None, None)
        info_items = [i for i in items if i.status == "info" and "does not exist yet" in i.detail]
        assert info_items

    def test_shada_file_reported(self, tmp_path):
        import platformdirs

        shada = tmp_path / "shada"
        shada.write_bytes(b"test")
        with patch.object(platformdirs, "user_data_dir", return_value=str(tmp_path)):
            items = check_data_dirs(_make_api(), None, None)
        shada_items = [i for i in items if "shada" in i.message and i.status == "ok"]
        assert shada_items


# ---------------------------------------------------------------------------
# check_lsp
# ---------------------------------------------------------------------------


class TestCheckLsp:
    def test_no_lsp_api_returns_info(self):
        api = _make_api()
        api.lsp = None
        items = check_lsp(api, None, None)
        assert items[0].status == "info"
        assert "not initialized" in items[0].message

    def test_no_configs_returns_info(self):
        api = _make_api()
        manager = MagicMock()
        manager._configs = []
        api.lsp = MagicMock()
        api.lsp._manager = manager
        items = check_lsp(api, None, None)
        assert any(i.status == "info" and "No LSP servers" in i.message for i in items)

    def test_configured_server_on_path_is_ok(self):
        api = _make_api()
        cfg = MagicMock()
        cfg.filetype = "python"
        cfg.cmd = ["ty", "server"]
        manager = MagicMock()
        manager._configs = [cfg]
        manager.list_servers.return_value = []
        api.lsp = MagicMock()
        api.lsp._manager = manager

        with patch("shutil.which", return_value="/usr/bin/ty"):
            items = check_lsp(api, None, None)

        ok = [i for i in items if i.status == "ok" and "python" in i.message]
        assert ok

    def test_configured_server_missing_is_warn(self):
        api = _make_api()
        cfg = MagicMock()
        cfg.filetype = "python"
        cfg.cmd = ["ty", "server"]
        manager = MagicMock()
        manager._configs = [cfg]
        manager.list_servers.return_value = []
        api.lsp = MagicMock()
        api.lsp._manager = manager

        with patch("shutil.which", return_value=None):
            items = check_lsp(api, None, None)

        warn = [i for i in items if i.status == "warn" and "not found" in i.message]
        assert warn

    def test_active_initialized_server_is_ok(self):
        api = _make_api()
        cfg = MagicMock()
        cfg.filetype = "python"
        cfg.cmd = ["ty", "server"]
        manager = MagicMock()
        manager._configs = [cfg]
        manager.list_servers.return_value = [
            {"filetype": "python", "root": "/repo", "cmd": ["ty", "server"], "initialized": True}
        ]
        api.lsp = MagicMock()
        api.lsp._manager = manager

        with patch("shutil.which", return_value="/usr/bin/ty"):
            items = check_lsp(api, None, None)

        initialized = [i for i in items if i.status == "ok" and "initialized" in i.message]
        assert initialized
