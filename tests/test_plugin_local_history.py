from __future__ import annotations

import json
from pathlib import Path

from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.parser import parse_ex_command
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine
from peovim.plugins import local_history as local_history_mod


def _make_api(tmp_path: Path) -> EditorAPI:
    path = tmp_path / "sample.py"
    path.write_text("alpha\nbeta\n", encoding="utf-8")
    doc = Document(path=path)
    doc.load(path)
    window = Window(doc)
    workspace = Workspace(window)
    registers = RegisterStore()
    editor_state = EditorState()
    command_registry = CommandRegistry()
    register_builtins(command_registry)
    engine = ModalEngine()
    engine.set_document(doc)
    dispatcher = ActionDispatcher(engine, window, registers, editor_state=editor_state)
    dispatcher._command_registry = command_registry
    return EditorAPI(workspace, engine, dispatcher, editor_state, command_registry)


def _replace_buffer_text(buf, text: str) -> None:
    line_count = buf.line_count()
    end_line = max(0, line_count - 1)
    end_col = len(buf.get_line(end_line)) if line_count > 0 else 0
    buf.replace(0, 0, end_line, end_col, text)


def _emit_saved(api: EditorAPI, buf=None) -> None:
    target = buf or api.active_buffer()
    api.events.emit("buffer_saved", buf_id=target.buf_id, path=str(target.path))


def test_buffer_saved_creates_snapshot_and_manifest(tmp_path) -> None:
    api = _make_api(tmp_path)
    api.options.set("local_history_root", str(tmp_path / "history"))
    local_history_mod.setup(api)

    _emit_saved(api)

    store = local_history_mod._controller._store
    items = store.items(api.active_buffer().path)
    assert len(items) == 1
    assert items[0].snapshot_path.exists()
    manifest = json.loads((items[0].snapshot_path.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["path"] == str(api.active_buffer().path.resolve())
    assert manifest["entries"][0]["snapshot"] == items[0].snapshot_path.name
    assert ":" in manifest["entries"][0]["timestamp"]
    assert "unknown age" not in str(items[0])


def test_history_prunes_oldest_entries_beyond_limit(tmp_path) -> None:
    api = _make_api(tmp_path)
    api.options.set("local_history_root", str(tmp_path / "history"))
    api.options.set("local_history_max_entries", 2)
    local_history_mod.setup(api)

    _emit_saved(api)
    _replace_buffer_text(api.active_buffer(), "bravo\ncharlie\n")
    _emit_saved(api)
    _replace_buffer_text(api.active_buffer(), "delta\necho\n")
    _emit_saved(api)

    items = local_history_mod._controller._store.items(api.active_buffer().path)
    assert len(items) == 2
    assert items[0].snapshot_path.read_text(encoding="utf-8") == "delta\necho\n"
    assert items[1].snapshot_path.read_text(encoding="utf-8") == "bravo\ncharlie\n"


def test_history_recovers_from_malformed_manifest(tmp_path) -> None:
    api = _make_api(tmp_path)
    api.options.set("local_history_root", str(tmp_path / "history"))
    local_history_mod.setup(api)

    store = local_history_mod._controller._store
    manifest_path = store._manifest_path(api.active_buffer().path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("{not json", encoding="utf-8")

    _emit_saved(api)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["entries"]) == 1


def test_history_skips_unnamed_buffers(tmp_path) -> None:
    api = _make_api(tmp_path)
    api.options.set("local_history_root", str(tmp_path / "history"))
    api.active_buffer()._doc.path = None
    local_history_mod.setup(api)

    api.events.emit("buffer_saved", buf_id=api.active_buffer().buf_id, path=None)

    root = Path(api.options.get("local_history_root"))
    assert not root.exists()


def test_history_skips_duplicate_identical_saves(tmp_path) -> None:
    api = _make_api(tmp_path)
    api.options.set("local_history_root", str(tmp_path / "history"))
    local_history_mod.setup(api)

    _emit_saved(api)
    _emit_saved(api)

    items = local_history_mod._controller._store.items(api.active_buffer().path)
    assert len(items) == 1


def test_history_picker_lists_newest_first(tmp_path, monkeypatch) -> None:
    api = _make_api(tmp_path)
    api.options.set("local_history_root", str(tmp_path / "history"))
    local_history_mod.setup(api)
    _emit_saved(api)
    _replace_buffer_text(api.active_buffer(), "bravo\ncharlie\n")
    _emit_saved(api)

    captured: dict[str, object] = {}

    def _capture_picker(title, source, **kwargs):
        captured["title"] = title
        captured["source"] = list(source)

    monkeypatch.setattr(api.ui, "open_picker", _capture_picker)

    local_history_mod._controller.show_history()

    assert captured["title"] == "Local History"
    items = captured["source"]
    assert len(items) == 2
    assert items[0].snapshot_path.read_text(encoding="utf-8") == "bravo\ncharlie\n"
    assert items[1].snapshot_path.read_text(encoding="utf-8") == "alpha\nbeta\n"


def test_history_open_command_opens_readonly_snapshot(tmp_path) -> None:
    api = _make_api(tmp_path)
    api.options.set("local_history_root", str(tmp_path / "history"))
    local_history_mod.setup(api)
    _emit_saved(api)

    handler = api._command_registry.get("HistoryOpen")
    assert handler is not None
    handler(parse_ex_command("HistoryOpen 1"), None)

    active = api.active_buffer()
    assert active.path is not None
    assert active.path.parent.name != tmp_path.name
    assert api.active_window().get_option("modifiable") is False
    assert api.active_window().get_option("readonly") is True


def test_history_restore_command_restores_text_into_current_buffer(tmp_path) -> None:
    api = _make_api(tmp_path)
    api.options.set("local_history_root", str(tmp_path / "history"))
    local_history_mod.setup(api)
    _emit_saved(api)

    _replace_buffer_text(api.active_buffer(), "changed\ntext\n")

    handler = api._command_registry.get("HistoryRestore")
    assert handler is not None
    handler(parse_ex_command("HistoryRestore 1"), None)

    buf = api.active_buffer()
    assert buf.get_text() == "alpha\nbeta\n"
    assert buf.is_modified() is True
