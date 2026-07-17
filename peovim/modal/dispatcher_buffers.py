from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from peovim.core.document import Document
from peovim.modal.actions import OpenBuffer, QuitEditor, SaveBuffer

if TYPE_CHECKING:
    from peovim.core.cursor import Cursor
    from peovim.modal.dispatcher import ActionDispatcher


def open_path_in_window(
    dispatcher: ActionDispatcher,
    path: str | Path,
    doc: Document,
    cur: Cursor,
    *,
    push_jumplist: bool,
) -> Document | None:
    resolved = Path(path).resolve()
    if not resolved.exists():
        return None
    if not resolved.is_file():
        dispatcher._set_message(f"Cannot open non-file path: {resolved}")
        return None

    current_path = doc.path.resolve() if doc.path is not None else None
    if push_jumplist and dispatcher.jumplist is not None:
        dispatcher.jumplist.push(
            cur.line,
            cur.col,
            str(doc.path) if doc.path else "",
            dispatcher.window.scroll_line,
        )

    if doc.path is not None and dispatcher._editor_state is not None:
        dispatcher._editor_state.alt_path = str(doc.path)
        dispatcher._editor_state.alt_cursor = (cur.line, cur.col)

    if current_path == resolved:
        target_doc = doc
    else:
        target_doc = None
        if dispatcher._workspace is not None:
            target_doc = dispatcher._workspace.find_document_by_path(resolved)
        if target_doc is None:
            target_doc = Document(path=resolved)
            try:
                target_doc.load(resolved)
            except OSError as exc:
                dispatcher._set_message(f"Could not open {resolved}: {exc}")
                return None
            if dispatcher._workspace is not None:
                dispatcher._workspace.add_document(target_doc)

    assert target_doc is not None
    dispatcher.window.document = target_doc
    dispatcher.window.scroll_line = 0
    dispatcher.window.scroll_col = 0
    dispatcher.window.options["fileformat"] = target_doc.fileformat

    if dispatcher._editor_state is not None and target_doc.had_mixed_line_endings:
        display_path = str(resolved)
        dispatcher._editor_state.message = (
            f"Mixed line endings detected: {display_path} (saving will normalize to {target_doc.fileformat})"
        )

    from peovim.core.filetype import detect_filetype

    filetype = detect_filetype(str(resolved))
    dispatcher._emit_later("buffer_opened", buf_id=id(target_doc), path=str(resolved), filetype=filetype)
    return target_doc


def handle_buffer_action(dispatcher: ActionDispatcher, action: object, doc: Document, cur: Cursor) -> bool:
    if isinstance(action, SaveBuffer):
        target_path = action.path or (str(doc.path) if doc.path else None)
        writing_current_path = action.path is None or (
            doc.path is not None and Path(action.path).resolve() == doc.path.resolve()
        )
        if target_path is not None:
            dispatcher._run_public_event_callbacks("buffer_pre_save", buf_id=dispatcher._buf_id, path=target_path)
        if writing_current_path and doc.has_external_changes() and not action.force:
            conflict_path = str(doc.path) if doc.path is not None else target_path
            dispatcher._set_message(
                f"E139: File changed on disk since last load: {conflict_path} (use :e! to reload or :w! to overwrite)"
            )
            return True
        if action.path:
            warn = doc.save(Path(action.path))
            if warn:
                dispatcher._set_message(warn)
            dispatcher._emit_later("buffer_saved", buf_id=dispatcher._buf_id, path=action.path)
        elif doc.path:
            warn = doc.save()
            if warn:
                dispatcher._set_message(warn)
            dispatcher._emit_later("buffer_saved", buf_id=dispatcher._buf_id, path=str(doc.path))
        else:
            dispatcher._set_message("E32: No file name")
        return True

    if isinstance(action, QuitEditor):
        if action.force:
            dispatcher.quit_requested = True
        elif doc.dirty:
            dispatcher._set_message("E37: No write since last change (add ! to override)")
        elif dispatcher._workspace is not None:
            dirty_docs = [d for d in dispatcher._workspace.documents if d.dirty]
            if dirty_docs:
                first = dirty_docs[0]
                dispatcher.window.document = first
                dispatcher.window.scroll_line = 0
                dispatcher.window.scroll_col = 0
                dispatcher.window.cursor.move_to(0, 0)
                name = first.path.name if first.path else "[No Name]"
                remaining = len(dirty_docs)
                dispatcher._set_message(
                    f"E37: No write since last change for '{name}'"
                    + (f" ({remaining - 1} more)" if remaining > 1 else "")
                    + " (add ! to override)"
                )
            else:
                dispatcher.quit_requested = True
        else:
            dispatcher.quit_requested = True
        return True

    if isinstance(action, OpenBuffer):
        target_doc = open_path_in_window(dispatcher, action.path, doc, cur, push_jumplist=True)
        if target_doc is not None:
            cur.move_to(0, 0)
        return True

    return False
