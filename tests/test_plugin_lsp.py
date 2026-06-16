from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.style import Style
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.ui.decorations import Sign, VirtualText


def _make_api(tmp_path: Path):
    doc = Document(path=tmp_path / "sample.py")
    doc.load_string("value = 1\n")
    window = Window(doc)
    workspace = Workspace(window)
    editor_state = EditorState()
    option_values = {"lsp_update_in_insert": False}

    options = MagicMock()
    options.get.side_effect = lambda name: option_values.get(name)

    def _define(name, _type, default, scope=("global",), validator=None, doc=""):
        option_values.setdefault(name, default)

    options.define.side_effect = _define

    manager = MagicMock()
    manager._configs = [
        SimpleNamespace(filetype="python", cmd=["pyright-langserver", "--stdio"], root_markers=[".git"])
    ]
    lsp = SimpleNamespace(
        _manager=manager,
        register_server=MagicMock(),
        trigger_completion=MagicMock(),
        dismiss_signature_help=MagicMock(),
        refresh_document_highlight=MagicMock(),
        clear_document_highlight=MagicMock(),
        remap_buffer_diagnostics=MagicMock(),
        registered_servers=MagicMock(return_value=[{"filetype": "python", "cmd": ["pyright-langserver", "--stdio"]}]),
        running_servers=MagicMock(return_value=[]),
    )

    def _resolve_buffer_document(buf_id: int = 0):
        if not buf_id or buf_id == id(doc):
            return doc
        return None

    def _attach_buffer(buf_id: int = 0):
        target = _resolve_buffer_document(buf_id)
        if target is not None:
            manager.attach(target)
        return target

    def _notify_buffer_changed(buf_id: int = 0):
        target = _resolve_buffer_document(buf_id)
        if target is not None:
            manager.notify_change(target)
        return target

    def _notify_buffer_saved(buf_id: int = 0):
        target = _resolve_buffer_document(buf_id)
        if target is not None:
            manager.notify_save(target)
        return target

    def _attach_open_buffers():
        if doc.path is not None:
            manager.attach(doc)

    def _current_buffer_status():
        return {
            "path": doc.path,
            "filetype": getattr(doc, "filetype", "") or "python",
            "attached": manager.get.return_value is not None,
            "initialized": bool(getattr(manager.get.return_value, "_initialized", False))
            if manager.get.return_value is not None
            else False,
            "server_registered": True,
        }

    lsp.resolve_buffer_document = MagicMock(side_effect=_resolve_buffer_document)
    lsp.attach_buffer = MagicMock(side_effect=_attach_buffer)
    lsp.notify_buffer_changed = MagicMock(side_effect=_notify_buffer_changed)
    lsp.notify_buffer_saved = MagicMock(side_effect=_notify_buffer_saved)
    lsp.attach_open_buffers = MagicMock(side_effect=_attach_open_buffers)
    lsp.current_buffer_status = MagicMock(side_effect=_current_buffer_status)

    api = MagicMock()
    api.lsp = lsp
    api._workspace = workspace
    api._editor_state = editor_state
    api.active_mode = SimpleNamespace(value="normal")
    api.options = options
    api.events = MagicMock()
    api.events.on = MagicMock()
    api.events.once = MagicMock()
    api.keymap = MagicMock()
    api.keymap.nmap = MagicMock()
    api.keymap.imap = MagicMock()
    api.commands = MagicMock()
    api.commands.register = MagicMock()
    api.health = MagicMock()
    api.health.register = MagicMock()
    api.set_interval = MagicMock()
    api.active_buffer.return_value = SimpleNamespace(
        buf_id=id(doc),
        path=doc.path,
        version=doc.version,
        insert=MagicMock(),
        clear_namespace=MagicMock(side_effect=lambda ns: editor_state.decorations.clear_namespace(id(doc), ns)),
    )
    api.buffer_by_id.side_effect = lambda buf_id: api.active_buffer.return_value if buf_id == id(doc) else None

    def _active_window():
        return SimpleNamespace(
            _window=window,
            cursor=(window.cursor.line, window.cursor.col),
            buffer=lambda: api.active_buffer.return_value,
            visible_range=lambda: (
                window.scroll_line,
                min(window.scroll_line + window.height - 1, doc.line_count() - 1),
            ),
        )

    api.active_window.side_effect = _active_window
    return api, doc, editor_state, option_values, manager


def _event_handlers(api) -> dict[str, object]:
    handlers: dict[str, object] = {}
    for call in api.events.on.call_args_list:
        handlers[call.args[0]] = call.args[1]
    return handlers


class TestLspPluginSetup:
    def test_registers_insert_mode_hooks(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, _doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        setup(api)

        events = [call.args[0] for call in api.events.on.call_args_list]
        assert "insert_entered" in events
        assert "insert_left" in events
        assert "buffer_text_changed" in events
        assert any(call.args[:3] == ("lsp_update_in_insert", bool, False) for call in api.options.define.call_args_list)
        assert any(call.args[:3] == ("lsp_inlay_hints", bool, False) for call in api.options.define.call_args_list)
        assert any(
            call.args[:3] == ("lsp_document_highlight", bool, True) for call in api.options.define.call_args_list
        )
        assert api.set_interval.call_count == 2
        assert all(call.args[1] == 120 for call in api.set_interval.call_args_list)

    def test_buffer_text_changed_remaps_diagnostics(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        setup(api)
        handlers = _event_handlers(api)

        handlers["buffer_text_changed"](
            buf_id=id(doc),
            start_line=0,
            start_col=0,
            end_line=1,
            end_col=0,
            new_text="",
        )

        api.lsp.remap_buffer_diagnostics.assert_called_once_with(
            buf_id=id(doc),
            start_line=0,
            start_col=0,
            end_line=1,
            end_col=0,
            new_text="",
        )

    def test_registers_code_action_keymaps(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, _doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        setup(api)

        assert any(call.args[0] == "<Plug>LspCodeAction" for call in api.keymap.nmap.call_args_list)
        assert any(
            call.args[0] == "<leader>ca" and call.args[1] == "<Plug>LspCodeAction"
            for call in api.keymap.nmap.call_args_list
        )

    def test_registers_implementation_and_type_definition_keymaps(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, _doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        setup(api)

        assert any(call.args[0] == "<Plug>LspImplementation" for call in api.keymap.nmap.call_args_list)
        assert any(call.args[0] == "<Plug>LspTypeDefinition" for call in api.keymap.nmap.call_args_list)
        assert any(
            call.args[0] == "<leader>cgi" and call.args[1] == "<Plug>LspImplementation"
            for call in api.keymap.nmap.call_args_list
        )
        assert any(
            call.args[0] == "<leader>cgt" and call.args[1] == "<Plug>LspTypeDefinition"
            for call in api.keymap.nmap.call_args_list
        )

    def test_registers_signature_help_insert_keymap(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, _doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        setup(api)

        assert any(call.args[0] == "<Plug>LspSignatureHelp" for call in api.keymap.imap.call_args_list)
        assert any(
            call.args[0] == "<C-k>" and call.args[1] == "<Plug>LspSignatureHelp"
            for call in api.keymap.imap.call_args_list
        )

    def test_ctrl_n_completes_single_buffer_word(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        doc.load_string("alpha beta\nalp\n")
        workspace_window = api._workspace.active_window
        workspace_window.cursor.move_to(1, 3)
        api._event_loop = SimpleNamespace(_completion_popup=MagicMock(), _invalidate=MagicMock())
        setup(api)

        complete = next(call.args[1] for call in api.keymap.imap.call_args_list if call.args[0] == "<Plug>LspComplete")
        complete()

        api.active_buffer.return_value.insert.assert_called_once_with(1, 3, "ha")
        assert not api._event_loop._completion_popup.open.called
        api.lsp.trigger_completion.assert_not_called()

    def test_ctrl_n_opens_prefix_popup_for_multiple_buffer_words(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        doc.load_string("alpha alphabet\nalp\n")
        workspace_window = api._workspace.active_window
        workspace_window.cursor.move_to(1, 3)
        popup = MagicMock()
        api._event_loop = SimpleNamespace(_completion_popup=popup, _invalidate=MagicMock())
        setup(api)

        complete = next(call.args[1] for call in api.keymap.imap.call_args_list if call.args[0] == "<Plug>LspComplete")
        complete()

        popup.open.assert_called_once()
        args = popup.open.call_args.args
        kwargs = popup.open.call_args.kwargs
        assert [item["label"] for item in args[0]] == ["alpha", "alphabet"]
        assert args[1:] == (1, 0)
        assert kwargs == {
            "filter_text": "alp",
            "match_mode": "prefix",
            "replace_filter_on_accept": True,
        }
        api._event_loop._invalidate.assert_called_once_with("full")
        api.active_buffer.return_value.insert.assert_not_called()
        api.lsp.trigger_completion.assert_not_called()

    def test_ctrl_n_falls_back_to_lsp_when_no_buffer_match(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        doc.load_string("beta gamma\nalp\n")
        workspace_window = api._workspace.active_window
        workspace_window.cursor.move_to(1, 3)
        api._event_loop = SimpleNamespace(_completion_popup=MagicMock(), _invalidate=MagicMock())
        api.lsp.trigger_completion = MagicMock()
        setup(api)

        complete = next(call.args[1] for call in api.keymap.imap.call_args_list if call.args[0] == "<Plug>LspComplete")
        complete()

        api.active_buffer.return_value.insert.assert_not_called()
        api.lsp.trigger_completion.assert_called_once_with()

    def test_registers_document_symbol_keymaps(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, _doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        setup(api)

        assert any(call.args[0] == "<Plug>LspDocumentSymbols" for call in api.keymap.nmap.call_args_list)
        assert any(
            call.args[0] == "go" and call.args[1] == "<Plug>LspDocumentSymbols"
            for call in api.keymap.nmap.call_args_list
        )
        assert any(
            call.args[0] == "<leader>csd" and call.args[1] == "<Plug>LspDocumentSymbols"
            for call in api.keymap.nmap.call_args_list
        )

    def test_registers_workspace_symbol_keymap(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, _doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        setup(api)

        assert any(call.args[0] == "<Plug>LspWorkspaceSymbols" for call in api.keymap.nmap.call_args_list)
        assert any(
            call.args[0] == "<leader>csw" and call.args[1] == "<Plug>LspWorkspaceSymbols"
            for call in api.keymap.nmap.call_args_list
        )

    def test_registers_inlay_hint_toggle_keymap(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, _doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        setup(api)

        assert any(call.args[0] == "<Plug>LspToggleInlayHints" for call in api.keymap.nmap.call_args_list)
        assert any(
            call.args[0] == "<leader>ci" and call.args[1] == "<Plug>LspToggleInlayHints"
            for call in api.keymap.nmap.call_args_list
        )

    def test_insert_left_dismisses_signature_help(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        setup(api)
        handlers = _event_handlers(api)

        handlers["insert_left"](buf_id=id(doc))

        api.lsp.dismiss_signature_help.assert_called_once_with()

    def test_insert_entered_clears_only_virtual_text_by_default(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, doc, editor_state, _option_values, _manager = _make_api(tmp_path)
        editor_state.decorations.add(id(doc), "lsp:diag:signs", Sign(line=0, char="E", style=Style(fg=(255, 0, 0))))
        editor_state.decorations.add(
            id(doc), "lsp:diag:text", VirtualText(line=0, text=" undefined name", style=Style(fg=(255, 0, 0)))
        )

        setup(api)
        handlers = _event_handlers(api)
        handlers["insert_entered"](buf_id=id(doc))

        assert len(editor_state.decorations.get_for_namespace(id(doc), "lsp:diag:signs")) == 1
        assert editor_state.decorations.get_for_namespace(id(doc), "lsp:diag:text") == []

    def test_insert_leave_refreshes_when_text_was_hidden_without_edits(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, doc, _editor_state, _option_values, manager = _make_api(tmp_path)
        setup(api)
        handlers = _event_handlers(api)

        handlers["insert_entered"](buf_id=id(doc))
        handlers["insert_left"](buf_id=id(doc))

        manager.notify_change.assert_called_once_with(doc)

    def test_insert_mode_refreshes_diagnostics_on_leave(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, doc, _editor_state, _option_values, manager = _make_api(tmp_path)
        setup(api)
        handlers = _event_handlers(api)

        api.active_mode = SimpleNamespace(value="insert")
        handlers["buffer_changed"](buf_id=id(doc))

        manager.notify_change.assert_called_once_with(doc)

        api.active_mode = SimpleNamespace(value="normal")
        handlers["insert_left"](buf_id=id(doc))

        assert manager.notify_change.call_count == 2
        assert manager.notify_change.call_args_list[-1].args == (doc,)

    def test_update_in_insert_option_skips_leave_refresh(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, doc, _editor_state, option_values, manager = _make_api(tmp_path)
        option_values["lsp_update_in_insert"] = True

        setup(api)
        handlers = _event_handlers(api)

        api.active_mode = SimpleNamespace(value="insert")
        handlers["buffer_changed"](buf_id=id(doc))

        api.active_mode = SimpleNamespace(value="normal")
        handlers["insert_left"](buf_id=id(doc))

        manager.notify_change.assert_called_once_with(doc)

    def test_document_highlight_poll_refreshes_after_idle_and_clears_on_move(self, tmp_path, monkeypatch):
        from peovim.plugins.lsp import setup

        api, doc, _editor_state, option_values, _manager = _make_api(tmp_path)
        option_values["lsp_document_highlight"] = True
        setup(api)

        callbacks = [call.args[0] for call in api.set_interval.call_args_list]
        document_highlight_poll = callbacks[-1]
        clock = iter([0.0, 0.2, 0.6, 0.7])
        monkeypatch.setattr("peovim.plugins.lsp.time.monotonic", lambda: next(clock))

        document_highlight_poll()
        document_highlight_poll()
        document_highlight_poll()

        assert api.lsp.refresh_document_highlight.call_count == 1

        api._workspace.active_window.cursor.move_to(0, 1)
        document_highlight_poll()

        assert api.lsp.clear_document_highlight.call_count >= 1

    def test_autodetect_registers_ruff_alongside_primary_python_server(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, _doc, _editor_state, _option_values, _manager = _make_api(tmp_path)
        available = {"ty": "C:/bin/ty.exe", "ruff": "C:/bin/ruff.exe"}

        with patch("peovim.plugins.lsp.shutil.which", side_effect=lambda name: available.get(name)):
            setup(api)

        assert any(call.args[:2] == ("python", ["ty", "server"]) for call in api.lsp.register_server.call_args_list)
        assert any(call.args[:2] == ("python", ["ruff", "server"]) for call in api.lsp.register_server.call_args_list)

    def test_autodetect_registers_clangd_for_c_and_cpp(self, tmp_path):
        from peovim.plugins.lsp import setup

        api, _doc, _editor_state, _option_values, _manager = _make_api(tmp_path)

        with patch(
            "peovim.plugins.lsp.shutil.which",
            side_effect=lambda name: "C:/bin/clangd.exe" if name == "clangd" else None,
        ):
            setup(api)

        assert any(call.args[:2] == ("c", ["clangd"]) for call in api.lsp.register_server.call_args_list)
        assert any(call.args[:2] == ("cpp", ["clangd"]) for call in api.lsp.register_server.call_args_list)
