"""
peovim plugin: Verilog LSP integration.

Registers the verilog-lsp server for .v/.sv files and provides:
  - Hierarchy panel (VerilogHierarchyPanel) fed by verilog/hierarchyTree
  - Signal trace picker (<leader>rt) using verilog/traceSignal
  - Top module pinning from the hierarchy panel
  - Preview-first hierarchy refactors (<leader>ru/rw)
  - :VerilogReparse command / <leader>rr keybinding
  - :VerilogStatus command for diagnostics
  - Standard LSP ops (definition, hover, rename, etc.) are handled by lsp.py
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

log = logging.getLogger(__name__)

_VERILOG_FT = "verilog"
_LSP_CMD = ["veriforge-lsp"]
_ROOT_MARKERS = [".git", "Makefile", "*.f", ".veriforge_lsp.json"]

# In-memory state (not persisted — PluginStore is JSON-only)
_state: dict = {}  # type: ignore[type-arg]

# Configuration set before setup() via configure()
_opts: dict = {}  # type: ignore[type-arg]


def configure(verible_rules: list[str] | None = None) -> None:
    """Call before plugins.load() to configure the Verilog LSP plugin.

    Args:
        verible_rules: Verible lint rule overrides, e.g. ["-line-length", "-no-tabs"].
                       Merged with any rules in .verilog_lsp.json.
    """
    if verible_rules is not None:
        _opts["verible_rules"] = list(verible_rules)


def setup(api: EditorAPI) -> None:
    _register_server(api)
    _register_panel(api)
    _register_keybindings(api)
    _register_commands(api)
    _register_lsp_handlers(api)


def _register_server(api: EditorAPI) -> None:
    if shutil.which("veriforge-lsp") is None:
        log.warning("verilog_lsp: 'veriforge-lsp' not found — run: uv tool install veriforge")
        return
    cmd = list(_LSP_CMD)
    rules = _opts.get("verible_rules")
    if rules:
        cmd.append(f"--verible-rules={','.join(rules)}")
    api.lsp.register_server(
        filetype=_VERILOG_FT,
        cmd=cmd,
        root_markers=_ROOT_MARKERS,
    )


def _register_panel(api: EditorAPI) -> None:
    from peovim.plugins.verilog_lsp.hierarchy_panel import VerilogHierarchyPanel

    panel = VerilogHierarchyPanel(api)
    _state["hierarchy_panel"] = panel
    api.ui.register_sidebar_panel("verilog_hierarchy", panel)


def _register_keybindings(api: EditorAPI) -> None:
    from peovim.plugins.verilog_lsp.signal_trace import trace_signal_under_cursor

    def _ft_guard(fn):
        """Wrap a function to only act when cursor is in a Verilog buffer."""

        def _wrapped(ctx):
            win = api.active_window()
            if win is None:
                return
            ft = win.buffer().filetype or ""
            if ft != _VERILOG_FT:
                return
            if _takes_context(fn):
                fn(ctx)
            else:
                fn()

        return _wrapped

    bindings = [
        ("<leader>rt", lambda: trace_signal_under_cursor(api), "Verilog: trace signal"),
        ("<leader>rh", lambda: _toggle_hierarchy_panel(api), "Verilog: hierarchy panel"),
        ("<leader>rr", lambda: _reparse(api), "Verilog: force re-parse"),
        ("<leader>ru", lambda ctx: _preview_pull_up_selection(api, ctx=ctx), "Verilog: hier-up selection"),
        (
            "<leader>rw",
            lambda ctx: _prompt_push_down_range(api, ctx=ctx, apply_edit=False),
            "Verilog: hier-down selection",
        ),
    ]
    for key, fn, _desc in bindings:
        try:
            api.keymap.nmap(key, _ft_guard(fn), _desc)
        except Exception:
            log.debug("verilog_lsp: failed to bind %s", key)
    for key, fn, desc in [
        ("<leader>ru", lambda ctx: _preview_pull_up_selection(api, ctx=ctx), "Verilog: hier-up selection"),
        (
            "<leader>rw",
            lambda ctx: _prompt_push_down_range(api, ctx=ctx, apply_edit=False),
            "Verilog: hier-down selection",
        ),
    ]:
        try:
            api.keymap.vmap(key, _ft_guard(fn), desc)
        except Exception:
            log.debug("verilog_lsp: failed to bind visual %s", key)


def _register_commands(api: EditorAPI) -> None:
    api.commands.register("VerilogTrace", lambda cmd, ctx: _do_trace(api))
    api.commands.register("VerilogReparse", lambda cmd, ctx: _reparse(api))
    api.commands.register("VerilogHierarchy", lambda cmd, ctx: _toggle_hierarchy_panel(api))
    api.commands.register("VerilogHierUp", lambda cmd, ctx: _preview_pull_up_selection(api, cmd=cmd, ctx=ctx))
    api.commands.register("VerilogHierDown", lambda cmd, ctx: _push_down(api, cmd=cmd, apply_edit=False))
    api.commands.register(
        "VerilogHierDownRange", lambda cmd, ctx: _push_down_range(api, cmd=cmd, ctx=ctx, apply_edit=False)
    )
    api.commands.register("VerilogPullUpPreview", lambda cmd, ctx: _preview_pull_up_selection(api, cmd=cmd, ctx=ctx))
    api.commands.register("VerilogPullUpApply", lambda cmd, ctx: _apply_pull_up_selection(api, cmd=cmd, ctx=ctx))
    api.commands.register("VerilogExtractPreview", lambda cmd, ctx: _preview_extract(api, cmd=cmd, ctx=ctx))
    api.commands.register("VerilogExtractApply", lambda cmd, ctx: _apply_extract(api, cmd=cmd, ctx=ctx))
    api.commands.register("VerilogPushDown", lambda cmd, ctx: _push_down(api, cmd=cmd, apply_edit=False))
    api.commands.register("VerilogPushDownApply", lambda cmd, ctx: _push_down(api, cmd=cmd, apply_edit=True))
    api.commands.register(
        "VerilogPushDownRange",
        lambda cmd, ctx: _push_down_range(api, cmd=cmd, ctx=ctx, apply_edit=False),
    )
    api.commands.register(
        "VerilogPushDownRangeApply",
        lambda cmd, ctx: _push_down_range(api, cmd=cmd, ctx=ctx, apply_edit=True),
    )
    api.commands.register("VerilogStatus", lambda cmd, ctx: _show_status(api))


def _register_lsp_handlers(api: EditorAPI) -> None:
    panel = _state.get("hierarchy_panel")

    def _on_hierarchy_tree(params: dict) -> None:
        roots = params.get("roots", [])
        log.warning("verilog_lsp: _on_hierarchy_tree received, %d roots", len(roots))
        _state["last_roots_count"] = len(roots)
        if panel is not None:
            panel.update(roots)
        else:
            log.warning("verilog_lsp: _on_hierarchy_tree — panel is None!")

    api.lsp.on_notification("verilog/hierarchyTree", _on_hierarchy_tree)

    def _on_progress(token: str, kind: str, value: dict) -> None:
        if token == "workspace-parse":
            _state["parse_progress"] = f"{kind}: {value.get('message', '')}"
            if panel is not None:
                panel.on_progress(kind, value)

    api.lsp.on_progress(_on_progress)


def _takes_context(callback: object) -> bool:
    try:
        signature = inspect.signature(callback)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    positional_kinds = {
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.VAR_POSITIONAL,
    }
    return any(
        parameter.kind in positional_kinds and parameter.default is inspect.Parameter.empty
        for parameter in signature.parameters.values()
    )


# ------------------------------------------------------------------
# Command helpers
# ------------------------------------------------------------------


def _toggle_hierarchy_panel(api: EditorAPI) -> None:
    panel = _state.get("hierarchy_panel")
    if panel is not None:
        panel.toggle()


def _reparse(api: EditorAPI) -> None:
    api.lsp.custom_request_to(
        "workspace/executeCommand",
        {"command": "verilog.reparse", "arguments": []},
        cb=lambda _r: None,
        cmd_contains="veriforge-lsp",
    )


def _do_trace(api: EditorAPI) -> None:
    from peovim.plugins.verilog_lsp.signal_trace import trace_signal_under_cursor

    trace_signal_under_cursor(api)


def _preview_extract(api: EditorAPI, cmd: object | None = None, ctx: object | None = None) -> None:
    panel = _state.get("hierarchy_panel")
    if panel is not None:
        panel.preview_extract_from_active_selection(line_range=_extract_line_range(cmd, ctx))


def _apply_extract(api: EditorAPI, cmd: object | None = None, ctx: object | None = None) -> None:
    panel = _state.get("hierarchy_panel")
    if panel is not None:
        panel.apply_extract_from_active_selection(line_range=_extract_line_range(cmd, ctx))


def _preview_pull_up_selection(api: EditorAPI, cmd: object | None = None, ctx: object | None = None) -> None:
    panel = _state.get("hierarchy_panel")
    if panel is not None:
        panel.preview_pull_up_from_active_selection(line_range=_extract_line_range(cmd, ctx))


def _apply_pull_up_selection(api: EditorAPI, cmd: object | None = None, ctx: object | None = None) -> None:
    panel = _state.get("hierarchy_panel")
    if panel is not None:
        panel.apply_pull_up_from_active_selection(line_range=_extract_line_range(cmd, ctx))


def _push_down(api: EditorAPI, cmd: object | None = None, *, apply_edit: bool = False) -> None:
    panel = _state.get("hierarchy_panel")
    if panel is None:
        return
    args = str(getattr(cmd, "args", "") or "").strip()
    panel.request_push_down_from_command(args, apply_edit=apply_edit)


def _push_down_range(
    api: EditorAPI,
    cmd: object | None = None,
    ctx: object | None = None,
    *,
    apply_edit: bool = False,
) -> None:
    panel = _state.get("hierarchy_panel")
    if panel is None:
        return
    args = str(getattr(cmd, "args", "") or "").strip()
    panel.request_push_down_range_from_command(
        args,
        line_range=_extract_line_range(cmd, ctx),
        apply_edit=apply_edit,
    )


def _prompt_push_down_range(
    api: EditorAPI,
    ctx: object | None = None,
    *,
    apply_edit: bool = False,
) -> None:
    panel = _state.get("hierarchy_panel")
    if panel is None:
        return
    panel.prompt_push_down_range(_extract_line_range(None, ctx), apply_edit=apply_edit)


def _extract_line_range(cmd: object | None = None, ctx: object | None = None) -> tuple[int, int] | None:
    visual_range = getattr(ctx, "visual_range", None)
    if visual_range is not None:
        return int(visual_range[0]), int(visual_range[1])

    parsed_start = getattr(cmd, "range_start", None)
    parsed_end = getattr(cmd, "range_end", None)
    if _is_visual_mark_range(parsed_start, parsed_end):
        engine = getattr(ctx, "engine", None)
        last_selection = getattr(engine, "_last_visual_selection", None)
        if last_selection is not None:
            _mode, anchor, cursor = last_selection
            return min(anchor[0], cursor[0]), max(anchor[0], cursor[0])

    if parsed_start is None:
        return None

    start = _line_from_range_addr(parsed_start)
    end = _line_from_range_addr(parsed_end) if parsed_end is not None else start
    if start is None or end is None:
        return None
    return start, end


def _is_visual_mark_range(start: object | None, end: object | None) -> bool:
    return (
        getattr(start, "kind", None) == "mark"
        and getattr(start, "value", None) == "<"
        and getattr(end, "kind", None) == "mark"
        and getattr(end, "value", None) == ">"
    )


def _line_from_range_addr(addr: object | None) -> int | None:
    if getattr(addr, "kind", None) != "line":
        return None
    return max(0, int(getattr(addr, "value", 1)) - 1 + int(getattr(addr, "offset", 0)))


def _show_status(api: EditorAPI) -> None:
    panel = _state.get("hierarchy_panel")
    roots_count = _state.get("last_roots_count", "never received")
    parse_progress = _state.get("parse_progress", "none")
    try:
        registered = api.lsp.registered_servers()
        verilog_registered = [s for s in registered if s.get("filetype") == _VERILOG_FT]
    except Exception as e:
        verilog_registered = [{"error": str(e)}]

    try:
        running = api.lsp.running_servers()
        verilog_running = [s for s in running if s.get("filetype") == _VERILOG_FT]
    except Exception as e:
        verilog_running = [{"error": str(e)}]

    verible_rules = _opts.get("verible_rules") or []
    lines = [
        f"panel: {'ok' if panel else 'MISSING'}",
        f"registered: {verilog_registered}",
        f"running: {verilog_running}",
        f"roots received: {roots_count}",
        f"last progress: {parse_progress}",
        f"verible_rules (init.py): {verible_rules or '(none)'}",
        "note: rules only take effect after LSP server restart (<leader>cx or reopen file)",
    ]
    msg = "\n".join(lines)
    log.warning("VerilogStatus:\n%s", msg)
    with contextlib.suppress(Exception):
        api.ui.notify(msg, level="info", title="Verilog LSP Status", timeout=15.0)
    with contextlib.suppress(Exception):
        api.set_status(" | ".join(lines[:3]))


# ------------------------------------------------------------------
# Public convenience API (for use in init.py)
# ------------------------------------------------------------------


def toggle_hierarchy(api: EditorAPI) -> None:
    _toggle_hierarchy_panel(api)


def trace_signal(api: EditorAPI) -> None:
    _do_trace(api)


def preview_pull_up_selection(api: EditorAPI) -> None:
    _preview_pull_up_selection(api)


def apply_pull_up_selection(api: EditorAPI) -> None:
    _apply_pull_up_selection(api)


def preview_extract(api: EditorAPI) -> None:
    _preview_extract(api)


def apply_extract(api: EditorAPI) -> None:
    _apply_extract(api)


def reparse(api: EditorAPI) -> None:
    _reparse(api)


def teardown(api: EditorAPI) -> None:
    import contextlib

    panel = _state.get("hierarchy_panel")
    if panel is not None:
        with contextlib.suppress(Exception):
            panel.close()
