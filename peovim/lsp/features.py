"""
lsp.features — LSP method wrappers and notification handlers.

One LspFeatures instance per (server, buffer). Handles LSP lifecycle
(initialize, did_open/change/save/close) and feature requests (hover,
definition, completion, references, rename, diagnostics).
"""

from __future__ import annotations

import logging
import pathlib
from collections import deque
from collections.abc import Callable
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from peovim.core.text_edits import transform_range
from peovim.lsp.protocol import path_to_uri, uri_to_path

if TYPE_CHECKING:
    from peovim.lsp.client import LspClient

log = logging.getLogger(__name__)

_CLIENT_CAPABILITIES = {
    "textDocument": {
        "hover": {"contentFormat": ["plaintext", "markdown"]},
        "codeAction": {},
        "completion": {
            "completionItem": {"snippetSupport": False},
        },
        "definition": {"linkSupport": False},
        "documentHighlight": {},
        "inlayHint": {"dynamicRegistration": False},
        "references": {},
        "rename": {"prepareSupport": False},
        "publishDiagnostics": {"relatedInformation": False},
    },
    "workspace": {
        "applyEdit": False,
        "workspaceFolders": True,
    },
}


class LspFeatures:  # cm:c8a1e6
    """
    High-level LSP feature interface for one connected server.

    All request methods take an optional `cb` callback that will be called
    on the asyncio thread with the result. Results are typically also posted
    to result_queue as editor-update callables.
    """

    def __init__(
        self, client: LspClient, result_queue: deque, filetype: str = "", init_options: dict | None = None
    ) -> None:
        self._client = client
        self._result_queue = result_queue
        self._filetype = filetype
        self._init_options = init_options or {}
        self._initialized = False
        # Per-server namespaces prevent multiple servers (e.g. ty + ruff) from
        # clearing each other's diagnostics when one sends an empty batch.
        _srv = client._cmd[0] if client._cmd else "lsp"
        self._sign_ns = f"lsp:diag:signs:{_srv}"
        self._text_ns = f"lsp:diag:text:{_srv}"
        self._server_capabilities: dict[str, Any] = {}
        # per-path version counters for textDocument sync
        self._versions: dict[str, int] = {}
        # diagnostics callbacks: path → list of (sign_type, line, msg)
        self._diag_callbacks: list[Callable[[str, list[dict]], None]] = []
        self._last_diagnostics: dict[str, list[dict]] = {}
        # plugin-registered handlers for server→client notifications
        self._custom_notification_handlers: dict[str, list[Callable[[dict], None]]] = {}
        # progress notification callbacks: token → list of callbacks
        self._progress_callbacks: list[Callable[[str, str, dict], None]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, root_uri: str) -> None:
        """Send LSP initialize handshake."""
        params: dict[str, Any] = {
            "processId": None,
            "rootUri": root_uri,
            "workspaceFolders": [
                {
                    "uri": root_uri,
                    "name": pathlib.PurePosixPath(uri_to_path(root_uri)).name or "workspace",
                }
            ],
            "capabilities": _CLIENT_CAPABILITIES,
            "initializationOptions": self._init_options,
        }
        try:
            result = await self._client.request("initialize", params)
            self._server_capabilities = result.get("capabilities", {}) if isinstance(result, dict) else {}
            self._client.notify("initialized", {})
            self._initialized = True
            ft = self._filetype or "unknown"
            self._result_queue.appendleft(lambda es, ws, f=ft: setattr(es, "message", f"LSP: {f} server ready"))
        except Exception:
            log.exception("LSP initialize failed")

    def supports_capability(self, name: str) -> bool:
        """Return True when the server advertised the named capability."""
        return bool(self._server_capabilities.get(name))

    def did_open(self, path: str, language_id: str, text: str) -> None:
        if not self._initialized:
            return
        self._versions[path] = 1
        self._client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path_to_uri(path),
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )

    def did_change(self, path: str, text: str) -> None:
        if not self._initialized:
            return
        version = self._versions.get(path, 0) + 1
        self._versions[path] = version
        self._client.notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": path_to_uri(path), "version": version},
                "contentChanges": [{"text": text}],
            },
        )

    def did_save(self, path: str) -> None:
        if not self._initialized:
            return
        self._client.notify(
            "textDocument/didSave",
            {
                "textDocument": {"uri": path_to_uri(path)},
            },
        )

    def did_close(self, path: str) -> None:
        if not self._initialized:
            return
        self._client.notify(
            "textDocument/didClose",
            {
                "textDocument": {"uri": path_to_uri(path)},
            },
        )
        self._versions.pop(path, None)

    # ------------------------------------------------------------------
    # Feature requests
    # ------------------------------------------------------------------

    def _schedule(self, coro) -> None:
        """Schedule a coroutine on the LSP background loop (thread-safe)."""
        import asyncio

        loop = self._client._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)

    def hover(self, path: str, line: int, col: int, cb: Callable[[str | None], None]) -> None:
        async def _do():
            try:
                result = await self._client.request(
                    "textDocument/hover",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                        "position": {"line": line, "character": col},
                    },
                )
                text = _extract_hover_text(result)
                self._result_queue.appendleft(lambda es, ws, t=text: cb(t))
            except Exception as e:
                log.debug("hover error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb(None))

        self._schedule(_do())

    def definition(self, path: str, line: int, col: int, cb: Callable[[list[dict]], None]) -> None:
        async def _do():
            try:
                result = await self._client.request(
                    "textDocument/definition",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                        "position": {"line": line, "character": col},
                    },
                )
                locs = _normalise_locations(result)
                self._result_queue.appendleft(lambda es, ws, r=locs: cb(r))
            except Exception as e:
                log.debug("definition error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb([]))

        self._schedule(_do())

    def implementation(self, path: str, line: int, col: int, cb: Callable[[list[dict]], None]) -> None:
        async def _do():
            try:
                result = await self._client.request(
                    "textDocument/implementation",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                        "position": {"line": line, "character": col},
                    },
                )
                locs = _normalise_locations(result)
                self._result_queue.appendleft(lambda es, ws, r=locs: cb(r))
            except Exception as e:
                log.debug("implementation error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb([]))

        self._schedule(_do())

    def signature_help(self, path: str, line: int, col: int, cb: Callable[[str | None], None]) -> None:
        async def _do():
            try:
                result = await self._client.request(
                    "textDocument/signatureHelp",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                        "position": {"line": line, "character": col},
                    },
                )
                text = _extract_signature_help_text(result)
                self._result_queue.appendleft(lambda es, ws, t=text: cb(t))
            except Exception as e:
                log.debug("signatureHelp error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb(None))

        self._schedule(_do())

    def document_symbols(self, path: str, cb: Callable[[list[dict]], None]) -> None:
        async def _do():
            try:
                result = await self._client.request(
                    "textDocument/documentSymbol",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                    },
                )
                symbols = _extract_document_symbols(result, path)
                self._result_queue.appendleft(lambda es, ws, s=symbols: cb(s))
            except Exception as e:
                log.debug("documentSymbol error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb([]))

        self._schedule(_do())

    def document_symbols_tree(self, path: str, cb: Callable[[list[dict]], None]) -> None:
        async def _do():
            try:
                result = await self._client.request(
                    "textDocument/documentSymbol",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                    },
                )
                symbols = _extract_document_symbol_tree(result, path)
                self._result_queue.appendleft(lambda es, ws, s=symbols: cb(s))
            except Exception as e:
                log.debug("documentSymbol tree error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb([]))

        self._schedule(_do())

    def document_highlight(self, path: str, line: int, col: int, cb: Callable[[list[dict]], None]) -> None:
        if not self.supports_capability("documentHighlightProvider"):
            self._result_queue.appendleft(lambda es, ws: cb([]))
            return

        async def _do():
            try:
                result = await self._client.request(
                    "textDocument/documentHighlight",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                        "position": {"line": line, "character": col},
                    },
                )
                highlights = _extract_document_highlights(result)
                self._result_queue.appendleft(lambda es, ws, h=highlights: cb(h))
            except Exception as e:
                log.debug("documentHighlight error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb([]))

        self._schedule(_do())

    def inlay_hints(self, path: str, start_line: int, end_line: int, cb: Callable[[list[dict]], None]) -> None:
        if not self.supports_capability("inlayHintProvider"):
            self._result_queue.appendleft(lambda es, ws: cb([]))
            return

        async def _do():
            try:
                result = await self._client.request(
                    "textDocument/inlayHint",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                        "range": {
                            "start": {"line": start_line, "character": 0},
                            "end": {"line": end_line, "character": 0x7FFFFFFF},
                        },
                    },
                )
                hints = _extract_inlay_hints(result)
                self._result_queue.appendleft(lambda es, ws, h=hints: cb(h))
            except Exception as e:
                log.debug("inlayHint error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb([]))

        self._schedule(_do())

    def workspace_symbols(self, query: str, cb: Callable[[list[dict]], None]) -> None:
        async def _do():
            try:
                result = await self._client.request(
                    "workspace/symbol",
                    {
                        "query": query,
                    },
                )
                symbols = _extract_document_symbols(result, "")
                self._result_queue.appendleft(lambda es, ws, s=symbols: cb(s))
            except Exception as e:
                log.debug("workspace/symbol error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb([]))

        self._schedule(_do())

    def type_definition(self, path: str, line: int, col: int, cb: Callable[[list[dict]], None]) -> None:
        async def _do():
            try:
                result = await self._client.request(
                    "textDocument/typeDefinition",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                        "position": {"line": line, "character": col},
                    },
                )
                locs = _normalise_locations(result)
                self._result_queue.appendleft(lambda es, ws, r=locs: cb(r))
            except Exception as e:
                log.debug("typeDefinition error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb([]))

        self._schedule(_do())

    def completion(self, path: str, line: int, col: int, cb: Callable[[list[dict]], None]) -> None:
        async def _do():
            try:
                result = await self._client.request(
                    "textDocument/completion",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                        "position": {"line": line, "character": col},
                        "context": {"triggerKind": 1},
                    },
                )
                items = _extract_completion_items(result)
                self._result_queue.appendleft(lambda es, ws, i=items: cb(i))
            except Exception as e:
                log.debug("completion error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb([]))

        self._schedule(_do())

    def code_action(self, path: str, line: int, col: int, cb: Callable[[list[dict]], None]) -> None:
        async def _do():
            try:
                diagnostics = self._diagnostics_for_line(path, line)
                result = await self._client.request(
                    "textDocument/codeAction",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                        "range": {
                            "start": {"line": line, "character": col},
                            "end": {"line": line, "character": col},
                        },
                        "context": {"diagnostics": diagnostics},
                    },
                )
                actions = _extract_code_actions(result)
                self._result_queue.appendleft(lambda es, ws, a=actions: cb(a))
            except Exception as e:
                log.debug("code action error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb([]))

        self._schedule(_do())

    def references(self, path: str, line: int, col: int, cb: Callable[[list[dict]], None]) -> None:
        async def _do():
            try:
                result = await self._client.request(
                    "textDocument/references",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                        "position": {"line": line, "character": col},
                        "context": {"includeDeclaration": True},
                    },
                )
                locs = _normalise_locations(result or [])
                self._result_queue.appendleft(lambda es, ws, r=locs: cb(r))
            except Exception as e:
                log.debug("references error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb([]))

        self._schedule(_do())

    def rename(self, path: str, line: int, col: int, new_name: str, cb: Callable[[dict | None], None]) -> None:
        async def _do():
            try:
                result = await self._client.request(
                    "textDocument/rename",
                    {
                        "textDocument": {"uri": path_to_uri(path)},
                        "position": {"line": line, "character": col},
                        "newName": new_name,
                    },
                )
                self._result_queue.appendleft(lambda es, ws, r=result: cb(r))
            except Exception as e:
                log.debug("rename error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb(None))

        self._schedule(_do())

    def execute_command(self, command: str, arguments: list[Any] | None, cb: Callable[[dict | None], None]) -> None:
        async def _do():
            try:
                result = await self._client.request(
                    "workspace/executeCommand",
                    {
                        "command": command,
                        "arguments": arguments or [],
                    },
                )
                self._result_queue.appendleft(lambda es, ws, r=result: cb(r if isinstance(r, dict) else None))
            except Exception as e:
                log.debug("executeCommand error: %s", e)
                self._result_queue.appendleft(lambda es, ws: cb(None))

        self._schedule(_do())

    # ------------------------------------------------------------------
    # Notification handler (called on asyncio thread from LspClient)
    # ------------------------------------------------------------------

    def on_notification(self, method: str, callback: Callable[[dict], None]) -> None:
        """Register a callback for a custom server→client notification method."""
        self._custom_notification_handlers.setdefault(method, []).append(callback)

    def on_progress(self, callback: Callable[[str, str, dict], None]) -> None:
        """Register a callback(token, kind, value) for $/progress notifications.

        kind is one of 'begin', 'report', 'end'.
        """
        self._progress_callbacks.append(callback)

    def custom_request(self, method: str, params: dict, cb: Callable[[Any], None]) -> None:
        """Send an arbitrary request to the server and deliver the result via cb."""

        async def _do() -> None:
            try:
                result = await self._client.request(method, params)
                self._result_queue.appendleft(lambda es, ws, r=result: cb(r))
            except Exception as e:
                log.debug("custom_request %s error: %s", method, e)
                self._result_queue.appendleft(lambda es, ws: cb(None))

        self._schedule(_do())

    def handle_notification(self, method: str, params: dict) -> None:
        if method == "textDocument/publishDiagnostics":
            self._on_diagnostics(params)
        elif method == "$/progress":
            self._on_progress(params)
        else:
            handlers = self._custom_notification_handlers.get(method)
            log.warning("LSP notification %r: %d handlers registered", method, len(handlers) if handlers else 0)
            if handlers:
                for cb in handlers:
                    try:
                        self._result_queue.appendleft(lambda es, ws, c=cb, p=params: c(p))
                    except Exception:
                        log.debug("custom notification handler error for %s", method)

    def _on_progress(self, params: dict) -> None:
        token = str(params.get("token", ""))
        value = params.get("value", {})
        kind = value.get("kind", "")
        for cb in self._progress_callbacks:
            try:
                self._result_queue.appendleft(lambda es, ws, c=cb, t=token, k=kind, v=value: c(t, k, v))
            except Exception:
                log.debug("progress callback error")

    def _on_diagnostics(self, params: dict) -> None:
        uri = params.get("uri", "")
        path = uri_to_path(uri)
        diags = params.get("diagnostics", [])
        self._last_diagnostics[path] = diags
        self._result_queue.appendleft(
            lambda es, ws, p=path, d=diags, sns=self._sign_ns, tns=self._text_ns: _apply_diagnostics(
                es, ws, p, d, sns, tns
            )
        )

    def on_diagnostics(self, cb: Callable[[str, list[dict]], None]) -> None:
        """Register a callback invoked with (path, diagnostics) on main thread."""
        self._diag_callbacks.append(cb)

    def _diagnostics_for_line(self, path: str, line: int) -> list[dict]:
        diagnostics = self._last_diagnostics.get(path, [])
        return [
            diag
            for diag in diagnostics
            if diag.get("range", {}).get("start", {}).get("line", -1)
            <= line
            <= diag.get("range", {}).get("end", {}).get("line", line)
        ]

    def remap_diagnostics(
        self,
        path: str,
        *,
        start_line: int,
        start_col: int,
        end_line: int,
        end_col: int,
        new_text: str,
    ) -> list[dict] | None:
        diagnostics = self._last_diagnostics.get(path)
        if diagnostics is None:
            return None
        remapped = [
            _remap_diagnostic(
                diag,
                start_line=start_line,
                start_col=start_col,
                end_line=end_line,
                end_col=end_col,
                new_text=new_text,
            )
            for diag in diagnostics
        ]
        self._last_diagnostics[path] = remapped
        return remapped


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _extract_hover_text(result: Any) -> str | None:
    if not result:
        return None
    contents = result.get("contents")
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return contents.get("value", "")
    if isinstance(contents, list):
        parts = []
        for item in contents:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("value", ""))
        return "\n".join(p for p in parts if p)
    return None


def _remap_diagnostic(
    diagnostic: dict,
    *,
    start_line: int,
    start_col: int,
    end_line: int,
    end_col: int,
    new_text: str,
) -> dict:
    remapped = deepcopy(diagnostic)
    range_info = remapped.setdefault("range", {})
    start_info = range_info.setdefault("start", {})
    end_info = range_info.setdefault("end", {})
    new_start, new_end = transform_range(
        (
            int(start_info.get("line", 0)),
            int(start_info.get("character", 0)),
        ),
        (
            int(end_info.get("line", start_info.get("line", 0))),
            int(end_info.get("character", start_info.get("character", 0))),
        ),
        edit_start_line=start_line,
        edit_start_col=start_col,
        edit_end_line=end_line,
        edit_end_col=end_col,
        new_text=new_text,
    )
    start_info["line"], start_info["character"] = new_start
    end_info["line"], end_info["character"] = new_end
    return remapped


def _normalise_locations(result: Any) -> list[dict]:
    """Normalise definition/references result to list of {path, line, col}."""
    if not result:
        return []
    if isinstance(result, dict):
        result = [result]
    out = []
    for loc in result:
        uri = loc.get("uri") or loc.get("targetUri", "")
        rng = loc.get("range") or loc.get("targetSelectionRange") or loc.get("targetRange", {})
        start = rng.get("start", {})
        out.append(
            {
                "path": uri_to_path(uri),
                "line": start.get("line", 0),
                "col": start.get("character", 0),
            }
        )
    return out


def _extract_completion_items(result: Any) -> list[dict]:
    if not result:
        return []
    if isinstance(result, dict):
        items = result.get("items", [])
    elif isinstance(result, list):
        items = result
    else:
        return []
    out = []
    for item in items[:100]:  # cap at 100
        out.append(
            {
                "label": item.get("label", ""),
                "kind": item.get("kind", 1),
                "detail": item.get("detail", ""),
                "insertText": item.get("insertText") or item.get("label", ""),
                "filterText": item.get("filterText") or item.get("label", ""),
            }
        )
    return out


def _extract_code_actions(result: Any) -> list[dict]:
    if not result:
        return []
    if isinstance(result, dict):
        result = [result]
    actions: list[dict] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        title = item.get("title", "")
        if not title:
            continue
        command = item.get("command")
        command_name = ""
        command_args: list[Any] = []
        if isinstance(command, dict):
            command_name = str(command.get("command", ""))
            raw_args = command.get("arguments", [])
            command_args = raw_args if isinstance(raw_args, list) else [raw_args]
        elif "command" in item and isinstance(item.get("command"), str):
            command_name = str(item.get("command", ""))
            raw_args = item.get("arguments", [])
            command_args = raw_args if isinstance(raw_args, list) else [raw_args]
        actions.append(
            {
                "title": title,
                "kind": item.get("kind", ""),
                "edit": item.get("edit"),
                "command": command_name,
                "arguments": command_args,
            }
        )
    return actions


def _extract_inlay_hints(result: Any) -> list[dict]:
    if not result:
        return []
    if isinstance(result, dict):
        result = [result]
    hints: list[dict] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        position = item.get("position", {})
        label = item.get("label", "")
        if isinstance(label, list):
            label = "".join(part.get("value", "") if isinstance(part, dict) else str(part) for part in label)
        text = str(label)
        if not text:
            continue
        if item.get("paddingLeft") and not text.startswith(" "):
            text = " " + text
        if item.get("paddingRight") and not text.endswith(" "):
            text = text + " "
        hints.append(
            {
                "line": int(position.get("line", 0)),
                "col": int(position.get("character", 0)),
                "text": text,
            }
        )
    hints.sort(key=lambda hint: (hint["line"], hint["col"], hint["text"]))
    return hints


def _extract_document_highlights(result: Any) -> list[dict]:
    if not result:
        return []
    if isinstance(result, dict):
        result = [result]
    highlights: list[dict] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        range_info = item.get("range", {})
        start = range_info.get("start", {})
        end = range_info.get("end", {})
        highlights.append(
            {
                "start_line": int(start.get("line", 0)),
                "start_col": int(start.get("character", 0)),
                "end_line": int(end.get("line", start.get("line", 0))),
                "end_col": int(end.get("character", start.get("character", 0))),
                "kind": int(item.get("kind", 0) or 0),
            }
        )
    highlights.sort(
        key=lambda highlight: (
            highlight["start_line"],
            highlight["start_col"],
            highlight["end_line"],
            highlight["end_col"],
        )
    )
    return highlights


def _extract_document_symbols(result: Any, path: str) -> list[dict]:
    if not result:
        return []
    if isinstance(result, dict):
        result = [result]
    symbols: list[dict] = []
    for item in result:
        symbols.extend(_flatten_document_symbol(item, path, parent_name=""))
    return symbols


def _extract_document_symbol_tree(result: Any, path: str) -> list[dict]:
    if not result:
        return []
    if isinstance(result, dict):
        result = [result]
    symbols: list[dict] = []
    for item in result:
        symbol = _build_document_symbol_tree_node(item, path, parent_name="")
        if symbol is not None:
            symbols.append(symbol)
    return symbols


def _build_document_symbol_tree_node(item: Any, path: str, parent_name: str) -> dict | None:
    if not isinstance(item, dict):
        return None

    if "location" in item:
        location = item.get("location", {})
        uri = location.get("uri", "")
        range_info = location.get("range", {})
        start = range_info.get("start", {})
        end = range_info.get("end", start)
        return {
            "name": str(item.get("name", "")),
            "kind": _symbol_kind_label(item.get("kind", 0)),
            "detail": str(item.get("containerName", "")),
            "path": uri_to_path(uri) or path,
            "line": start.get("line", 0),
            "col": start.get("character", 0),
            "end_line": end.get("line", start.get("line", 0)),
            "end_col": end.get("character", start.get("character", 0)),
            "children": [],
        }

    symbol_path = path
    selection_range = item.get("selectionRange") or item.get("range", {})
    full_range = item.get("range") or selection_range
    start = selection_range.get("start", {}) if isinstance(selection_range, dict) else {}
    end = full_range.get("end", {}) if isinstance(full_range, dict) else {}
    name = str(item.get("name", ""))
    children = []
    raw_children = item.get("children", [])
    if isinstance(raw_children, list):
        for child in raw_children:
            child_symbol = _build_document_symbol_tree_node(child, path, parent_name=name)
            if child_symbol is not None:
                children.append(child_symbol)
    return {
        "name": name,
        "kind": _symbol_kind_label(item.get("kind", 0)),
        "detail": str(item.get("detail", "") or parent_name),
        "path": symbol_path,
        "line": start.get("line", 0),
        "col": start.get("character", 0),
        "end_line": end.get("line", start.get("line", 0)),
        "end_col": end.get("character", start.get("character", 0)),
        "children": children,
    }


def _flatten_document_symbol(item: Any, path: str, parent_name: str) -> list[dict]:
    if not isinstance(item, dict):
        return []

    if "location" in item:
        location = item.get("location", {})
        uri = location.get("uri", "")
        range_info = location.get("range", {})
        start = range_info.get("start", {})
        return [
            {
                "name": str(item.get("name", "")),
                "kind": _symbol_kind_label(item.get("kind", 0)),
                "detail": str(item.get("containerName", "")),
                "path": uri_to_path(uri) or path,
                "line": start.get("line", 0),
                "col": start.get("character", 0),
            }
        ]

    symbol_path = path
    selection_range = item.get("selectionRange") or item.get("range", {})
    start = selection_range.get("start", {}) if isinstance(selection_range, dict) else {}
    name = str(item.get("name", ""))
    detail = str(item.get("detail", "") or parent_name)
    current = {
        "name": name,
        "kind": _symbol_kind_label(item.get("kind", 0)),
        "detail": detail,
        "path": symbol_path,
        "line": start.get("line", 0),
        "col": start.get("character", 0),
    }
    children = item.get("children", [])
    flattened = [current]
    if isinstance(children, list):
        for child in children:
            flattened.extend(_flatten_document_symbol(child, path, parent_name=name))
    return flattened


def _extract_signature_help_text(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    signatures = result.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        return None
    active_signature = result.get("activeSignature", 0)
    if not isinstance(active_signature, int) or active_signature >= len(signatures):
        active_signature = 0
    signature = signatures[active_signature]
    if not isinstance(signature, dict):
        return None
    label = str(signature.get("label", "")).strip()
    if not label:
        return None
    active_parameter = signature.get("activeParameter", result.get("activeParameter"))
    parameter_label = _extract_signature_parameter_label(signature, active_parameter)
    documentation = signature.get("documentation")
    doc_text = _extract_hover_text({"contents": documentation}) if documentation else None
    lines = [label]
    if parameter_label:
        lines.append(f"parameter: {parameter_label}")
    if doc_text:
        lines.extend(line for line in doc_text.splitlines() if line)
    return "\n".join(lines)


def _extract_signature_parameter_label(signature: dict, active_parameter: Any) -> str | None:
    parameters = signature.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        return None
    if not isinstance(active_parameter, int) or not (0 <= active_parameter < len(parameters)):
        active_parameter = 0
    parameter = parameters[active_parameter]
    if not isinstance(parameter, dict):
        return None
    label = parameter.get("label")
    if isinstance(label, str):
        return label
    if isinstance(label, list | tuple) and len(label) == 2:
        start, end = label
        if isinstance(start, int) and isinstance(end, int):
            sig_label = str(signature.get("label", ""))
            return sig_label[start:end]
    return None


_SYMBOL_KIND_LABELS = {
    1: "file",
    2: "module",
    3: "ns",
    4: "pkg",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "ctor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "const",
    15: "string",
    16: "number",
    17: "bool",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enum member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type",
}


def _symbol_kind_label(kind: Any) -> str:
    return _SYMBOL_KIND_LABELS.get(kind, "symbol")


_COMPLETION_KIND_LABELS = {
    1: "txt",
    2: "meth",
    3: "fn",
    4: "ctor",
    5: "fld",
    6: "var",
    7: "cls",
    8: "iface",
    9: "mod",
    10: "prop",
    11: "unit",
    12: "val",
    13: "enum",
    14: "kw",
    15: "snip",
    16: "col",
    17: "ref",
    18: "file",
    19: "fold",
    20: "const",
    21: "struct",
    22: "event",
    23: "op",
    24: "type",
}


def completion_kind_label(kind: int) -> str:
    return _COMPLETION_KIND_LABELS.get(kind, "   ")


_DIAG_SIGN_NS = "lsp:diag:signs"
_DIAG_TEXT_NS = "lsp:diag:text"


def _diagnostics_visible_in_current_mode(editor_state: Any) -> bool:
    api = getattr(editor_state, "_api", None)
    engine = getattr(api, "_engine", None)
    mode = getattr(engine, "mode", None)
    mode_name = getattr(mode, "value", mode)
    update_in_insert = bool(editor_state.options.get("lsp_update_in_insert"))
    if update_in_insert:
        return True
    return mode_name not in {"insert", "replace"}


def _apply_diagnostics(
    editor_state: Any,
    workspace: Any,
    path: str,
    diags: list[dict],
    sign_ns: str = _DIAG_SIGN_NS,
    text_ns: str = _DIAG_TEXT_NS,
) -> None:
    """Update decorations with new diagnostics. Called on main thread."""
    from peovim.core.style import Style
    from peovim.ui.decorations import Sign, VirtualText

    show_virtual_text = _diagnostics_visible_in_current_mode(editor_state)

    # Find all documents matching this path
    docs = []
    for tab in workspace.tabs:
        for win in tab.all_windows():
            if win.document.path and str(win.document.path) == path:
                docs.append(win.document)
    if not docs:
        return
    for doc in docs:
        buf_id = id(doc)
        editor_state.decorations.clear_namespace(buf_id, sign_ns)
        editor_state.decorations.clear_namespace(buf_id, text_ns)
        for d in diags:
            sev = d.get("severity", 1)
            ln = d["range"]["start"]["line"]
            msg = d.get("message", "").replace("\n", " ")
            if sev == 1:
                sign_char = "E"
                sign_style = Style(fg=(255, 85, 85))
                text_style = Style(fg=(255, 85, 85))
            elif sev == 2:
                sign_char = "W"
                sign_style = Style(fg=(255, 170, 0))
                text_style = Style(fg=(255, 170, 0))
            else:
                sign_char = "I"
                sign_style = Style(fg=(136, 170, 255))
                text_style = Style(fg=(136, 136, 136))
            editor_state.decorations.add(buf_id, sign_ns, Sign(line=ln, char=sign_char, style=sign_style, priority=10))
            if show_virtual_text:
                editor_state.decorations.add(
                    buf_id, text_ns, VirtualText(line=ln, text=" " + msg[:80], style=text_style)
                )
    editor_state.event_bus.emit("diagnostics_updated", path=path, diagnostics=diags, count=len(diags))
