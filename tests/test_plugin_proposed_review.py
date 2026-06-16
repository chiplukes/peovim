"""Tests for proposed edit review mode."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import VSplitNode, Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine
from peovim.plugins import proposed_review
from peovim.plugins.proposed_review import ProposedEditReview


def _make_real_api(active_path: Path) -> EditorAPI:
    doc = Document(path=active_path)
    doc.load(active_path)
    window = Window(doc)
    workspace = Workspace(window)
    registers = RegisterStore()
    editor_state = EditorState()
    command_registry = CommandRegistry()
    register_builtins(command_registry)
    engine = ModalEngine()
    engine.set_document(doc)
    dispatcher = ActionDispatcher(
        engine,
        window,
        registers,
        editor_state=editor_state,
        workspace=workspace,
    )
    dispatcher._command_registry = command_registry
    return EditorAPI(workspace, engine, dispatcher, editor_state, command_registry)


class TestProposedReview:
    def test_setup_registers_review_commands_and_keymaps(self):
        api = MagicMock()

        proposed_review.setup(api)

        commands = [call.args[0] for call in api.commands.register.call_args_list]
        keys = [call.args[0] for call in api.keymap.nmap.call_args_list]
        assert "ProposedReviewAccept" in commands
        assert "ProposedReviewCancel" in commands
        assert "ProposedReviewFiles" in commands
        assert "<leader>ra" in keys
        assert "<leader>rq" in keys
        assert "<leader>rf" in keys

    def test_open_review_uses_scratch_side_by_side_diff(self, tmp_path):
        source = tmp_path / "top.v"
        source.write_text("module top;\nassign y = a;\nendmodule\n", encoding="utf-8")
        api = _make_real_api(source)
        proposed_review.setup(api)

        assert proposed_review._controller is not None
        proposed_review._controller.open_review(
            ProposedEditReview(
                title="Collapse top/u_wrap",
                current_label="current top.v",
                proposed_label="proposed top.v",
                current_text="module top;\nassign y = a;\nendmodule\n",
                proposed_text="module top;\nassign y = b;\nendmodule\n",
                filetype="verilog",
            )
        )

        tab = api._workspace.active_tab
        assert isinstance(tab.root, VSplitNode)
        windows = tab.all_windows()
        assert len(windows) == 2
        assert {window.document.path for window in windows} == {None}
        assert {window.document.filetype for window in windows} == {"verilog"}
        summary = proposed_review._controller.session_summary()
        assert summary == {
            "title": "Collapse top/u_wrap",
            "current": "current top.v",
            "proposed": "proposed top.v",
            "blocks": 1,
        }
        left_doc = windows[0].document
        right_doc = windows[1].document
        assert api._editor_state.decorations.get_for_namespace(id(left_doc), "proposed_review")
        assert api._editor_state.decorations.get_for_namespace(id(right_doc), "proposed_review")
        assert api._editor_state.compare_status["left"] == "current top.v"
        assert api._editor_state.compare_status["right"] == "proposed top.v"

    def test_confirm_restores_original_file_and_runs_callback(self, tmp_path):
        source = tmp_path / "top.v"
        source.write_text("module top;\nendmodule\n", encoding="utf-8")
        api = _make_real_api(source)
        proposed_review.setup(api)
        callback = MagicMock()

        assert proposed_review._controller is not None
        proposed_review._controller.open_review(
            ProposedEditReview(
                title="Extract logic",
                current_label="current top.v",
                proposed_label="proposed top.v",
                current_text="module top;\nendmodule\n",
                proposed_text="module top;\nwire x;\nendmodule\n",
                on_confirm=callback,
            )
        )
        proposed_review._controller.confirm()

        callback.assert_called_once()
        assert api.window_count() == 1
        assert api.active_buffer().path == source.resolve()
        assert proposed_review._controller.session_summary() is None

    def test_cancel_restores_original_file_without_callback(self, tmp_path):
        source = tmp_path / "top.v"
        source.write_text("module top;\nendmodule\n", encoding="utf-8")
        api = _make_real_api(source)
        proposed_review.setup(api)
        callback = MagicMock()

        assert proposed_review._controller is not None
        proposed_review._controller.open_review(
            ProposedEditReview(
                title="Extract logic",
                current_label="current top.v",
                proposed_label="proposed top.v",
                current_text="module top;\nendmodule\n",
                proposed_text="module top;\nwire x;\nendmodule\n",
                on_confirm=callback,
            )
        )
        proposed_review._controller.cancel()

        callback.assert_not_called()
        assert api.window_count() == 1
        assert api.active_buffer().path == source.resolve()

    def test_open_reviews_uses_picker_for_multiple_files(self, tmp_path):
        source = tmp_path / "top.v"
        source.write_text("module top;\nendmodule\n", encoding="utf-8")
        api = _make_real_api(source)
        api.ui.open_picker = MagicMock()
        proposed_review.setup(api)
        reviews = [
            ProposedEditReview(
                title="Refactor",
                current_label="current top.v",
                proposed_label="proposed top.v",
                current_text="module top;\nendmodule\n",
                proposed_text="module top;\nwire x;\nendmodule\n",
            ),
            ProposedEditReview(
                title="Refactor",
                current_label="current child.v",
                proposed_label="proposed child.v",
                current_text="module child;\nendmodule\n",
                proposed_text="module child;\nwire y;\nendmodule\n",
            ),
        ]

        assert proposed_review._controller is not None
        proposed_review._controller.open_reviews(reviews)

        api.ui.open_picker.assert_called_once()
        kwargs = api.ui.open_picker.call_args.kwargs
        assert kwargs["title"] == "Proposed edit files (2)"
        assert kwargs["source"] == reviews
        assert "proposed child.v" in str(kwargs["source"][1])

    def test_multifile_picker_selection_opens_selected_review(self, tmp_path):
        source = tmp_path / "top.v"
        source.write_text("module top;\nendmodule\n", encoding="utf-8")
        api = _make_real_api(source)
        selected_callbacks = []

        def _open_picker(*_args, **kwargs):
            selected_callbacks.append(kwargs["on_confirm"])

        api.ui.open_picker = MagicMock(side_effect=_open_picker)
        proposed_review.setup(api)
        reviews = [
            ProposedEditReview(
                title="Refactor",
                current_label="current top.v",
                proposed_label="proposed top.v",
                current_text="module top;\nendmodule\n",
                proposed_text="module top;\nwire x;\nendmodule\n",
            ),
            ProposedEditReview(
                title="Refactor",
                current_label="current child.v",
                proposed_label="proposed child.v",
                current_text="module child;\nendmodule\n",
                proposed_text="module child;\nwire y;\nendmodule\n",
            ),
        ]

        assert proposed_review._controller is not None
        proposed_review._controller.open_reviews(reviews)
        selected_callbacks[0](reviews[1])

        summary = proposed_review._controller.session_summary()
        assert summary is not None
        assert summary["current"] == "current child.v"
        assert summary["proposed"] == "proposed child.v"
