"""LSP-oriented UI helpers extracted from `EventLoop`."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from peovim.lsp.protocol import uri_to_path

if TYPE_CHECKING:
    from peovim.ui.event_loop import EventLoop


class LspUiAdapter:
    """Owns hover, picker, workspace-edit, rename, completion, and signature-help UI helpers."""

    def __init__(self, host: EventLoop) -> None:
        self._host = host

    def show_hover_float(self, text: str, title: str = "Hover") -> None:
        host = self._host
        if host._float_manager is None:
            if host._editor_state is not None:
                host._editor_state.message = text[:120]
                host._invalidate_message()
            return

        if host._float_manager.has_focused:
            host._float_manager.close_all()

        from peovim.ui.float_manager import CursorRelative
        from peovim.ui.markdown import render_rich_markdown

        theme = host._resolve_frame_theme()
        lines = render_rich_markdown(text, theme=theme)
        if host._grid is not None:
            cols, rows = host._grid.width, host._grid.height
        else:
            cols, rows = 80, 24
        max_content_w = max(
            len(line_text) if isinstance(line_text, str) else sum(len(part) for part, _style in line_text)
            for line_text in lines
        )
        width = min(cols - 4, max(40, max_content_w + 4))
        max_height = max(5, rows * 2 // 5)
        height = min(max_height, len(lines) + 2)
        anchor = CursorRelative(row_offset=1, col_offset=0)

        def _on_key(key: str) -> bool:
            if key != "y":
                return False
            self.yank_popup_text(lines)
            return True

        handle = host._float_manager.open_float(
            content=lines,
            width=width,
            height=height,
            border=True,
            title=title,
            anchor=anchor,
            focusable=True,
            on_key=_on_key,
        )
        host._float_manager.focus(handle)
        host._invalidate("full")

    def yank_popup_text(self, lines: list[object]) -> None:
        host = self._host
        text = "\n".join(
            line if isinstance(line, str) else "".join(part for part, _style in line) for line in lines
        ).rstrip()
        if not text:
            return
        dispatcher = host._dispatcher
        dispatcher.registers.set('"', text, "char")
        dispatcher.registers.set("0", text, "char")
        if host._editor_state is not None:
            clipboard = host._editor_state.options.get("clipboard") or ""
            if "unnamedplus" in clipboard:
                dispatcher.registers.set("+", text, "char")
            elif "unnamed" in clipboard:
                dispatcher.registers.set("*", text, "char")
            host._editor_state.message = "Yanked hover text"
            host._invalidate_message()

    def goto_location(self, loc: dict) -> None:
        from peovim.modal.actions import OpenBuffer

        host = self._host
        path = loc.get("path", "")
        line = loc.get("line", 0)
        col = loc.get("col", 0)
        if not path:
            return
        host._dispatcher.dispatch([OpenBuffer(path)])
        win = host._workspace.active_window
        win.cursor.move_to(line, col)
        win.scroll_to_cursor()
        jumplist = getattr(host._dispatcher, "jumplist", None)
        if jumplist is not None:
            jumplist.push(max(0, line), max(0, col), str(Path(path).resolve()), win.scroll_line)
        host._invalidate("full")

    def show_picker_for_locations(
        self, items: list[str], locs: list[dict], title: str = "", *, preview: bool = True
    ) -> None:
        host = self._host
        if host._picker is None:
            return

        lookup = dict(zip(items, locs, strict=False))

        def _on_confirm(item: str | None) -> None:
            if item is None:
                return
            loc = lookup.get(item)
            if loc is not None:
                self.goto_location(loc)

        preview_fn = None
        if preview:
            preview_fn = lambda item: _loc_preview(lookup.get(item, {}))  # noqa: E731

        host._picker.open(title or "Locations", items, on_confirm=_on_confirm, preview=preview_fn)
        host._invalidate("full")

    def show_picker_for_code_actions(self, feats: object, actions: list[dict]) -> None:
        host = self._host
        if host._picker is None:
            return
        titles = [action.get("title", "") for action in actions if action.get("title")]
        action_lookup = {action["title"]: action for action in actions if action.get("title")}

        def _on_confirm(item: str | None) -> None:
            if item is None:
                return
            action = action_lookup.get(item)
            if action is not None:
                self.apply_code_action(feats, action)

        host._picker.open("Code Actions", titles, on_confirm=_on_confirm)
        host._invalidate("full")

    def apply_code_action(self, feats: object, action: dict) -> None:
        host = self._host
        applied = False
        edit = action.get("edit")
        if isinstance(edit, dict):
            self.apply_workspace_edit(edit)
            applied = True

        command = action.get("command")
        if isinstance(command, str) and command:
            arguments = action.get("arguments")

            def _on_result(result: dict | None) -> None:
                if isinstance(result, dict):
                    if "edit" in result and isinstance(result["edit"], dict):
                        self.apply_workspace_edit(result["edit"])
                    elif "changes" in result or "documentChanges" in result:
                        self.apply_workspace_edit(result)
                if host._editor_state is not None:
                    host._editor_state.message = f"Applied code action: {action.get('title', command)}"
                    host._invalidate_message()

            execute = getattr(feats, "execute_command", None)
            if callable(execute):
                execute(command, arguments if isinstance(arguments, list) else [], _on_result)
                return

        if applied and host._editor_state is not None:
            host._editor_state.message = f"Applied code action: {action.get('title', '')}"
            host._invalidate_message()

    def apply_workspace_edit(self, edit: dict) -> None:
        host = self._host
        targets = self.workspace_edit_targets(edit)
        changed_open_docs: set[int] = set()
        for path, edits in targets.items():
            document, should_save = self.document_for_edit_path(path)
            self.apply_text_edits(document, edits)
            if should_save:
                document.save(Path(path))
            else:
                changed_open_docs.add(id(document))
        if host._editor_state is not None:
            for buf_id in changed_open_docs:
                host._editor_state.event_bus.emit("buffer_changed", buf_id=buf_id)
        if targets:
            host._invalidate("full")

    def workspace_edit_targets(self, edit: dict) -> dict[str, list[dict]]:
        targets: dict[str, list[dict]] = {}
        changes = edit.get("changes", {})
        if isinstance(changes, dict):
            for uri, edits in changes.items():
                path = uri_to_path(uri)
                if path:
                    targets.setdefault(path, []).extend(edits or [])
        document_changes = edit.get("documentChanges", [])
        if isinstance(document_changes, list):
            for change in document_changes:
                if not isinstance(change, dict):
                    continue
                text_document = change.get("textDocument", {})
                uri = text_document.get("uri", "")
                edits = change.get("edits", [])
                path = uri_to_path(uri)
                if path:
                    targets.setdefault(path, []).extend(edits or [])
        return targets

    def document_for_edit_path(self, path: str):
        from peovim.core.document import Document

        target = Path(path).resolve()
        for tab in self._host._workspace.tabs:
            for win in tab.all_windows():
                doc = win.document
                if doc.path is not None and doc.path.resolve() == target:
                    return doc, False
        find_document = getattr(self._host._workspace, "find_document_by_path", None)
        if callable(find_document):
            existing = find_document(target)
            if existing is not None:
                return existing, False
        document = Document(path=target)
        if target.exists():
            document.load(target)
        else:
            document.load_string("")
            document.path = target
        return document, True

    @staticmethod
    def apply_text_edits(document: object, edits: list[dict]) -> None:
        ordered = sorted(
            [edit for edit in edits if isinstance(edit, dict)],
            key=lambda edit: (
                edit.get("range", {}).get("start", {}).get("line", 0),
                edit.get("range", {}).get("start", {}).get("character", 0),
                edit.get("range", {}).get("end", {}).get("line", 0),
                edit.get("range", {}).get("end", {}).get("character", 0),
            ),
            reverse=True,
        )
        for edit in ordered:
            range_info = edit.get("range", {})
            start = range_info.get("start", {})
            end = range_info.get("end", {})
            new_text = edit.get("newText", "")
            document.replace(
                start.get("line", 0),
                start.get("character", 0),
                end.get("line", 0),
                end.get("character", 0),
                new_text,
            )

    def prompt_rename(self, feats: object, path: str, line: int, col: int) -> None:
        host = self._host
        if host._editor_state is not None:
            host._editor_state.message = "Rename: (type new name in : prompt)"
            host._invalidate_message()
        host._cmdline.enter("Rename: ", "")
        host._invalidate_cmdline()
        host._pending_rename = (feats, path, line, col)

    def show_completion(self, items: list[dict]) -> None:
        host = self._host
        if host._completion_popup is None:
            return
        win = host._workspace.active_window
        host._completion_popup.open(items, win.cursor.line, win.cursor.col)
        host._invalidate("full")

    def show_signature_help(self, text: str | None) -> None:
        host = self._host
        if host._float_manager is None:
            if text and host._editor_state is not None:
                host._editor_state.message = text.splitlines()[0][:120]
                host._invalidate_message()
            return
        if not text:
            self.dismiss_signature_help()
            return
        lines = text.splitlines()
        width = max(24, min(80, max(len(line) for line in lines) + 2))
        height = min(max(3, len(lines) + 2), 8)

        from peovim.ui.float_manager import CursorRelative

        self.dismiss_signature_help()
        host._signature_help_handle = host._float_manager.open_float(
            content=lines,
            width=width,
            height=height,
            border=True,
            title="Signature",
            anchor=CursorRelative(row_offset=1, col_offset=0),
            focusable=False,
            z_order=10,
        )
        host._invalidate("full")

    def dismiss_signature_help(self) -> None:
        host = self._host
        handle = host._signature_help_handle
        host._signature_help_handle = None
        if handle is not None and hasattr(handle, "close"):
            handle.close()
            host._invalidate("full")


def _loc_preview(loc: dict) -> list:
    """Return a syntax-highlighted preview for a location dict with path/line keys."""
    from peovim.plugins.picker import _preview_location  # type: ignore[attr-defined]

    path = loc.get("path", "")
    line = loc.get("line", 0)
    if not path:
        return []
    return _preview_location(path, line)
