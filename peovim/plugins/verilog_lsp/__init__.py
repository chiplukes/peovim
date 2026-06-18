from peovim.plugins.verilog_lsp.plugin import _state, configure, setup, teardown


def toggle_hierarchy(api):  # type: ignore[no-untyped-def]
    """Toggle the Verilog hierarchy sidebar panel."""
    panel = _state.get("hierarchy_panel")
    if panel is not None:
        panel.toggle()


def trace_signal(api):  # type: ignore[no-untyped-def]
    """Open the signal trace picker for the signal under cursor."""
    from peovim.plugins.verilog_lsp.signal_trace import trace_signal_under_cursor

    trace_signal_under_cursor(api)


def reparse(api):  # type: ignore[no-untyped-def]
    """Force a full Verilog workspace re-parse."""
    api.lsp.custom_request_to(
        "workspace/executeCommand",
        {"command": "verilog.reparse", "arguments": []},
        cb=lambda _r: None,
        cmd_contains="veriforge-lsp",
    )


def preview_pull_up_selection(api):  # type: ignore[no-untyped-def]
    """Preview hierarchy-up for the selected source instance."""
    from peovim.plugins.verilog_lsp.plugin import preview_pull_up_selection as _preview

    _preview(api)


def apply_pull_up_selection(api):  # type: ignore[no-untyped-def]
    """Apply hierarchy-up for the selected source instance."""
    from peovim.plugins.verilog_lsp.plugin import apply_pull_up_selection as _apply

    _apply(api)


__all__ = [
    "configure",
    "setup",
    "teardown",
    "toggle_hierarchy",
    "trace_signal",
    "reparse",
    "preview_pull_up_selection",
    "apply_pull_up_selection",
]
