"""tests/test_lsp — Tests for LSP protocol, manager, and completion popup."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


class TestProtocol:
    def test_encode_decode_roundtrip(self):
        from peovim.lsp.protocol import FrameBuffer, encode, make_request

        msg = make_request(1, "initialize", {"rootUri": "file:///tmp"})
        data = encode(msg)
        buf = FrameBuffer()
        msgs = buf.feed(data)
        assert len(msgs) == 1
        assert msgs[0]["method"] == "initialize"
        assert msgs[0]["id"] == 1

    def test_encode_content_length(self):
        from peovim.lsp.protocol import encode, make_notification

        msg = make_notification("initialized", {})
        data = encode(msg)
        header, body = data.split(b"\r\n\r\n", 1)
        cl = int(header.split(b":")[1].strip())
        assert cl == len(body)

    def test_framebuffer_partial(self):
        from peovim.lsp.protocol import FrameBuffer, encode, make_request

        msg = make_request(1, "test", None)
        data = encode(msg)
        buf = FrameBuffer()
        half = len(data) // 2
        msgs = buf.feed(data[:half])
        assert msgs == []
        msgs = buf.feed(data[half:])
        assert len(msgs) == 1
        assert msgs[0]["method"] == "test"

    def test_framebuffer_multiple_messages(self):
        from peovim.lsp.protocol import FrameBuffer, encode, make_request

        data = encode(make_request(1, "a", {})) + encode(make_request(2, "b", {}))
        buf = FrameBuffer()
        msgs = buf.feed(data)
        assert len(msgs) == 2
        assert msgs[0]["method"] == "a"
        assert msgs[1]["method"] == "b"

    def test_make_request_structure(self):
        from peovim.lsp.protocol import make_request

        msg = make_request(42, "textDocument/hover", {"key": "val"})
        assert msg["jsonrpc"] == "2.0"
        assert msg["id"] == 42
        assert msg["method"] == "textDocument/hover"
        assert msg["params"] == {"key": "val"}

    def test_make_notification_no_id(self):
        from peovim.lsp.protocol import make_notification

        msg = make_notification("initialized", {})
        assert "id" not in msg
        assert msg["method"] == "initialized"

    def test_path_uri_roundtrip(self):
        import pathlib

        from peovim.lsp.protocol import path_to_uri, uri_to_path

        p = str(pathlib.Path(__file__).resolve())
        uri = path_to_uri(p)
        assert uri.startswith("file://")
        back = uri_to_path(uri)
        assert back == p

    def test_uri_to_path_non_file(self):
        from peovim.lsp.protocol import uri_to_path

        result = uri_to_path("untitled:foo")
        assert result == "untitled:foo"

    def test_make_response(self):
        from peovim.lsp.protocol import make_response

        r = make_response(5, {"result": "ok"})
        assert r["id"] == 5
        assert r["jsonrpc"] == "2.0"

    def test_make_request_no_params(self):
        from peovim.lsp.protocol import make_request

        msg = make_request(1, "shutdown", None)
        assert "params" not in msg

    def test_make_notification_no_params(self):
        from peovim.lsp.protocol import make_notification

        msg = make_notification("exit", None)
        assert "params" not in msg


class TestFeatureHelpers:
    def test_extract_hover_text_str(self):
        from peovim.lsp.features import _extract_hover_text

        assert _extract_hover_text({"contents": "hello"}) == "hello"

    def test_extract_hover_text_dict(self):
        from peovim.lsp.features import _extract_hover_text

        result = _extract_hover_text({"contents": {"kind": "markdown", "value": "**bold**"}})
        assert result == "**bold**"

    def test_extract_hover_text_list(self):
        from peovim.lsp.features import _extract_hover_text

        result = _extract_hover_text({"contents": ["line1", {"value": "line2"}]})
        assert result == "line1\nline2"

    def test_extract_hover_text_none(self):
        from peovim.lsp.features import _extract_hover_text

        assert _extract_hover_text(None) is None
        assert _extract_hover_text({}) is None

    def test_normalise_locations_single(self):
        import pathlib

        from peovim.lsp.features import _normalise_locations
        from peovim.lsp.protocol import path_to_uri

        p = str(pathlib.Path(__file__).resolve())
        locs = _normalise_locations(
            {
                "uri": path_to_uri(p),
                "range": {"start": {"line": 5, "character": 10}, "end": {"line": 5, "character": 20}},
            }
        )
        assert len(locs) == 1
        assert locs[0]["line"] == 5
        assert locs[0]["col"] == 10
        assert locs[0]["path"] == p

    def test_normalise_locations_list(self):
        import pathlib

        from peovim.lsp.features import _normalise_locations
        from peovim.lsp.protocol import path_to_uri

        p = str(pathlib.Path(__file__).resolve())
        locs = _normalise_locations(
            [
                {
                    "uri": path_to_uri(p),
                    "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 5}},
                },
                {
                    "uri": path_to_uri(p),
                    "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 5}},
                },
            ]
        )
        assert len(locs) == 2
        assert locs[0]["line"] == 1
        assert locs[1]["line"] == 2

    def test_normalise_locations_empty(self):
        from peovim.lsp.features import _normalise_locations

        assert _normalise_locations(None) == []
        assert _normalise_locations([]) == []

    def test_extract_completion_items_list(self):
        from peovim.lsp.features import _extract_completion_items

        items = _extract_completion_items(
            [
                {"label": "foo", "kind": 3, "detail": "fn", "insertText": "foo()"},
                {"label": "bar", "kind": 6},
            ]
        )
        assert len(items) == 2
        assert items[0]["label"] == "foo"
        assert items[0]["insertText"] == "foo()"
        assert items[1]["label"] == "bar"
        assert items[1]["insertText"] == "bar"

    def test_extract_completion_items_dict(self):
        from peovim.lsp.features import _extract_completion_items

        items = _extract_completion_items({"isIncomplete": False, "items": [{"label": "x", "kind": 1}]})
        assert len(items) == 1
        assert items[0]["label"] == "x"

    def test_extract_completion_items_cap(self):
        from peovim.lsp.features import _extract_completion_items

        many = [{"label": f"item{i}", "kind": 1} for i in range(200)]
        result = _extract_completion_items(many)
        assert len(result) == 100

    def test_completion_kind_label(self):
        from peovim.lsp.features import completion_kind_label

        assert completion_kind_label(3) == "fn"
        assert completion_kind_label(7) == "cls"
        assert completion_kind_label(999) == "   "

    def test_extract_completion_items_empty(self):
        from peovim.lsp.features import _extract_completion_items

        assert _extract_completion_items(None) == []
        assert _extract_completion_items([]) == []

    def test_extract_code_actions_normalises_command_and_edit_shapes(self):
        from peovim.lsp.features import _extract_code_actions

        actions = _extract_code_actions(
            [
                {
                    "title": "Organize Imports",
                    "kind": "source.organizeImports",
                    "edit": {"changes": {}},
                    "command": {"command": "organize", "arguments": ["file.py"]},
                },
                {
                    "title": "Fix All",
                    "command": "fixAll",
                    "arguments": [1, 2],
                },
            ]
        )

        assert actions == [
            {
                "title": "Organize Imports",
                "kind": "source.organizeImports",
                "edit": {"changes": {}},
                "command": "organize",
                "arguments": ["file.py"],
            },
            {
                "title": "Fix All",
                "kind": "",
                "edit": None,
                "command": "fixAll",
                "arguments": [1, 2],
            },
        ]

    def test_extract_signature_help_text_formats_active_parameter_and_docs(self):
        from peovim.lsp.features import _extract_signature_help_text

        text = _extract_signature_help_text(
            {
                "activeSignature": 0,
                "activeParameter": 1,
                "signatures": [
                    {
                        "label": "func(a, b)",
                        "documentation": {"value": "Function docs"},
                        "parameters": [
                            {"label": "a"},
                            {"label": [8, 9]},
                        ],
                    }
                ],
            }
        )

        assert text == "func(a, b)\nparameter: b\nFunction docs"

    def test_extract_inlay_hints_normalises_label_parts_and_padding(self):
        from peovim.lsp.features import _extract_inlay_hints

        hints = _extract_inlay_hints(
            [
                {
                    "position": {"line": 2, "character": 4},
                    "label": [{"value": "name:"}, {"value": " int"}],
                    "paddingLeft": True,
                },
                {
                    "position": {"line": 2, "character": 10},
                    "label": "-> str",
                    "paddingRight": True,
                },
            ]
        )

        assert hints == [
            {"line": 2, "col": 4, "text": " name: int"},
            {"line": 2, "col": 10, "text": "-> str "},
        ]

    def test_extract_document_highlights_normalises_ranges_and_kind(self):
        from peovim.lsp.features import _extract_document_highlights

        highlights = _extract_document_highlights(
            [
                {
                    "range": {
                        "start": {"line": 1, "character": 2},
                        "end": {"line": 1, "character": 6},
                    },
                    "kind": 2,
                }
            ]
        )

        assert highlights == [{"start_line": 1, "start_col": 2, "end_line": 1, "end_col": 6, "kind": 2}]

    def test_extract_document_symbols_flattens_nested_document_symbols(self):
        from peovim.lsp.features import _extract_document_symbols

        symbols = _extract_document_symbols(
            [
                {
                    "name": "Outer",
                    "kind": 5,
                    "range": {"start": {"line": 0, "character": 0}},
                    "selectionRange": {"start": {"line": 0, "character": 0}},
                    "children": [
                        {
                            "name": "inner",
                            "kind": 12,
                            "selectionRange": {"start": {"line": 3, "character": 4}},
                        }
                    ],
                }
            ],
            "file.py",
        )

        assert symbols == [
            {
                "name": "Outer",
                "kind": "class",
                "detail": "",
                "path": "file.py",
                "line": 0,
                "col": 0,
            },
            {
                "name": "inner",
                "kind": "function",
                "detail": "Outer",
                "path": "file.py",
                "line": 3,
                "col": 4,
            },
        ]

    def test_extract_document_symbols_supports_symbol_information_shape(self):
        from peovim.lsp.features import _extract_document_symbols
        from peovim.lsp.protocol import path_to_uri, uri_to_path

        symbols = _extract_document_symbols(
            [
                {
                    "name": "CONST",
                    "kind": 14,
                    "containerName": "mod",
                    "location": {
                        "uri": path_to_uri("C:/tmp/file.py"),
                        "range": {"start": {"line": 2, "character": 1}},
                    },
                }
            ],
            "fallback.py",
        )

        assert symbols == [
            {
                "name": "CONST",
                "kind": "const",
                "detail": "mod",
                "path": uri_to_path(path_to_uri("C:/tmp/file.py")),
                "line": 2,
                "col": 1,
            }
        ]

    def test_extract_document_symbol_tree_preserves_children(self):
        from peovim.lsp.features import _extract_document_symbol_tree

        symbols = _extract_document_symbol_tree(
            [
                {
                    "name": "Outer",
                    "kind": 5,
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 10, "character": 0},
                    },
                    "selectionRange": {"start": {"line": 0, "character": 0}},
                    "children": [
                        {
                            "name": "inner",
                            "kind": 12,
                            "range": {
                                "start": {"line": 3, "character": 4},
                                "end": {"line": 5, "character": 2},
                            },
                            "selectionRange": {"start": {"line": 3, "character": 4}},
                        }
                    ],
                }
            ],
            "file.py",
        )

        assert symbols == [
            {
                "name": "Outer",
                "kind": "class",
                "detail": "",
                "path": "file.py",
                "line": 0,
                "col": 0,
                "end_line": 10,
                "end_col": 0,
                "children": [
                    {
                        "name": "inner",
                        "kind": "function",
                        "detail": "Outer",
                        "path": "file.py",
                        "line": 3,
                        "col": 4,
                        "end_line": 5,
                        "end_col": 2,
                        "children": [],
                    }
                ],
            }
        ]

    def test_apply_diagnostics_keeps_signs_but_hides_virtual_text_in_insert_mode(self, tmp_path):
        from types import SimpleNamespace

        from peovim.core.document import Document
        from peovim.core.editor_state import EditorState
        from peovim.core.window import Window
        from peovim.core.workspace import Workspace
        from peovim.lsp.features import _apply_diagnostics
        from peovim.ui.decorations import Sign

        path = tmp_path / "sample.py"
        doc = Document(path=path)
        doc.load_string("value = 1\n")
        window = Window(doc)
        workspace = Workspace(window)
        editor_state = EditorState()
        editor_state._api = SimpleNamespace(_engine=SimpleNamespace(mode=SimpleNamespace(value="insert")))

        _apply_diagnostics(
            editor_state,
            workspace,
            str(path),
            [{"severity": 1, "message": "undefined name", "range": {"start": {"line": 0}}}],
        )

        sign_decs = editor_state.decorations.get_for_namespace(id(doc), "lsp:diag:signs")
        text_decs = editor_state.decorations.get_for_namespace(id(doc), "lsp:diag:text")
        assert len(sign_decs) == 1
        assert isinstance(sign_decs[0], Sign)
        assert text_decs == []

    def test_apply_diagnostics_can_update_in_insert_when_enabled(self, tmp_path):
        from types import SimpleNamespace

        from peovim.core.document import Document
        from peovim.core.editor_state import EditorState
        from peovim.core.window import Window
        from peovim.core.workspace import Workspace
        from peovim.lsp.features import _apply_diagnostics
        from peovim.ui.decorations import VirtualText

        path = tmp_path / "sample.py"
        doc = Document(path=path)
        doc.load_string("value = 1\n")
        window = Window(doc)
        workspace = Workspace(window)
        editor_state = EditorState()
        editor_state.options.define("lsp_update_in_insert", bool, False)
        editor_state.options.set_global("lsp_update_in_insert", True)
        editor_state._api = SimpleNamespace(_engine=SimpleNamespace(mode=SimpleNamespace(value="insert")))

        _apply_diagnostics(
            editor_state,
            workspace,
            str(path),
            [{"severity": 1, "message": "undefined name", "range": {"start": {"line": 0}}}],
        )

        sign_decs = editor_state.decorations.get_for_namespace(id(doc), "lsp:diag:signs")
        text_decs = editor_state.decorations.get_for_namespace(id(doc), "lsp:diag:text")
        assert len(sign_decs) == 1
        assert len(text_decs) == 1
        assert isinstance(text_decs[0], VirtualText)

    def test_apply_diagnostics_emits_event_after_decorations_exist(self, tmp_path):
        from peovim.core.document import Document
        from peovim.core.editor_state import EditorState
        from peovim.core.window import Window
        from peovim.core.workspace import Workspace
        from peovim.lsp.features import _apply_diagnostics

        path = tmp_path / "sample.py"
        doc = Document(path=path)
        doc.load_string("value = 1\n")
        window = Window(doc)
        workspace = Workspace(window)
        editor_state = EditorState()
        seen: list[tuple[str, int, int, int]] = []

        def _on_diagnostics_updated(path: str, diagnostics: list[dict], count: int) -> None:
            sign_decs = editor_state.decorations.get_for_namespace(id(doc), "lsp:diag:signs")
            text_decs = editor_state.decorations.get_for_namespace(id(doc), "lsp:diag:text")
            seen.append((path, count, len(sign_decs), len(text_decs)))

        editor_state.event_bus.on("diagnostics_updated", _on_diagnostics_updated)

        _apply_diagnostics(
            editor_state,
            workspace,
            str(path),
            [{"severity": 1, "message": "undefined name", "range": {"start": {"line": 0}}}],
        )

        assert seen == [(str(path), 1, 1, 1)]

    def test_remap_diagnostic_moves_range_after_line_delete(self):
        from peovim.lsp.features import _remap_diagnostic

        diagnostic = {
            "severity": 1,
            "message": "broken",
            "range": {
                "start": {"line": 4, "character": 2},
                "end": {"line": 4, "character": 6},
            },
        }

        remapped = _remap_diagnostic(
            diagnostic,
            start_line=1,
            start_col=0,
            end_line=2,
            end_col=0,
            new_text="",
        )

        assert remapped["range"]["start"] == {"line": 3, "character": 2}
        assert remapped["range"]["end"] == {"line": 3, "character": 6}
        assert diagnostic["range"]["start"] == {"line": 4, "character": 2}
        assert diagnostic["range"]["end"] == {"line": 4, "character": 6}


class TestManagerHelpers:
    def test_find_root_finds_git(self, tmp_path):
        from peovim.lsp.manager import _find_root

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        sub = tmp_path / "src" / "main.py"
        sub.parent.mkdir(parents=True)
        sub.touch()
        root = _find_root(str(sub), [".git"])
        assert root == str(tmp_path)

    def test_find_root_falls_back_to_file_dir(self, tmp_path):
        from peovim.lsp.manager import _find_root

        f = tmp_path / "file.py"
        f.touch()
        root = _find_root(str(f), ["nonexistent_marker"])
        assert root == str(tmp_path)

    def test_detect_filetype(self):
        from peovim.lsp.manager import _detect_filetype

        assert _detect_filetype("foo.py") == "python"
        assert _detect_filetype("foo.rs") == "rust"
        assert _detect_filetype("foo.ts") == "typescript"
        assert _detect_filetype("foo.sv") == "verilog"
        assert _detect_filetype("foo.unknown") == ""

    def test_detect_filetype_case_insensitive(self):
        from peovim.lsp.manager import _detect_filetype

        assert _detect_filetype("foo.PY") == "python"

    def test_server_config_defaults(self):
        from peovim.lsp.manager import ServerConfig

        cfg = ServerConfig(filetype="python", cmd=["pylsp"])
        assert ".git" in cfg.root_markers

    def test_manager_register_missing_cmd(self):
        from peovim.lsp.manager import LspManager, ServerConfig

        q: deque = deque()
        mgr = LspManager(q)
        mgr.register(ServerConfig("python", ["__nonexistent_lsp_cmd__"]))
        assert mgr.get_config("python") is None

    def test_manager_get_no_server(self):
        from peovim.core.document import Document
        from peovim.lsp.manager import LspManager

        q: deque = deque()
        mgr = LspManager(q)
        doc = Document()
        assert mgr.get(doc) is None

    def test_manager_attach_no_path(self):
        from peovim.core.document import Document
        from peovim.lsp.manager import LspManager

        q: deque = deque()
        mgr = LspManager(q)
        doc = Document()
        result = mgr.attach(doc)
        assert result is None

    def test_manager_attach_no_loop(self, tmp_path):
        from peovim.core.document import Document
        from peovim.lsp.manager import LspManager

        q: deque = deque()
        mgr = LspManager(q)
        doc = Document(path=tmp_path / "test.py")
        result = mgr.attach(doc)
        assert result is None

    def test_manager_list_servers_empty(self):
        from peovim.lsp.manager import LspManager

        q: deque = deque()
        mgr = LspManager(q)
        assert mgr.list_servers() == []


class TestLspApiNavigation:
    def _make_api(self, tmp_path: Path):
        from peovim.api.lsp_api import LspAPI
        from peovim.core.document import Document
        from peovim.core.editor_state import EditorState
        from peovim.core.window import Window
        from peovim.core.workspace import Workspace

        doc = Document(path=tmp_path / "sample.py")
        doc.load_string("value = 1\n")
        window = Window(doc)
        workspace = Workspace(window)
        editor_state = EditorState()
        manager = SimpleNamespace(get=lambda _doc: None, list_servers=lambda: [], flush_pending_changes=lambda: None)
        return LspAPI(manager, workspace, editor_state), editor_state

    def test_implementation_jumps_directly_for_single_result(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        jumped: list[dict] = []

        api._lsp_ui = SimpleNamespace(
            goto_location=lambda loc: jumped.append(loc),
            show_picker_for_locations=lambda items, locs, title="": None,
        )
        feats = SimpleNamespace(
            implementation=lambda path, line, col, cb: cb([{"path": str(tmp_path / "impl.py"), "line": 3, "col": 2}])
        )
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.implementation()

        assert jumped == [{"path": str(tmp_path / "impl.py"), "line": 3, "col": 2}]

    def test_type_definition_uses_picker_for_multiple_results(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        picked: list[tuple[list[str], list[dict], str]] = []

        api._lsp_ui = SimpleNamespace(
            goto_location=lambda loc: None,
            show_picker_for_locations=lambda items, locs, title="": picked.append((items, locs, title)),
        )
        locs = [
            {"path": str(tmp_path / "a.py"), "line": 1, "col": 0},
            {"path": str(tmp_path / "b.py"), "line": 4, "col": 5},
        ]
        feats = SimpleNamespace(type_definition=lambda path, line, col, cb: cb(locs))
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.type_definition()

        assert picked == [
            (
                [f"{locs[0]['path']}:2:1", f"{locs[1]['path']}:5:6"],
                locs,
                "Type Definitions",
            )
        ]

    def test_implementation_sets_empty_message_when_no_results(self, tmp_path):
        api, editor_state = self._make_api(tmp_path)
        api._lsp_ui = SimpleNamespace(
            goto_location=lambda loc: None,
            show_picker_for_locations=lambda items, locs, title="": None,
        )
        feats = SimpleNamespace(implementation=lambda path, line, col, cb: cb([]))
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.implementation()

        assert editor_state.message == "No implementation found"

    def test_signature_help_uses_event_loop_helper(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        shown: list[str | None] = []
        api._lsp_ui = SimpleNamespace(show_signature_help=lambda text: shown.append(text))
        feats = SimpleNamespace(signature_help=lambda path, line, col, cb: cb("fn(a, b)\nparameter: b"))
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.signature_help()

        assert shown == ["fn(a, b)\nparameter: b"]

    def test_toggle_inlay_hints_enables_option_and_refreshes(self, tmp_path):
        api, editor_state = self._make_api(tmp_path)
        editor_state.options.define("lsp_inlay_hints", bool, False)
        refreshed: list[tuple[str, int, int]] = []
        feats = SimpleNamespace(
            inlay_hints=lambda path, start, end, cb: (
                refreshed.append((path, start, end)),
                cb([{"line": 0, "col": 5, "text": ": int"}]),
            )[-1],
            supports_capability=lambda name: name == "inlayHintProvider",
        )
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.toggle_inlay_hints()

        assert editor_state.options.get("lsp_inlay_hints") is True
        assert refreshed == [(str(tmp_path / "sample.py"), 0, 1)]
        decs = editor_state.decorations.get_for_namespace(id(api._workspace.active_window.document), "lsp:hints")
        assert len(decs) == 1
        assert decs[0].text == " : int"

    def test_toggle_inlay_hints_rejects_unsupported_server(self, tmp_path):
        api, editor_state = self._make_api(tmp_path)
        editor_state.options.define("lsp_inlay_hints", bool, False)
        feats = SimpleNamespace(supports_capability=lambda name: False)
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.toggle_inlay_hints()

        assert editor_state.options.get("lsp_inlay_hints") is False
        assert editor_state.message == "Inlay hints unavailable"

    def test_refresh_document_highlight_applies_regions(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        feats = SimpleNamespace(
            document_highlight=lambda path, line, col, cb: cb(
                [{"start_line": 0, "start_col": 0, "end_line": 0, "end_col": 5, "kind": 1}]
            ),
            supports_capability=lambda name: name == "documentHighlightProvider",
        )
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.refresh_document_highlight()

        decs = api._editor_state.decorations.get_for_namespace(id(api._workspace.active_window.document), "lsp:ref")
        assert len(decs) == 1
        assert decs[0].start_line == 0
        assert decs[0].end_col == 5

    def test_refresh_document_highlight_clears_when_unsupported(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        api._editor_state.decorations.add(
            id(api._workspace.active_window.document),
            "lsp:ref",
            SimpleNamespace(),
        )
        feats = SimpleNamespace(supports_capability=lambda name: False)
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.refresh_document_highlight()

        assert (
            api._editor_state.decorations.get_for_namespace(id(api._workspace.active_window.document), "lsp:ref") == []
        )

    def test_document_symbols_uses_picker_for_results(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        picked: list[tuple[list[str], list[dict], str]] = []
        api._lsp_ui = SimpleNamespace(
            show_picker_for_locations=lambda items, locs, title="": picked.append((items, locs, title)),
        )
        feats = SimpleNamespace(
            document_symbols=lambda path, cb: cb(
                [
                    {
                        "name": "func",
                        "kind": "function",
                        "detail": "Outer",
                        "path": str(tmp_path / "sample.py"),
                        "line": 4,
                        "col": 2,
                    }
                ]
            )
        )
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.document_symbols()

        assert picked == [
            (
                ["function     func:5:3 — Outer"],
                [{"path": str(tmp_path / "sample.py"), "line": 4, "col": 2}],
                "Document Symbols",
            )
        ]

    def test_workspace_symbols_uses_word_under_cursor_query(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        api._workspace.active_window.document.load_string("alpha beta gamma\n")
        api._workspace.active_window.cursor.move_to(0, 7)
        seen_queries: list[str] = []
        picked: list[tuple[list[str], list[dict], str]] = []
        api._lsp_ui = SimpleNamespace(
            show_picker_for_locations=lambda items, locs, title="": picked.append((items, locs, title)),
        )
        feats = SimpleNamespace(
            workspace_symbols=lambda query, cb: (
                seen_queries.append(query),
                cb(
                    [
                        {
                            "name": "beta",
                            "kind": "function",
                            "detail": "pkg.mod",
                            "path": str(tmp_path / "other.py"),
                            "line": 8,
                            "col": 1,
                        }
                    ]
                ),
            )[-1]
        )
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.workspace_symbols()

        assert seen_queries == ["beta"]
        assert picked == [
            (
                ["function     beta:9:2 — pkg.mod"],
                [{"path": str(tmp_path / "other.py"), "line": 8, "col": 1}],
                "Workspace Symbols: beta",
            )
        ]

    def test_document_symbol_tree_prefers_tree_callback(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        captured: list[list[dict]] = []
        feats = SimpleNamespace(
            supports_capability=lambda name: name == "documentSymbolProvider",
            document_symbols_tree=lambda path, cb: cb(
                [
                    {
                        "name": "func",
                        "kind": "function",
                        "detail": "",
                        "path": str(tmp_path / "sample.py"),
                        "line": 4,
                        "col": 2,
                        "end_line": 6,
                        "end_col": 0,
                        "children": [],
                    }
                ]
            ),
        )
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.document_symbol_tree(lambda symbols: captured.append(symbols))

        assert captured == [
            [
                {
                    "name": "func",
                    "kind": "function",
                    "detail": "",
                    "path": str(tmp_path / "sample.py"),
                    "line": 4,
                    "col": 2,
                    "end_line": 6,
                    "end_col": 0,
                    "children": [],
                }
            ]
        ]

    def test_workspace_symbols_requires_word_under_cursor(self, tmp_path):
        api, editor_state = self._make_api(tmp_path)
        api._workspace.active_window.document.load_string("()\n")
        api._workspace.active_window.cursor.move_to(0, 0)
        feats = SimpleNamespace(workspace_symbols=lambda query, cb: cb([]))
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.workspace_symbols()

        assert editor_state.message == "No symbol under cursor"

    def test_remap_buffer_diagnostics_updates_visible_decorations(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        doc = api._workspace.active_window.document
        features = SimpleNamespace(
            remap_diagnostics=lambda _path, **kwargs: [
                {
                    "severity": 1,
                    "message": "broken",
                    "range": {
                        "start": {"line": 2, "character": 0},
                        "end": {"line": 2, "character": 4},
                    },
                }
            ]
        )
        api._manager = SimpleNamespace(
            get=lambda _doc: features,
            get_all=lambda _doc: [features],
        )

        api.remap_buffer_diagnostics(
            buf_id=id(doc),
            start_line=0,
            start_col=0,
            end_line=1,
            end_col=0,
            new_text="",
        )

        signs = api._editor_state.decorations.get_for_namespace(id(doc), "lsp:diag:signs")
        text = api._editor_state.decorations.get_for_namespace(id(doc), "lsp:diag:text")
        assert len(signs) == 1
        assert signs[0].line == 2
        assert len(text) == 1
        assert text[0].line == 2

    def test_workspace_symbol_search_uses_explicit_query(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        seen_queries: list[str] = []
        captured: list[list[dict]] = []
        feats = SimpleNamespace(
            workspace_symbols=lambda query, cb: (
                seen_queries.append(query),
                cb(
                    [
                        {
                            "name": "beta",
                            "kind": "function",
                            "detail": "pkg.mod",
                            "path": str(tmp_path / "other.py"),
                            "line": 8,
                            "col": 1,
                        }
                    ]
                ),
            )[-1]
        )
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.workspace_symbol_search("beta", lambda symbols: captured.append(symbols))

        assert seen_queries == ["beta"]
        assert captured == [
            [
                {
                    "name": "beta",
                    "kind": "function",
                    "detail": "pkg.mod",
                    "path": str(tmp_path / "other.py"),
                    "line": 8,
                    "col": 1,
                }
            ]
        ]

    def test_references_search_returns_raw_locations(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        captured: list[list[dict]] = []
        feats = SimpleNamespace(
            references=lambda path, line, col, cb: cb([{"path": str(tmp_path / "other.py"), "line": 8, "col": 1}])
        )
        api._manager = SimpleNamespace(get=lambda _doc: feats, flush_pending_changes=lambda: None)

        api.references_search(lambda locs: captured.append(locs))

        assert captured == [[{"path": str(tmp_path / "other.py"), "line": 8, "col": 1}]]

    def test_custom_request_to_prefers_active_buffer_attached_matching_server(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        received: list[tuple[str, dict]] = []
        wrong_received: list[tuple[str, dict]] = []

        right_feats = SimpleNamespace(
            _client=SimpleNamespace(_cmd=["python", "-m", "verilog_lsp"]),
            custom_request=lambda method, params, cb: (
                received.append((method, params)),
                cb({"ok": True, "root": "right"}),
            )[-1],
        )
        wrong_feats = SimpleNamespace(
            _client=SimpleNamespace(_cmd=["python", "-m", "verilog_lsp"]),
            custom_request=lambda method, params, cb: (
                wrong_received.append((method, params)),
                cb({"ok": True, "root": "wrong"}),
            )[-1],
        )

        api._manager = SimpleNamespace(
            get=lambda _doc: right_feats,
            get_all=lambda _doc: [right_feats],
            _servers={
                ("verilog", str(tmp_path / "wrong-root"), ("python", "-m", "verilog_lsp")): (None, wrong_feats),
                ("verilog", str(tmp_path), ("python", "-m", "verilog_lsp")): (None, right_feats),
            },
        )

        seen: list[dict] = []
        api.custom_request_to(
            "workspace/executeCommand",
            {"command": "verilog/previewHierarchyBoundaryMove"},
            lambda result: seen.append(result),
            cmd_contains="verilog_lsp",
        )

        assert received == [("workspace/executeCommand", {"command": "verilog/previewHierarchyBoundaryMove"})]
        assert wrong_received == []
        assert seen == [{"ok": True, "root": "right"}]

    def test_custom_request_to_falls_back_to_global_matching_server_when_unattached(self, tmp_path):
        api, _editor_state = self._make_api(tmp_path)
        received: list[tuple[str, dict]] = []
        feats = SimpleNamespace(
            _client=SimpleNamespace(_cmd=["python", "-m", "verilog_lsp"]),
            custom_request=lambda method, params, cb: (
                received.append((method, params)),
                cb({"ok": True}),
            )[-1],
        )

        api._manager = SimpleNamespace(
            get=lambda _doc: None,
            get_all=lambda _doc: [],
            _servers={
                ("verilog", str(tmp_path), ("python", "-m", "verilog_lsp")): (None, feats),
            },
        )

        seen: list[dict] = []
        api.custom_request_to(
            "workspace/executeCommand",
            {"command": "verilog/previewHierarchyBoundaryMove"},
            lambda result: seen.append(result),
            cmd_contains="verilog_lsp",
        )

        assert received == [("workspace/executeCommand", {"command": "verilog/previewHierarchyBoundaryMove"})]
        assert seen == [{"ok": True}]

    def test_manager_detach_noop_if_not_attached(self):
        from peovim.core.document import Document
        from peovim.lsp.manager import LspManager

        q: deque = deque()
        mgr = LspManager(q)
        doc = Document()
        mgr.detach(doc)  # should not raise

    def test_manager_notify_change_noop_if_not_attached(self):
        from peovim.core.document import Document
        from peovim.lsp.manager import LspManager

        q: deque = deque()
        mgr = LspManager(q)
        doc = Document()
        mgr.notify_change(doc)  # should not raise

    def test_manager_notify_save_noop_if_not_attached(self):
        from peovim.core.document import Document
        from peovim.lsp.manager import LspManager

        q: deque = deque()
        mgr = LspManager(q)
        doc = Document()
        mgr.notify_save(doc)  # should not raise

    def test_manager_restart_empty(self):
        from peovim.lsp.manager import LspManager

        q: deque = deque()
        mgr = LspManager(q)
        mgr.restart()  # should not raise when no servers active

    def test_manager_get_configs_returns_multiple_configs_for_same_filetype(self):
        from peovim.lsp.manager import LspManager, ServerConfig

        q: deque = deque()
        mgr = LspManager(q)
        mgr._configs = [
            ServerConfig("python", ["ty", "server"]),
            ServerConfig("python", ["ruff", "server"]),
        ]

        configs = mgr.get_configs("python")

        assert [cfg.cmd for cfg in configs] == [["ty", "server"], ["ruff", "server"]]

    def test_manager_notify_change_and_save_fan_out_to_all_attached_servers(self, tmp_path):
        from peovim.core.document import Document
        from peovim.lsp.manager import LspManager

        q: deque = deque()
        mgr = LspManager(q)
        doc = Document(path=tmp_path / "sample.py")
        doc.load_string("value = 1\n")
        key1 = ("python", str(tmp_path), ("ty", "server"))
        key2 = ("python", str(tmp_path), ("ruff", "server"))
        feats1 = SimpleNamespace(did_change=MagicMock(), did_save=MagicMock())
        feats2 = SimpleNamespace(did_change=MagicMock(), did_save=MagicMock())
        mgr._doc_servers[id(doc)] = [key1, key2]
        mgr._servers[key1] = (SimpleNamespace(), feats1)
        mgr._servers[key2] = (SimpleNamespace(), feats2)
        mgr._loop = SimpleNamespace(call_soon_threadsafe=lambda fn, *args: fn(*args))

        mgr.notify_change(doc)
        mgr.notify_save(doc)

        feats1.did_change.assert_called_once_with(str(doc.path), "value = 1\n")
        feats2.did_change.assert_called_once_with(str(doc.path), "value = 1\n")
        feats1.did_save.assert_called_once_with(str(doc.path))
        feats2.did_save.assert_called_once_with(str(doc.path))


def _make_mgr_with_doc(tmp_path, text="hello\n"):
    """Return (mgr, doc, feats) wired with a synchronous event loop."""
    from peovim.core.document import Document
    from peovim.lsp.manager import LspManager

    q: deque = deque()
    mgr = LspManager(q)
    doc = Document(path=tmp_path / "sample.py")
    doc.load_string(text)
    key = ("python", str(tmp_path), ("ty", "server"))
    feats = SimpleNamespace(did_change=MagicMock(), did_save=MagicMock())
    mgr._doc_servers[id(doc)] = [key]
    mgr._servers[key] = (SimpleNamespace(), feats)
    mgr._loop = SimpleNamespace(call_soon_threadsafe=lambda fn, *args: fn(*args))
    return mgr, doc, feats


class TestLspDebounce:
    def test_notify_change_does_not_send_immediately(self, tmp_path):
        mgr, doc, feats = _make_mgr_with_doc(tmp_path)
        mgr.notify_change(doc)
        feats.did_change.assert_not_called()

    def test_flush_pending_sends_once_regardless_of_call_count(self, tmp_path):
        mgr, doc, feats = _make_mgr_with_doc(tmp_path)
        mgr.notify_change(doc)
        mgr.notify_change(doc)
        mgr.notify_change(doc)
        mgr.flush_pending_changes()
        assert feats.did_change.call_count == 1

    def test_flush_sends_latest_text(self, tmp_path):
        mgr, doc, feats = _make_mgr_with_doc(tmp_path, "old\n")
        mgr.notify_change(doc)
        doc.load_string("new\n")
        mgr.notify_change(doc)
        mgr.flush_pending_changes()
        feats.did_change.assert_called_once_with(str(doc.path), "new\n")

    def test_flush_twice_sends_once(self, tmp_path):
        mgr, doc, feats = _make_mgr_with_doc(tmp_path)
        mgr.notify_change(doc)
        mgr.flush_pending_changes()
        mgr.flush_pending_changes()
        assert feats.did_change.call_count == 1

    def test_notify_save_flushes_pending_change_first(self, tmp_path):
        mgr, doc, feats = _make_mgr_with_doc(tmp_path)
        mgr.notify_change(doc)
        mgr.notify_save(doc)
        feats.did_change.assert_called_once()
        feats.did_save.assert_called_once()

    def test_notify_save_change_before_save(self, tmp_path):
        """did_change must be sent before did_save."""
        call_order = []
        mgr, doc, feats = _make_mgr_with_doc(tmp_path)
        feats.did_change.side_effect = lambda *a: call_order.append("change")
        feats.did_save.side_effect = lambda *a: call_order.append("save")
        mgr.notify_change(doc)
        mgr.notify_save(doc)
        assert call_order == ["change", "save"]

    def test_pending_cleared_after_flush(self, tmp_path):
        mgr, doc, feats = _make_mgr_with_doc(tmp_path)
        mgr.notify_change(doc)
        mgr.flush_pending_changes()
        mgr.notify_change(doc)
        mgr.flush_pending_changes()
        assert feats.did_change.call_count == 2

    def test_timer_cleared_on_flush(self, tmp_path):
        mgr, doc, feats = _make_mgr_with_doc(tmp_path)
        mgr.notify_change(doc)
        assert mgr._notify_timer is not None
        mgr.flush_pending_changes()
        assert mgr._notify_timer is None

    def test_fire_pending_drains_dict(self, tmp_path):
        mgr, doc, feats = _make_mgr_with_doc(tmp_path)
        mgr.notify_change(doc)
        mgr._fire_pending()
        feats.did_change.assert_called_once()
        assert not mgr._pending_notify

    def test_two_docs_both_flushed(self, tmp_path):
        from peovim.core.document import Document
        from peovim.lsp.manager import LspManager

        q: deque = deque()
        mgr = LspManager(q)
        doc1 = Document(path=tmp_path / "a.py")
        doc1.load_string("a\n")
        doc2 = Document(path=tmp_path / "b.py")
        doc2.load_string("b\n")
        key1 = ("python", str(tmp_path), ("ty", "server"))
        key2 = ("python", str(tmp_path), ("ruff", "server"))
        feats1 = SimpleNamespace(did_change=MagicMock(), did_save=MagicMock())
        feats2 = SimpleNamespace(did_change=MagicMock(), did_save=MagicMock())
        mgr._doc_servers[id(doc1)] = [key1]
        mgr._doc_servers[id(doc2)] = [key2]
        mgr._servers[key1] = (SimpleNamespace(), feats1)
        mgr._servers[key2] = (SimpleNamespace(), feats2)
        mgr._loop = SimpleNamespace(call_soon_threadsafe=lambda fn, *args: fn(*args))

        mgr.notify_change(doc1)
        mgr.notify_change(doc2)
        mgr.flush_pending_changes()

        feats1.did_change.assert_called_once_with(str(doc1.path), "a\n")
        feats2.did_change.assert_called_once_with(str(doc2.path), "b\n")

    def test_flush_noop_when_nothing_pending(self, tmp_path):
        mgr, doc, feats = _make_mgr_with_doc(tmp_path)
        mgr.flush_pending_changes()  # should not raise
        feats.did_change.assert_not_called()

    def test_notify_change_noop_without_loop(self, tmp_path):
        mgr, doc, feats = _make_mgr_with_doc(tmp_path)
        mgr._loop = None
        mgr.notify_change(doc)
        mgr.flush_pending_changes()
        feats.did_change.assert_not_called()


class TestCompletionPopup:
    def _make_items(self, n=5):
        return [
            {
                "label": f"item{i}",
                "kind": i + 1,
                "detail": f"detail{i}",
                "insertText": f"item{i}()",
                "filterText": f"item{i}",
            }
            for i in range(n)
        ]

    def test_initially_closed(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        assert not cp.is_open

    def test_open_close(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(), 0, 0)
        assert cp.is_open
        cp.close()
        assert not cp.is_open

    def test_open_empty_stays_closed(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open([], 0, 0)
        assert not cp.is_open

    def test_accept_returns_insert_text(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(), 0, 0)
        text = cp.accept()
        assert text == "item0()"
        assert not cp.is_open

    def test_navigate_down(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(3), 0, 0)
        cp.feed_key("<Down>")
        assert cp.accept() == "item1()"

    def test_navigate_up_wraps(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(3), 0, 0)
        cp.feed_key("<Up>")
        assert cp.accept() == "item2()"

    def test_escape_closes(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(), 0, 0)
        consumed = cp.feed_key("<Esc>")
        assert consumed
        assert not cp.is_open

    def test_ctrl_e_closes(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(), 0, 0)
        cp.feed_key("<C-e>")
        assert not cp.is_open

    def test_tab_not_consumed(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(), 0, 0)
        consumed = cp.feed_key("<Tab>")
        assert not consumed
        assert cp.is_open

    def test_filter_narrows_items(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        items = [
            {"label": "foo", "kind": 1, "detail": "", "insertText": "foo", "filterText": "foo"},
            {"label": "bar", "kind": 1, "detail": "", "insertText": "bar", "filterText": "bar"},
        ]
        cp.open(items, 0, 0, filter_text="foo")
        assert cp.is_open
        text = cp.accept()
        assert text == "foo"

    def test_update_filter_closes_when_no_match(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(), 0, 0)
        cp.update_filter("zzzzznomatch")
        assert not cp.is_open

    def test_prefix_filter_closes_when_only_substring_matches(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(), 0, 0, match_mode="prefix")
        cp.update_filter("tem1")
        assert not cp.is_open

    def test_accept_replaces_prefix_when_requested(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(
            [{"label": "foobar", "kind": 1, "detail": "", "insertText": "foobar", "filterText": "foobar"}],
            0,
            0,
            filter_text="foo",
            match_mode="prefix",
            replace_filter_on_accept=True,
        )
        assert cp.accept() == "bar"

    def test_ctrl_n_ctrl_p(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(5), 0, 0)
        cp.feed_key("<C-n>")
        cp.feed_key("<C-n>")
        assert cp.accept() == "item2()"

    def test_render_does_not_crash(self):
        from peovim.ui.cell_grid import CellGrid
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(5), 5, 10)
        grid = CellGrid(80, 24)
        cp.render(grid, 5, 10)

    def test_render_draws_border_and_first_item(self):
        from peovim.ui.cell_grid import CellGrid
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(3), 5, 10)
        grid = CellGrid(80, 24)

        cp.render(grid, 5, 10)

        assert grid._current[6][10][0] == "┌"
        row_text = "".join(cell[0] for cell in grid._current[7])
        assert "item0" in row_text

    def test_render_closed_noop(self):
        from peovim.ui.cell_grid import CellGrid
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        grid = CellGrid(80, 24)
        cp.render(grid, 5, 10)  # should not raise

    def test_accept_none_when_closed(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        assert cp.accept() is None

    def test_render_near_bottom_right_edge(self):
        from peovim.ui.cell_grid import CellGrid
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        cp.open(self._make_items(15), 20, 75)
        grid = CellGrid(80, 24)
        cp.render(grid, 22, 75)  # should not raise

    def test_scroll_when_many_items(self):
        from peovim.ui.completion import CompletionPopup

        cp = CompletionPopup()
        items = self._make_items(20)
        cp.open(items, 0, 0)
        # Navigate past the visible window
        for _ in range(12):
            cp.feed_key("<Down>")
        assert cp.accept() == "item12()"
