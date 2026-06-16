"""
LspAPI — LSP server registration and interaction for plugins.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.core.editor_state import EditorState
    from peovim.core.workspace import Workspace
    from peovim.lsp.manager import LspManager

log = logging.getLogger(__name__)

_INLAY_HINT_NS = "lsp:hints"
_DOCUMENT_HIGHLIGHT_NS = "lsp:ref"


class LspAPI:
    """
    Plugin-facing LSP API.

    Plugins use api.lsp to register servers and trigger LSP actions.
    All feature requests are asynchronous; results appear via the result_queue
    that the EventLoop drains each frame.
    """

    def __init__(
        self,
        manager: LspManager,
        workspace: Workspace,
        editor_state: EditorState,
        event_loop: Any = None,
    ) -> None:
        self._manager = manager
        self._workspace = workspace
        self._editor_state = editor_state
        self._event_loop = event_loop  # kept for external compatibility; use _lsp_ui for UI calls
        self._lsp_ui: Any = None  # LspUiAdapter — set by main.py after EventLoop construction
        self._latest_inlay_request_token: object | None = None

    def attach_event_loop(self, event_loop: Any) -> None:
        """Wire the EventLoop and LspUiAdapter after EventLoop is constructed."""
        self._event_loop = event_loop
        self._lsp_ui = event_loop.lsp_ui

    # ------------------------------------------------------------------
    # Server registration
    # ------------------------------------------------------------------

    def register_server(
        self,
        filetype: str,
        cmd: list[str],
        root_markers: list[str] | None = None,
    ) -> None:
        """Register a language server for a filetype.

        cmd: command + args, e.g. ["ty", "server"] or ["basedpyright", "--stdio"]
        root_markers: files/dirs that mark the workspace root (default: .git, pyproject.toml, …)
        """
        from peovim.lsp.manager import ServerConfig

        cfg = ServerConfig(
            filetype=filetype,
            cmd=cmd,
            root_markers=root_markers or [".git", "pyproject.toml", "setup.py", "Cargo.toml"],
        )
        self._manager.register(cfg)

    def registered_servers(self) -> list[dict[str, Any]]:
        """Return registered LSP server configs as plain dicts."""
        return [
            {
                "filetype": cfg.filetype,
                "cmd": list(cfg.cmd),
                "root_markers": list(cfg.root_markers),
            }
            for cfg in self._manager._configs
        ]

    def running_servers(self) -> list[dict[str, Any]]:
        """Return snapshots of active server processes."""
        return list(self._manager.list_servers())

    def current_buffer_status(self) -> dict[str, Any]:
        """Return current-buffer attachment status for health/reporting."""
        win = self._workspace.active_window
        doc = win.document
        path = doc.path
        filetype = getattr(doc, "filetype", "") or ""
        if not filetype:
            from peovim.lsp.manager import _detect_filetype

            filetype = _detect_filetype(str(path)) if path else ""
        feats = self._manager.get(doc)
        cfg = self._manager.get_config(filetype) if filetype else None
        initialized = bool(getattr(feats, "_initialized", False)) if feats is not None else False
        return {
            "path": path,
            "filetype": filetype,
            "attached": feats is not None,
            "initialized": initialized,
            "server_registered": cfg is not None,
        }

    def resolve_buffer_document(self, buf_id: int = 0) -> Any:
        """Return the matching document, or the active document when `buf_id` is omitted."""
        return self._document_for_buf_id(buf_id)

    def attach_buffer(self, buf_id: int = 0) -> Any:
        """Attach the matching document, or the active document when `buf_id` is omitted."""
        doc = self._document_for_buf_id(buf_id)
        if doc is None:
            return None
        self._manager.attach(doc)
        return doc

    def notify_buffer_changed(self, buf_id: int = 0) -> Any:
        """Forward a buffer change notification to attached LSP servers."""
        doc = self._document_for_buf_id(buf_id)
        if doc is None:
            return None
        self._manager.notify_change(doc)
        return doc

    def notify_buffer_saved(self, buf_id: int = 0) -> Any:
        """Forward a buffer save notification to attached LSP servers."""
        doc = self._document_for_buf_id(buf_id)
        if doc is None:
            return None
        self._manager.notify_save(doc)
        return doc

    def attach_open_buffers(self) -> None:
        """Attach all currently open file-backed documents."""
        seen: set[int] = set()
        for tab in self._workspace.tabs:
            for win in tab.all_windows():
                doc = win.document
                doc_id = id(doc)
                if doc.path is None or doc_id in seen:
                    continue
                seen.add(doc_id)
                self._manager.attach(doc)

    # ------------------------------------------------------------------
    # Feature actions (triggered at current cursor position)
    # ------------------------------------------------------------------

    def flush_pending_changes(self) -> None:
        """Immediately send any buffered didChange notifications."""
        self._manager.flush_pending_changes()

    def hover(self) -> None:
        """Show hover information for the symbol under the cursor."""
        self.flush_pending_changes()
        feats, path, line, col = self._current_context()
        if feats is None:
            return

        def _cb(text: str | None) -> None:
            if not text:
                self._editor_state.message = "No hover info"
                return
            if self._lsp_ui is not None:
                self._lsp_ui.show_hover_float(text)
            else:
                self._editor_state.message = text[:120]

        feats.hover(path, line, col, _cb)

    def definition(self) -> None:
        """Jump to the definition of the symbol under the cursor."""
        self.flush_pending_changes()
        feats, path, line, col = self._current_context()
        if feats is None:
            return

        self._jump_from_locations_request(
            lambda cb: feats.definition(path, line, col, cb),
            empty_message="No definition found",
            summary_label="definitions",
        )

    def implementation(self) -> None:
        """Jump to implementations of the symbol under the cursor."""
        feats, path, line, col = self._current_context()
        if feats is None:
            return

        self._jump_from_locations_request(
            lambda cb: feats.implementation(path, line, col, cb),
            empty_message="No implementation found",
            summary_label="implementations",
        )

    def type_definition(self) -> None:
        """Jump to the type definition of the symbol under the cursor."""
        feats, path, line, col = self._current_context()
        if feats is None:
            return

        self._jump_from_locations_request(
            lambda cb: feats.type_definition(path, line, col, cb),
            empty_message="No type definition found",
            summary_label="type definitions",
        )

    def document_symbols(self) -> None:
        """Show document symbols for the active buffer in the picker."""
        feats, path, _line, _col = self._current_context()
        if feats is None:
            return

        def _cb(symbols: list[dict]) -> None:
            if not symbols:
                self._editor_state.message = "No document symbols found"
                return
            items = []
            locs = []
            for symbol in symbols:
                detail = f" — {symbol['detail']}" if symbol.get("detail") else ""
                items.append(f"{symbol['kind']:<12} {symbol['name']}:{symbol['line'] + 1}:{symbol['col'] + 1}{detail}")
                locs.append({"path": symbol["path"], "line": symbol["line"], "col": symbol["col"]})
            if self._lsp_ui is not None:
                self._lsp_ui.show_picker_for_locations(items, locs, title="Document Symbols")
            else:
                self._editor_state.message = f"{len(symbols)} document symbols"

        feats.document_symbols(path, _cb)

    def document_symbol_tree(self, cb) -> None:
        """Request document symbols for the active buffer as a tree for plugins."""
        feats, path, _line, _col = self._current_context()
        if feats is None or not path:
            cb([])
            return

        supports_capability = getattr(feats, "supports_capability", None)
        if callable(supports_capability) and not supports_capability("documentSymbolProvider"):
            cb([])
            return

        request_tree = getattr(feats, "document_symbols_tree", None)
        if callable(request_tree):
            request_tree(path, cb)
            return

        request_flat = getattr(feats, "document_symbols", None)
        if callable(request_flat):
            request_flat(path, lambda symbols: cb([{**symbol, "children": []} for symbol in symbols]))
            return

        cb([])

    def workspace_symbols(self) -> None:
        """Show workspace symbols for the word under cursor in the picker."""
        feats, _path, _line, _col = self._current_context()
        if feats is None:
            return
        query = self._word_under_cursor()
        if not query:
            self._editor_state.message = "No symbol under cursor"
            return

        def _cb(symbols: list[dict]) -> None:
            if not symbols:
                self._editor_state.message = f"No workspace symbols found for '{query}'"
                return
            items = []
            locs = []
            for symbol in symbols:
                detail = f" — {symbol['detail']}" if symbol.get("detail") else ""
                items.append(f"{symbol['kind']:<12} {symbol['name']}:{symbol['line'] + 1}:{symbol['col'] + 1}{detail}")
                locs.append({"path": symbol["path"], "line": symbol["line"], "col": symbol["col"]})
            if self._lsp_ui is not None:
                self._lsp_ui.show_picker_for_locations(items, locs, title=f"Workspace Symbols: {query}")
            else:
                self._editor_state.message = f"{len(symbols)} workspace symbols"

        feats.workspace_symbols(query, _cb)

    def workspace_symbol_search(self, query: str, cb) -> None:
        """Request workspace symbols for an explicit query for plugin use."""
        feats, _path, _line, _col = self._current_context()
        if feats is None or not query:
            cb([])
            return

        request = getattr(feats, "workspace_symbols", None)
        if callable(request):
            request(query, cb)
            return

        cb([])

    def references(self) -> None:
        """Show all references to the symbol under the cursor in the picker."""
        self.flush_pending_changes()
        feats, path, line, col = self._current_context()
        if feats is None:
            return

        def _cb(locs: list[dict]) -> None:
            if not locs:
                self._editor_state.message = "No references found"
                return
            items = [f"{loc['path']}:{loc['line'] + 1}:{loc['col'] + 1}" for loc in locs]
            if self._lsp_ui is not None:
                self._lsp_ui.show_picker_for_locations(items, locs, title="References")
            else:
                self._editor_state.message = f"{len(locs)} references"

        feats.references(path, line, col, _cb)

    def references_search(self, cb) -> None:
        """Request raw references for the symbol under the cursor for plugin use."""
        self.flush_pending_changes()
        feats, path, line, col = self._current_context()
        if feats is None or not path:
            cb([])
            return

        request = getattr(feats, "references", None)
        if callable(request):
            request(path, line, col, cb)
            return

        cb([])

    def code_actions(self) -> None:
        """Show available code actions for the current cursor position."""
        self.flush_pending_changes()
        feats, path, line, col = self._current_context()
        if feats is None:
            return

        def _cb(actions: list[dict]) -> None:
            if not actions:
                self._editor_state.message = "No code actions available"
                return
            if self._lsp_ui is not None:
                self._lsp_ui.show_picker_for_code_actions(feats, actions)
            else:
                self._editor_state.message = f"{len(actions)} code actions"

        feats.code_action(path, line, col, _cb)

    def apply_workspace_edit(self, edit: dict) -> None:
        """Apply a WorkspaceEdit returned by a custom LSP command."""
        if self._lsp_ui is not None:
            self._lsp_ui.apply_workspace_edit(edit)

    def signature_help(self) -> None:
        """Show signature help for the current insert-mode cursor position."""
        feats, path, line, col = self._current_context()
        if feats is None:
            return

        def _cb(text: str | None) -> None:
            if self._lsp_ui is not None:
                self._lsp_ui.show_signature_help(text)
            elif text:
                self._editor_state.message = text.splitlines()[0][:120]

        feats.signature_help(path, line, col, _cb)

    def dismiss_signature_help(self) -> None:
        """Dismiss any visible signature help UI."""
        if self._lsp_ui is not None:
            self._lsp_ui.dismiss_signature_help()

    def toggle_inlay_hints(self) -> None:
        """Toggle visible-range LSP inlay hints for the active buffer."""
        enabled = bool(self._editor_state.options.get("lsp_inlay_hints"))
        if enabled:
            self._editor_state.options.set_global("lsp_inlay_hints", False)
            self.clear_inlay_hints()
            self._editor_state.message = "Inlay hints off"
            return

        feats, _path, _line, _col = self._current_context()
        if feats is None:
            return
        supports_capability = getattr(feats, "supports_capability", None)
        if callable(supports_capability) and not supports_capability("inlayHintProvider"):
            self._editor_state.message = "Inlay hints unavailable"
            return

        self._editor_state.options.set_global("lsp_inlay_hints", True)
        self._editor_state.message = "Inlay hints on"
        self.refresh_inlay_hints()

    def refresh_inlay_hints(self) -> None:
        """Refresh LSP inlay hints for the active window's visible range."""
        if not bool(self._editor_state.options.get("lsp_inlay_hints")):
            self.clear_inlay_hints()
            return

        feats, path, _line, _col = self._current_context()
        if feats is None:
            self.clear_inlay_hints()
            return

        supports_capability = getattr(feats, "supports_capability", None)
        if callable(supports_capability) and not supports_capability("inlayHintProvider"):
            self.clear_inlay_hints()
            return

        win = self._workspace.active_window
        start_line = win.scroll_line
        end_line = min(start_line + win.height - 1, win.document.line_count() - 1)
        request_token = object()
        self._latest_inlay_request_token = request_token

        def _cb(hints: list[dict]) -> None:
            if request_token is not self._latest_inlay_request_token:
                return
            if not bool(self._editor_state.options.get("lsp_inlay_hints")):
                return
            _apply_inlay_hints(self._editor_state, self._workspace, path, hints)

        feats.inlay_hints(path, start_line, end_line, _cb)

    def clear_inlay_hints(self) -> None:
        """Clear any visible LSP inlay hints for the active buffer."""
        self._latest_inlay_request_token = None
        win = self._workspace.active_window
        self._editor_state.decorations.clear_namespace(id(win.document), _INLAY_HINT_NS)

    def refresh_document_highlight(self) -> None:
        """Refresh LSP document highlights for the current cursor position."""
        feats, path, line, col = self._current_context()
        if feats is None:
            self.clear_document_highlight()
            return

        supports_capability = getattr(feats, "supports_capability", None)
        if callable(supports_capability) and not supports_capability("documentHighlightProvider"):
            self.clear_document_highlight()
            return

        feats.document_highlight(
            path,
            line,
            col,
            lambda highlights: _apply_document_highlights(
                self._editor_state,
                self._workspace,
                path,
                highlights,
            ),
        )

    def clear_document_highlight(self) -> None:
        """Clear any visible LSP document highlights for the active buffer."""
        win = self._workspace.active_window
        self._editor_state.decorations.clear_namespace(id(win.document), _DOCUMENT_HIGHLIGHT_NS)

    def remap_buffer_diagnostics(
        self,
        *,
        buf_id: int = 0,
        start_line: int,
        start_col: int,
        end_line: int,
        end_col: int,
        new_text: str,
    ) -> None:
        """Optimistically remap visible diagnostics after an in-editor text change."""
        from peovim.lsp.features import _apply_diagnostics

        doc = self._document_for_buf_id(buf_id)
        if doc is None or doc.path is None:
            return
        path = str(doc.path)
        get_all = getattr(self._manager, "get_all", None)
        features = get_all(doc) if callable(get_all) else [self._manager.get(doc)]
        for feats in features:
            if feats is None:
                continue
            remap = getattr(feats, "remap_diagnostics", None)
            if not callable(remap):
                continue
            diagnostics = remap(
                path,
                start_line=start_line,
                start_col=start_col,
                end_line=end_line,
                end_col=end_col,
                new_text=new_text,
            )
            if diagnostics is not None:
                sign_ns = getattr(feats, "_sign_ns", "lsp:diag:signs")
                text_ns = getattr(feats, "_text_ns", "lsp:diag:text")
                _apply_diagnostics(self._editor_state, self._workspace, path, diagnostics, sign_ns, text_ns)

    def rename(self) -> None:
        """Prompt for a new name and apply a workspace rename."""
        self.flush_pending_changes()
        feats, path, line, col = self._current_context()
        if feats is None:
            return
        if self._lsp_ui is not None:
            self._lsp_ui.prompt_rename(feats, path, line, col)
        else:
            log.debug("rename: no event_loop integration")

    def trigger_completion(self) -> None:
        """Trigger completion at the cursor (called from insert mode Ctrl-n)."""
        self.flush_pending_changes()
        feats, path, line, col = self._current_context()
        if feats is None:
            return

        def _cb(items: list[dict]) -> None:
            if not items:
                return
            if self._lsp_ui is not None:
                self._lsp_ui.show_completion(items)

        feats.completion(path, line, col, _cb)

    # ------------------------------------------------------------------
    # Custom server extensions (for plugin-defined LSP methods)
    # ------------------------------------------------------------------

    def on_notification(self, method: str, callback: Callable[[dict], None]) -> None:
        """Register a callback for a custom server→client notification.

        Registers on all currently running servers and on any server that
        attaches in the future for the same filetype.
        """
        for _, feats in self._manager._servers.values():
            feats.on_notification(method, callback)
        self._manager._pending_notification_handlers.setdefault(method, []).append(callback)

    def on_progress(self, callback: Callable[[str, str, dict], None]) -> None:
        """Register a callback(token, kind, value) for $/progress notifications."""
        for _, feats in self._manager._servers.values():
            feats.on_progress(callback)
        self._manager._pending_progress_handlers.append(callback)

    def custom_request(self, method: str, params: dict, cb: Callable[[Any], None]) -> None:
        """Send a custom request to the server for the active buffer."""
        feats, _path, _line, _col = self._current_context()
        if feats is None:
            cb(None)
            return
        feats.custom_request(method, params, cb)

    def custom_request_to(
        self,
        method: str,
        params: dict,
        cb: Callable[[Any], None],
        *,
        cmd_contains: str,
    ) -> None:
        """Send a custom request to the server whose cmd contains cmd_contains.

        Bypasses the normal active-buffer routing (which always picks the first
        registered server) so verilog_lsp-specific commands don't go to verible.
        Prefer servers attached to the active buffer/root before falling back to
        any globally running server with a matching command.
        """
        win = self._workspace.active_window
        doc = win.document if win is not None else None
        attached: list[Any] = []
        if doc is not None:
            get_all = getattr(self._manager, "get_all", None)
            if callable(get_all):
                attached = [feats for feats in get_all(doc) if feats is not None]
            else:
                get_one = getattr(self._manager, "get", None)
                if callable(get_one):
                    feat = get_one(doc)
                    if feat is not None:
                        attached = [feat]
        for feats in attached:
            client = getattr(feats, "_client", None)
            cmd = getattr(client, "_cmd", ())
            if any(cmd_contains in str(part) for part in cmd):
                feats.custom_request(method, params, cb)
                return

        for key, (_client, feats) in getattr(self._manager, "_servers", {}).items():
            if any(cmd_contains in str(part) for part in key[2]):
                feats.custom_request(method, params, cb)
                return
        cb(None)

    # ------------------------------------------------------------------
    # Lifecycle commands
    # ------------------------------------------------------------------

    def info(self) -> None:
        """Show information about active LSP servers in a float."""
        servers = self._manager.list_servers()
        if not servers:
            self._editor_state.message = "No active LSP servers"
            return
        lines = ["Active LSP servers:"]
        for s in servers:
            status = "initialized" if s["initialized"] else "starting"
            lines.append(f"  {s['filetype']} ({status}): {' '.join(s['cmd'])}")
            lines.append(f"    root: {s['root']}")
        msg = "\n".join(lines)
        if self._lsp_ui is not None:
            self._lsp_ui.show_hover_float(msg)
        else:
            self._editor_state.message = lines[0]

    def restart(self, filetype: str = "") -> None:
        """Restart LSP servers (all, or just the given filetype)."""
        self._manager.restart(filetype.strip())
        label = filetype or "all"
        self._editor_state.message = f"LSP restarted: {label}"

    def goto_next_diag(self) -> None:
        """Jump to the next diagnostic in the current buffer (wraps around)."""
        self._goto_diag(1)

    def goto_prev_diag(self) -> None:
        """Jump to the previous diagnostic in the current buffer (wraps around)."""
        self._goto_diag(-1)

    def diag_detail(self) -> None:
        """Show the full diagnostic message at the cursor line in a float."""
        from peovim.ui.decorations import VirtualText

        win = self._workspace.active_window
        buf_id = id(win.document)
        cur_line = win.cursor.line
        decs = self._editor_state.decorations.get_for_namespace(buf_id, "lsp:diag:text")
        msgs = [d.text.strip() for d in decs if isinstance(d, VirtualText) and d.line == cur_line]
        if not msgs:
            self._editor_state.message = "No diagnostic at cursor"
            return
        if self._lsp_ui is not None:
            self._lsp_ui.show_hover_float("\n".join(msgs))
        else:
            self._editor_state.message = msgs[0]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _goto_diag(self, direction: int) -> None:
        from peovim.ui.decorations import Sign

        win = self._workspace.active_window
        buf_id = id(win.document)
        decs = self._editor_state.decorations.get_for_namespace(buf_id, "lsp:diag:signs")
        diag_lines = sorted({d.line for d in decs if isinstance(d, Sign)})
        if not diag_lines:
            self._editor_state.message = "No diagnostics"
            return
        cur_line = win.cursor.line
        if direction < 0:
            candidates = [ln for ln in diag_lines if ln < cur_line]
            target = candidates[-1] if candidates else diag_lines[-1]
        else:
            candidates = [ln for ln in diag_lines if ln > cur_line]
            target = candidates[0] if candidates else diag_lines[0]
        win.cursor.move_to(target, 0)
        win.scroll_to_cursor()

    def _jump_from_locations_request(
        self,
        request: Any,
        *,
        empty_message: str,
        summary_label: str,
    ) -> None:
        def _cb(locs: list[dict]) -> None:
            if not locs:
                self._editor_state.message = empty_message
                return
            if len(locs) == 1:
                loc = locs[0]
                if self._lsp_ui is not None:
                    self._lsp_ui.goto_location(loc)
                else:
                    log.debug("jump: %s", loc)
                return
            items = [f"{loc['path']}:{loc['line'] + 1}:{loc['col'] + 1}" for loc in locs]
            if self._lsp_ui is not None:
                self._lsp_ui.show_picker_for_locations(items, locs, title=summary_label.title())
            else:
                self._editor_state.message = f"{len(locs)} {summary_label}"

        request(_cb)

    def _word_under_cursor(self) -> str:
        win = self._workspace.active_window
        line = win.document.get_line(win.cursor.line)
        if not line:
            return ""
        col = min(win.cursor.col, max(0, len(line) - 1))
        if not (line[col].isalnum() or line[col] == "_"):
            if col > 0 and (line[col - 1].isalnum() or line[col - 1] == "_"):
                col -= 1
            else:
                return ""
        start = col
        end = col + 1
        while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
            start -= 1
        while end < len(line) and (line[end].isalnum() or line[end] == "_"):
            end += 1
        return line[start:end]

    def _current_context(self) -> tuple:
        """Return (LspFeatures | None, path, line, col) for the active buffer."""
        win = self._workspace.active_window
        doc = win.document
        feats = self._manager.get(doc)
        if feats is None:
            return None, "", 0, 0
        path = str(doc.path) if doc.path else ""
        line = win.cursor.line
        col = win.cursor.col
        return feats, path, line, col

    def _document_for_buf_id(self, buf_id: int = 0) -> Any:
        if not buf_id:
            return self._workspace.active_window.document
        for tab in self._workspace.tabs:
            for win in tab.all_windows():
                if id(win.document) == buf_id:
                    return win.document
        return None


def _apply_inlay_hints(editor_state: Any, workspace: Any, path: str, hints: list[dict]) -> None:
    from peovim.core.style import Style
    from peovim.ui.decorations import VirtualText

    docs = []
    for tab in workspace.tabs:
        for win in tab.all_windows():
            if win.document.path and str(win.document.path) == path:
                docs.append(win.document)
    if not docs:
        return

    hints_by_line: dict[int, list[str]] = defaultdict(list)
    for hint in hints:
        text = str(hint.get("text", ""))
        if text:
            hints_by_line[int(hint.get("line", 0))].append(text)

    hint_style = Style(fg=(120, 120, 120))
    for doc in docs:
        buf_id = id(doc)
        editor_state.decorations.clear_namespace(buf_id, _INLAY_HINT_NS)
        for line, chunks in sorted(hints_by_line.items()):
            joined = "".join(chunks)
            if joined and not joined.startswith(" "):
                joined = " " + joined
            editor_state.decorations.add(
                buf_id,
                _INLAY_HINT_NS,
                VirtualText(line=line, text=joined, style=hint_style),
            )


def _apply_document_highlights(editor_state: Any, workspace: Any, path: str, highlights: list[dict]) -> None:
    from peovim.core.style import Style
    from peovim.ui.decorations import HighlightRegion

    docs = []
    for tab in workspace.tabs:
        for win in tab.all_windows():
            if win.document.path and str(win.document.path) == path:
                docs.append(win.document)
    if not docs:
        return

    for doc in docs:
        buf_id = id(doc)
        editor_state.decorations.clear_namespace(buf_id, _DOCUMENT_HIGHLIGHT_NS)
        for highlight in highlights:
            kind = int(highlight.get("kind", 0) or 0)
            if kind == 3:
                style = Style(bg=(90, 70, 60))
            elif kind == 2:
                style = Style(bg=(60, 70, 90))
            else:
                style = Style(bg=(70, 70, 90))
            editor_state.decorations.add(
                buf_id,
                _DOCUMENT_HIGHLIGHT_NS,
                HighlightRegion(
                    int(highlight.get("start_line", 0)),
                    int(highlight.get("start_col", 0)),
                    int(highlight.get("end_line", 0)),
                    int(highlight.get("end_col", 0)),
                    style,
                    priority=15,
                ),
            )
