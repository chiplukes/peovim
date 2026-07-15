"""
lsp.manager — LspManager: server registry and attach/detach lifecycle.

Tracks registered server configurations and active LspClient+LspFeatures
instances. Attaches/detaches per buffer based on filetype. One server
process per (filetype, workspace_root) pair.

All asyncio operations run on a shared background event loop thread.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import pathlib
import shutil
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ServerConfig:
    filetype: str
    cmd: list[str]
    root_markers: list[str] = field(default_factory=lambda: [".git", "pyproject.toml", "setup.py"])


ServerKey = tuple[str, str, tuple[str, ...]]


class LspManager:  # cm:2b9e4c
    """
    Manages LSP server processes and buffer attachments.

    Usage:
        manager = LspManager(result_queue)
        manager.start_background_loop()
        manager.register(ServerConfig("python", ["ty", "server"]))
        features = manager.attach(doc)
        manager.stop()
    """

    _DEBOUNCE_S: float = 0.15  # seconds to wait before flushing didChange

    def __init__(self, result_queue: deque) -> None:
        self._result_queue = result_queue
        self._configs: list[ServerConfig] = []
        # Key: (filetype, root, cmd) → (LspClient, LspFeatures)
        self._servers: dict[ServerKey, tuple] = {}
        # doc id → attached server keys for quick lookup
        self._doc_servers: dict[int, list[ServerKey]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        # handlers registered before servers start — applied to each new LspFeatures
        self._pending_notification_handlers: dict[str, list] = {}
        self._pending_progress_handlers: list = []
        # Debounce state: pending didChange per doc, guarded by _notify_lock
        self._notify_lock = threading.Lock()
        self._pending_notify: dict[int, tuple[str, list, str]] = {}  # doc_id → (path, keys, text)
        self._notify_timer: threading.Timer | None = None

    # ------------------------------------------------------------------
    # Background asyncio thread
    # ------------------------------------------------------------------

    def start_background_loop(self) -> None:
        """Start the asyncio event loop in a daemon thread.

        Blocks until the loop is confirmed running so that the first
        attach() call (triggered by buffer_opened shortly after editor_ready)
        sees is_running() == True.
        """
        self._loop = asyncio.new_event_loop()
        _ready = threading.Event()

        def _run() -> None:
            assert self._loop
            asyncio.set_event_loop(self._loop)
            self._loop.call_soon(_ready.set)
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True, name="lsp-asyncio")
        self._thread.start()
        _ready.wait(timeout=2.0)  # wait until loop.run_forever() is actually running

    def stop(self) -> None:
        """Shut down all server processes and stop the event loop."""
        if self._loop is None:
            return
        for key in list(self._servers):
            self._schedule(self._shutdown_server(key))
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _schedule(self, coro) -> concurrent.futures.Future | None:  # type: ignore[type-arg]
        if self._loop and self._loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self._loop)
        return None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def register(self, config: ServerConfig) -> None:
        """Register a server configuration for a filetype."""
        import os

        cmd0 = config.cmd[0]
        # Accept absolute paths directly; fall back to PATH search for bare names.
        if os.path.isabs(cmd0):
            if not os.path.isfile(cmd0):
                log.debug("LSP server binary not found: %s", cmd0)
                return
        elif not shutil.which(cmd0):
            log.debug("LSP server not found on PATH: %s", cmd0)
            return
        # Deduplicate: skip if same filetype+cmd already registered
        for existing in self._configs:
            if existing.filetype == config.filetype and existing.cmd == config.cmd:
                return
        self._configs.append(config)
        log.debug("Registered LSP server %r for filetype %r", cmd0, config.filetype)

    def get_config(self, filetype: str) -> ServerConfig | None:
        configs = self.get_configs(filetype)
        return configs[0] if configs else None

    def get_configs(self, filetype: str) -> list[ServerConfig]:
        return [cfg for cfg in self._configs if cfg.filetype == filetype]

    def list_servers(self) -> list[dict]:
        """Return info dicts for all active server instances."""
        result = []
        for (ft, root, _cmd_key), (client, features) in self._servers.items():
            result.append(
                {
                    "filetype": ft,
                    "root": root,
                    "cmd": client._cmd,
                    "initialized": features._initialized,
                }
            )
        return result

    # ------------------------------------------------------------------
    # Attach / detach
    # ------------------------------------------------------------------

    def attach(self, doc: Any) -> Any:
        """
        Attach an LSP server to a document.

        Returns the LspFeatures instance (or None if no server configured
        for this filetype or file has no path). Initialization is async —
        the server may not be ready instantly; did_open is sent after initialize.
        """
        from peovim.lsp.client import LspClient
        from peovim.lsp.features import LspFeatures
        from peovim.lsp.protocol import path_to_uri

        if self._loop is None:
            log.debug("attach: loop is None, skipping")
            return None
        path = doc.path
        if path is None:
            log.debug("attach: doc.path is None, skipping")
            return None
        path_str = str(path)
        filetype = getattr(doc, "filetype", "") or _detect_filetype(path_str)
        log.debug("attach: path=%s filetype=%r", path_str, filetype)
        if not filetype:
            log.debug("attach: no filetype detected, skipping")
            return None

        configs = self.get_configs(filetype)
        if not configs:
            log.debug("attach: no config for filetype %r", filetype)
            return None

        text = doc.get_text()
        attached_keys: list[ServerKey] = []
        primary_features = None

        for cfg in configs:
            root = _find_root(path_str, cfg.root_markers)
            key: ServerKey = (cfg.filetype, root, tuple(cfg.cmd))
            log.debug("attach: key=%s", key)

            if key not in self._servers:
                venv = _find_venv(root)
                env = _build_venv_env(venv) if venv else None
                extra_paths = _find_editable_extra_paths(venv) if venv else []
                if venv:
                    log.debug("attach: venv detected at %s", venv)
                if extra_paths:
                    log.debug("attach: extra editable paths: %s", extra_paths)
                client = LspClient(
                    cmd=cfg.cmd,
                    root=root,
                    loop=self._loop,
                    result_queue=self._result_queue,
                    env=env,
                )
                init_options: dict = {}
                if extra_paths:
                    init_options["environment"] = {"extra-paths": extra_paths}
                features = LspFeatures(client, self._result_queue, filetype=cfg.filetype, init_options=init_options)
                client._notification_handler = features.handle_notification
                for method, cbs in self._pending_notification_handlers.items():
                    for cb in cbs:
                        features.on_notification(method, cb)
                for cb in self._pending_progress_handlers:
                    features.on_progress(cb)
                self._servers[key] = (client, features)
                root_uri = path_to_uri(root)
                self._schedule(self._start_server_and_open(client, features, root_uri, path_str, cfg.filetype, text))
            else:
                _client, features = self._servers[key]
                self._loop.call_soon_threadsafe(features.did_open, path_str, cfg.filetype, text)

            attached_keys.append(key)
            if primary_features is None:
                primary_features = self._servers[key][1]

        self._doc_servers[id(doc)] = attached_keys
        return primary_features

    def detach(self, doc: Any) -> None:
        """Notify the server that this document was closed."""
        keys = self._doc_servers.pop(id(doc), None)
        if not keys:
            return
        path = doc.path
        if path is None or self._loop is None:
            return
        for key in keys:
            server = self._servers.get(key)
            if server:
                _client, feats = server
                self._loop.call_soon_threadsafe(feats.did_close, str(path))

    def notify_change(self, doc: Any) -> None:
        """Schedule a debounced textDocument/didChange for a modified buffer.

        Multiple rapid calls coalesce: only the latest text per document is
        sent, after _DEBOUNCE_S seconds of inactivity.
        """
        keys = self._doc_servers.get(id(doc))
        if not keys or self._loop is None:
            return
        path = doc.path
        if path is None:
            return
        text = doc.get_text()  # capture on main thread while doc is safe to read
        with self._notify_lock:
            self._pending_notify[id(doc)] = (str(path), list(keys), text)
            if self._notify_timer is None:
                self._notify_timer = threading.Timer(self._DEBOUNCE_S, self._fire_pending)
                self._notify_timer.daemon = True
                self._notify_timer.start()

    def _fire_pending(self) -> None:
        """Timer callback: flush all pending debounced change notifications."""
        with self._notify_lock:
            pending = dict(self._pending_notify)
            self._pending_notify.clear()
            self._notify_timer = None
        self._send_pending(pending)

    def flush_pending_changes(self) -> None:
        """Flush pending debounced changes immediately (call before LSP requests)."""
        with self._notify_lock:
            if self._notify_timer is not None:
                self._notify_timer.cancel()
                self._notify_timer = None
            pending = dict(self._pending_notify)
            self._pending_notify.clear()
        self._send_pending(pending)

    def _send_pending(self, pending: dict) -> None:
        if not self._loop:
            return
        for path, keys, text in pending.values():
            for key in keys:
                server = self._servers.get(key)
                if server is None:
                    continue
                _client, feats = server
                self._loop.call_soon_threadsafe(feats.did_change, path, text)

    def notify_save(self, doc: Any) -> None:
        """Send textDocument/didSave (flushes any pending debounced didChange first)."""
        self.flush_pending_changes()
        keys = self._doc_servers.get(id(doc))
        if not keys:
            return
        path = doc.path
        if path is None or self._loop is None:
            return
        for key in keys:
            server = self._servers.get(key)
            if server is None:
                continue
            _client, feats = server
            self._loop.call_soon_threadsafe(feats.did_save, str(path))

    def get(self, doc: Any) -> Any:
        """Return the LspFeatures for this document, or None."""
        keys = self._doc_servers.get(id(doc))
        if not keys:
            return None
        server = self._servers.get(keys[0])
        return server[1] if server else None

    def get_all(self, doc: Any) -> list[Any]:
        """Return all LspFeatures attached to this document."""
        keys = self._doc_servers.get(id(doc), [])
        features: list[Any] = []
        for key in keys:
            server = self._servers.get(key)
            if server is not None:
                features.append(server[1])
        return features

    def restart(self, filetype: str = "") -> None:
        """Restart servers matching filetype (or all if empty)."""
        to_restart = [key for key in self._servers if not filetype or key[0] == filetype]
        for key in to_restart:
            self._schedule(self._restart_server(key))

    # ------------------------------------------------------------------
    # Internal coroutines
    # ------------------------------------------------------------------

    async def _start_server(self, client, features, root_uri: str) -> None:
        await client.start()
        await features.initialize(root_uri)

    async def _start_server_and_open(
        self, client, features, root_uri: str, path: str, language_id: str, text: str
    ) -> None:
        try:
            await client.start()
            await features.initialize(root_uri)
            features.did_open(path, language_id, text)
        except Exception:
            log.exception("LSP server start failed for %s", path)

    async def _shutdown_server(self, key: ServerKey) -> None:
        server = self._servers.pop(key, None)
        if server:
            client, _feats = server
            await client.shutdown()

    async def _restart_server(self, key: ServerKey) -> None:
        from peovim.lsp.client import LspClient
        from peovim.lsp.features import LspFeatures
        from peovim.lsp.protocol import path_to_uri

        server = self._servers.pop(key, None)
        if server:
            client, _feats = server
            await client.shutdown()

        ft, root, cmd_key = key
        cfg = next((cfg for cfg in self.get_configs(ft) if tuple(cfg.cmd) == cmd_key), None)
        if cfg is None:
            return
        venv = _find_venv(root)
        env = _build_venv_env(venv) if venv else None
        extra_paths = _find_editable_extra_paths(venv) if venv else []
        init_options: dict = {}
        if extra_paths:
            init_options["environment"] = {"extra-paths": extra_paths}
        assert self._loop is not None
        client = LspClient(cmd=cfg.cmd, root=root, loop=self._loop, result_queue=self._result_queue, env=env)
        features = LspFeatures(client, self._result_queue, filetype=ft, init_options=init_options)
        client._notification_handler = features.handle_notification
        self._servers[key] = (client, features)
        await self._start_server(client, features, path_to_uri(root))


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _find_root(path: str, markers: list[str]) -> str:
    """Walk up from path's directory looking for root markers.

    A .git file (rather than a directory) indicates a git submodule
    checkout.  We skip those and keep walking up so that the LSP
    workspace root is the superproject, not the submodule.
    """
    p = pathlib.Path(path).resolve().parent
    while True:
        for marker in markers:
            candidate = p / marker
            if not candidate.exists():
                continue
            if marker == ".git" and not candidate.is_dir():
                continue
            return str(p)
        parent = p.parent
        if parent == p:
            break
        p = parent
    return str(pathlib.Path(path).resolve().parent)


def _find_venv(root: str) -> str | None:
    """Detect a virtualenv directory at the workspace root."""
    import sys

    p = pathlib.Path(root)
    for name in (".venv", "venv", ".env", "env"):
        venv_dir = p / name
        python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        if python.is_file():
            return str(venv_dir)
    return None


def _build_venv_env(venv_dir: str) -> dict[str, str]:
    """Return an environment dict with VIRTUAL_ENV and PATH set for the given venv."""
    import os
    import sys

    env = dict(os.environ)
    env["VIRTUAL_ENV"] = venv_dir
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    venv_bin = str(pathlib.Path(venv_dir) / scripts)
    current_path = env.get("PATH", "")
    if venv_bin not in current_path.split(os.pathsep):
        env["PATH"] = venv_bin + os.pathsep + current_path
    env.pop("PYTHONHOME", None)
    return env


def _find_editable_extra_paths(venv_dir: str) -> list[str]:
    """
    Scan venv site-packages for finder-based editable installs that static
    type checkers can't follow, and return the parent source directories.

    Standard editable installs write a plain path into a .pth file (e.g.
    ``/src/pkg``). Some build backends instead install a ``__editable__*.pth``
    file that executes ``import __editable___pkg_finder; finder.install()``.
    Static tools like ty skip the import-style .pth entries, so we parse the
    MAPPING dict from the companion finder module to get the real source path.
    """
    site_packages = pathlib.Path(venv_dir) / "Lib" / "site-packages"
    if not site_packages.is_dir():
        site_packages = pathlib.Path(venv_dir) / "lib"
        if site_packages.is_dir():
            # Unix: Lib/pythonX.Y/site-packages
            for child in site_packages.iterdir():
                candidate = child / "site-packages"
                if candidate.is_dir():
                    site_packages = candidate
                    break

    extra_paths: list[str] = []
    for pth_file in site_packages.glob("__editable__*.pth"):
        try:
            content = pth_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not content.startswith("import "):
            continue  # plain path-based .pth; ty can handle it
        # Derive the finder module name: "__editable__bigsky-0.1.0.pth"
        # → the pth imports "__editable___bigsky_0_1_0_finder"
        finder_name = pth_file.stem.replace("-", "_").replace(".", "_") + "_finder"
        finder_file = site_packages / f"{finder_name}.py"
        if not finder_file.is_file():
            continue
        try:
            finder_src = finder_file.read_text(encoding="utf-8")
        except OSError:
            continue
        # Parse: MAPPING: dict[str, str] = {'pkg': '/path/to/pkg/src/pkg'}
        for line in finder_src.splitlines():
            line = line.strip()
            if not line.startswith("MAPPING"):
                continue
            # Extract the dict literal value part
            brace_start = line.find("{")
            brace_end = line.rfind("}")
            if brace_start == -1 or brace_end == -1:
                continue
            try:
                import ast

                mapping: dict = ast.literal_eval(line[brace_start : brace_end + 1])
            except Exception:
                continue
            for pkg_src in mapping.values():
                # pkg_src is the package directory itself; we need its parent
                # so that ``import pkg`` resolves via the parent directory.
                parent = str(pathlib.Path(pkg_src).parent)
                if parent not in extra_paths:
                    extra_paths.append(parent)
            break
    return extra_paths


_EXT_TO_FT: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".lua": "lua",
    ".rb": "ruby",
    ".java": "java",
    ".cs": "csharp",
}


def _detect_filetype(path: str) -> str:
    from peovim.core.filetype import detect_filetype

    return detect_filetype(path)
