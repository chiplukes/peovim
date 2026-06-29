"""
core.health_checks — built-in health checker functions

Each function has the signature:
    fn(api, plugin_manager, config_loader) -> list[HealthItem]

plugin_manager and config_loader may be None (e.g. in tests).
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import Any

from peovim.core.health import HealthItem
from peovim.core.persistence_policy import persistence_policy_items
from peovim.ui.render_jobs import (
    render_execution_policy_from_values,
    render_runtime_diagnostics,
)

# ---------------------------------------------------------------------------
# 1. Python environment
# ---------------------------------------------------------------------------


def check_python_env(api: Any, plugin_manager: Any, config_loader: Any) -> list[HealthItem]:
    items: list[HealthItem] = []

    # Python version
    vi = sys.version_info
    major, minor, micro = vi[0], vi[1], vi[2]
    ver_str = f"Python {major}.{minor}.{micro}"
    if vi >= (3, 11):
        items.append(HealthItem("ok", ver_str))
    else:
        items.append(HealthItem("warn", ver_str, detail="peovim requires Python 3.11+"))

    # Required packages — (import_name, display_name)
    _REQUIRED = [
        ("platformdirs", "platformdirs"),
        ("prompt_toolkit", "prompt-toolkit"),
        ("msgpack", "msgpack"),
        ("portalocker", "portalocker"),
        ("wcwidth", "wcwidth"),
        ("watchdog", "watchdog"),
        ("charset_normalizer", "charset-normalizer"),
        ("pyte", "pyte"),
        ("lsprotocol", "lsprotocol"),
        ("editorconfig", "editorconfig"),
        ("spellchecker", "pyspellchecker"),
    ]
    for import_name, display_name in _REQUIRED:
        try:
            mod = __import__(import_name)
            ver = getattr(mod, "__version__", "?")
            items.append(HealthItem("ok", f"{display_name} {ver}"))
        except ImportError:
            items.append(HealthItem("error", f"{display_name} not installed", detail="Run: uv sync"))

    # uv (optional runtime helper)
    if shutil.which("uv"):
        items.append(HealthItem("ok", f"uv {_cmd_version('uv', '--version')}"))
    else:
        items.append(HealthItem("info", "uv not found in PATH (not required at runtime)"))

    return items


# ---------------------------------------------------------------------------
# 2. Syntax highlighting (tree-sitter)
# ---------------------------------------------------------------------------

_GRAMMARS = [
    ("tree_sitter_python", "python", True),
    ("tree_sitter_javascript", "javascript", True),
    ("tree_sitter_typescript", "typescript", True),
    ("tree_sitter_rust", "rust", False),
    ("tree_sitter_c", "c", False),
    ("tree_sitter_cpp", "cpp", False),
    ("tree_sitter_go", "go", False),
    ("tree_sitter_lua", "lua", False),
    ("tree_sitter_bash", "bash", False),
    ("tree_sitter_json", "json", False),
    ("tree_sitter_toml", "toml", False),
    ("tree_sitter_yaml", "yaml", False),
    ("tree_sitter_markdown", "markdown", False),
    ("tree_sitter_verilog", "verilog", False),
]


def check_syntax(api: Any, plugin_manager: Any, config_loader: Any) -> list[HealthItem]:
    items: list[HealthItem] = []
    missing: list[str] = []

    # Core tree-sitter
    try:
        import tree_sitter

        items.append(HealthItem("ok", f"tree-sitter {getattr(tree_sitter, '__version__', '?')}"))
    except ImportError:
        items.append(
            HealthItem("error", "tree-sitter not installed", detail="Run: uv sync --extra grammars --extra dev")
        )
        return items

    for pkg, lang, required in _GRAMMARS:
        try:
            __import__(pkg)
            items.append(HealthItem("ok", f"tree-sitter-{lang}"))
        except ImportError:
            level = "warn" if required else "info"
            items.append(HealthItem(level, f"tree-sitter-{lang} not installed"))
            missing.append(pkg)

    if missing:
        items.append(HealthItem("info", "To install missing grammars:", detail="uv sync --extra grammars --extra dev"))

    return items


# ---------------------------------------------------------------------------
# 3. Optional dependencies
# ---------------------------------------------------------------------------


def check_optional_deps(api: Any, plugin_manager: Any, config_loader: Any) -> list[HealthItem]:
    items: list[HealthItem] = []

    # rapidfuzz — fuzzy picker scoring
    try:
        import rapidfuzz

        items.append(
            HealthItem("ok", f"rapidfuzz {getattr(rapidfuzz, '__version__', '?')} — fuzzy picker scoring enabled")
        )
    except ImportError:
        items.append(
            HealthItem("info", "rapidfuzz not installed — picker uses substring fallback", detail="uv add rapidfuzz")
        )

    # pygit2 — faster git queries
    try:
        import pygit2  # type: ignore[import-not-found]

        items.append(HealthItem("ok", f"pygit2 {getattr(pygit2, '__version__', '?')} — fast git backend available"))
    except ImportError:
        items.append(
            HealthItem("info", "pygit2 not installed — git uses subprocess fallback", detail="uv sync --extra fast")
        )

    # jedi — offline Python completion
    try:
        import jedi  # type: ignore[import-not-found]

        items.append(
            HealthItem("ok", f"jedi {getattr(jedi, '__version__', '?')} — offline Python completion available")
        )
    except ImportError:
        items.append(
            HealthItem("info", "jedi not installed — Python completion requires LSP", detail="uv sync --extra fast")
        )

    # ed_crossterm — native crossterm backend
    try:
        import ed_crossterm  # type: ignore[import-not-found]

        ver = getattr(ed_crossterm, "__version__", "?")
        items.append(HealthItem("ok", f"ed_crossterm {ver} — native crossterm backend available"))
    except ImportError:
        items.append(HealthItem("info", "ed_crossterm not installed — using prompt_toolkit backend"))

    # git
    git_path = shutil.which("git")
    if git_path:
        items.append(HealthItem("ok", f"git {_cmd_version('git', '--version')}"))
    else:
        items.append(HealthItem("warn", "git not found in PATH — gitsigns disabled"))

    # ripgrep
    rg_path = shutil.which("rg")
    if rg_path:
        items.append(HealthItem("ok", f"ripgrep {_cmd_version('rg', '--version')}"))
    else:
        items.append(HealthItem("info", "ripgrep not found — grep uses Python fallback"))

    return items


# ---------------------------------------------------------------------------
# 3b. Render runtime
# ---------------------------------------------------------------------------


def check_render_runtime(api: Any, plugin_manager: Any, config_loader: Any) -> list[HealthItem]:
    items: list[HealthItem] = []

    editor_state = getattr(api, "_editor_state", None)
    if editor_state is None:
        return [HealthItem("info", "EditorState not available")]

    parallel_mode = editor_state.options.get("parallelrender")
    worker_setting = editor_state.options.get("parallelrenderworkers")
    policy = render_execution_policy_from_values(parallel_mode, worker_setting)
    diagnostics = render_runtime_diagnostics(policy)

    items.append(HealthItem("info", f"parallelrender={parallel_mode}"))
    if isinstance(worker_setting, int) and worker_setting > 0:
        items.append(HealthItem("info", f"parallelrenderworkers={worker_setting}"))
    else:
        items.append(HealthItem("info", "parallelrenderworkers=auto"))
    items.append(
        HealthItem("info", f"effective render workers={diagnostics.worker_count} ({diagnostics.worker_source})")
    )
    items.append(
        HealthItem(
            "info",
            f"free-threaded runtime={'yes' if diagnostics.free_threaded else 'no'}",
            detail=f"Py_GIL_DISABLED={diagnostics.gil_disabled_value!r}",
        )
    )

    if diagnostics.requested and not diagnostics.runtime_supported:
        items.append(
            HealthItem(
                "warn",
                "Parallel rendering requested but unavailable",
                detail=diagnostics.reason,
            )
        )
    elif diagnostics.requested and not diagnostics.effective_parallelism:
        items.append(
            HealthItem(
                "info",
                "Parallel rendering requested but still sequential in practice",
                detail=diagnostics.reason,
            )
        )
    elif diagnostics.effective_parallelism:
        items.append(HealthItem("ok", "Parallel rendering available"))
    else:
        items.append(HealthItem("info", "Parallel rendering not requested"))

    return items


# ---------------------------------------------------------------------------
# 3c. Persistence policy
# ---------------------------------------------------------------------------


def check_persistence(api: Any, plugin_manager: Any, config_loader: Any) -> list[HealthItem]:
    items: list[HealthItem] = [
        HealthItem(
            "warn",
            "Shared persistence is not fully coordinated across multiple editor instances",
            detail="Most shared stores are still last-writer-wins or single-writer-friendly rather than merged or lock-protected.",
        )
    ]

    for policy in persistence_policy_items():
        status = "ok" if policy.write_mode == "atomic replace" else "warn"
        items.append(
            HealthItem(
                status,
                f"{policy.name}: {policy.coordination}",
                detail=(
                    f"scope={policy.scope}\n"
                    f"storage={policy.storage}\n"
                    f"writes={policy.write_mode}\n"
                    f"guidance={policy.guidance}"
                ),
            )
        )

    return items


# ---------------------------------------------------------------------------
# 4. User configuration
# ---------------------------------------------------------------------------


def check_config(api: Any, plugin_manager: Any, config_loader: Any) -> list[HealthItem]:
    items: list[HealthItem] = []

    if config_loader is not None:
        # Which init.py was actually loaded
        loaded = getattr(config_loader, "_loaded_path", None)
        load_error = getattr(config_loader, "_load_error", None)
        candidates = config_loader.user_config_candidates()

        if loaded is not None:
            items.append(HealthItem("ok", f"Loaded: {loaded}"))
        else:
            items.append(HealthItem("info", "No user init.py found — using defaults"))
            items.append(HealthItem("info", "Searched:"))
            for p in candidates:
                items.append(HealthItem("info", f"  {p}"))

        if load_error:
            items.append(HealthItem("error", "Error loading init.py", detail=load_error))

        # Project config
        project_loaded = getattr(config_loader, "_project_loaded_path", None)
        project_trust_status = getattr(config_loader, "_project_trust_status", "missing")
        project_prompted = bool(getattr(config_loader, "_project_trust_prompted", False))
        if project_loaded is not None:
            items.append(HealthItem("ok", f"Project config: {project_loaded}"))
            detail = "Decision persisted in shada"
            if project_prompted:
                detail += " (prompted this run)"
            items.append(HealthItem("info", f"Project config trust: {project_trust_status}", detail=detail))
        elif project_trust_status == "blocked":
            detail = "Project-local .peovim/init.py exists but is currently blocked"
            if project_prompted:
                detail += " (prompted this run)"
            items.append(HealthItem("warn", "Project config blocked by trust policy", detail=detail))
        else:
            items.append(HealthItem("info", "No project config (.peovim/init.py) found"))
    else:
        items.append(HealthItem("info", "Config loader not available"))

    # Leader key
    try:
        leader = api.keymap.leader
        leader_display = repr(leader) if leader in (" ", "\t") else leader
        items.append(HealthItem("info", f"Leader key: {leader_display}"))
    except Exception:
        pass

    return items


# ---------------------------------------------------------------------------
# 5. Plugins
# ---------------------------------------------------------------------------


def check_plugins(api: Any, plugin_manager: Any, config_loader: Any) -> list[HealthItem]:
    items: list[HealthItem] = []

    if plugin_manager is None:
        items.append(HealthItem("info", "Plugin manager not available"))
        return items

    loaded = plugin_manager.list_loaded()
    errors: dict[str, str] = getattr(plugin_manager, "_load_errors", {})

    if not loaded and not errors:
        items.append(HealthItem("info", "No plugins loaded"))
        return items

    for name in loaded:
        items.append(HealthItem("ok", name))

    for name, err in errors.items():
        items.append(HealthItem("error", name, detail=err))

    return items


# ---------------------------------------------------------------------------
# 6. Editor version and API namespace status
# ---------------------------------------------------------------------------


def check_editor_version(api: Any, plugin_manager: Any, config_loader: Any) -> list[HealthItem]:
    items: list[HealthItem] = []

    try:
        import importlib.metadata

        ver = importlib.metadata.version("peovim")
    except Exception:
        from peovim.api._metadata import VERSION_STR

        ver = VERSION_STR
    items.append(HealthItem("info", f"peovim {ver}"))

    from peovim.api._metadata import API_NAMESPACE_STATUS, VERSION_STR

    items.append(HealthItem("info", f"API version: {VERSION_STR}"))

    _STATUS_LEVEL = {"implemented": "ok", "experimental": "warn", "planned": "info"}
    for ns, ns_status in API_NAMESPACE_STATUS.items():
        level = _STATUS_LEVEL.get(ns_status.status, "info")
        items.append(HealthItem(level, f"api.{ns}: {ns_status.status}", detail=ns_status.note))

    return items


# ---------------------------------------------------------------------------
# 7. Terminal environment
# ---------------------------------------------------------------------------


def check_terminal(api: Any, plugin_manager: Any, config_loader: Any) -> list[HealthItem]:
    items: list[HealthItem] = []

    # OS / platform
    items.append(HealthItem("info", f"OS: {platform.system()} {platform.release()} ({sys.platform})"))

    # TERM / COLORTERM
    term = os.environ.get("TERM", "(not set)")
    items.append(HealthItem("info", f"TERM={term}"))

    colorterm = os.environ.get("COLORTERM", "")
    if colorterm in ("truecolor", "24bit"):
        items.append(HealthItem("ok", f"True color (COLORTERM={colorterm})"))
    elif colorterm:
        items.append(HealthItem("info", f"COLORTERM={colorterm}"))
    elif "256color" in term:
        items.append(HealthItem("ok", "256-color terminal (inferred from TERM)"))
    else:
        items.append(
            HealthItem(
                "warn",
                "True color (COLORTERM) not set — colors may be degraded",
                detail="Set COLORTERM=truecolor in your shell profile",
            )
        )

    term_program = os.environ.get("TERM_PROGRAM", "")
    if term_program:
        items.append(HealthItem("info", f"TERM_PROGRAM={term_program}"))

    wt_session = os.environ.get("WT_SESSION", "")
    if wt_session:
        items.append(HealthItem("info", "Windows Terminal detected"))

    # Terminal size
    try:
        cols, rows = shutil.get_terminal_size(fallback=(0, 0))
        if cols > 0 and rows > 0:
            items.append(HealthItem("info", f"Terminal size: {cols}×{rows}"))
        else:
            items.append(HealthItem("warn", "Terminal size unavailable (headless or redirected)"))
    except Exception:
        items.append(HealthItem("warn", "Could not determine terminal size"))

    # stdout encoding
    try:
        encoding = getattr(sys.stdout, "encoding", None) or ""
        if "utf" in encoding.lower():
            items.append(HealthItem("ok", f"stdout encoding: {encoding}"))
        elif encoding:
            items.append(
                HealthItem(
                    "warn", f"stdout encoding: {encoding}", detail="UTF-8 is recommended for full Unicode support"
                )
            )
        else:
            items.append(HealthItem("warn", "stdout encoding: unknown"))
    except Exception:
        pass

    return items


# ---------------------------------------------------------------------------
# 8. Data directories
# ---------------------------------------------------------------------------


def check_data_dirs(api: Any, plugin_manager: Any, config_loader: Any) -> list[HealthItem]:
    import pathlib

    import platformdirs

    items: list[HealthItem] = []

    dirs = [
        ("data dir", platformdirs.user_data_dir("peovim")),
        ("config dir", platformdirs.user_config_dir("peovim")),
        ("log dir", platformdirs.user_log_dir("peovim")),
        ("cache dir", platformdirs.user_cache_dir("peovim")),
    ]

    for label, dir_str in dirs:
        p = pathlib.Path(dir_str)
        if p.exists():
            try:
                test = p / ".health_write_test"
                test.touch()
                test.unlink()
                items.append(HealthItem("ok", f"{label}: {p}"))
            except OSError:
                items.append(HealthItem("warn", f"{label}: {p}", detail="exists but not writable"))
        else:
            items.append(
                HealthItem("info", f"{label}: {p}", detail="does not exist yet (will be created on first use)")
            )

    # Shada file specifically
    shada_path = pathlib.Path(platformdirs.user_data_dir("peovim")) / "shada"
    if shada_path.exists():
        try:
            size = shada_path.stat().st_size
            items.append(HealthItem("ok", f"shada: {shada_path}", detail=f"{size} bytes"))
        except OSError:
            items.append(HealthItem("warn", f"shada: {shada_path}", detail="exists but stat() failed"))
    else:
        items.append(HealthItem("info", "shada: not yet created"))

    return items


# ---------------------------------------------------------------------------
# 9. Language Server Protocol
# ---------------------------------------------------------------------------


def check_lsp(api: Any, plugin_manager: Any, config_loader: Any) -> list[HealthItem]:
    items: list[HealthItem] = []

    lsp_api = getattr(api, "lsp", None)
    if lsp_api is None:
        items.append(HealthItem("info", "LSP subsystem not initialized"))
        return items

    manager = getattr(lsp_api, "_manager", None)
    if manager is None:
        items.append(HealthItem("info", "LSP manager not available"))
        return items

    configs = getattr(manager, "_configs", [])
    if not configs:
        items.append(HealthItem("info", "No LSP servers configured", detail="Use api.lsp.register_server() in init.py"))
        return items

    items.append(HealthItem("info", f"{len(configs)} server configuration(s):"))
    for cfg in configs:
        cmd0 = cfg.cmd[0]
        cmd_str = " ".join(cfg.cmd)
        found = shutil.which(cmd0)
        if found:
            items.append(HealthItem("ok", f"{cfg.filetype}: {cmd0}", detail=f"cmd: {cmd_str}\npath: {found}"))
        else:
            items.append(HealthItem("warn", f"{cfg.filetype}: {cmd0} not found in PATH", detail=f"cmd: {cmd_str}"))

    # Active server instances
    try:
        servers = manager.list_servers()
        if servers:
            items.append(HealthItem("info", f"{len(servers)} active server instance(s):"))
            for srv in servers:
                initialized = srv.get("initialized", False)
                status_str = "initialized" if initialized else "starting"
                level = "ok" if initialized else "info"
                items.append(HealthItem(level, f"  {srv['filetype']} @ {srv['root']} ({status_str})"))
        else:
            items.append(HealthItem("info", "No active server instances (opens a file to trigger attach)"))
    except Exception as exc:
        items.append(HealthItem("warn", f"Could not query active servers: {exc}"))

    return items


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cmd_version(cmd: str, *args: str) -> str:
    """Run `cmd *args` and return the first line of stdout, or '?'."""
    import subprocess

    try:
        result = subprocess.run([cmd, *args], capture_output=True, text=True, timeout=3)
        first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        # Strip leading command name / "version" word from e.g. "git version 2.44.0"
        # or "ripgrep 14.1.0 (rev ...)" → keep just the version token
        import re as _re

        m = _re.search(r"\d+\.\d+[\w.\-+()\ ]*", first_line)
        return m.group(0).strip() if m else (first_line or "?")
    except Exception:
        return "?"
