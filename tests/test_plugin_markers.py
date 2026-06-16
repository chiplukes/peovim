"""Tests for the marker groups plugin."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.parser import parse_ex_command
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.jumplist import JumpList
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.actions import InsertNewline
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine
from peovim.plugins.markers import MarkerStore, _context_lines, _MarkersController
from peovim.ui.decorations import GhostText


def _make_api(tmp_path, monkeypatch) -> EditorAPI:
    from peovim.core import store_api as store_mod

    monkeypatch.setattr(store_mod, "_get_data_dir", lambda: tmp_path)

    doc = Document()
    doc.load_string("alpha\nbeta\ngamma\n")
    doc.path = tmp_path / "sample.py"
    doc.path.write_text(doc.get_line(0) + "\n" + doc.get_line(1) + "\n" + doc.get_line(2) + "\n", encoding="utf-8")
    window = Window(doc)
    workspace = Workspace(window)
    registers = RegisterStore()
    editor_state = EditorState()
    command_registry = CommandRegistry()
    register_builtins(command_registry)
    engine = ModalEngine()
    engine.set_document(doc)
    dispatcher = ActionDispatcher(engine, window, registers, jumplist=JumpList(), editor_state=editor_state)
    dispatcher._command_registry = command_registry
    api = EditorAPI(workspace, engine, dispatcher, editor_state, command_registry)
    api._event_loop = SimpleNamespace(
        _cmdline=SimpleNamespace(enter=lambda *args, **kwargs: None), _invalidate_cmdline=lambda: None
    )
    return api


class TestMarkerStore:
    def test_defaults_to_single_default_group(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)

        store = MarkerStore(api)

        assert store.active_group() == "default"
        assert store.group_names() == ["default"]

    def test_uses_project_local_markers_file_when_root_exists(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        api = _make_api(project, monkeypatch)
        api.active_buffer()._doc.path = project / "sample.py"

        store = MarkerStore(api)

        assert store.storage_path() == project / ".peovim" / "markers.json"

    def test_falls_back_to_global_store_without_project_root(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        api.active_buffer()._doc.path = tmp_path / "sample.py"

        store = MarkerStore(api)

        assert store.storage_path() == tmp_path / "stores" / "markers.json"

    def test_add_and_delete_marker(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        api = _make_api(project, monkeypatch)
        store = MarkerStore(api)

        store.add_marker("default", str(project / "sample.py"), 1, 2)

        assert store.markers("default")[0]["line"] == 1
        assert store.delete_marker("default", str(project / "sample.py"), 1, 2)
        assert store.markers("default") == []

    def test_project_local_marker_store_writes_valid_json(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        api = _make_api(project, monkeypatch)
        api.active_buffer()._doc.path = project / "sample.py"
        store = MarkerStore(api)

        store.add_marker("default", str(project / "sample.py"), 1, 2, "note")

        data = json.loads((project / ".peovim" / "markers.json").read_text(encoding="utf-8"))
        assert data["groups"]["default"][0]["annotation"] == "note"

    def test_add_marker_preserves_annotation_when_text_not_provided(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        store = MarkerStore(api)

        store.add_marker("default", str(api.active_buffer().path), 1, 0, "todo")
        store.add_marker("default", str(api.active_buffer().path), 1, 4)

        assert store.markers("default")[0]["annotation"] == "todo"

    def test_group_create_rename_delete(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        store = MarkerStore(api)

        assert store.create_group("todo") is True
        assert store.active_group() == "todo"
        assert store.rename_group("todo", "later") is True
        assert store.delete_group("later") is True
        assert store.group_names() == ["default"]

    def test_apply_text_change_moves_marker_with_inserted_newline(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        store = MarkerStore(api)
        store.add_marker("default", str(api.active_buffer().path), 1, 2, "todo")

        changed = store.apply_text_change(
            api.active_buffer().path,
            start_line=0,
            start_col=0,
            end_line=0,
            end_col=0,
            new_text="top\n",
        )

        assert changed is True
        marker = store.markers("default")[0]
        assert marker["line"] == 2
        assert marker["col"] == 2
        assert marker["annotation"] == "todo"

    def test_apply_text_change_moves_marker_with_same_line_delete(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        store = MarkerStore(api)
        store.add_marker("default", str(api.active_buffer().path), 1, 3)

        changed = store.apply_text_change(
            api.active_buffer().path,
            start_line=1,
            start_col=1,
            end_line=1,
            end_col=2,
            new_text="",
        )

        assert changed is True
        marker = store.markers("default")[0]
        assert marker["line"] == 1
        assert marker["col"] == 2


class TestMarkersController:
    def test_add_marker_uses_active_cursor_location(self, tmp_path, monkeypatch):
        from peovim.core.style import Style

        api = _make_api(tmp_path, monkeypatch)
        api.register_sign_type("markers.bookmark", "●", Style(fg=(220, 180, 90)))
        controller = _MarkersController(api)
        api.active_window().set_cursor(1, 0)

        controller.add_marker()

        markers = controller.store.markers("default")
        assert len(markers) == 1
        assert markers[0]["line"] == 1
        signs = api._editor_state.decorations.get_for_namespace(api.active_buffer().buf_id, "markers")
        assert len(signs) == 1

    def test_delete_marker_removes_current_location(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        controller.store.add_marker("default", str(api.active_buffer().path), 1, 0)
        controller.refresh_signs_for_open_buffers()
        api.active_window().set_cursor(1, 0)

        controller.delete_marker()

        assert controller.store.markers("default") == []

    def test_next_and_prev_marker_jump_across_saved_locations(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        other = tmp_path / "zeta.py"
        other.write_text("one\ntwo\n", encoding="utf-8")
        controller.store.add_marker("default", str(api.active_buffer().path), 1, 0)
        controller.store.add_marker("default", str(other), 0, 1)
        api.active_window().set_cursor(0, 0)

        controller.next_marker()

        assert api.active_buffer().path == tmp_path / "sample.py"
        assert api.active_window().cursor == (1, 0)

        controller.next_marker()

        assert api.active_buffer().path == other
        assert api.active_window().cursor == (0, 1)

        controller.prev_marker()

        assert api.active_buffer().path == tmp_path / "sample.py"
        assert api.active_window().cursor == (1, 0)

    def test_prompt_create_group_opens_cmdline(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        prompts = []
        monkeypatch.setattr(api, "open_cmdline", lambda initial="", prompt=":": prompts.append((prompt, initial)))

        controller.prompt_create_group()

        assert prompts == [(":", "MarkerGroupCreate ")]

    def test_prompt_marker_text_prefills_existing_annotation(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        controller.store.add_marker("default", str(api.active_buffer().path), 1, 0, "todo")
        api.active_window().set_cursor(1, 0)
        prompts = []
        monkeypatch.setattr(api, "open_cmdline", lambda initial="", prompt=":": prompts.append((prompt, initial)))

        controller.prompt_marker_text()

        assert prompts == [(":", "MarkerText todo")]

    def test_panel_g_jumps_to_selected_marker(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        controller.store.add_marker("default", str(api.active_buffer().path), 1, 0)
        panel = controller._get_panel()
        panel.refresh()
        panel.tree.select_value(("marker", str(api.active_buffer().path.resolve()), 1, 0))
        api.active_window().set_cursor(0, 0)

        panel.feed_key("g")

        assert api.active_window().cursor == (1, 0)

    def test_panel_e_targets_selected_marker_annotation(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        controller.store.add_marker("default", str(api.active_buffer().path), 1, 0, "todo")
        panel = controller._get_panel()
        panel.refresh()
        panel.tree.select_value(("marker", str(api.active_buffer().path.resolve()), 1, 0))
        prompts = []
        monkeypatch.setattr(api, "open_cmdline", lambda initial="", prompt=":": prompts.append((prompt, initial)))
        api.active_window().set_cursor(0, 0)

        panel.feed_key("e")
        controller.command_marker_text(parse_ex_command("MarkerText revisit"))

        assert prompts == [(":", "MarkerText todo")]
        assert controller.store.markers("default")[0]["annotation"] == "revisit"

    def test_cursor_move_same_file_scrolls_editor_without_blurring(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        panel = controller._get_panel()
        path = str(api.active_buffer().path)
        set_cursor_calls: list[tuple[int, int]] = []
        scroll_calls: list[bool] = []
        fake_win = SimpleNamespace(
            cursor=(0, 0),
            set_cursor=lambda ln, c: set_cursor_calls.append((ln, c)),
            scroll_to_cursor=lambda: scroll_calls.append(True),
        )
        monkeypatch.setattr(api, "active_window", lambda: fake_win)
        node = SimpleNamespace(value=("marker", path, 3, 0))

        panel._on_cursor_move(node)

        assert set_cursor_calls == [(3, 0)]
        assert scroll_calls == [True]

    def test_cursor_move_cross_file_opens_buffer_without_blurring(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        panel = controller._get_panel()
        opened: list[tuple] = []
        blurred: list[bool] = []
        monkeypatch.setattr(api, "open_buffer", lambda path, line=0, col=0: opened.append((Path(path), line, col)))
        monkeypatch.setattr(api.ui, "blur_sidebar", lambda: blurred.append(True))
        node = SimpleNamespace(value=("marker", "/other/file.py", 5, 2))

        panel._on_cursor_move(node)

        assert opened == [(Path("/other/file.py"), 5, 2)]
        assert blurred == []

    def test_cursor_move_on_group_node_is_ignored(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        panel = controller._get_panel()
        opened: list = []
        monkeypatch.setattr(api, "open_buffer", lambda *a, **k: opened.append(a))
        node = SimpleNamespace(value=("group", "default"))

        panel._on_cursor_move(node)

        assert opened == []

    def test_prompt_select_group_opens_picker(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        controller.store.create_group("todo")
        opened = {}
        monkeypatch.setattr(
            api.ui,
            "open_picker",
            lambda title, items, **kwargs: opened.update({"title": title, "items": items, **kwargs}),
        )

        controller.prompt_select_group()

        assert opened["title"] == "Marker Groups"
        assert any(str(item).strip().endswith("todo (0)") for item in opened["items"])
        todo_item = next(item for item in opened["items"] if item.name == "todo")
        opened["on_confirm"](todo_item)
        assert controller.store.active_group() == "todo"

    def test_command_group_create_rename_delete(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)

        controller.command_group_create(parse_ex_command("MarkerGroupCreate todo"))
        controller.command_group_rename(parse_ex_command("MarkerGroupRename later"))
        controller.command_group_delete(parse_ex_command("MarkerGroupDelete later"))

        assert controller.store.group_names() == ["default"]

    def test_command_marker_text_sets_annotation_and_shows_in_sidebar_node(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        api.active_window().set_cursor(1, 0)

        controller.command_marker_text(parse_ex_command("MarkerText revisit"))

        marker = controller.store.markers("default")[0]
        assert marker["annotation"] == "revisit"
        nodes = controller.build_nodes()
        marker_nodes = nodes[0].get_children()
        assert "revisit" in marker_nodes[0].label

    def test_annotation_shows_as_ghost_text_on_active_marker_line(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        controller.store.add_marker("default", str(api.active_buffer().path), 1, 0, "todo")
        api.active_window().set_cursor(1, 0)

        controller.refresh_annotation_ghost_text()

        decs = api._editor_state.decorations.get_for_namespace(api.active_buffer().buf_id, "markers.annotation")
        assert len(decs) == 1
        ghost = decs[0]
        assert isinstance(ghost, GhostText)
        assert ghost.line == 1
        assert ghost.text.endswith("todo")

    def test_annotation_ghost_text_clears_when_cursor_moves_off_marker(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        controller.store.add_marker("default", str(api.active_buffer().path), 1, 0, "todo")
        api.active_window().set_cursor(1, 0)
        controller.refresh_annotation_ghost_text()

        api.active_window().set_cursor(0, 0)
        controller.refresh_annotation_ghost_text()

        decs = api._editor_state.decorations.get_for_namespace(api.active_buffer().buf_id, "markers.annotation")
        assert decs == []

    def test_text_changed_event_moves_marker_and_refreshes_ghost_text(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        controller.store.add_marker("default", str(api.active_buffer().path), 1, 1, "todo")
        api.active_window().set_cursor(1, 1)
        controller.refresh_annotation_ghost_text()

        controller.on_text_changed(
            path=str(api.active_buffer().path),
            start_line=0,
            start_col=0,
            end_line=0,
            end_col=0,
            new_text="top\n",
        )

        marker = controller.store.markers("default")[0]
        assert marker["line"] == 2
        ghost = api._editor_state.decorations.get_for_namespace(api.active_buffer().buf_id, "markers.annotation")
        assert ghost == []

    def test_insert_newline_action_moves_later_marker(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        from peovim.plugins import markers as markers_mod

        markers_mod.setup(api)
        controller = markers_mod._controller
        assert controller is not None
        controller.store.add_marker("default", str(api.active_buffer().path), 0, 0)
        controller.store.add_marker("default", str(api.active_buffer().path), 2, 0)

        api._dispatcher.dispatch([InsertNewline(1, 0)])

        markers = controller.store.markers("default")
        assert markers[0]["line"] == 0
        assert markers[1]["line"] == 3

    def test_toggle_panel_shows_sidebar(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)

        controller.toggle_panel()

        assert api.ui.is_sidebar_visible("markers")

    def test_build_nodes_include_group_and_context(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        controller = _MarkersController(api)
        controller.store.add_marker("default", str(api.active_buffer().path), 1, 0)

        nodes = controller.build_nodes()

        assert nodes[0].label.startswith("* default")
        marker_nodes = nodes[0].get_children()
        assert marker_nodes[0].label.startswith("sample.py:2:1")
        context = marker_nodes[0].get_children()
        assert any(line.label.startswith(">2:") or line.label.startswith("> 2:") for line in context)


class TestMarkerHelpers:
    def test_context_lines_show_surrounding_lines(self, tmp_path):
        path = tmp_path / "ctx.py"
        path.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

        lines = _context_lines(path, 2, radius=2)

        assert len(lines) == 5
        assert any(line.startswith(">3:") or line.startswith("> 3:") for line in lines)


class TestMarkersPluginSetup:
    def test_setup_registers_commands(self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        from peovim.plugins import markers as markers_mod

        markers_mod.setup(api)

        assert api.commands._registry.get("Markers") is not None
        assert api.commands._registry.get("MarkerText") is not None
        assert api.commands._registry.get("MarkerGroupCreate") is not None
        assert api.commands._registry.get("MarkerGroupRename") is not None
        assert api.commands._registry.get("MarkerGroupDelete") is not None
