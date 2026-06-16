from __future__ import annotations

from pathlib import Path

from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.jumplist import JumpList
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.actions import OpenBuffer, QuitEditor, SaveBuffer
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine


def _session(content: str = "", *, path: Path | None = None):
    doc = Document(path=path)
    doc.load_string(content)
    if path is not None:
        doc.path = path
    window = Window(doc)
    editor_state = EditorState()
    jumplist = JumpList()
    engine = ModalEngine()
    engine.set_document(doc)
    engine.set_cursor(0, 0)
    engine.set_line_count(doc.line_count())
    workspace = Workspace(window)
    dispatcher = ActionDispatcher(
        engine,
        window,
        RegisterStore(),
        jumplist=jumplist,
        editor_state=editor_state,
        workspace=workspace,
    )
    return doc, window, dispatcher, jumplist, editor_state, workspace


def test_save_buffer_without_path_sets_message() -> None:
    _doc, _window, dispatcher, _jumplist, editor_state, _workspace = _session("hello\n")

    dispatcher.dispatch([SaveBuffer()])

    assert "No file name" in editor_state.message


def test_save_buffer_with_explicit_path_writes_file_and_emits_event(tmp_path: Path) -> None:
    doc, _window, dispatcher, _jumplist, editor_state, _workspace = _session("hello\n")
    target = tmp_path / "saved.txt"
    saved: list[str] = []
    editor_state.event_bus.on("buffer_saved", lambda **kwargs: saved.append(kwargs["path"]))

    dispatcher.dispatch([SaveBuffer(path=str(target))])

    assert target.read_text(encoding="utf-8") == "hello\n"
    assert doc.path == target
    assert saved == [str(target)]


def test_save_buffer_emits_pre_save_before_saved(tmp_path: Path) -> None:
    _doc, _window, dispatcher, _jumplist, editor_state, _workspace = _session("hello\n")
    target = tmp_path / "saved.txt"
    events: list[tuple[str, str | None]] = []
    editor_state.event_bus.on("buffer_pre_save", lambda **kwargs: events.append(("pre", kwargs.get("path"))))
    editor_state.event_bus.on("buffer_saved", lambda **kwargs: events.append(("saved", kwargs.get("path"))))

    dispatcher.dispatch([SaveBuffer(path=str(target))])

    assert events == [("pre", str(target)), ("saved", str(target))]


def test_save_buffer_blocks_when_file_changed_on_disk_without_force(tmp_path: Path) -> None:
    target = tmp_path / "saved.txt"
    target.write_text("original\n", encoding="utf-8")
    doc, _window, dispatcher, _jumplist, editor_state, _workspace = _session("original\n", path=target)
    doc.load(target)
    doc.insert(0, len("original"), " local")
    # Use a different-length string so the (mtime, size) fingerprint differs even
    # when the filesystem mtime tick is coarser than the time between the two writes.
    external_content = "externally modified\n"
    target.write_text(external_content, encoding="utf-8")
    saved: list[str] = []
    editor_state.event_bus.on("buffer_saved", lambda **kwargs: saved.append(kwargs["path"]))

    dispatcher.dispatch([SaveBuffer()])

    assert target.read_text(encoding="utf-8") == external_content
    assert saved == []
    assert editor_state.message.startswith("E139: File changed on disk since last load:")
    assert ":e! to reload" in editor_state.message
    assert ":w! to overwrite" in editor_state.message


def test_save_buffer_force_overwrites_file_changed_on_disk(tmp_path: Path) -> None:
    target = tmp_path / "saved.txt"
    target.write_text("original\n", encoding="utf-8")
    doc, _window, dispatcher, _jumplist, editor_state, _workspace = _session("original\n", path=target)
    doc.load(target)
    doc.insert(0, len("original"), " local")
    target.write_text("external\n", encoding="utf-8")

    dispatcher.dispatch([SaveBuffer(force=True)])

    assert target.read_text(encoding="utf-8") == "original local\n"
    assert doc.has_external_changes() is False


def test_pasting_lf_text_into_dos_buffer_saves_as_crlf(tmp_path: Path) -> None:
    doc, _window, dispatcher, _jumplist, _editor_state, _workspace = _session("alpha\n")
    target = tmp_path / "saved.txt"
    doc.set_fileformat("dos")

    doc.insert(0, len("alpha"), "\nbeta\ngamma")
    dispatcher.dispatch([SaveBuffer(path=str(target))])

    assert "\r" not in doc.get_text()
    assert doc.get_text().splitlines() == ["alpha", "beta", "gamma"]
    saved = target.read_bytes()
    assert b"\r\n" in saved
    assert saved.replace(b"\r\n", b"") == b"alphabetagamma"
    assert b"\n" not in saved.replace(b"\r\n", b"")


def test_open_buffer_reloads_document_updates_alt_path_and_emits_event(tmp_path: Path) -> None:
    original_path = tmp_path / "original.txt"
    target_path = tmp_path / "target.txt"
    original_path.write_text("original\n", encoding="utf-8")
    target_path.write_text("target\nline\n", encoding="utf-8")
    doc, window, dispatcher, jumplist, editor_state, workspace = _session("original\n", path=original_path)
    opened: list[str] = []
    editor_state.event_bus.on("buffer_opened", lambda **kwargs: opened.append(kwargs["path"]))
    window.cursor.move_to(0, 3)

    dispatcher.dispatch([OpenBuffer(str(target_path))])

    assert doc.path == original_path
    assert doc.get_line(0) == "original"
    assert window.document is not doc
    assert window.document.path == target_path.resolve()
    assert window.document.get_line(0) == "target"
    assert window.cursor.line == 0
    assert window.cursor.col == 0
    assert window.scroll_line == 0
    assert editor_state.alt_path == str(original_path)
    assert editor_state.alt_cursor == (0, 3)
    assert jumplist.current() == (str(original_path), 0, 3, 0)
    assert workspace.find_document_by_path(original_path) is doc
    assert workspace.find_document_by_path(target_path) is window.document
    assert opened == [str(target_path.resolve())]


def test_open_buffer_rejects_directory_target_and_keeps_current_document(tmp_path: Path) -> None:
    original_path = tmp_path / "original.txt"
    original_path.write_text("original\n", encoding="utf-8")
    target_dir = tmp_path / "nested"
    target_dir.mkdir()
    doc, window, dispatcher, _jumplist, editor_state, workspace = _session("original\n", path=original_path)

    dispatcher.dispatch([OpenBuffer(str(target_dir))])

    assert window.document is doc
    assert doc.path == original_path
    assert workspace.find_document_by_path(target_dir) is None
    assert editor_state.message == f"Cannot open non-file path: {target_dir.resolve()}"


def test_open_buffer_warns_when_file_has_mixed_line_endings(tmp_path: Path) -> None:
    original_path = tmp_path / "original.txt"
    target_path = tmp_path / "mixed.txt"
    original_path.write_text("original\n", encoding="utf-8")
    target_path.write_bytes(b"one\r\ntwo\nthree\r\n")
    _doc, window, dispatcher, _jumplist, editor_state, _workspace = _session("original\n", path=original_path)

    dispatcher.dispatch([OpenBuffer(str(target_path))])

    assert window.document.had_mixed_line_endings is True
    assert window.options["fileformat"] == "dos"
    assert "Mixed line endings detected" in editor_state.message
    assert "normalize to dos" in editor_state.message


def test_open_buffer_reuses_existing_live_document_without_losing_unsaved_changes(tmp_path: Path) -> None:
    original_path = tmp_path / "original.py"
    target_path = tmp_path / "target.py"
    original_path.write_text("import os\n", encoding="utf-8")
    target_path.write_text("from os import path\n", encoding="utf-8")
    doc, window, dispatcher, jumplist, _editor_state, workspace = _session("import os\n", path=original_path)

    doc.insert(1, 0, "unsaved_change = True\n")
    window.cursor.move_to(1, 7)

    dispatcher.dispatch([OpenBuffer(str(target_path))])
    jumplist.push(0, 5, str(target_path))
    dispatcher.dispatch([OpenBuffer(str(original_path))])

    assert workspace.find_document_by_path(original_path) is doc
    assert window.document is doc
    assert doc.get_text() == "import os\nunsaved_change = True\n"
    assert window.cursor.line == 0
    assert window.cursor.col == 0


def test_quit_dirty_buffer_is_blocked_without_force() -> None:
    doc, _window, dispatcher, _jumplist, editor_state, _workspace = _session("hello\n")
    doc.insert(0, 5, "!")

    dispatcher.dispatch([QuitEditor(force=False)])

    assert dispatcher.quit_requested is False
    assert "No write since last change" in editor_state.message


def test_quit_clean_buffer_or_forced_quit_succeeds() -> None:
    _doc, _window, dispatcher, _jumplist, _editor_state, _workspace = _session("hello\n")

    dispatcher.dispatch([QuitEditor()])

    assert dispatcher.quit_requested is True

    doc2, _window2, dispatcher2, _jumplist2, _editor_state2, _workspace2 = _session("hello\n")
    doc2.insert(0, 5, "!")

    dispatcher2.dispatch([QuitEditor(force=True)])

    assert dispatcher2.quit_requested is True
