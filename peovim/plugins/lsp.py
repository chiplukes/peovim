"""
LSP auto-setup: server detection, filetype attach, default key mappings.

Implemented against the public peovim.api — no internal imports.
"""

from __future__ import annotations

import logging
import re
import shutil
import time

log = logging.getLogger(__name__)
_WORD_RE = re.compile(r"\w+")

# Server detection: (filetype, command candidates in preference order)
_PYTHON_CANDIDATES = [
    ["ty", "server"],
    ["basedpyright", "--stdio"],
    ["pyright-langserver", "--stdio"],
    ["pylsp"],
]

_PYTHON_RUFF_CANDIDATES = [
    ["ruff", "server"],
]

_RUST_CANDIDATES = [
    ["rust-analyzer"],
]

_TS_CANDIDATES = [
    ["typescript-language-server", "--stdio"],
]

_LUA_CANDIDATES = [
    ["lua-language-server"],
]

_CLANGD_CANDIDATES = [
    ["clangd"],
]

_ALL_CANDIDATES: list[tuple[str, list[list[str]]]] = [
    ("python", _PYTHON_CANDIDATES),
    ("python (ruff)", _PYTHON_RUFF_CANDIDATES),
    ("rust", _RUST_CANDIDATES),
    ("typescript", _TS_CANDIDATES),
    ("lua", _LUA_CANDIDATES),
    ("c/c++", _CLANGD_CANDIDATES),
]


def _check_lsp(api, lsp) -> list:
    """Health checker for LSP — returns list[HealthItem]."""
    from peovim.core.health import HealthItem

    items: list[HealthItem] = []

    # 1. Server binary detection
    items.append(HealthItem("info", "Server detection (PATH)"))
    any_found = False
    for filetype, candidates in _ALL_CANDIDATES:
        found = _find_server(candidates)
        if found:
            items.append(HealthItem("ok", f"  {filetype}: {' '.join(found)}"))
            any_found = True
        else:
            names = " / ".join(c[0] for c in candidates)
            items.append(HealthItem("warn", f"  {filetype}: not found ({names})"))

    if not any_found:
        items.append(
            HealthItem(
                "error",
                "No LSP servers found on PATH",
                detail="Install a server, e.g.:\n  uv tool install ty\n  pip install basedpyright\n  pip install python-lsp-server",
            )
        )

    # 2. Registered configs
    items.append(HealthItem("info", "Registered server configs"))
    registered = getattr(lsp, "registered_servers", None)
    running = getattr(lsp, "running_servers", None)
    if not callable(registered) or not callable(running):
        items.append(HealthItem("error", "LspManager not available"))
        return items

    configs = registered()
    if not configs:
        items.append(HealthItem("warn", "  No servers registered (none found on PATH)"))
    else:
        for cfg in configs:
            items.append(HealthItem("ok", f"  {cfg['filetype']}: {' '.join(cfg['cmd'])}"))

    # 3. Active (running) servers
    items.append(HealthItem("info", "Active server processes"))
    active = running()
    if not active:
        items.append(
            HealthItem(
                "warn", "  No servers running yet", detail="Servers start when you open a file of a supported filetype"
            )
        )
    else:
        for s in active:
            status = "initialized" if s["initialized"] else "starting…"
            items.append(
                HealthItem("ok", f"  {s['filetype']} ({status}): {' '.join(s['cmd'])}", detail=f"root: {s['root']}")
            )

    # 4. Current buffer status
    items.append(HealthItem("info", "Current buffer"))
    try:
        status = lsp.current_buffer_status() if hasattr(lsp, "current_buffer_status") else {}
        path = status.get("path")
        filetype = status.get("filetype", "")

        if path is None:
            items.append(HealthItem("info", "  [No file] — open a file to attach LSP"))
        else:
            items.append(HealthItem("info", f"  file:     {path}"))
            items.append(HealthItem("info", f"  filetype: {filetype or '(unknown)'}"))

            if not status.get("attached"):
                if not status.get("server_registered"):
                    items.append(HealthItem("warn", "  LSP: not attached (no server registered for this filetype)"))
                else:
                    items.append(
                        HealthItem("warn", "  LSP: not attached yet (server registered but buffer_opened not fired?)")
                    )
            else:
                if status.get("initialized"):
                    items.append(HealthItem("ok", "  LSP: attached and initialized"))
                else:
                    items.append(HealthItem("warn", "  LSP: attached but still initializing…"))
    except Exception as exc:
        items.append(HealthItem("error", f"  Error checking buffer: {exc}"))

    return items


def _find_server(candidates: list[list[str]]) -> list[str] | None:
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return cmd
    return None


def _current_word_prefix(doc, line: int, col: int) -> tuple[int, str]:
    line_text = doc.get_line(line) or ""
    prefix_end = max(0, min(col, len(line_text)))
    prefix_start = prefix_end
    while prefix_start > 0 and _WORD_RE.fullmatch(line_text[prefix_start - 1 : prefix_start]):
        prefix_start -= 1
    return prefix_start, line_text[prefix_start:prefix_end]


def _buffer_word_completion_items(doc, prefix: str) -> list[dict]:
    if not prefix:
        return []
    seen: set[str] = set()
    items: list[dict] = []
    for line_index in range(doc.line_count()):
        line_text = doc.get_line(line_index) or ""
        for match in _WORD_RE.finditer(line_text):
            word = match.group(0)
            if word in seen or len(word) <= len(prefix) or not word.startswith(prefix):
                continue
            seen.add(word)
            items.append(
                {
                    "label": word,
                    "kind": 1,
                    "detail": "",
                    "insertText": word,
                    "filterText": word,
                }
            )
    return items


def setup(api) -> None:  # cm:5b3d7f
    lsp = getattr(api, "lsp", None)
    if lsp is None:
        log.warning("LspAPI not available on api.lsp — skipping lsp plugin setup")
        return

    api.options.define(
        "lsp_update_in_insert",
        bool,
        False,
        doc="Show LSP diagnostics while in insert mode. When false, diagnostics refresh on insert leave.",
    )
    api.options.define(
        "lsp_inlay_hints",
        bool,
        False,
        doc="Render LSP inlay hints for the visible range when supported by the server.",
    )
    api.options.define(
        "lsp_document_highlight",
        bool,
        True,
        doc="Highlight symbol references in the current document after the cursor idles.",
    )
    pending_diag_refresh: set[int] = set()
    inlay_state: dict[str, object | None] = {
        "pending_signature": None,
        "pending_since": None,
        "applied_signature": None,
    }
    highlight_state: dict[str, object | None] = {
        "pending_signature": None,
        "pending_since": None,
        "applied_signature": None,
    }

    def _update_in_insert() -> bool:
        return bool(api.options.get("lsp_update_in_insert"))

    def _is_insert_mode() -> bool:
        mode = getattr(api, "active_mode", None)
        mode_name = getattr(mode, "value", mode)
        return mode_name in {"insert", "replace"}

    def _clear_insert_mode_diag_text(doc) -> None:
        buf = api.buffer_by_id(id(doc))
        if buf is not None:
            buf.clear_namespace("lsp:diag:text")

    def _reset_document_highlight_tracking() -> None:
        highlight_state["pending_signature"] = None
        highlight_state["pending_since"] = None
        highlight_state["applied_signature"] = None

    def _reset_inlay_tracking() -> None:
        inlay_state["pending_signature"] = None
        inlay_state["pending_since"] = None
        inlay_state["applied_signature"] = None

    def _current_inlay_signature() -> tuple[int, int, int, int] | None:
        if not bool(api.options.get("lsp_inlay_hints")):
            return None
        win = api.active_window()
        buf = win.buffer()
        if buf.path is None:
            return None
        start_line, last_visible = win.visible_range()
        return buf.buf_id, buf.version, start_line, last_visible

    def _poll_inlay_hints() -> None:
        refresh_inlay_hints = getattr(lsp, "refresh_inlay_hints", None)
        clear_inlay_hints = getattr(lsp, "clear_inlay_hints", None)
        signature = _current_inlay_signature()
        if signature is None:
            if callable(clear_inlay_hints):
                clear_inlay_hints()
            _reset_inlay_tracking()
            return

        now = time.monotonic()
        if signature != inlay_state["pending_signature"]:
            inlay_state["pending_signature"] = signature
            inlay_state["pending_since"] = now
            return
        if signature == inlay_state["applied_signature"]:
            return
        pending_since = inlay_state["pending_since"]
        if isinstance(pending_since, int | float) and now - pending_since < 0.15:
            return
        if callable(refresh_inlay_hints):
            refresh_inlay_hints()
            inlay_state["applied_signature"] = signature

    def _current_document_highlight_signature() -> tuple[int, int, int, int] | None:
        if not bool(api.options.get("lsp_document_highlight")) or _is_insert_mode():
            return None
        win = api.active_window()
        buf = win.buffer()
        if buf.path is None:
            return None
        line, col = win.cursor
        return buf.buf_id, buf.version, line, col

    def _poll_document_highlight() -> None:
        refresh_document_highlight = getattr(lsp, "refresh_document_highlight", None)
        clear_document_highlight = getattr(lsp, "clear_document_highlight", None)
        signature = _current_document_highlight_signature()
        if signature is None:
            if callable(clear_document_highlight):
                clear_document_highlight()
            _reset_document_highlight_tracking()
            return

        now = time.monotonic()
        if signature != highlight_state["pending_signature"]:
            if callable(clear_document_highlight):
                clear_document_highlight()
            highlight_state["pending_signature"] = signature
            highlight_state["pending_since"] = now
            highlight_state["applied_signature"] = None
            return
        if signature == highlight_state["applied_signature"]:
            return
        pending_since = highlight_state["pending_since"]
        if isinstance(pending_since, int | float) and now - pending_since < 0.5:
            return
        if callable(refresh_document_highlight):
            refresh_document_highlight()
            highlight_state["applied_signature"] = signature

    # --- Auto-detect servers ---
    python_cmd = _find_server(_PYTHON_CANDIDATES)
    if python_cmd:
        log.debug("LSP: detected python server: %s", python_cmd[0])
        lsp.register_server("python", python_cmd)

    ruff_cmd = _find_server(_PYTHON_RUFF_CANDIDATES)
    if ruff_cmd:
        log.debug("LSP: detected python secondary server: %s", ruff_cmd[0])
        lsp.register_server(
            "python", ruff_cmd, root_markers=[".git", "pyproject.toml", "ruff.toml", ".ruff.toml", "setup.py"]
        )

    rust_cmd = _find_server(_RUST_CANDIDATES)
    if rust_cmd:
        log.debug("LSP: detected rust server: %s", rust_cmd[0])
        lsp.register_server("rust", rust_cmd)

    ts_cmd = _find_server(_TS_CANDIDATES)
    if ts_cmd:
        log.debug("LSP: detected typescript server: %s", ts_cmd[0])
        for ft in ("typescript", "javascript", "typescriptreact", "javascriptreact"):
            lsp.register_server(ft, ts_cmd)

    lua_cmd = _find_server(_LUA_CANDIDATES)
    if lua_cmd:
        log.debug("LSP: detected lua server: %s", lua_cmd[0])
        lsp.register_server("lua", lua_cmd)

    clangd_cmd = _find_server(_CLANGD_CANDIDATES)
    if clangd_cmd:
        log.debug("LSP: detected c/c++ server: %s", clangd_cmd[0])
        for ft in ("c", "cpp"):
            lsp.register_server(ft, clangd_cmd, root_markers=["compile_commands.json", ".clangd", ".git"])

    # Verilog is handled by the dedicated verilog_lsp plugin (peovim.plugins.verilog_lsp),
    # which provides hierarchy, signal tracing, and configurable Verible rules.
    # Do not auto-register verible-verilog-ls here; it would conflict and take priority.

    # --- Buffer lifecycle events ---
    def _on_buf_open(buf_id: int = 0, **kwargs) -> None:
        try:
            log.debug("LSP buffer_opened: buf_id=%s", buf_id)
            doc = lsp.attach_buffer(buf_id)
            if doc is None:
                log.debug("LSP buffer_opened: doc not found for buf_id=%s", buf_id)
                return
            log.debug("LSP buffer_opened: found doc path=%s", doc.path)
        except Exception:
            log.exception("LSP attach error for buf_id=%s", buf_id)

    def _on_buf_change(buf_id: int = 0, **kwargs) -> None:
        try:
            doc = lsp.notify_buffer_changed(buf_id)
            if doc is None:
                return
            clear_document_highlight = getattr(lsp, "clear_document_highlight", None)
            if callable(clear_document_highlight):
                clear_document_highlight()
            _reset_document_highlight_tracking()
            if not _update_in_insert() and _is_insert_mode():
                pending_diag_refresh.add(id(doc))
        except Exception:
            log.exception("LSP notify_change error")

    def _on_buf_text_change(
        buf_id: int = 0,
        start_line: int = 0,
        start_col: int = 0,
        end_line: int = 0,
        end_col: int = 0,
        new_text: str = "",
        **kwargs,
    ) -> None:
        try:
            lsp.remap_buffer_diagnostics(
                buf_id=buf_id,
                start_line=start_line,
                start_col=start_col,
                end_line=end_line,
                end_col=end_col,
                new_text=new_text,
            )
        except Exception:
            log.exception("LSP diagnostic remap error")

    def _on_buf_save(buf_id: int = 0, **kwargs) -> None:
        try:
            lsp.notify_buffer_saved(buf_id)
        except Exception:
            log.exception("LSP notify_save error")

    def _on_insert_entered(buf_id: int = 0, **kwargs) -> None:
        clear_document_highlight = getattr(lsp, "clear_document_highlight", None)
        if callable(clear_document_highlight):
            clear_document_highlight()
        _reset_document_highlight_tracking()
        if _update_in_insert():
            return
        try:
            doc = lsp.resolve_buffer_document(buf_id)
            if doc is None:
                return
            pending_diag_refresh.add(id(doc))
            _clear_insert_mode_diag_text(doc)
        except Exception:
            log.exception("LSP insert_entered handling error")

    def _on_insert_left(buf_id: int = 0, **kwargs) -> None:
        dismiss_signature_help = getattr(lsp, "dismiss_signature_help", None)
        if callable(dismiss_signature_help):
            dismiss_signature_help()
        _reset_document_highlight_tracking()
        if _update_in_insert():
            return
        try:
            doc = lsp.resolve_buffer_document(buf_id)
            if doc is None:
                return
            doc_id = id(doc)
            if doc_id not in pending_diag_refresh:
                return
            pending_diag_refresh.discard(doc_id)
            lsp.notify_buffer_changed(buf_id)
        except Exception:
            log.exception("LSP insert_left handling error")

    # Attach to all already-open buffers when the editor is ready
    # (catches the initial file opened before event subscriptions were wired)
    def _on_editor_ready(**kwargs) -> None:
        try:
            log.debug("LSP editor_ready: scanning open buffers")
            lsp.attach_open_buffers()
        except Exception:
            log.exception("LSP editor_ready attach error")

    def _trigger_insert_completion() -> None:
        win = getattr(api, "_workspace", None)
        if win is None:
            lsp.trigger_completion()
            return
        active_window = win.active_window
        doc = active_window.document
        line = active_window.cursor.line
        col = active_window.cursor.col
        prefix_start, prefix = _current_word_prefix(doc, line, col)
        items = _buffer_word_completion_items(doc, prefix)
        if len(items) == 1:
            completion = items[0]["insertText"]
            if completion.startswith(prefix):
                suffix = completion[len(prefix) :]
                if suffix:
                    api.active_buffer().insert(line, col, suffix)
                    return
        elif len(items) > 1:
            event_loop = getattr(api, "_event_loop", None)
            popup = getattr(event_loop, "_completion_popup", None)
            if popup is not None:
                popup.open(
                    items,
                    line,
                    prefix_start,
                    filter_text=prefix,
                    match_mode="prefix",
                    replace_filter_on_accept=True,
                )
                event_loop._invalidate("full")
                return
        lsp.trigger_completion()

    api.events.on("buffer_opened", _on_buf_open)
    api.events.on("buffer_changed", _on_buf_change)
    api.events.on("buffer_text_changed", _on_buf_text_change)
    api.events.on("buffer_saved", _on_buf_save)
    api.events.on("insert_entered", _on_insert_entered)
    api.events.on("insert_left", _on_insert_left)
    api.events.once("editor_ready", _on_editor_ready)
    api.set_interval(_poll_inlay_hints, 120)
    api.set_interval(_poll_document_highlight, 120)

    # --- Named actions (<Plug> names) — remap these in init.py ---
    api.keymap.nmap("<Plug>LspHover", lambda: lsp.hover(), desc="LSP: hover docs")
    api.keymap.nmap("<Plug>LspCodeAction", lambda: lsp.code_actions(), desc="LSP: code actions")
    api.keymap.nmap("<Plug>LspDefinition", lambda: lsp.definition(), desc="LSP: go to definition")
    api.keymap.nmap("<Plug>LspDocumentSymbols", lambda: lsp.document_symbols(), desc="LSP: document symbols")
    api.keymap.nmap("<Plug>LspToggleInlayHints", lambda: lsp.toggle_inlay_hints(), desc="LSP: toggle inlay hints")
    api.keymap.nmap("<Plug>LspWorkspaceSymbols", lambda: lsp.workspace_symbols(), desc="LSP: workspace symbols")
    api.keymap.nmap("<Plug>LspImplementation", lambda: lsp.implementation(), desc="LSP: go to implementation")
    api.keymap.nmap("<Plug>LspTypeDefinition", lambda: lsp.type_definition(), desc="LSP: go to type definition")
    api.keymap.nmap("<Plug>LspReferences", lambda: lsp.references(), desc="LSP: find references")
    api.keymap.nmap("<Plug>LspRename", lambda: lsp.rename(), desc="LSP: rename symbol")
    api.keymap.nmap("<Plug>LspNextDiag", lambda: lsp.goto_next_diag(), desc="LSP: next diagnostic")
    api.keymap.nmap("<Plug>LspPrevDiag", lambda: lsp.goto_prev_diag(), desc="LSP: prev diagnostic")
    api.keymap.nmap("<Plug>LspDiagDetail", lambda: lsp.diag_detail(), desc="LSP: diagnostic detail")
    api.keymap.imap("<Plug>LspSignatureHelp", lambda: lsp.signature_help(), desc="LSP: signature help")
    api.keymap.imap("<Plug>LspComplete", _trigger_insert_completion, desc="Insert completion")

    # --- Default key bindings (override any of these in init.py) ---
    api.keymap.nmap("K", "<Plug>LspHover", desc="LSP hover")
    api.keymap.nmap("go", "<Plug>LspDocumentSymbols", desc="Document symbols")
    api.keymap.nmap("<leader>ca", "<Plug>LspCodeAction", desc="LSP code actions")
    api.keymap.nmap("<leader>ci", "<Plug>LspToggleInlayHints", desc="Toggle inlay hints")
    api.keymap.nmap("<leader>csd", "<Plug>LspDocumentSymbols", desc="Document symbols")
    api.keymap.nmap("<leader>csw", "<Plug>LspWorkspaceSymbols", desc="Workspace symbols")
    api.keymap.nmap("gd", "<Plug>LspDefinition", desc="Go to definition")
    api.keymap.nmap("<leader>cgi", "<Plug>LspImplementation", desc="Go to implementation")
    api.keymap.nmap("<leader>cgt", "<Plug>LspTypeDefinition", desc="Go to type definition")
    api.keymap.nmap("<leader>gr", "<Plug>LspReferences", desc="Find references")
    api.keymap.nmap("<leader>rn", "<Plug>LspRename", desc="Rename symbol")
    api.keymap.nmap("[d", "<Plug>LspPrevDiag", desc="Prev diagnostic")
    api.keymap.nmap("]d", "<Plug>LspNextDiag", desc="Next diagnostic")
    api.keymap.nmap("ge", "<Plug>LspNextDiag", desc="Next diagnostic")
    api.keymap.nmap("<leader>c.d", "<Plug>LspDiagDetail", desc="Diagnostic detail")
    api.keymap.imap("<C-k>", "<Plug>LspSignatureHelp", desc="LSP signature help")
    api.keymap.imap("<C-n>", "<Plug>LspComplete", desc="Complete word")

    # --- Commands ---
    api.commands.register("LspInfo", lambda _ctx, _ws: lsp.info(), min_abbrev=4)
    api.commands.register("LspRestart", lambda ctx, _ws: lsp.restart(getattr(ctx, "args", "")), min_abbrev=4)

    # --- Health check ---
    api.health.register("lsp", lambda a, pm, cl: _check_lsp(a, lsp), label="LSP")
