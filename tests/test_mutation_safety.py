from __future__ import annotations

import asyncio

import pytest

from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.actions import InsertText, RunNormalKeys
from peovim.modal.dispatcher import ActionDispatcher, ReentrancyError
from peovim.modal.engine import ModalEngine


def _make_api(content: str = "hello\n") -> EditorAPI:
    doc = Document()
    doc.load_string(content)
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


def test_buffer_mutation_inside_buffer_changed_handler_raises() -> None:
    api = _make_api("hello\n")
    buf = api.active_buffer()
    errors: list[str] = []

    def on_change(**_kwargs) -> None:
        try:
            buf.insert(0, 0, "X")
        except ReentrancyError as exc:
            errors.append(str(exc))

    api.events.on("buffer_changed", on_change)

    api._dispatcher.dispatch([InsertText(0, 5, "!")])

    assert buf.get_line(0) == "hello!"
    assert len(errors) == 1
    assert "buffer_changed" in errors[0]
    assert "editor.defer()" in errors[0]


def test_batched_buffer_mutation_inside_buffer_changed_handler_still_raises() -> None:
    api = _make_api("hello\n")
    buf = api.active_buffer()
    errors: list[str] = []

    def on_change(**_kwargs) -> None:
        try:
            with buf.batch():
                buf.insert(0, 0, "X")
                buf.insert(0, 1, "Y")
        except ReentrancyError as exc:
            errors.append(str(exc))

    api.events.on("buffer_changed", on_change)

    api._dispatcher.dispatch([InsertText(0, 5, "!")])

    assert buf.get_line(0) == "hello!"
    assert len(errors) == 1
    assert "buffer_changed" in errors[0]


@pytest.mark.asyncio
async def test_deferred_mutation_from_buffer_changed_handler_succeeds() -> None:
    api = _make_api("hello\n")
    buf = api.active_buffer()
    scheduled = False

    def on_change(**_kwargs) -> None:
        nonlocal scheduled
        if scheduled:
            return
        scheduled = True
        api.defer(lambda: buf.insert(0, 0, "X"), 0)

    api.events.on("buffer_changed", on_change)

    api._dispatcher.dispatch([InsertText(0, 5, "!")])
    await asyncio.sleep(0)

    assert buf.get_line(0) == "Xhello!"


def test_plugin_keymap_callback_can_still_mutate_buffer() -> None:
    api = _make_api("hello\n")
    buf = api.active_buffer()
    called: list[bool] = []

    def mutate_buffer() -> None:
        called.append(True)
        buf.insert(0, 0, "X")

    callback_id = 99
    api._dispatcher._plugin_callbacks[callback_id] = mutate_buffer

    from peovim.modal.actions import RunPlugin

    api._dispatcher.dispatch([RunPlugin(callback_id)])

    assert called == [True]
    assert buf.get_line(0) == "Xhello"


def test_plugin_keymap_callback_policy_remains_explicitly_allowed() -> None:
    api = _make_api("hello\n")

    assert api._dispatcher._public_mutation_guard_stack == []
    assert api._dispatcher._current_plugin_callback is None


def test_plugin_keymap_callback_runs_inside_explicit_mutation_allowance() -> None:
    api = _make_api("hello\n")
    observed: list[bool] = []

    def inspect_policy() -> None:
        observed.append(api._dispatcher.allows_public_mutation())

    callback_id = 101
    api._dispatcher._plugin_callbacks[callback_id] = inspect_policy

    from peovim.modal.actions import RunPlugin

    api._dispatcher.dispatch([RunPlugin(callback_id)])

    assert observed == [True]


def test_align_ex_command_can_batch_mutations_during_dispatch() -> None:
    from peovim.plugins import align as align_mod

    api = _make_api("a = 1\nlong_name = 2\n")
    align_mod.setup(api)
    align_mod._pending_visual_range = (0, 1)
    align_mod._pending_buf_id = api.active_buffer().buf_id

    api._dispatcher.dispatch([RunNormalKeys(":AlignChar =<CR>")])

    lines = [line for line in api.active_buffer().get_lines() if "=" in line]
    positions = [line.index("=") for line in lines]
    assert positions == [positions[0], positions[0]]
    assert api._editor_state.message == ""
