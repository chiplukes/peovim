from __future__ import annotations

from pathlib import Path

from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.jumplist import JumpList
from peovim.core.marks import MarkStore
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.actions import JumpBack, JumpForward, JumpToMark, SetMark
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine


def _session(content: str = "", *, path: Path | None = None):
    doc = Document(path=path)
    doc.load_string(content)
    if path is not None:
        doc.path = path
    window = Window(doc)
    marks = MarkStore()
    jumplist = JumpList()
    editor_state = EditorState()
    engine = ModalEngine()
    engine.set_document(doc)
    engine.set_cursor(0, 0)
    engine.set_line_count(doc.line_count())
    workspace = Workspace(window)
    dispatcher = ActionDispatcher(
        engine,
        window,
        RegisterStore(),
        marks=marks,
        jumplist=jumplist,
        editor_state=editor_state,
        workspace=workspace,
    )
    return doc, window, dispatcher, marks, jumplist, editor_state, workspace


def test_set_mark_and_jump_to_exact_position() -> None:
    _doc, window, dispatcher, _marks, _jumplist, _editor_state, _workspace = _session("alpha\nbeta\n")
    window.cursor.move_to(1, 2)

    dispatcher.dispatch([SetMark("a")])

    window.cursor.move_to(0, 0)
    dispatcher.dispatch([JumpToMark("a")])

    assert window.cursor.line == 1
    assert window.cursor.col == 2


def test_jump_to_mark_line_only_moves_to_first_non_blank() -> None:
    _doc, window, dispatcher, _marks, _jumplist, _editor_state, _workspace = _session("  alpha\nbeta\n")
    window.cursor.move_to(0, 6)

    dispatcher.dispatch([SetMark("a")])

    window.cursor.move_to(1, 0)
    dispatcher.dispatch([JumpToMark("a", line_only=True)])

    assert window.cursor.line == 0
    assert window.cursor.col == 2


def test_jumplist_back_and_forward_restore_positions() -> None:
    _doc, window, dispatcher, _marks, jumplist, _editor_state, _workspace = _session("a\nb\nc\n")
    jumplist.push(0, 0)
    jumplist.push(2, 0)

    dispatcher.dispatch([JumpBack()])

    assert window.cursor.line == 0
    assert window.cursor.col == 0

    dispatcher.dispatch([JumpForward()])

    assert window.cursor.line == 2
    assert window.cursor.col == 0


def test_jumplist_cross_file_reopens_buffer_and_emits_buffer_opened(tmp_path: Path) -> None:
    first_path = tmp_path / "first.py"
    second_path = tmp_path / "second.py"
    first_path.write_text("first\nline\n", encoding="utf-8")
    second_path.write_text("second\nline\n", encoding="utf-8")

    doc, window, dispatcher, _marks, jumplist, editor_state, workspace = _session("second\nline\n", path=second_path)
    opened: list[str] = []
    editor_state.event_bus.on("buffer_opened", lambda **kwargs: opened.append(kwargs["path"]))

    jumplist.push(1, 1, str(first_path))
    jumplist.push(0, 0, str(second_path))

    dispatcher.dispatch([JumpBack()])

    assert doc.path == second_path
    assert window.document is not doc
    assert window.document.path == first_path.resolve()
    assert window.document.get_line(0) == "first"
    assert window.cursor.line == 1
    assert window.cursor.col == 1
    assert workspace.find_document_by_path(second_path) is doc
    assert workspace.find_document_by_path(first_path) is window.document
    assert opened == [str(first_path.resolve())]

    dispatcher.dispatch([JumpForward()])

    assert window.document is doc
    assert doc.path == second_path
    assert doc.get_line(0) == "second"
    assert window.cursor.line == 0
    assert window.cursor.col == 0
    assert opened == [str(first_path.resolve()), str(second_path.resolve())]


def test_jumplist_cross_file_reuses_existing_unsaved_document_and_restores_cursor(tmp_path: Path) -> None:
    first_path = tmp_path / "first.py"
    second_path = tmp_path / "second.py"
    first_path.write_text("import os\n", encoding="utf-8")
    second_path.write_text("from os import path\n", encoding="utf-8")

    doc, window, dispatcher, _marks, jumplist, _editor_state, workspace = _session("import os\n", path=first_path)
    doc.insert(1, 0, "unsaved_value = 1\n")
    window.cursor.move_to(1, 8)

    target_doc = workspace.find_document_by_path(second_path)
    assert target_doc is None

    from peovim.modal.actions import OpenBuffer

    dispatcher.dispatch([OpenBuffer(str(second_path))])
    jumplist.push(0, 5, str(second_path))

    dispatcher.dispatch([JumpBack()])

    assert window.document is doc
    assert doc.get_text() == "import os\nunsaved_value = 1\n"
    assert window.cursor.line == 1
    assert window.cursor.col == 8


def test_jumplist_directory_target_keeps_current_buffer_and_cursor(tmp_path: Path) -> None:
    first_path = tmp_path / "first.py"
    first_path.write_text("first\nline\n", encoding="utf-8")
    target_dir = tmp_path / "pkg"
    target_dir.mkdir()

    doc, window, dispatcher, _marks, jumplist, editor_state, workspace = _session("first\nline\n", path=first_path)
    window.cursor.move_to(1, 2)
    jumplist.push(0, 0, str(target_dir))
    jumplist.push(1, 2, str(first_path))

    dispatcher.dispatch([JumpBack()])

    assert window.document is doc
    assert window.document.path == first_path
    assert window.cursor.line == 1
    assert window.cursor.col == 2
    assert workspace.find_document_by_path(target_dir) is None
    assert editor_state.message == f"Cannot open non-file path: {target_dir.resolve()}"
