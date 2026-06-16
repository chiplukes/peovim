"""
config.loader — init.py config loader

Locates and executes the user's init.py at:
  platformdirs.user_config_dir("peovim") / "init.py"

Also loads project-local .peovim/init.py when the project has been trusted.

The EditorAPI object and its sub-APIs are injected into the script namespace
so that user config can be written in a flat style, e.g.:

    keymap.nmap('<leader>ff', ...)
    options.set('tabstop', 4)
    plugins.load('peovim.plugins.lsp')

If init.py defines a setup(api) function (sync or async), it is called
with the EditorAPI as the sole argument after the module-level code runs.

All errors are caught and logged; a bad init.py never crashes the editor.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
from typing import Any

from peovim.config.project import find_project_config

log = logging.getLogger("peovim.config")


def preferred_user_config_path() -> Path:
    """Return the preferred init.py path, preferring an existing candidate."""
    loader = ConfigLoader()
    for candidate in loader.user_config_candidates():
        if candidate.exists():
            return candidate
    return loader.user_config_path()


class ConfigLoader:  # cm:4f2e9b
    """
    Loads user and project-local configuration files.

    Usage:
        loader = ConfigLoader()
        loader.load_user_config(api, plugin_manager=plugin_manager)
    """

    def __init__(self) -> None:
        from peovim.config.project import TrustStore

        self._trust = TrustStore()
        self._loaded_path: Path | None = None  # which init.py was executed
        self._load_error: str = ""  # error text if exec failed
        self._project_loaded_path: Path | None = None  # project-local config if loaded
        self._project_trust_status: str = "missing"
        self._project_trust_prompted: bool = False

    def user_config_path(self) -> Path:
        """Return the primary platform config path (may not exist)."""
        import platformdirs

        return Path(platformdirs.user_config_dir("peovim")) / "init.py"

    def user_config_candidates(self) -> list[Path]:
        """
        Return all candidate init.py paths in priority order.

        On Windows the XDG path (~/.config/peovim/init.py) is included as a
        fallback so users working in WSL, Git Bash, or MSYS2 environments
        can keep a single config in the Linux-style location.
        """
        import platform

        candidates: list[Path] = [self.user_config_path()]
        if platform.system() == "Windows":
            xdg = Path.home() / ".config" / "peovim" / "init.py"
            if xdg not in candidates:
                candidates.append(xdg)
        return candidates

    def load_user_config(self, api: Any, plugin_manager: Any = None) -> None:
        """
        Execute the user init.py once and any project-local .peovim/init.py.

        Args:
            api:            The EditorAPI instance.
            plugin_manager: Optional PluginManager; exposed as `plugins` in init.py.
        """
        editor_state = getattr(api, "_editor_state", None)
        shada = getattr(editor_state, "shada", None)
        if shada is not None:
            self._trust.attach_shada(shada)

        # 1. User-level config — first candidate that exists wins
        for user_cfg in self.user_config_candidates():
            if user_cfg.is_file():
                ok = self._exec_config(user_cfg, api, plugin_manager, label="user config")
                if ok:
                    self._loaded_path = user_cfg
                break

        # 2. Project-local config (<root>/.peovim/init.py)
        try:
            start = _cwd_or_buffer(api)
            project_cfg = find_project_config(start)
            if project_cfg is not None:
                root = project_cfg.parent.parent
                prior_decision = self._trust.get_decision(root)
                trusted = self._trust.is_trusted(root)
                self._project_trust_prompted = prior_decision is None
                self._project_trust_status = "trusted" if trusted else "blocked"
                if trusted:
                    ok = self._exec_config(project_cfg, api, plugin_manager, label="project config")
                    if ok:
                        self._project_loaded_path = project_cfg
                elif editor_state is not None:
                    editor_state.message = f"Skipped untrusted project config: {project_cfg}"
            else:
                self._project_trust_status = "missing"
        except Exception as exc:
            log.warning("Error checking project config: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _exec_config(
        self,
        path: Path,
        api: Any,
        plugin_manager: Any,
        label: str,
    ) -> bool:
        """Compile and exec a single config file, then call setup() if defined.

        Returns True on success, False on error.
        """
        try:
            code_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("Cannot read %s (%s): %s", label, path, exc)
            self._load_error = str(exc)
            return False

        namespace = _build_namespace(api, plugin_manager)
        try:
            compiled = compile(code_text, str(path), "exec")
            exec(compiled, namespace)  # noqa: S102
        except Exception as exc:
            log.error("Error in %s (%s):\n%s", label, path, exc)
            self._load_error = str(exc)
            return False

        setup_fn = namespace.get("setup")
        if callable(setup_fn):
            self._call_setup(setup_fn, api, label, path)
        return True

    def _call_setup(self, fn: Any, api: Any, label: str, path: Path) -> None:
        """Call setup(api), handling both sync and async variants."""
        try:
            if inspect.iscoroutinefunction(fn):
                # No running loop at startup — run it to completion
                asyncio.run(fn(api))
            else:
                fn(api)
        except Exception as exc:
            log.error("Error in setup() from %s (%s):\n%s", label, path, exc)


# ---------------------------------------------------------------------------
# Namespace helpers
# ---------------------------------------------------------------------------


def _build_namespace(api: Any, plugin_manager: Any) -> dict:
    """
    Build the globals dict injected into init.py scripts.

    Flat-style access (e.g. ``keymap.nmap(...)`` without ``api.``) is provided
    by injecting each sub-API directly.
    """
    import logging as _logging

    ns: dict[str, Any] = {
        "__builtins__": __builtins__,
        "api": api,
        "editor": api,  # alias
        "keymap": api.keymap,
        "commands": api.commands,
        "events": api.events,
        "options": api.options,
        "ui": api.ui,
        "git": api.git,
        "store": api.store,
        "lsp": api.lsp,
        # remember(fn) — wrap a keymap callback so <leader><leader> can repeat it.
        # repeat     — call the last remember()-wrapped command directly.
        # Both are public attributes on EditorAPI, set by the editor_utils plugin.
        # Default no-ops on EditorAPI ensure init.py is safe if the plugin is not loaded.
        "remember": api.remember,
        "repeat": api.repeat,
        # Pre-configured logger under the peovim hierarchy — use log.info("…") in init.py
        # to emit messages that appear in the bottom panel output tab.
        "log": _logging.getLogger("peovim.user"),
    }
    if plugin_manager is not None:
        ns["plugins"] = plugin_manager
    return ns


def _cwd_or_buffer(api: Any) -> Path:
    """Return the active buffer's directory, falling back to cwd."""
    try:
        buf = api.active_buffer()
        p = buf.path
        if p is not None:
            return p.parent
    except Exception:
        pass
    return Path.cwd()
