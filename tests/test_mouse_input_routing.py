"""Mouse focus changes must leave the next key with the intended input owner."""

from types import SimpleNamespace

import pytest

from peovim.api.editor import EditorAPI
from peovim.commands.builtin import register_builtins
from peovim.commands.registry import CommandRegistry
from peovim.core.document import Document
from peovim.core.editor_state import ConfirmSubState, EditorState
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine, Mode
from peovim.plugins import flash
from peovim.ui.backend import KeyEvent, MouseEvent
from peovim.ui.backends.headless import HeadlessBackend
from peovim.ui.event_loop import EventLoop
from peovim.ui.layout import Rect
from peovim.ui.sidebar import TreeSidebarPanel
from peovim.ui.target_chooser import ChooserTarget, TargetChooser
from peovim.ui.tree_view import TreeNode, TreeView


class _Backend(HeadlessBackend):
    events = ()

    async def read_events(self):
        for event in self.events:
            yield KeyEvent(event) if isinstance(event, str) else event


@pytest.fixture
def editor():
    doc = Document()
    doc.load_string("alpha initial omega\nsecond line\nthird line\n")
    win = Window(doc, width=60, height=20)
    workspace = Workspace(win)
    state = EditorState()
    engine = ModalEngine()
    engine.set_document(doc)
    engine.set_context_provider(lambda: workspace.active_window)
    commands = CommandRegistry()
    register_builtins(commands)
    dispatcher = ActionDispatcher(engine, win, RegisterStore(), workspace=workspace, editor_state=state)
    dispatcher.set_command_registry(commands)
    backend = _Backend(cols=80, rows=24)
    loop = EventLoop(backend, engine, dispatcher, workspace, editor_state=state)
    api = EditorAPI(workspace, engine, dispatcher, state, commands)
    api.attach_event_loop(loop)
    loop.attach_ui(api.ui, api.binding_registry)
    loop._running = True
    loop._current_layout = {workspace.active_tab.all_leaves()[0]: Rect(20, 0, 60, 20)}

    async def send(*events):
        backend.events = events
        await loop._input_loop()

    async def click(*, row=0, col=25):
        await send(MouseEvent(row, col, 0, True), MouseEvent(row, col, 0, False))

    yield SimpleNamespace(api=api, loop=loop, engine=engine, doc=doc, win=win, state=state, send=send, click=click)
    api.ui._log_output_tab.detach()
    loop._executor.shutdown(wait=True)
    loop._render_executor.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", [("f",), ("r",), ("d",), ('"',), ("5",), ('"', "b"), ("g",)])
async def test_text_click_discards_pending_command_without_editing(editor, prefix):
    before = editor.doc.get_text()
    await editor.send(*prefix)
    await editor.click()
    assert editor.doc.get_text() == before
    await editor.send("i")
    assert editor.engine.mode == Mode.INSERT
    assert editor.doc.get_text() == before
    await editor.send("X", "<Esc>")
    assert editor.doc.get_line(0) == "alphaX initial omega"


@pytest.mark.asyncio
async def test_text_click_discards_count_for_next_motion(editor):
    await editor.send("3")
    await editor.click()
    await editor.send("j")
    assert editor.win.cursor.line == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [Mode.INSERT, Mode.REPLACE])
async def test_text_click_keeps_active_typing_mode(editor, mode):
    await editor.send("i" if mode == Mode.INSERT else "R")
    await editor.click()
    assert editor.engine.mode == mode
    await editor.send("X", "<Esc>")
    expected = "alphaX initial omega" if mode == Mode.INSERT else "alphaXinitial omega"
    assert editor.doc.get_line(0) == expected


@pytest.mark.asyncio
async def test_insert_register_prefix_is_cancelled_by_click(editor):
    await editor.send("i", "<C-r>")
    await editor.click()
    await editor.send("X", "<Esc>")
    assert editor.doc.get_line(0) == "alphaX initial omega"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode_key", ["v", "V", "<C-v>"])
async def test_fresh_click_ends_old_visual_selection(editor, mode_key):
    await editor.send(mode_key, "l")
    await editor.click()
    await editor.send("i")
    assert editor.engine.mode == Mode.INSERT


@pytest.mark.asyncio
async def test_new_drag_still_selects_from_clicked_position(editor):
    await editor.send("v", "l")
    await editor.send(
        MouseEvent(0, 23, 0, True), MouseEvent(0, 26, 0, True, dragging=True), MouseEvent(0, 26, 0, False)
    )
    assert editor.engine.mode == Mode.VISUAL_CHAR
    assert editor.engine._visual_anchor == (0, 3)
    assert editor.win.cursor.col == 6


def _show_tree_panel(editor, host):
    panel = TreeSidebarPanel(TreeView([TreeNode(label="first"), TreeNode(label="second")]))
    if host == "sidebar":
        editor.api.ui.show_sidebar_panel("study", panel, focus=True)
        editor.loop._current_sidebar_rect = Rect(0, 0, 20, 20)
        return editor.loop._sidebar
    editor.api.ui.show_bottom_tab("study", panel, focus=True)
    editor.loop._current_bottom_panel_rect = Rect(0, 20, 80, 4)
    return editor.loop._bottom_panel


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["sidebar", "bottom"])
async def test_text_click_returns_keyboard_focus_from_panel(editor, host):
    panel = _show_tree_panel(editor, host)
    await editor.click()
    assert panel.visible and not panel.focused
    await editor.send("i", "X", "<Esc>")
    assert editor.doc.get_line(0) == "alphaX initial omega"


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["sidebar", "bottom"])
async def test_panel_click_keeps_local_navigation_and_blurs_other_host(editor, host):
    sidebar = _show_tree_panel(editor, "sidebar")
    bottom = _show_tree_panel(editor, "bottom")
    await editor.send("g")
    await editor.click(row=0 if host == "sidebar" else 22, col=8)
    assert sidebar.focused == (host == "sidebar")
    assert bottom.focused == (host == "bottom")
    assert editor.engine._state.key_buffer == []
    await editor.send("j")
    assert editor.engine.mode == Mode.NORMAL
    assert editor.win.cursor.line == 0


_FLASH_PHASES = [((), "char1"), (("i",), "char2"), (("i", "n"), "label"), (("i", "n", "a"), "sublabel")]


async def _start_flash(editor, query, phase):
    if phase == "sublabel":
        editor.doc.load_string("in " * 30 + "\n")
    flash.setup(editor.api)
    editor.loop.attach_flash(editor.api.flash_plugin)
    await editor.send("s", *query)
    assert editor.api.flash_plugin._state == phase


@pytest.mark.asyncio
@pytest.mark.parametrize("query,phase", _FLASH_PHASES)
async def test_escape_immediately_releases_flash_input(editor, query, phase):
    await _start_flash(editor, query, phase)
    await editor.send("<Esc>")
    assert not editor.api.flash_plugin.is_active
    assert editor.state.decorations.get_for_namespace(id(editor.win), "flash:labels") == []
    await editor.send("i")
    assert editor.engine.mode == Mode.INSERT


@pytest.mark.asyncio
@pytest.mark.parametrize("query,phase", _FLASH_PHASES)
async def test_text_click_cancels_flash_before_moving_cursor(editor, query, phase):
    await _start_flash(editor, query, phase)
    await editor.click(col=24)
    assert not editor.api.flash_plugin.is_active
    assert editor.win.cursor.col == 4
    await editor.send("i")
    assert editor.engine.mode == Mode.INSERT


@pytest.mark.asyncio
async def test_escape_from_visual_flash_restores_selection_and_releases_keys(editor):
    await editor.send("v", "l")
    anchor = editor.engine._visual_anchor
    await _start_flash(editor, ("i", "n"), "label")
    await editor.send("<Esc>")
    assert not editor.api.flash_plugin.is_active
    assert editor.engine.mode == Mode.VISUAL_CHAR
    assert editor.engine._visual_anchor == anchor
    await editor.send("j")
    assert editor.win.cursor.line == 1


@pytest.mark.asyncio
async def test_click_cancels_visual_flash_then_starts_fresh_edit(editor):
    await editor.send("v", "l")
    await _start_flash(editor, ("i", "n"), "label")
    await editor.click(col=24)
    assert editor.win.cursor.col == 4
    await editor.send("i")
    assert editor.engine.mode == Mode.INSERT


@pytest.mark.asyncio
@pytest.mark.parametrize("event", [MouseEvent(0, 25, 3, True), MouseEvent(0, 25, 0, False)])
async def test_scroll_and_release_do_not_abandon_flash_or_chooser(editor, event):
    await _start_flash(editor, (), "char1")
    await editor.send(event)
    assert editor.api.flash_plugin.is_active
    await editor.send("<Esc>")
    chooser = TargetChooser(editor.api, [ChooserTarget("target", None, lambda: None)])
    await editor.send(event)
    assert chooser.is_active
    await editor.click()
    assert not chooser.is_active
    await editor.send("i")
    assert editor.engine.mode == Mode.INSERT


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [":", "/", "?", "!!"])
async def test_text_click_cancels_command_entry_without_running_it(editor, command):
    before = editor.doc.get_text()
    calls = []
    editor.api.commands.register("Danger", lambda cmd, ctx: calls.append(cmd))
    await editor.send(*command, *"Danger")
    assert editor.loop._cmdline.active
    await editor.click()
    assert not editor.loop._cmdline.active
    assert editor.engine.consume_filter_range() is None
    assert editor.doc.get_text() == before
    assert calls == []
    await editor.send("i")
    assert editor.engine.mode == Mode.INSERT


@pytest.mark.asyncio
async def test_text_click_cancels_substitute_confirmation_without_accepting_match(editor):
    before = editor.doc.get_text()
    confirmation = ConfirmSubState([(0, 0, 5, "REPLACED")], "REPLACED")
    editor.state.confirm_sub = confirmation
    await editor.click()
    assert editor.state.confirm_sub is None
    assert confirmation.applied == 0
    assert editor.doc.get_text() == before
    await editor.send("i")
    assert editor.engine.mode == Mode.INSERT


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["f", "r", "d", '"', "3"])
async def test_opening_file_discards_partial_command(editor, tmp_path, prefix):
    path = tmp_path / "new.py"
    path.write_text("new file\n")
    await editor.send(prefix)
    editor.api.open_buffer(path)
    await editor.send("i")
    assert editor.engine.mode == Mode.INSERT
    assert editor.api.active_buffer().get_text() == "new file\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("shared_doc", [False, True])
async def test_returning_to_window_discards_partial_command(editor, shared_doc):
    old_text = editor.doc.get_text()
    editor.engine.sync_window_context(editor.win)
    editor.api.active_window().split("v")
    if not shared_doc:
        editor.api.open_scratch_buffer("other window\n")
    await editor.send("r")
    editor.api.activate_window(editor.win)
    await editor.send("i")
    assert editor.engine.mode == Mode.INSERT
    assert editor.doc.get_text() == old_text


@pytest.mark.asyncio
async def test_uninterrupted_multi_key_commands_still_work(editor):
    await editor.send('"', "b", "d", "i", "w")
    assert editor.doc.get_line(0) == " initial omega"
    assert editor.api._dispatcher.registers.get("b")[0] == "alpha"


@pytest.mark.asyncio
async def test_click_in_another_document_keeps_insert_mode_and_separate_undo(editor):
    editor.api.active_window().split("v")
    editor.api.open_scratch_buffer("other window\n")
    second_doc = editor.api.active_buffer()._doc
    editor.api.activate_window(editor.win)
    leaves = editor.api._workspace.active_tab.all_leaves()
    editor.loop._current_layout = {leaves[0]: Rect(20, 0, 30, 20), leaves[1]: Rect(50, 0, 30, 20)}
    await editor.send("i", "A")
    await editor.click(col=55)
    assert editor.engine.mode == Mode.INSERT
    await editor.send("B", "<Esc>")
    assert editor.doc.get_line(0) == "Aalpha initial omega"
    assert second_doc.get_line(0) == "otherB window"
    await editor.send("u")
    assert second_doc.get_line(0) == "other window"
    editor.api.activate_window(editor.win)
    await editor.send("u")
    assert editor.doc.get_line(0) == "alpha initial omega"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scroll", "scrollbar", "release", "outside"])
async def test_non_text_mouse_events_preserve_pending_command(editor, kind):
    editor.state.options.set("scrollbar", True)
    await editor.send("r")
    events = {
        "scroll": MouseEvent(0, 25, 3, True),
        "scrollbar": MouseEvent(0, 79, 0, True),
        "release": MouseEvent(0, 25, 0, False),
        "outside": MouseEvent(23, 25, 0, True),
    }
    await editor.send(events[kind])
    assert editor.engine._state.key_buffer == ["r"]


@pytest.mark.asyncio
async def test_click_clears_which_key_prefix_immediately(editor):
    prefixes = []
    editor.api.keymap.nmap("<leader>xx", lambda: None)
    editor.api.events.on("key_prefix_done", lambda **kw: prefixes.append("done"))
    await editor.send(editor.api.keymap.leader)
    assert editor.loop._last_key_buf_len > 0
    await editor.click()
    assert prefixes == ["done"]
    assert editor.loop._last_key_buf_len == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("escaped_first", [False, True])
async def test_genuine_alt_key_is_preserved_after_returning_from_command_line(editor, escaped_first):
    calls = []
    editor.api.keymap.nmap("<A-i>", lambda: calls.append("alt"))
    await editor.send(":")
    if escaped_first:
        await editor.send("<Esc>")
    await editor.click()
    await editor.send("<A-i>")
    assert calls == ["alt"]
    assert editor.engine.mode == Mode.NORMAL
