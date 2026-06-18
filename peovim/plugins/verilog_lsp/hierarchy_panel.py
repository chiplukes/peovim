"""
Verilog hierarchy sidebar panel.

Displays the instantiation hierarchy of the current project as a tree.
Updated by verilog/hierarchyTree notifications from the LSP server.
"""

from __future__ import annotations

import contextlib
import logging
import os
import pathlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

log = logging.getLogger(__name__)

_PANEL_NAME = "verilog_hierarchy"

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

_FLOAT_BG = (30, 30, 40)
_TEXT_FG = (175, 185, 175)
_KW_FG = (100, 140, 210)
_HL_FG = (255, 230, 80)
_NUM_FG = (200, 150, 80)
_CMT_FG = (90, 120, 90)

_WRAPPER_BADGES = {
    "pure_pass_through": "collapse",
    "structural_wrapper": "struct",
    "behavioral_wrapper": "behavior",
    "unknown_or_unsupported": "blocked",
}

_WRAPPER_COLORS = {
    "pure_pass_through": (120, 220, 120),
    "structural_wrapper": (220, 200, 90),
    "behavioral_wrapper": (220, 140, 80),
    "unknown_or_unsupported": (220, 90, 90),
}


@dataclass(frozen=True)
class ExtractSelectionSuggestion:
    """Picker row for a partial-selection expand suggestion."""

    label: str
    start_line: int
    end_line: int
    kind: str

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class WrapperCandidate:
    """Picker row for a hierarchy wrapper candidate."""

    label: str
    node: dict

    def __str__(self) -> str:
        return self.label


def _load_preview(file_path: str, rng: dict, context: int = 20) -> str:
    """Return source lines around the given LSP range."""
    try:
        from pathlib import Path

        lines = Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines()
        start = rng.get("start", {})
        idx = start.get("line", 0)  # 0-based
        lo = max(0, idx - context)
        hi = min(len(lines), idx + context + 1)
        return "\n".join(lines[lo:hi])
    except OSError:
        return ""


def _highlight_verilog(text: str, highlight: str = "") -> list:
    """Return list of FloatLine (each a list of (str, Style) segments) with Verilog highlighting."""
    from peovim.core.style import Style

    parts: list[str] = []
    if highlight:
        parts.append(rf"(?P<hl>\b{re.escape(highlight)}\b)")
    parts += [
        r"(?P<id>[a-zA-Z_]\w*)",
        r"(?P<num>(?:'?[0-9])[0-9A-Fa-f_bBhHxXoOdD']*)",
        r"(?P<cmt>//[^\n]*)",
        r"(?P<other>\s+|[^\w\s])",
    ]
    token_re = re.compile("|".join(parts))

    result = []
    for line in text.splitlines():
        segments: list[tuple[str, Any]] = []
        pos = 0
        for m in token_re.finditer(line):
            if m.start() > pos:
                segments.append((line[pos : m.start()], Style(fg=_TEXT_FG)))
            tok = m.group()
            g = m.lastgroup
            if g == "hl":
                segments.append((tok, Style(fg=_HL_FG)))
            elif g == "id":
                fg = _KW_FG if tok in _VERILOG_KEYWORDS else _TEXT_FG
                segments.append((tok, Style(fg=fg)))
            elif g == "num":
                segments.append((tok, Style(fg=_NUM_FG)))
            elif g == "cmt":
                segments.append((tok, Style(fg=_CMT_FG)))
            else:
                segments.append((tok, Style(fg=_TEXT_FG)))
            pos = m.end()
        if pos < len(line):
            segments.append((line[pos:], Style(fg=_TEXT_FG)))
        result.append(segments or [("", Style(fg=_TEXT_FG))])
    return result


def _uri_to_path(uri: str) -> str:
    import os

    if not uri.startswith("file://"):
        return uri
    from urllib.parse import unquote

    path = unquote(uri[7:])
    if os.name == "nt" and path.startswith("/"):
        path = path[1:]
    return os.path.normpath(path)


class _SubGrid:
    """Wraps a CellGrid and limits the visible height, forwarding all writes."""

    def __init__(self, grid: Any, height: int) -> None:
        self._grid = grid
        self.height = height
        self.width = grid.width

    def write_str(self, row: int, col: int, text: str, **kw: Any) -> None:
        if row < self.height:
            self._grid.write_str(row, col, text, **kw)

    def fill(self, row: int, col: int, width: int, **kw: Any) -> None:
        if row < self.height:
            self._grid.fill(row, col, width, **kw)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._grid, name)


class VerilogHierarchyPanel:  # cm:8c1e4a
    """Sidebar panel that displays the Verilog instantiation hierarchy.

    Follows the same pattern as outline.py: pre-created in setup(), registered
    once with register_sidebar_panel(), shown via show_sidebar_panel().
    """

    @staticmethod
    def _engine_payload(result: dict) -> dict:
        """Return the engine-specific preview payload from a unified response.

        verilog/{preview,apply}HierarchyBoundaryMove returns the boundary
        summary on ``preview`` and the engine-specific payload (extract or
        collapse) on ``details``. Legacy commands returned the engine payload
        directly on ``preview``. Prefer ``details`` when present so the
        rendering helpers consume the engine-specific shape regardless of
        which surface the server used.
        """

        details = result.get("details")
        if isinstance(details, dict):
            return details
        preview = result.get("preview")
        return preview if isinstance(preview, dict) else {}

    def __init__(self, api: EditorAPI, *, width: int = 35) -> None:
        from peovim.ui.tree_view import TreeView

        self._api = api
        self.width = width
        self._roots: list[dict] = []
        self._base_title = "Hierarchy"
        self._title = "Hierarchy"
        self._parse_state = ""
        self._preview_float: Any = None
        self._refactor_source_path = ""
        self._refactor_destination_path = ""
        self._refactor_source_explicit = False
        self._refactor_destination_explicit = False
        self._pending_push_down: dict[str, Any] | None = None
        self._pending_push_down_range: tuple[int, int] | None = None
        self._tree = TreeView(
            [],
            title=self._title,
            on_select=self._on_select,
            on_cursor_move=self._on_cursor_move,
            on_key=self._on_key,
            width=width,
        )

    _HELP_LINES = [
        " i  jump to instantiation",
        " d  jump to definition",
        " s/t mark source/dest",
        " c  hier-up (safe collapse when possible)",
        " w  hier-down into submodule",
        " g  wrapper candidates",
        " p  pin as top module",
        " P  unpin top module",
        " j/k  move  h  collapse",
    ]

    # ------------------------------------------------------------------
    # SidebarPanel protocol
    # ------------------------------------------------------------------

    def render(self, grid: Any) -> None:
        self._tree.focused = getattr(self, "_sidebar_focused", False)
        self._tree.blink_on = getattr(self, "_sidebar_blink_on", True)
        # Reserve bottom rows for help text
        help_rows = len(self._HELP_LINES)
        tree_height = max(1, grid.height - help_rows - 1)  # -1 for separator
        # Render tree into a sub-grid slice
        sub = _SubGrid(grid, tree_height)
        self._tree._width = grid.width  # noqa: SLF001
        self._tree.render(sub)
        # Separator
        sep_row = tree_height
        if sep_row < grid.height:
            sep = ("─" * grid.width)[: grid.width].ljust(grid.width)
            grid.write_str(sep_row, 0, sep, fg=(80, 80, 120))
        # Help lines
        for i, text in enumerate(self._HELP_LINES):
            row = sep_row + 1 + i
            if row >= grid.height:
                break
            grid.fill(row, 0, grid.width)
            grid.write_str(row, 0, text[: grid.width].ljust(grid.width), fg=(130, 130, 160))

    def feed_key(self, key: str) -> bool:
        self._tree.feed_key(key)
        return True

    def cursor_row(self, panel_height: int) -> int | None:
        tree_height = max(1, panel_height - len(self._HELP_LINES) - 1)
        return self._tree.cursor_row(tree_height)

    def on_focus(self) -> None:
        self._tree.focused = True

    def on_blur(self) -> None:
        self._tree.focused = False
        self._close_preview_float()

    # ------------------------------------------------------------------
    # Data updates (called from LSP notification callbacks)
    # ------------------------------------------------------------------

    def update(self, roots: list[dict]) -> None:
        self._roots = roots
        pin_count = len(roots)
        if pin_count == 1:
            self._base_title = f"Hierarchy  [top: {roots[0].get('name', '?')} \u2713]"
        elif pin_count > 1:
            self._base_title = f"Hierarchy  [top: auto ({pin_count} roots)]"
        else:
            self._base_title = "Hierarchy"
        self._sync_title()
        self._parse_state = "ready"
        self._rebuild_tree()
        self._refresh_if_visible()

    def on_progress(self, kind: str, value: dict) -> None:
        if kind == "begin":
            msg = value.get("message", "")
            self._parse_state = f"parsing\u2026 {msg}".strip()
            self._base_title = f"Hierarchy  [{self._parse_state}]"
        elif kind == "report":
            msg = value.get("message", "")
            self._parse_state = msg
            self._base_title = f"Hierarchy  [{msg}]"
        elif kind == "end":
            self._refresh_if_visible()
            return
        self._sync_title()
        self._refresh_if_visible()

    # ------------------------------------------------------------------
    # Panel show/hide/toggle
    # ------------------------------------------------------------------

    def toggle(self) -> None:
        if self._api.ui.is_sidebar_visible(_PANEL_NAME):
            self._api.ui.hide_sidebar()
        else:
            self.show()

    def show(self) -> None:
        try:
            self._api.ui.show_sidebar_panel(_PANEL_NAME, self, focus=True)
        except Exception as e:
            log.debug("show hierarchy panel error: %s", e)

    def close(self) -> None:
        self._clear_refactor_marks(refresh=False)
        self._close_preview_float()
        import contextlib

        with contextlib.suppress(Exception):
            self._api.ui.hide_sidebar()

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_select(self, node: Any) -> None:
        """Jump to instantiation location (leaf <CR>), or definition if no instantiation."""
        self._jump_to_instantiation(node)

    def _close_preview_float(self) -> None:
        if self._preview_float is not None:
            with contextlib.suppress(Exception):
                self._preview_float.close()
            self._preview_float = None

    def _clear_refactor_marks(self, *, refresh: bool = True) -> None:
        self._refactor_source_path = ""
        self._refactor_destination_path = ""
        self._refactor_source_explicit = False
        self._refactor_destination_explicit = False
        self._sync_title()
        if refresh:
            self._rebuild_tree()
            self._refresh_if_visible()

    def _sync_title(self) -> None:
        suffix = ""
        if self._refactor_source_path or self._refactor_destination_path:
            source = self._refactor_source_path or "?"
            destination = self._refactor_destination_path or "?"
            suffix = f"  [refactor: {source} -> {destination}]"
        self._title = f"{self._base_title}{suffix}"
        self._tree._title = self._title  # noqa: SLF001

    def _on_cursor_move(self, node: Any) -> None:
        """Show a source preview float when cursor moves to a new node."""
        import contextlib

        self._close_preview_float()
        value = getattr(node, "value", None)
        if not value:
            return
        # Prefer instantiation location for preview; fall back to definition
        inst_file_uri = value.get("instanceFile", "")
        inst_rng = value.get("instanceRange", {})
        file_uri = inst_file_uri or value.get("file", "")
        rng = inst_rng if inst_file_uri else value.get("range", {})
        if not file_uri:
            return
        path = _uri_to_path(file_uri)
        raw = _load_preview(path, rng)
        if not raw:
            return
        highlight = value.get("moduleName") or value.get("instanceName") or value.get("name", "")
        content = _highlight_verilog(raw, highlight)
        title = value.get("instanceName") or value.get("name", "")
        with contextlib.suppress(Exception):
            self._preview_float = self._api.ui.open_float(
                content,
                title=title,
                width=90,
                height=28,
                focusable=False,
            )

    def _on_key(self, key: str, node: Any) -> bool:
        """Handle panel-specific keys. Return True if consumed."""
        if key in ("esc", "escape", "<esc>", "Esc", "ESC"):
            self._close_preview_float()
            self._clear_refactor_marks()
            self._set_status("Hierarchy refactor selection cleared")
            return True
        if key == "i":
            self._jump_to_instantiation(node)
            return True
        if key == "d":
            self._jump_to_definition(node)
            return True
        if key in ("p", "<leader>vp"):
            self._pin_top_module(node)
            return True
        if key in ("P", "<leader>vP"):
            self._clear_pin()
            return True
        if key == "s":
            self._mark_refactor_source(node)
            return True
        if key == "t":
            self._mark_refactor_destination(node)
            return True
        if key == "c":
            self._preview_collapse(node)
            return True
        if key == "C":
            self._preview_collapse(node)
            return True
        if key == "w":
            self._prompt_push_down(node, apply_edit=False)
            return True
        if key == "W":
            self._prompt_push_down(node, apply_edit=False)
            return True
        if key == "e":
            self._preview_extract_from_node(node)
            return True
        if key == "E":
            self._preview_extract_from_node(node)
            return True
        if key == "g":
            self._open_candidate_picker()
            return True
        return False

    def _jump_to_instantiation(self, node: Any) -> None:
        """Jump to where this instance is declared in the parent file."""
        value = getattr(node, "value", None)
        if not value:
            return
        inst_file_uri = value.get("instanceFile", "")
        inst_rng = value.get("instanceRange", {})
        if inst_file_uri:
            path = _uri_to_path(inst_file_uri)
            start = inst_rng.get("start", {})
            line = start.get("line", 0)
            char = start.get("character", 0)
        else:
            # Root module — jump to definition
            file_uri = value.get("file", "")
            if not file_uri:
                return
            path = _uri_to_path(file_uri)
            start = value.get("range", {}).get("start", {})
            line = start.get("line", 0)
            char = start.get("character", 0)
        try:
            self._api.goto_location(path, line, char)
        except Exception as e:
            log.debug("goto_location error: %s", e)

    def _jump_to_definition(self, node: Any) -> None:
        """Jump to the module definition file."""
        value = getattr(node, "value", None)
        if not value:
            return
        file_uri = value.get("file", "")
        if not file_uri:
            return
        path = _uri_to_path(file_uri)
        start = value.get("range", {}).get("start", {})
        line = start.get("line", 0)
        char = start.get("character", 0)
        try:
            self._api.goto_location(path, line, char)
        except Exception as e:
            log.debug("goto_location error: %s", e)

    def _pin_top_module(self, node: Any) -> None:
        value = getattr(node, "value", None)
        if not value:
            return
        module_name = value.get("moduleName", "") or value.get("name", "")
        if not module_name:
            return
        self._api.lsp.custom_request_to(
            "workspace/executeCommand",
            {"command": "verilog/setTopModule", "arguments": [{"moduleName": module_name}]},
            cb=lambda result: self._on_set_top_module(result),
            cmd_contains="veriforge-lsp",
        )

    def _clear_pin(self) -> None:
        self._api.lsp.custom_request_to(
            "workspace/executeCommand",
            {"command": "verilog/setTopModule", "arguments": [{"moduleName": None}]},
            cb=lambda result: self._on_set_top_module(result),
            cmd_contains="veriforge-lsp",
        )

    def _on_set_top_module(self, result: dict | None) -> None:
        if not result:
            return
        tree = result.get("hierarchyTree", {})
        roots = tree.get("roots", [])
        if roots:
            self.update(roots)
            self.show()

    def preview_extract_from_active_selection(self, line_range: tuple[int, int] | None = None) -> None:
        self._preview_extract(line_range=line_range)

    def apply_extract_from_active_selection(self, line_range: tuple[int, int] | None = None) -> None:
        self._preview_extract(line_range=line_range)

    def preview_pull_up_from_active_selection(self, line_range: tuple[int, int] | None = None) -> None:
        self._preview_pull_up_selection(line_range=line_range)

    def apply_pull_up_from_active_selection(self, line_range: tuple[int, int] | None = None) -> None:
        self._preview_pull_up_selection(line_range=line_range)

    def _preview_extract_from_node(self, node: Any) -> None:
        if _hierarchy_instance_path(getattr(node, "value", None)):
            self._preview_collapse(node)
            return
        self._preview_extract()

    def _apply_extract_from_node(self, node: Any) -> None:
        self._preview_extract_from_node(node)

    def _preview_collapse(self, node: Any) -> None:
        self._close_preview_float()
        value = getattr(node, "value", None)
        instance_path = _collapse_instance_path(value)
        hierarchy_path = instance_path or _hierarchy_instance_path(value)
        if self._refactor_source_path and (self._refactor_source_explicit or self._refactor_destination_explicit):
            destination = self._refactor_destination_path or _parent_instance_path(self._refactor_source_path)
            self._request_hierarchy_pull_up(self._refactor_source_path, destination)
            return
        if hierarchy_path:
            self._mark_refactor_boundary(hierarchy_path)
        if instance_path:
            self._api.lsp.custom_request_to(
                "workspace/executeCommand",
                {
                    "command": "verilog/previewHierarchyBoundaryMove",
                    "arguments": [
                        {
                            "direction": "collapse",
                            "selection": {"kind": "instance", "instancePath": instance_path},
                        }
                    ],
                },
                cb=lambda result: self._on_collapse_preview(result, apply_edit=False),
                cmd_contains="veriforge-lsp",
            )
            return
        if not hierarchy_path:
            self._set_status("Selected node is not a hierarchy instance")
            return
        self._request_hierarchy_pull_up(hierarchy_path, _parent_instance_path(hierarchy_path))

    def _request_hierarchy_pull_up(
        self, source_path: str, destination_path: str = "", *, apply_edit: bool = False
    ) -> None:
        self._close_preview_float()
        if destination_path:
            self._refactor_destination_path = destination_path
            self._sync_title()
            self._rebuild_tree()
            self._refresh_if_visible()
        request: dict[str, Any] = {
            "direction": "pull_up",
            "selection": {"kind": "instance", "instancePath": source_path},
        }
        if destination_path:
            request["targetParentPath"] = destination_path
        self._api.lsp.custom_request_to(
            "workspace/executeCommand",
            {"command": "verilog/previewHierarchyBoundaryMove", "arguments": [request]},
            cb=lambda result: self._on_boundary_move_preview(result, apply_edit=apply_edit),
            cmd_contains="veriforge-lsp",
        )

    def _prompt_push_down(self, node: Any, *, apply_edit: bool) -> None:
        self._close_preview_float()
        value = getattr(node, "value", None) if node is not None else None
        module_name = _push_down_module_name(value)
        if not module_name:
            self._set_status("Selected node has no module to hier-down")
            return
        instance_path = ""
        if isinstance(value, dict):
            raw = value.get("instancePath") or value.get("path") or ""
            instance_path = str(raw) if raw else ""
        self._pending_push_down = {
            "moduleName": module_name,
            "instancePath": instance_path,
            "apply": apply_edit,
        }
        target_label = instance_path or module_name
        self._set_status(f"Hier-down for {target_label}: enter <module_name> [instance_name]")
        command = "VerilogHierDown"
        self._api.open_cmdline(f"{command} ")

    def prompt_push_down_range(
        self,
        line_range: tuple[int, int] | None,
        *,
        apply_edit: bool = False,
    ) -> None:
        """Stash a captured line range and open the cmdline pre-staged.

        Invoked by the buffer keymaps so the user can type the new module
        name (and optional instance name) into the cmdline while the
        captured selection survives leaving visual mode.
        """
        self._pending_push_down_range = line_range
        command = "VerilogHierDownRange"
        if line_range is not None:
            start_line, end_line = min(line_range), max(line_range)
            self._set_status(
                f"Hier-down selection for lines {start_line + 1}-{end_line + 1}: enter <module_name> [instance_name]"
            )
        else:
            self._set_status("Hier-down selection: enter <module_name> [instance_name]")
        self._api.open_cmdline(f"{command} ")

    def request_push_down_range_from_command(
        self,
        args: str,
        *,
        line_range: tuple[int, int] | None = None,
        apply_edit: bool = False,
    ) -> None:
        """Entry point for the range-based push-down ex-commands.

        Builds a unified boundary-move request with a ``kind=range``
        selection. The server routes range push-downs through the extract
        engine, so the response is handled by the extract preview path.

        If ``line_range`` is ``None`` and a previously-stashed range from
        :meth:`prompt_push_down_range` is available, that pending range is
        consumed (and cleared) so the keymap-then-cmdline flow keeps the
        captured selection across leaving visual mode.
        """
        parts = args.split()
        if not parts:
            self._pending_push_down_range = None
            self._set_status("Hier-down selection requires a module name")
            return
        new_module_name = parts[0]
        new_instance_name = parts[1] if len(parts) > 1 else ""
        if line_range is None:
            line_range = self._pending_push_down_range
        self._pending_push_down_range = None
        request = _active_range_push_down_request(
            self._api,
            line_range=line_range,
            new_module_name=new_module_name,
            new_instance_name=new_instance_name,
        )
        if request is None:
            self._set_status("No source buffer available for hier-down selection")
            return
        self._close_preview_float()
        self._api.lsp.custom_request_to(
            "workspace/executeCommand",
            {"command": "verilog/previewHierarchyBoundaryMove", "arguments": [request]},
            cb=lambda result: self._on_extract_preview(result, apply_edit=apply_edit),
            cmd_contains="veriforge-lsp",
        )

    def request_push_down_from_command(self, args: str, *, apply_edit: bool) -> None:
        """Entry point invoked by the registered ex-commands."""
        pending = self._pending_push_down
        self._pending_push_down = None
        if pending is None:
            self._set_status("No hier-down target marked; press 'w' on a hierarchy node first")
            return
        parts = args.split()
        if not parts:
            self._set_status("Hier-down requires a module name")
            return
        new_module_name = parts[0]
        new_instance_name = parts[1] if len(parts) > 1 else ""
        self._request_hierarchy_push_down(
            module_name=pending["moduleName"],
            instance_path=pending.get("instancePath", ""),
            new_module_name=new_module_name,
            new_instance_name=new_instance_name,
            apply_edit=apply_edit,
        )

    def _request_hierarchy_push_down(
        self,
        *,
        module_name: str,
        new_module_name: str,
        instance_path: str = "",
        new_instance_name: str = "",
        apply_edit: bool = False,
    ) -> None:
        if instance_path:
            selection: dict[str, Any] = {"kind": "instance", "instancePath": instance_path}
        else:
            selection = {"kind": "module", "moduleName": module_name}
        request: dict[str, Any] = {
            "direction": "push_down",
            "selection": selection,
            "newModuleName": new_module_name,
        }
        if new_instance_name:
            request["newInstanceName"] = new_instance_name
        self._api.lsp.custom_request_to(
            "workspace/executeCommand",
            {"command": "verilog/previewHierarchyBoundaryMove", "arguments": [request]},
            cb=lambda result: self._on_push_down_preview(result, apply_edit=apply_edit),
            cmd_contains="veriforge-lsp",
        )

    def _on_push_down_preview(self, result: dict | None, *, apply_edit: bool) -> None:
        if not isinstance(result, dict):
            self._set_status("No hier-down preview from verilog_lsp; restart the Verilog LSP server if it is stale")
            return
        preview = result.get("preview", {})
        edit = result.get("edit")
        source = preview.get("source", {}) if isinstance(preview, dict) else {}
        source_name = (
            source.get("moduleName")
            or source.get("instancePath")
            or (preview.get("afterHierarchy", {}).get("createdModule", "") if isinstance(preview, dict) else "")
        )
        if (
            result.get("ok")
            and isinstance(edit, dict)
            and self._open_refactor_review(
                result,
                title=f"Hier-down {source_name}".strip() or "Hier-down hierarchy",
                edit=edit,
                applied_status="Applied hier-down edit",
            )
        ):
            return
        if apply_edit and not isinstance(edit, dict):
            self._set_status("Hier-down edit was not available")
            return
        if isinstance(preview, dict) and preview.get("ok") and self._open_boundary_move_review(preview):
            return
        self._show_boundary_move_preview(preview if isinstance(preview, dict) else {})

    def _mark_refactor_source(self, node: Any) -> None:
        value = getattr(node, "value", None)
        path = _refactor_node_path(value)
        if not path:
            self._set_status("Selected node cannot be marked as a refactor source")
            return
        self._refactor_source_path = path
        self._refactor_source_explicit = True
        self._sync_title()
        self._rebuild_tree()
        self._refresh_if_visible()
        self._set_status(f"Refactor source: {path}")

    def _mark_refactor_destination(self, node: Any) -> None:
        value = getattr(node, "value", None)
        path = _refactor_node_path(value)
        if not path:
            self._set_status("Selected node cannot be marked as a refactor destination")
            return
        self._refactor_destination_path = path
        self._refactor_destination_explicit = True
        self._sync_title()
        self._rebuild_tree()
        self._refresh_if_visible()
        self._set_status(f"Refactor destination: {path}")

    def _mark_refactor_boundary(self, source_path: str) -> None:
        self._refactor_source_path = source_path
        self._refactor_destination_path = _parent_instance_path(source_path)
        self._refactor_source_explicit = False
        self._refactor_destination_explicit = False
        self._sync_title()
        self._rebuild_tree()
        self._refresh_if_visible()

    def _apply_collapse(self, node: Any) -> None:
        self._preview_collapse(node)

    def _on_collapse_preview(self, result: dict | None, *, apply_edit: bool) -> None:
        if not isinstance(result, dict):
            self._set_status("No collapse result from verilog_lsp")
            return
        preview = self._engine_payload(result)
        edit = result.get("edit")
        if (
            result.get("ok")
            and isinstance(edit, dict)
            and self._open_refactor_review(
                result,
                title=f"Hier-up {preview.get('instancePath', '')}"
                if isinstance(preview, dict)
                else "Hier-up hierarchy",
                edit=edit,
                applied_status="Applied hier-up edit",
            )
        ):
            return
        self._show_collapse_preview(preview if isinstance(preview, dict) else {})
        if not apply_edit:
            return
        if not result.get("ok") or not isinstance(edit, dict):
            self._set_status("Hier-up edit was not available")
            return
        apply_workspace_edit = getattr(self._api.lsp, "apply_workspace_edit", None)
        if not callable(apply_workspace_edit):
            self._set_status("WorkspaceEdit application is unavailable")
            return
        apply_workspace_edit(edit)
        self._set_status("Applied hier-up edit")

    def _on_boundary_move_preview(self, result: dict | None, *, apply_edit: bool = False) -> None:
        if not isinstance(result, dict):
            self._set_status(
                "No hierarchy boundary preview from verilog_lsp; restart the Verilog LSP server if it is stale"
            )
            return
        preview = result.get("preview", {})
        edit = result.get("edit")
        source = preview.get("source", {}) if isinstance(preview, dict) else {}
        source_path = source.get("instancePath", "") if isinstance(source, dict) else ""
        if (
            result.get("ok")
            and isinstance(edit, dict)
            and self._open_refactor_review(
                result,
                title=f"Hier-up {source_path}".strip() or "Hier-up hierarchy",
                edit=edit,
                applied_status="Applied hier-up edit",
            )
        ):
            return
        if apply_edit and not isinstance(edit, dict):
            self._set_status("Hier-up edit was not available")
            return
        if isinstance(preview, dict) and preview.get("ok") and self._open_boundary_move_review(preview):
            return
        self._show_boundary_move_preview(preview if isinstance(preview, dict) else {})

    def _preview_extract(self, line_range: tuple[int, int] | None = None) -> None:
        self._close_preview_float()
        request = _active_extract_request(self._api, line_range=line_range)
        if not request:
            self._set_status("No source buffer available for hier-down preview")
            return
        self._api.lsp.custom_request_to(
            "workspace/executeCommand",
            {"command": "verilog/previewHierarchyBoundaryMove", "arguments": [request]},
            cb=lambda result: self._on_extract_preview(result, apply_edit=False),
            cmd_contains="veriforge-lsp",
        )

    def _apply_extract(self, line_range: tuple[int, int] | None = None) -> None:
        self._preview_extract(line_range=line_range)

    def _preview_pull_up_selection(self, line_range: tuple[int, int] | None = None) -> None:
        self._close_preview_float()
        request = _active_pull_up_request(self._api, line_range=line_range)
        if not request:
            self._set_status("No source buffer available for hierarchy-up preview")
            return
        self._api.lsp.custom_request_to(
            "workspace/executeCommand",
            {"command": "verilog/previewHierarchyBoundaryMove", "arguments": [request]},
            cb=lambda result: self._on_boundary_move_preview(result, apply_edit=False),
            cmd_contains="veriforge-lsp",
        )

    def _apply_pull_up_selection(self, line_range: tuple[int, int] | None = None) -> None:
        self._preview_pull_up_selection(line_range=line_range)

    def _on_extract_preview(self, result: dict | None, *, apply_edit: bool) -> None:
        if not isinstance(result, dict):
            self._set_status("No hier-down response from verilog_lsp; restart the Verilog LSP server if it is stale")
            return
        preview = self._engine_payload(result)
        edit = result.get("edit")
        if (
            result.get("ok")
            and isinstance(edit, dict)
            and self._open_refactor_review(
                result,
                title=(
                    f"Hier-down {preview.get('extractedModuleName', '')}"
                    if isinstance(preview, dict)
                    else "Hier-down module"
                ),
                edit=edit,
                applied_status="Applied hier-down edit",
                post_apply_open_path=_first_created_review_file(result),
            )
        ):
            return
        self._show_extract_preview(preview if isinstance(preview, dict) else {})
        if not result.get("ok"):
            self._maybe_open_extract_suggestion_picker(
                preview if isinstance(preview, dict) else {},
                apply_edit=apply_edit,
            )
        if not apply_edit:
            return
        if not result.get("ok") or not isinstance(edit, dict):
            self._set_status("Hier-down edit was not available")
            return
        apply_workspace_edit = getattr(self._api.lsp, "apply_workspace_edit", None)
        if not callable(apply_workspace_edit):
            self._set_status("WorkspaceEdit application is unavailable")
            return
        apply_workspace_edit(edit)
        self._set_status("Applied hier-down edit")

    def _maybe_open_extract_suggestion_picker(self, preview: dict, *, apply_edit: bool) -> None:
        suggestions = _extract_selection_suggestions(preview)
        if not suggestions:
            return
        open_picker = getattr(getattr(self._api, "ui", None), "open_picker", None)
        if not callable(open_picker):
            return

        def _on_confirm(suggestion: ExtractSelectionSuggestion | None) -> None:
            if suggestion is None:
                return
            line_range = (suggestion.start_line - 1, suggestion.end_line - 1)
            if apply_edit:
                self._apply_extract(line_range=line_range)
            else:
                self._preview_extract(line_range=line_range)

        self._close_preview_float()
        open_picker(
            title="Verilog: expand hier-down selection",
            source=suggestions,
            on_confirm=_on_confirm,
        )

    def _open_refactor_review(
        self,
        result: dict,
        *,
        title: str,
        edit: dict,
        applied_status: str,
        post_apply_open_path: str | None = None,
    ) -> bool:
        review = result.get("review", {})
        files = review.get("files", []) if isinstance(review, dict) else []
        if not files:
            return False
        self._close_preview_float()

        def _confirm() -> None:
            apply_workspace_edit = getattr(self._api.lsp, "apply_workspace_edit", None)
            if not callable(apply_workspace_edit):
                self._set_status("WorkspaceEdit application is unavailable")
                return
            apply_workspace_edit(edit)
            if post_apply_open_path:
                with contextlib.suppress(Exception):
                    self._api.open_buffer(post_apply_open_path)
            self._set_status(applied_status)

        from peovim.plugins.proposed_review import (
            ProposedEditReview,
            open_proposed_edits,
            open_proposed_edits_with_initial,
        )

        reviews: list[ProposedEditReview] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            current_text = item.get("currentText")
            proposed_text = item.get("proposedText")
            if not isinstance(current_text, str) or not isinstance(proposed_text, str):
                continue
            reviews.append(
                ProposedEditReview(
                    title=title.strip(),
                    current_label=str(item.get("currentLabel") or "current"),
                    proposed_label=str(item.get("proposedLabel") or "proposed"),
                    current_text=current_text,
                    proposed_text=proposed_text,
                    filetype="verilog",
                    file_path=str(item.get("file") or ""),
                    on_confirm=_confirm,
                )
            )
        if not reviews:
            return False
        initial_review = _preferred_review_for_active_buffer(self._api, reviews)
        if initial_review is not None:
            open_proposed_edits_with_initial(self._api, reviews, initial_review=initial_review)
        else:
            open_proposed_edits(self._api, reviews)
        return True

    def _open_boundary_move_review(self, preview: dict) -> bool:
        current_text, proposed_text = _boundary_move_review_text(preview)
        if not current_text or not proposed_text:
            return False
        self._close_preview_float()
        direction = _display_direction(preview.get("direction", "boundary"))
        source = preview.get("source", {})
        source_path = source.get("instancePath", "") if isinstance(source, dict) else ""
        title = f"{direction} preview"
        if source_path:
            title = f"{title}: {source_path}"

        def _confirm_preview_only() -> None:
            self._set_status(f"{direction} preview is not apply-ready yet")

        from peovim.plugins.proposed_review import ProposedEditReview, open_proposed_edit

        open_proposed_edit(
            self._api,
            ProposedEditReview(
                title=title,
                current_label="Current hierarchy",
                proposed_label="Planned hierarchy (preview only)",
                current_text=current_text,
                proposed_text=proposed_text,
                filetype="text",
                on_confirm=_confirm_preview_only,
            ),
        )
        return True

    def _show_collapse_preview(self, preview: dict) -> None:
        self._close_preview_float()
        lines = _format_collapse_preview(preview)
        title = f"Hier-up {preview.get('instancePath', '')}".strip() or "Hier-up preview"
        handle = self._api.ui.open_float(
            lines,
            title=title,
            width=100,
            height=min(30, max(8, len(lines) + 2)),
            focusable=True,
        )
        if handle is not None:
            self._preview_float = handle

    def _show_extract_preview(self, preview: dict) -> None:
        self._close_preview_float()
        lines = _format_extract_preview(preview)
        title = f"Hier-down {preview.get('extractedModuleName', '')}".strip() or "Hier-down preview"
        handle = self._api.ui.open_float(
            lines,
            title=title,
            width=100,
            height=min(30, max(8, len(lines) + 2)),
            focusable=True,
        )
        if handle is not None:
            self._preview_float = handle

    def _show_boundary_move_preview(self, preview: dict) -> None:
        self._close_preview_float()
        lines = _format_boundary_move_preview(preview)
        direction = _display_direction(preview.get("direction", "boundary"))
        title = f"{direction} preview"
        handle = self._api.ui.open_float(
            lines,
            title=title,
            width=100,
            height=min(30, max(8, len(lines) + 2)),
            focusable=True,
        )
        if handle is not None:
            self._preview_float = handle

    def _open_candidate_picker(self) -> None:
        self._api.lsp.custom_request_to(
            "workspace/executeCommand",
            {"command": "verilog/hierarchyGraph", "arguments": [{"format": "json"}]},
            cb=lambda result: self._on_candidate_graph(result),
            cmd_contains="veriforge-lsp",
        )

    def _on_candidate_graph(self, result: dict | None) -> None:
        if not isinstance(result, dict) or not result.get("ok"):
            self._set_status("No hierarchy graph from verilog_lsp")
            return
        graph = result.get("hierarchyGraph", {})
        wrappers = graph.get("wrappers", []) if isinstance(graph, dict) else []
        candidates = [_wrapper_candidate(wrapper) for wrapper in wrappers if isinstance(wrapper, dict)]
        candidates = [candidate for candidate in candidates if candidate is not None]
        if not candidates:
            self._set_status("No wrapper candidates found")
            return

        def _preview(candidate: WrapperCandidate) -> list[str]:
            return _format_wrapper_candidate(candidate.node)

        def _on_confirm(candidate: WrapperCandidate | None) -> None:
            if candidate is None:
                return
            self._jump_to_value_instantiation(candidate.node)

        self._api.ui.open_picker(
            title="Verilog wrapper candidates",
            source=candidates,
            on_confirm=_on_confirm,
            preview=_preview,
            item_style=lambda candidate: (_wrapper_color(candidate.node),),
        )

    # ------------------------------------------------------------------
    # Tree helpers
    # ------------------------------------------------------------------

    def _rebuild_tree(self) -> None:
        nodes = self._build_nodes(self._roots)
        self._tree._title = self._title  # noqa: SLF001
        self._tree.set_nodes(nodes)

    def _build_nodes(self, roots: list[dict]) -> list[Any]:
        from peovim.ui.tree_view import TreeNode

        nodes: list[TreeNode] = []
        for root in roots:
            nodes.append(self._dict_to_node(root, is_root=True))
        return nodes

    def _dict_to_node(self, data: dict, is_root: bool = False) -> Any:
        from peovim.ui.tree_view import TreeNode

        name = data.get("instanceName") or data.get("name", "?")
        module_name = data.get("moduleName", "")
        label = f"{name}  [{module_name}]" if module_name and module_name != name else name
        badge = _wrapper_badge(data)
        if badge:
            label = f"{label}  <{badge}>"
        refactor_badge = _refactor_badge(data, self._refactor_source_path, self._refactor_destination_path)
        if refactor_badge:
            label = f"{label}  [{refactor_badge}]"

        children_data = data.get("children", [])
        has_more = data.get("hasMoreChildren", False)
        fg = _wrapper_color(data)

        if children_data or has_more:

            def _children_fn(d=data, m=module_name):
                if d.get("children"):
                    return [self._dict_to_node(c) for c in d["children"]]
                return self._lazy_load_children(m, d.get("instancePath", m))

            return TreeNode(
                label=label,
                value=data,
                fg=fg,
                children_fn=_children_fn,
                expanded=is_root,
            )
        return TreeNode(label=label, value=data, fg=fg)

    def _lazy_load_children(self, module_name: str, instance_path: str = "") -> list[Any]:
        from peovim.ui.tree_view import TreeNode

        self._api.lsp.custom_request_to(
            "workspace/executeCommand",
            {
                "command": "verilog/resolveHierarchyChildren",
                "arguments": [{"moduleName": module_name, "instancePath": instance_path or module_name}],
            },
            cb=lambda result: self._on_children_loaded(result),
            cmd_contains="veriforge-lsp",
        )
        return [TreeNode(label="Loading\u2026", value=None)]

    def _on_children_loaded(self, result: dict | None) -> None:
        if not result:
            return
        self._refresh_if_visible()

    def _refresh_if_visible(self) -> None:
        try:
            if self._api.ui.is_sidebar_visible(_PANEL_NAME):
                self._rebuild_tree()
                self._api.ui.show_sidebar_panel(_PANEL_NAME, self, focus=False)
        except Exception:
            pass

    def _jump_to_value_instantiation(self, value: dict) -> None:
        inst_file_uri = value.get("instanceFile", "")
        inst_rng = value.get("instanceRange", {})
        if not inst_file_uri:
            return
        start = inst_rng.get("start", {})
        self._api.goto_location(_uri_to_path(inst_file_uri), start.get("line", 0), start.get("character", 0))

    def _set_status(self, message: str) -> None:
        set_status = getattr(self._api, "set_status", None)
        if callable(set_status):
            set_status(message)


def _collapse_instance_path(value: dict | None) -> str:
    if not isinstance(value, dict):
        return ""
    actions = value.get("refactorActions", [])
    instance_path = value.get("instancePath", "")
    if "previewCollapse" not in actions or not instance_path:
        return ""
    return str(instance_path)


def _hierarchy_instance_path(value: dict | None) -> str:
    if not isinstance(value, dict):
        return ""
    instance_path = value.get("instancePath", "")
    if value.get("instanceName") and instance_path:
        return str(instance_path)
    return ""


def _refactor_node_path(value: dict | None) -> str:
    if not isinstance(value, dict):
        return ""
    instance_path = value.get("instancePath", "")
    if instance_path:
        return str(instance_path)
    name = value.get("name") or value.get("moduleName") or ""
    return str(name) if name else ""


def _push_down_module_name(value: dict | None) -> str:
    if not isinstance(value, dict):
        return ""
    name = value.get("moduleName") or value.get("name") or ""
    return str(name) if name else ""


def _parent_instance_path(instance_path: str) -> str:
    if "/" not in instance_path:
        return ""
    return instance_path.rsplit("/", 1)[0]


def _refactor_badge(value: dict | None, source_path: str, destination_path: str) -> str:
    path = _refactor_node_path(value)
    badges: list[str] = []
    if path and path == source_path:
        badges.append("SRC")
    if path and path == destination_path:
        badges.append("DST")
    return ",".join(badges)


def _active_extract_request(api: Any, line_range: tuple[int, int] | None = None) -> dict | None:
    buffer = api.active_buffer()
    path = getattr(buffer, "path", None)
    if not path:
        return None
    start_line, start_col, end_line, end_col = _active_extract_range(api, buffer, line_range=line_range)
    return {
        "direction": "extract",
        "textDocument": {"uri": _path_to_uri(path)},
        "range": {
            "start": {"line": start_line, "character": start_col},
            "end": {"line": end_line, "character": end_col},
        },
        "extractedModuleName": "extracted_logic",
    }


def _active_pull_up_request(api: Any, line_range: tuple[int, int] | None = None) -> dict | None:
    buffer = api.active_buffer()
    path = getattr(buffer, "path", None)
    if not path:
        return None
    start_line, start_col, end_line, end_col = _active_extract_range(api, buffer, line_range=line_range)
    return {
        "direction": "pull_up",
        "selection": {
            "kind": "range",
            "textDocument": {"uri": _path_to_uri(path)},
            "range": {
                "start": {"line": start_line, "character": start_col},
                "end": {"line": end_line, "character": end_col},
            },
        },
    }


def _active_range_push_down_request(
    api: Any,
    *,
    line_range: tuple[int, int] | None = None,
    new_module_name: str,
    new_instance_name: str = "",
) -> dict | None:
    buffer = api.active_buffer()
    path = getattr(buffer, "path", None)
    if not path:
        return None
    start_line, start_col, end_line, end_col = _active_extract_range(api, buffer, line_range=line_range)
    selection: dict[str, Any] = {
        "kind": "range",
        "textDocument": {"uri": _path_to_uri(path)},
        "range": {
            "start": {"line": start_line, "character": start_col},
            "end": {"line": end_line, "character": end_col},
        },
    }
    request: dict[str, Any] = {
        "direction": "push_down",
        "selection": selection,
        "newModuleName": new_module_name,
    }
    if new_instance_name:
        request["newInstanceName"] = new_instance_name
    return request


def _first_created_review_file(result: dict) -> str | None:
    review = result.get("review", {})
    files = review.get("files", []) if isinstance(review, dict) else []
    for item in files:
        if not isinstance(item, dict):
            continue
        current_text = item.get("currentText")
        file_path = item.get("file")
        if current_text == "" and isinstance(file_path, str) and file_path:
            return file_path
    return None


def _preferred_review_for_active_buffer(api: Any, reviews: list[Any]) -> Any | None:
    try:
        active_path = getattr(api.active_buffer(), "path", None)
    except Exception:
        active_path = None
    if not active_path:
        return None
    active_norm = os.path.normcase(os.path.normpath(str(active_path)))
    for review in reviews:
        review_path = getattr(review, "file_path", "")
        if review_path and os.path.normcase(os.path.normpath(review_path)) == active_norm:
            return review
    return None


def _active_extract_range(
    api: Any,
    buffer: Any,
    line_range: tuple[int, int] | None = None,
) -> tuple[int, int, int, int]:
    if line_range is not None:
        start_line = min(line_range)
        end_line = max(line_range)
        return start_line, 0, end_line, _line_length(buffer, end_line)

    engine = getattr(api, "_engine", None)
    mode_name = getattr(getattr(api, "active_mode", None), "value", getattr(api, "active_mode", ""))
    regions: list[tuple[int, int, int, int]] = []
    if mode_name in {"visual_char", "visual_line", "visual_block"} and engine is not None:
        with contextlib.suppress(Exception):
            regions = list(engine.visual_selection_regions())
    if regions:
        start_line = min(region[0] for region in regions)
        end_line = max(region[2] for region in regions)
        return start_line, 0, end_line, _line_length(buffer, end_line)

    line, _col = api.active_window().cursor
    return line, 0, line, _line_length(buffer, line)


def _line_length(buffer: Any, line: int) -> int:
    with contextlib.suppress(Exception):
        return len(buffer.get_line(line))
    return 0


def _path_to_uri(path: str | pathlib.Path) -> str:
    raw_path = pathlib.Path(path)
    try:
        return raw_path.resolve().as_uri()
    except ValueError:
        return "file://" + quote(str(raw_path).replace("\\", "/"))


def _wrapper_badge(data: dict) -> str:
    wrapper_class = data.get("wrapperClass", "")
    return _WRAPPER_BADGES.get(wrapper_class, "")


def _wrapper_color(data: dict) -> tuple[int, int, int] | None:
    wrapper_class = data.get("wrapperClass", "")
    return _WRAPPER_COLORS.get(wrapper_class)


def _wrapper_candidate(wrapper: dict) -> WrapperCandidate | None:
    instance_path = wrapper.get("instancePath", "")
    if not instance_path:
        return None
    wrapper_class = wrapper.get("wrapperClass", "unknown")
    confidence = wrapper.get("confidence", "")
    module_name = wrapper.get("moduleName", "")
    label = f"{instance_path}  [{module_name}]  <{wrapper_class}:{confidence}>"
    return WrapperCandidate(label=label, node=wrapper)


def _format_wrapper_candidate(wrapper: dict) -> list[str]:
    lines = [
        f"Instance: {wrapper.get('instancePath', '')}",
        f"Module:   {wrapper.get('moduleName', '')}",
        f"Class:    {wrapper.get('wrapperClass', '')}",
        f"Confidence: {wrapper.get('confidence', '')}",
    ]
    actions = wrapper.get("refactorActions", [])
    if actions:
        lines.append(f"Actions:  {', '.join(str(action) for action in actions)}")
    diagnostics = wrapper.get("diagnostics", [])
    if diagnostics:
        lines.append("")
        lines.append("Diagnostics:")
        lines.extend(_format_diagnostics(diagnostics))
    return lines


def _display_direction(direction: Any) -> str:
    value = str(direction or "boundary").replace("_", "-")
    if value in {"pull-up", "collapse"}:
        return "hier-up"
    if value in {"push-down", "extract"}:
        return "hier-down"
    return value


def _format_collapse_preview(preview: dict) -> list[str]:
    lines = [
        "Operation: hier-up",
        "Mode:      safe collapse",
        f"Instance:  {preview.get('instancePath', '')}",
        f"Status:    {'safe' if preview.get('ok') else 'blocked'}",
        f"Confidence: {preview.get('confidence', '')}",
    ]
    diagnostics = preview.get("diagnostics", [])
    if diagnostics:
        lines.append("")
        lines.append("Diagnostics:")
        lines.extend(_format_diagnostics(diagnostics))
    renames = preview.get("renames", [])
    if renames:
        lines.append("")
        lines.append("Renames:")
        for rename in renames:
            if isinstance(rename, dict):
                lines.append(f"  {rename.get('from', '')} -> {rename.get('to', '')}")
    diff = preview.get("diff", "")
    if diff:
        lines.append("")
        lines.append("Diff:")
        lines.extend(str(diff).splitlines())
    return lines


def _format_extract_preview(preview: dict) -> list[str]:
    lines = [
        "Operation: hier-down",
        f"Module:    {preview.get('moduleName', '')}",
        f"Target:    {preview.get('extractedModuleName', '')}",
        f"Status:    {'safe' if preview.get('ok') else 'blocked'}",
        f"Confidence: {preview.get('confidence', '')}",
    ]
    selection = preview.get("selection", {})
    if isinstance(selection, dict):
        lines.append(
            f"Selection: {selection.get('file', '')}:{selection.get('startLine', '')}-{selection.get('endLine', '')}"
        )
    presentation = _extract_presentation(preview)
    sections = presentation.get("sections", []) if isinstance(presentation, dict) else []
    if sections:
        lines.extend(_format_extract_presentation_sections(sections))
        return lines
    normalized = _extract_normalization(preview)
    if normalized:
        lines.append("")
        lines.append("Normalized selection:")
        items = normalized.get("items", [])
        if items:
            for item in items:
                if not isinstance(item, dict):
                    continue
                support = item.get("support", "")
                status = "supported" if item.get("supported") else f"unsupported:{support}"
                lines.append(f"  {item.get('kind', '')} {item.get('name', '')} ({status})")
        else:
            lines.append("  no complete semantic nodes")
    boundary = preview.get("boundary", {})
    if isinstance(boundary, dict):
        lines.append("")
        lines.append("Boundary:")
        for key in ("inputs", "outputs", "internals"):
            values = boundary.get(key, [])
            if values:
                lines.append(f"  {key}: {', '.join(str(value) for value in values)}")
    diagnostics = preview.get("diagnostics", [])
    if diagnostics:
        lines.append("")
        lines.append("Diagnostics:")
        lines.extend(_format_diagnostics(diagnostics))
    generated = preview.get("generatedModule", "")
    if generated:
        lines.append("")
        lines.append("Generated module:")
        lines.extend(str(generated).splitlines())
    diff = preview.get("diff", "")
    if diff:
        lines.append("")
        lines.append("Diff:")
        lines.extend(str(diff).splitlines())
    return lines


def _extract_presentation(preview: dict) -> dict:
    presentation = preview.get("presentation", {})
    return presentation if isinstance(presentation, dict) else {}


def _format_extract_presentation_sections(sections: Any) -> list[str]:
    lines: list[str] = []
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        text = str(section.get("text") or "")
        if not title and not text:
            continue
        lines.append("")
        if title:
            lines.append(f"{title}:")
        if text:
            lines.extend(text.splitlines())
    return lines


def _format_boundary_move_preview(preview: dict) -> list[str]:
    direction = _display_direction(preview.get("direction", "moveHierarchyBoundary"))
    lines = [
        f"Operation: {direction}",
        f"Direction: {direction}",
        f"Status:    {'preview' if preview.get('ok') else 'blocked'}",
        f"Confidence: {preview.get('confidence', '')}",
        f"Apply ready: {preview.get('applyReady', False)}",
    ]
    source = preview.get("source", {})
    if isinstance(source, dict) and source:
        lines.append("")
        lines.append("Source:")
        lines.extend(_format_endpoint(source))
    parent = preview.get("parent", {})
    if isinstance(parent, dict) and parent:
        lines.append("")
        lines.append("Parent:")
        lines.extend(_format_endpoint(parent))
    target = preview.get("target", {})
    if isinstance(target, dict) and target:
        lines.append("")
        lines.append("Target:")
        lines.extend(_format_endpoint(target))
    moved = preview.get("movedItems", {})
    if isinstance(moved, dict) and moved:
        lines.append("")
        lines.append("Moved items:")
        for key in ("ports", "parameters", "nets", "variables", "instances"):
            values = moved.get(key, [])
            if values:
                lines.append(f"  {key}: {', '.join(str(value) for value in values)}")
        for key in ("continuousAssignments", "alwaysBlocks", "initialBlocks"):
            value = moved.get(key, 0)
            if value:
                lines.append(f"  {key}: {value}")
    after = preview.get("afterHierarchy", {})
    if isinstance(after, dict) and after:
        lines.append("")
        lines.append("After hierarchy:")
        for key, value in after.items():
            lines.append(f"  {key}: {value}")
    diagnostics = preview.get("diagnostics", [])
    if diagnostics:
        lines.append("")
        lines.append("Diagnostics:")
        lines.extend(_format_diagnostics(diagnostics))
    if preview.get("ok") and not preview.get("applyReady"):
        lines.append("")
        lines.append("Preview only: generalized hierarchy rewrites are not apply-ready yet.")
    return lines


def _boundary_move_review_text(preview: dict) -> tuple[str, str]:
    before = preview.get("beforeHierarchy", {})
    after = preview.get("afterHierarchy", {})
    moved = preview.get("movedItems", {})
    if not any(isinstance(value, dict) and value for value in (before, after, moved)):
        return "", ""

    direction = _display_direction(preview.get("direction", "moveHierarchyBoundary"))
    current = [
        "Hierarchy boundary preview",
        f"Direction: {direction}",
        f"Status:    {'preview' if preview.get('ok') else 'blocked'}",
        f"Apply ready: {preview.get('applyReady', False)}",
    ]
    proposed = [
        "Hierarchy boundary preview",
        f"Direction: {direction}",
        "Status:    planned hierarchy",
        f"Apply ready: {preview.get('applyReady', False)}",
    ]

    source = preview.get("source", {})
    if isinstance(source, dict) and source:
        current.extend(["", "Source:"])
        current.extend(_format_endpoint(source))
        proposed.extend(["", "Moved source:"])
        proposed.extend(_format_endpoint(source))
    parent = preview.get("parent", {})
    if isinstance(parent, dict) and parent:
        current.extend(["", "Current parent:"])
        current.extend(_format_endpoint(parent))
    target = preview.get("target", {})
    if isinstance(target, dict) and target:
        proposed.extend(["", "Target:"])
        proposed.extend(_format_endpoint(target))

    if isinstance(before, dict) and before:
        current.extend(["", "Before hierarchy:"])
        current.extend(_format_key_values(before))
    if isinstance(after, dict) and after:
        proposed.extend(["", "After hierarchy:"])
        proposed.extend(_format_key_values(after))
    if isinstance(moved, dict) and moved:
        current.extend(["", "Items in selected source:"])
        current.extend(_format_moved_items(moved))
        proposed.extend(["", "Items moved across boundary:"])
        proposed.extend(_format_moved_items(moved))

    diagnostics = preview.get("diagnostics", [])
    if diagnostics:
        current.extend(["", "Diagnostics:"])
        current.extend(_format_diagnostics(diagnostics))
        proposed.extend(["", "Diagnostics:"])
        proposed.extend(_format_diagnostics(diagnostics))
    if preview.get("ok") and not preview.get("applyReady"):
        proposed.extend(["", "Preview only: generalized hierarchy rewrites are not apply-ready yet."])
    return "\n".join(current) + "\n", "\n".join(proposed) + "\n"


def _format_key_values(values: dict) -> list[str]:
    return [f"  {key}: {value}" for key, value in values.items()]


def _format_moved_items(moved: dict) -> list[str]:
    lines: list[str] = []
    for key in ("ports", "parameters", "nets", "variables", "instances"):
        values = moved.get(key, [])
        if values:
            lines.append(f"  {key}: {', '.join(str(value) for value in values)}")
    for key in ("continuousAssignments", "alwaysBlocks", "initialBlocks"):
        value = moved.get(key, 0)
        if value:
            lines.append(f"  {key}: {value}")
    return lines or ["  no movable items summarized"]


def _format_endpoint(endpoint: dict) -> list[str]:
    lines = [f"  module: {endpoint.get('moduleName', '')}"]
    if endpoint.get("instancePath"):
        lines.append(f"  path:   {endpoint.get('instancePath', '')}")
    if endpoint.get("instanceName"):
        lines.append(f"  inst:   {endpoint.get('instanceName', '')}")
    if endpoint.get("file"):
        lines.append(f"  file:   {endpoint.get('file', '')}")
    return lines


def _extract_normalization(preview: dict) -> dict:
    metadata = preview.get("metadata", {})
    if not isinstance(metadata, dict):
        return {}
    normalized = metadata.get("selectionNormalization", {})
    return normalized if isinstance(normalized, dict) else {}


def _extract_selection_suggestions(preview: dict) -> list[ExtractSelectionSuggestion]:
    normalization = _extract_normalization(preview)
    raw = normalization.get("suggestions", []) if isinstance(normalization, dict) else []
    if not isinstance(raw, list):
        return []
    suggestions: list[ExtractSelectionSuggestion] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            start_line = int(entry.get("startLine", 0))
            end_line = int(entry.get("endLine", 0))
        except (TypeError, ValueError):
            continue
        if start_line <= 0 or end_line < start_line:
            continue
        kind = str(entry.get("kind") or "expand")
        label = str(entry.get("label") or f"{kind} (lines {start_line}-{end_line})")
        suggestions.append(
            ExtractSelectionSuggestion(
                label=label,
                start_line=start_line,
                end_line=end_line,
                kind=kind,
            )
        )
    return suggestions


def _format_diagnostics(diagnostics: Any) -> list[str]:
    lines: list[str] = []
    for diagnostic in diagnostics or []:
        if not isinstance(diagnostic, dict):
            continue
        severity = diagnostic.get("severity", "info")
        code = diagnostic.get("code", "")
        message = diagnostic.get("message", "")
        lines.append(f"  [{severity}] {code}: {message}")
    return lines
