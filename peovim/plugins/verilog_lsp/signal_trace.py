"""
Signal connectivity trace picker.

<leader>vt on a signal name opens a full-screen picker listing every
driver and load across the project.  Preview pane shows source context.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

log = logging.getLogger(__name__)

_VERILOG_KEYWORDS = frozenset(
    {
        "always",
        "assign",
        "begin",
        "case",
        "casex",
        "casez",
        "default",
        "else",
        "end",
        "endcase",
        "endmodule",
        "for",
        "forever",
        "if",
        "initial",
        "input",
        "inout",
        "integer",
        "module",
        "negedge",
        "output",
        "parameter",
        "posedge",
        "reg",
        "repeat",
        "wait",
        "while",
        "wire",
        "localparam",
        "logic",
        "bit",
        "byte",
        "int",
        "time",
        "genvar",
        "task",
        "function",
        "endtask",
        "endfunction",
        "generate",
        "endgenerate",
    }
)

# Preview palette (matches picker._BG so blends in)
_PREVIEW_BG = (25, 25, 38)
_PREVIEW_FG = (140, 160, 140)
_KW_FG = (100, 140, 210)
_SIG_FG = (255, 230, 80)
_NUM_FG = (200, 150, 80)
_CMT_FG = (90, 120, 90)


_COLOR_DRIVER: tuple = (80, 220, 80)  # green — true driver (assign/always)
_COLOR_BOUNDARY: tuple = (220, 200, 80)  # yellow — hierarchy transition / rename
_COLOR_UNCONNECTED: tuple = (220, 80, 80)  # red — unconnected port

_ARROW_LOAD = "|---> "
_ARROW_DRIVER = "|<=== "
_ARROW_PORT = "|---<*> "
_ARROW_RENAME = "|---<=> "
_ARROW_UNCONNECTED = "|---<x> "
_INDENT = "     "  # 5 spaces per hierarchy level


@dataclass
class TraceItem:
    label: str
    is_header: bool = False
    kind: str = ""
    file: str = ""
    range: dict = field(default_factory=dict)
    preview: str = ""
    detail: str = ""
    style: str = ""  # "driver" | "boundary" | "unconnected" | ""
    indent: int = 0  # nesting level
    signal_names: tuple[str, ...] = ()

    def __str__(self) -> str:
        return _INDENT * self.indent + self.label


def trace_signal_under_cursor(api: EditorAPI) -> None:
    win = api.active_window()
    if win is None:
        return
    buf = win.buffer()
    cursor_line, cursor_col = win.cursor
    uri = _buf_uri(buf)
    if not uri:
        return

    params = {
        "textDocument": {"uri": uri},
        "position": {"line": cursor_line, "character": cursor_col},
    }

    def _on_result(result: Any) -> None:
        if not result:
            api.set_status("No signal found at cursor")
            return
        open_trace_picker(api, result)

    api.lsp.custom_request_to(
        "workspace/executeCommand",
        {
            "command": "verilog/traceSignal",
            "arguments": [params],
        },
        cb=_on_result,
        cmd_contains="veriforge-lsp",
    )


def open_trace_picker(api: EditorAPI, result: dict) -> None:
    signal = result.get("signal", {})
    name = signal.get("name", "?")
    width = signal.get("width", "")
    title = f"Trace: {name}  {width}".strip()

    items: list[TraceItem] = []
    drivers = result.get("drivers", [])
    loads = result.get("loads", [])

    if drivers:
        items.append(TraceItem(label="-- Drivers --", is_header=True))
        for d in drivers:
            items.append(_make_item(d))

    if loads:
        items.append(TraceItem(label="-- Loads --", is_header=True))
        for ld in loads:
            items.append(_make_item(ld))

    if not items:
        api.set_status(f"No drivers or loads found for {name}")
        return

    def _preview(item: TraceItem) -> str | list:
        if item.is_header or not item.file:
            return ""
        raw = item.preview or _load_preview(item.file, item.range)
        if not raw:
            return ""
        highlight_names = item.signal_names or (name,)
        return _highlight_preview(raw, highlight_names)

    def _on_confirm(item: TraceItem) -> None:
        if item.is_header or not item.file:
            return
        start = item.range.get("start", {})
        line = start.get("line", 0)
        char = start.get("character", 0)
        api.goto_location(item.file, line, char)

    def _item_style(item: TraceItem) -> tuple | None:
        if item.is_header:
            return None
        if item.style == "driver":
            return (_COLOR_DRIVER,)
        if item.style in ("boundary", "rename"):
            return (_COLOR_BOUNDARY,)
        if item.style == "unconnected":
            return (_COLOR_UNCONNECTED,)
        return None

    api.ui.open_picker(
        title=title,
        source=items,
        on_confirm=_on_confirm,
        preview=_preview,
        item_style=_item_style,
    )


def _make_item(entry: dict) -> TraceItem:
    file_uri = entry.get("file", "")
    file_path = _uri_to_path(file_uri)
    rng = entry.get("range", {})
    start = rng.get("start", {})
    line_num = start.get("line", 0) + 1  # display 1-based
    short = os.path.basename(file_path) if file_path else "?"
    instance_path = entry.get("instancePath", "")
    chain = entry.get("signalChain", [])
    label_text = entry.get("label", "")
    style = entry.get("style", "")
    kind = entry.get("kind", "")
    indent = entry.get("indent", 0)

    if style == "driver":
        arrow = _ARROW_DRIVER
    elif style == "boundary":
        arrow = _ARROW_PORT
    elif style == "rename":
        arrow = _ARROW_RENAME
    elif style == "unconnected":
        arrow = _ARROW_UNCONNECTED
    else:
        arrow = _ARROW_LOAD

    # Content: (inst_path filename.v:line#) with optional name1<->name2 for ports/renames
    inst_display = instance_path.replace("/", ".") if instance_path else ""
    location = f"{short}:{line_num}"
    content = f"{inst_display} {location}".strip() if inst_display else location

    if style in ("boundary", "rename") and len(chain) >= 2:
        content += f"  {chain[0]}<->{chain[-1]}"

    label = arrow + f"({content})"

    return TraceItem(
        label=label,
        kind=kind,
        file=file_path,
        range=rng,
        preview=entry.get("preview", ""),
        detail=label_text,
        style=style,
        indent=indent,
        signal_names=_normalize_signal_names(chain),
    )


def _normalize_signal_names(names: Any) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names or []:
        if not isinstance(name, str):
            continue
        cleaned = name.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return tuple(ordered)


def _highlight_preview(text: str, signals: str | list[str] | tuple[str, ...]) -> list:
    """Return list of PreviewLine (list of (str, Style) segments) with syntax + signal highlight."""
    from peovim.core.style import Style

    signal_names = _normalize_signal_names([signals] if isinstance(signals, str) else signals)
    signal_alts = "|".join(re.escape(signal) for signal in sorted(signal_names, key=len, reverse=True))
    signal_pat = rf"(\b(?:{signal_alts})\b)" if signal_alts else r"($^)"
    # Groups: 1=signal, 2=identifier, 3=number, 4=whitespace/punct, 5=comment
    token_re = re.compile(
        signal_pat + r"|([a-zA-Z_]\w*)" + r"|((?:'?[0-9])[0-9A-Fa-f_bBhHxXoOdD']*)" + r"|(//[^\n]*)" + r"|(\s+|[^\w\s])"
    )
    result = []
    for line in text.splitlines():
        segments = []
        pos = 0
        for m in token_re.finditer(line):
            if m.start() > pos:
                segments.append((line[pos : m.start()], Style(fg=_PREVIEW_FG)))
            tok = m.group()
            if m.group(1):
                segments.append((tok, Style(fg=_SIG_FG)))
            elif m.group(2):
                fg = _KW_FG if tok in _VERILOG_KEYWORDS else _PREVIEW_FG
                segments.append((tok, Style(fg=fg)))
            elif m.group(3):
                segments.append((tok, Style(fg=_NUM_FG)))
            elif m.group(4):
                segments.append((tok, Style(fg=_CMT_FG)))
            else:
                segments.append((tok, Style(fg=_PREVIEW_FG)))
            pos = m.end()
        if pos < len(line):
            segments.append((line[pos:], Style(fg=_PREVIEW_FG)))
        result.append(segments or [("", Style(fg=_PREVIEW_FG))])
    return result


def _load_preview(file_path: str, rng: dict, context: int = 20) -> str:
    try:
        lines = Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines()
        start = rng.get("start", {})
        idx = start.get("line", 0)  # 0-based
        lo = max(0, idx - context)
        hi = min(len(lines), idx + context + 1)
        return "\n".join(lines[lo:hi])
    except OSError:
        return ""


def _uri_to_path(uri: str) -> str:
    if not uri.startswith("file://"):
        return uri
    from urllib.parse import unquote

    path = unquote(uri[7:])
    if os.name == "nt" and path.startswith("/"):
        path = path[1:]
    return os.path.normpath(path)


def _buf_uri(buf: Any) -> str:
    path = getattr(buf, "path", None)
    if not path:
        return ""
    try:
        from urllib.request import pathname2url

        return "file:///" + pathname2url(os.path.abspath(str(path))).lstrip("/")
    except Exception:
        return ""
