"""
plugins.copilot_client — Copilot language server client.

Wraps the GitHub Copilot language server using standard LSP JSON-RPC over
stdio.  Runs on the main asyncio event loop (no background thread needed).

Binary lookup order:
  1. ~/.config/peovim/copilot/copilot-language-server[.exe]  (native binary)
  2. copilot-language-server on PATH  (npm global install)

To install via npm:
    npm install -g @github/copilot-language-server
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import deque

from peovim.lsp.client import LspClient
from peovim.lsp.protocol import path_to_uri

log = logging.getLogger(__name__)


def find_copilot_binary() -> str | None:
    """Return the path to copilot-language-server, or None if not found."""
    import shutil
    from pathlib import Path

    suffix = ".exe" if sys.platform == "win32" else ""
    native = Path.home() / ".config" / "peovim" / "copilot" / f"copilot-language-server{suffix}"
    if native.exists():
        return str(native)

    return shutil.which("copilot-language-server")


class CopilotClient:
    """Manages one Copilot language server subprocess."""

    def __init__(self) -> None:
        self._client: LspClient | None = None
        self._initialized = False
        self._open_docs: dict[str, int] = {}  # path -> last-sent version

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """Launch and initialize the language server. Returns True on success."""
        binary = find_copilot_binary()
        if binary is None:
            log.warning(
                "copilot: language server not found — "
                "run: npm install -g @github/copilot-language-server  "
                "or place the native binary at ~/.config/peovim/copilot/copilot-language-server"
            )
            return False

        loop = asyncio.get_running_loop()
        self._client = LspClient(
            cmd=[binary, "--stdio"],
            root=".",
            loop=loop,
            result_queue=deque(),
        )
        try:
            await self._client.start()
        except Exception as exc:
            log.warning("copilot: failed to start server: %s", exc)
            return False

        try:
            await asyncio.wait_for(
                self._client.request(
                    "initialize",
                    {
                        "processId": None,
                        "clientInfo": {"name": "peovim", "version": "0.1"},
                        "rootUri": None,
                        "capabilities": {},
                        "initializationOptions": {
                            "editorInfo": {"name": "peovim", "version": "0.1"},
                            "editorPluginInfo": {"name": "peovim-copilot", "version": "0.1"},
                        },
                    },
                ),
                timeout=10.0,
            )
            self._client.notify("initialized", {})
            self._initialized = True
            log.debug("copilot: server initialized")
        except Exception as exc:
            log.warning("copilot: initialization failed: %s", exc)
            return False

        return True

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.shutdown()
            self._client = None
            self._initialized = False

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def check_status(self) -> str:
        """Return auth status string: 'OK', 'NotAuthorized', 'NotSignedIn', etc."""
        if self._client is None:
            return "NotStarted"
        try:
            result = await asyncio.wait_for(
                self._client.request("checkStatus", {"options": {}}),
                timeout=5.0,
            )
            return result.get("status", "Unknown") if isinstance(result, dict) else "Unknown"
        except Exception as exc:
            log.debug("copilot: checkStatus error: %s", exc)
            return "Error"

    async def sign_in(self) -> tuple[str, str] | None:
        """Initiate device-flow sign-in. Returns (userCode, verificationUri) or None."""
        if self._client is None:
            return None
        try:
            result = await asyncio.wait_for(
                self._client.request("signInInitiate", {}),
                timeout=10.0,
            )
            if not isinstance(result, dict):
                return None
            return result.get("userCode", ""), result.get("verificationUri", "")
        except Exception as exc:
            log.warning("copilot: signIn error: %s", exc)
            return None

    async def sign_in_confirm(self, user_code: str) -> bool:
        """Poll sign-in status. Returns True when the user has authorized."""
        if self._client is None:
            return False
        try:
            result = await asyncio.wait_for(
                self._client.request("signInConfirm", {"userCode": user_code}),
                timeout=10.0,
            )
            if not isinstance(result, dict):
                return False
            return result.get("status", "") == "OK"
        except Exception as exc:
            log.debug("copilot: signInConfirm error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Document sync
    # ------------------------------------------------------------------

    def notify_did_open(self, path: str, language_id: str, version: int, text: str) -> None:
        if not self._initialized or self._client is None:
            return
        self._open_docs[path] = version
        self._client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path_to_uri(path),
                    "languageId": language_id,
                    "version": version,
                    "text": text,
                }
            },
        )

    def notify_did_change(self, path: str, version: int, text: str) -> None:
        if not self._initialized or self._client is None:
            return
        self._open_docs[path] = version
        self._client.notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": path_to_uri(path), "version": version},
                "contentChanges": [{"text": text}],
            },
        )

    # ------------------------------------------------------------------
    # Completions
    # ------------------------------------------------------------------

    async def get_completions(
        self,
        path: str,
        language_id: str,
        version: int,
        text: str,
        line: int,
        col: int,
        *,
        tab_size: int = 4,
        insert_spaces: bool = True,
    ) -> list[dict]:
        """Request inline completions. Returns a list of completion dicts."""
        if not self._initialized or self._client is None:
            return []
        try:
            result = await asyncio.wait_for(
                self._client.request(
                    "getCompletions",
                    {
                        "doc": {
                            "source": text,
                            "tabSize": tab_size,
                            "indentSize": tab_size,
                            "insertSpaces": insert_spaces,
                            "path": path,
                            "uri": path_to_uri(path),
                            "relativePath": path.replace("\\", "/").lstrip("/"),
                            "languageId": language_id,
                            "position": {"line": line, "character": col},
                            "version": version,
                        }
                    },
                ),
                timeout=5.0,
            )
            if isinstance(result, dict):
                return result.get("completions", [])
        except TimeoutError:
            log.debug("copilot: getCompletions timed out")
        except Exception as exc:
            log.debug("copilot: getCompletions error: %s", exc)
        return []


def language_id_from_path(path: str) -> str:
    """Guess LSP languageId from file extension."""
    import os

    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascriptreact",
        ".tsx": "typescriptreact",
        ".rs": "rust",
        ".go": "go",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".java": "java",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".lua": "lua",
        ".sh": "shellscript",
        ".bash": "shellscript",
        ".md": "markdown",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".vim": "vim",
        ".zig": "zig",
        ".swift": "swift",
        ".kt": "kotlin",
    }.get(os.path.splitext(path)[1].lower(), "plaintext")
