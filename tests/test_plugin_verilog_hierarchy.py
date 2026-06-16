"""Tests for VerilogHierarchyPanel."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import peovim.plugins.verilog_lsp.plugin as plugin
from peovim.commands.parser import parse_ex_command
from peovim.plugins import proposed_review
from peovim.plugins.verilog_lsp.hierarchy_panel import (
    VerilogHierarchyPanel,
    _format_boundary_move_preview,
    _format_collapse_preview,
    _format_extract_preview,
)


def _make_api(sidebar_visible: bool = False) -> MagicMock:
    api = MagicMock()
    api.ui.is_sidebar_visible.return_value = sidebar_visible
    return api


# ── update() ─────────────────────────────────────────────────────────────────


class TestUpdate:
    def test_single_root_title(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel.update([{"name": "top", "children": []}])
        assert "top" in panel._title
        assert "✓" in panel._title

    def test_multi_root_title(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel.update([{"name": "a"}, {"name": "b"}])
        assert "auto" in panel._title
        assert "2" in panel._title

    def test_empty_roots_resets_title(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel.update([])
        assert panel._title == "Hierarchy"

    def test_parse_state_becomes_ready(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel.update([{"name": "top"}])
        assert panel._parse_state == "ready"

    def test_refreshes_if_visible(self):
        api = _make_api(sidebar_visible=True)
        panel = VerilogHierarchyPanel(api)
        panel.update([{"name": "top"}])
        api.ui.show_sidebar_panel.assert_called()


# ── on_progress() ────────────────────────────────────────────────────────────


class TestOnProgress:
    def test_begin_sets_parsing_state(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel.on_progress("begin", {"message": "top.v"})
        assert "parsing" in panel._parse_state
        assert "top.v" in panel._title

    def test_report_updates_title(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel.on_progress("report", {"message": "3/10"})
        assert "3/10" in panel._title

    def test_end_does_not_change_title(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel._title = "Hierarchy  [sentinel]"
        panel.on_progress("end", {})
        assert "sentinel" in panel._title


# ── _build_nodes / _dict_to_node ─────────────────────────────────────────────


class TestBuildNodes:
    def test_leaf_node_label(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        nodes = panel._build_nodes([{"instanceName": "u1", "moduleName": "adder", "children": []}])
        assert len(nodes) == 1
        assert "u1" in nodes[0].label
        assert "adder" in nodes[0].label

    def test_same_name_no_brackets(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        nodes = panel._build_nodes([{"name": "top", "moduleName": "top", "children": []}])
        assert "[top]" not in nodes[0].label

    def test_root_node_expanded(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        nodes = panel._build_nodes([{"name": "top", "children": [{"name": "child"}]}])
        assert nodes[0].expanded is True

    def test_children_fn_for_lazy(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        nodes = panel._build_nodes([{"name": "top", "hasMoreChildren": True, "children": []}])
        node = nodes[0]
        assert node.children_fn is not None

    def test_safe_wrapper_label_has_collapse_badge_and_color(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        nodes = panel._build_nodes(
            [
                {
                    "instanceName": "u_wrap",
                    "moduleName": "wrapper",
                    "wrapperClass": "pure_pass_through",
                    "confidence": "safe",
                    "refactorActions": ["previewCollapse"],
                    "children": [],
                }
            ]
        )
        assert "<collapse>" in nodes[0].label
        assert nodes[0].fg == (120, 220, 120)

    def test_refactor_marks_are_rendered_as_source_and_destination_badges(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel._refactor_source_path = "top/u_wrap"
        panel._refactor_destination_path = "top"

        roots = panel._build_nodes(
            [
                {
                    "name": "top",
                    "moduleName": "top",
                    "children": [
                        {
                            "instanceName": "u_wrap",
                            "moduleName": "wrapper",
                            "instancePath": "top/u_wrap",
                            "children": [],
                        }
                    ],
                }
            ]
        )

        child = roots[0].children_fn()[0]
        assert "[DST]" in roots[0].label
        assert "[SRC]" in child.label

    def test_lazy_children_request_includes_instance_path(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel._lazy_load_children("wrapper", "top/u_wrap")
        payload = api.lsp.custom_request_to.call_args.args[1]
        assert payload["command"] == "verilog/resolveHierarchyChildren"
        assert payload["arguments"] == [{"moduleName": "wrapper", "instancePath": "top/u_wrap"}]


class TestCollapseActions:
    _SAFE_NODE = MagicMock(
        value={
            "instanceName": "u_wrap",
            "moduleName": "wrapper",
            "instancePath": "top/u_wrap",
            "wrapperClass": "pure_pass_through",
            "refactorActions": ["previewCollapse"],
        }
    )

    def test_preview_key_requests_collapse_preview(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)

        consumed = panel._on_key("c", self._SAFE_NODE)

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert consumed is True
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        assert payload["arguments"] == [
            {
                "direction": "collapse",
                "selection": {"kind": "instance", "instancePath": "top/u_wrap"},
            }
        ]
        assert panel._refactor_source_path == "top/u_wrap"
        assert panel._refactor_destination_path == "top"

    def test_preview_key_falls_back_to_pull_up_for_structural_instance(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        node = MagicMock(
            value={
                "instanceName": "u_wrap",
                "moduleName": "wrapper",
                "instancePath": "top/u_wrap",
                "wrapperClass": "structural_wrapper",
                "refactorActions": [],
            }
        )

        consumed = panel._on_key("c", node)

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert consumed is True
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        assert payload["arguments"] == [
            {
                "direction": "pull_up",
                "selection": {"kind": "instance", "instancePath": "top/u_wrap"},
                "targetParentPath": "top",
            }
        ]
        assert panel._refactor_source_path == "top/u_wrap"
        assert panel._refactor_destination_path == "top"

    def test_preview_key_uses_marked_source_and_destination_for_pull_up(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel._refactor_source_path = "top/u_wrap"
        panel._refactor_destination_path = "top"
        panel._refactor_source_explicit = True
        panel._refactor_destination_explicit = True
        selected_destination = MagicMock(value={"name": "top", "moduleName": "top"})

        consumed = panel._on_key("c", selected_destination)

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert consumed is True
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        assert payload["arguments"] == [
            {
                "direction": "pull_up",
                "selection": {"kind": "instance", "instancePath": "top/u_wrap"},
                "targetParentPath": "top",
            }
        ]

    def test_preview_key_supports_multi_level_pull_up_with_ancestor_destination(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel._refactor_source_path = "top/u_outer/u_mid/u_core"
        panel._refactor_destination_path = "top"
        panel._refactor_source_explicit = True
        panel._refactor_destination_explicit = True
        selected_destination = MagicMock(value={"name": "top", "moduleName": "top"})

        consumed = panel._on_key("c", selected_destination)

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert consumed is True
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        assert payload["arguments"] == [
            {
                "direction": "pull_up",
                "selection": {
                    "kind": "instance",
                    "instancePath": "top/u_outer/u_mid/u_core",
                },
                "targetParentPath": "top",
            }
        ]

    def test_source_and_destination_keys_mark_refactor_boundary(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        source = MagicMock(value={"instanceName": "u_wrap", "instancePath": "top/u_wrap"})
        destination = MagicMock(value={"name": "top", "moduleName": "top"})

        assert panel._on_key("s", source) is True
        assert panel._on_key("t", destination) is True

        assert panel._refactor_source_path == "top/u_wrap"
        assert panel._refactor_destination_path == "top"
        assert "top/u_wrap -> top" in panel._title

    def test_escape_closes_preview_and_clears_refactor_marks(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        handle = MagicMock()
        panel._preview_float = handle
        panel._refactor_source_path = "top/u_wrap"
        panel._refactor_destination_path = "top"

        assert panel._on_key("Esc", MagicMock(value={})) is True

        handle.close.assert_called_once()
        assert panel._preview_float is None
        assert panel._refactor_source_path == ""
        assert panel._refactor_destination_path == ""
        api.set_status.assert_called_with("Hierarchy refactor selection cleared")

    def test_uppercase_c_key_reuses_collapse_preview(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)

        consumed = panel._on_key("C", self._SAFE_NODE)

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert consumed is True
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        assert payload["arguments"] == [
            {
                "direction": "collapse",
                "selection": {"kind": "instance", "instancePath": "top/u_wrap"},
            }
        ]

    def test_apply_key_with_marked_pull_up_requests_apply_ready_preview(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel._refactor_source_path = "top/u_wrap"
        panel._refactor_destination_path = "top"
        panel._refactor_source_explicit = True
        panel._refactor_destination_explicit = True

        consumed = panel._on_key("C", MagicMock(value={"name": "top", "moduleName": "top"}))

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert consumed is True
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        assert payload["arguments"] == [
            {
                "direction": "pull_up",
                "selection": {"kind": "instance", "instancePath": "top/u_wrap"},
                "targetParentPath": "top",
            }
        ]

    def test_apply_key_for_structural_instance_requests_pull_up_apply_ready_preview(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        node = MagicMock(
            value={
                "instanceName": "u_wrap",
                "moduleName": "wrapper",
                "instancePath": "top/u_wrap",
                "wrapperClass": "structural_wrapper",
                "refactorActions": [],
            }
        )

        consumed = panel._on_key("C", node)

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert consumed is True
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        assert payload["arguments"] == [
            {
                "direction": "pull_up",
                "selection": {"kind": "instance", "instancePath": "top/u_wrap"},
                "targetParentPath": "top",
            }
        ]

    def test_apply_result_applies_workspace_edit(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        edit = {"changes": {"file:///top.v": []}}

        panel._on_collapse_preview({"ok": True, "preview": {"ok": True}, "edit": edit}, apply_edit=True)

        api.lsp.apply_workspace_edit.assert_called_once_with(edit)
        api.set_status.assert_called_once_with("Applied hier-up edit")

    def test_apply_ready_collapse_preview_opens_proposed_review(self, monkeypatch):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        edit = {"changes": {"file:///top.v": []}}
        reviews = []
        monkeypatch.setattr(
            proposed_review, "open_proposed_edits", lambda _api, review_list: reviews.extend(review_list)
        )

        panel._on_collapse_preview(
            {
                "ok": True,
                "preview": {"ok": True, "instancePath": "top/u_wrap"},
                "edit": edit,
                "review": {
                    "files": [
                        {
                            "currentLabel": "current top.v",
                            "proposedLabel": "proposed top.v",
                            "currentText": "module top; endmodule",
                            "proposedText": "module top; wire x; endmodule",
                        }
                    ]
                },
            },
            apply_edit=False,
        )

        assert len(reviews) == 1
        assert reviews[0].title == "Hier-up top/u_wrap"
        assert reviews[0].current_label == "current top.v"
        api.ui.open_float.assert_not_called()
        reviews[0].on_confirm()
        api.lsp.apply_workspace_edit.assert_called_once_with(edit)
        api.set_status.assert_called_with("Applied hier-up edit")

    def test_blocked_apply_result_does_not_apply_edit(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)

        panel._on_collapse_preview(
            {
                "ok": False,
                "preview": {
                    "ok": False,
                    "diagnostics": [{"severity": "error", "code": "blocked", "message": "not safe"}],
                },
            },
            apply_edit=True,
        )

        api.lsp.apply_workspace_edit.assert_not_called()
        api.set_status.assert_called_once_with("Hier-up edit was not available")

    def test_push_down_preview_key_opens_cmdline_with_module_name(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        node = MagicMock(value={"name": "top", "moduleName": "top"})

        consumed = panel._on_key("w", node)

        assert consumed is True
        api.open_cmdline.assert_called_once_with("VerilogHierDown ")
        assert panel._pending_push_down == {"moduleName": "top", "instancePath": "", "apply": False}

    def test_uppercase_w_key_reuses_hier_down_cmdline(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        node = MagicMock(value={"instanceName": "u_wrap", "moduleName": "wrapper", "instancePath": "top/u_wrap"})

        consumed = panel._on_key("W", node)

        assert consumed is True
        api.open_cmdline.assert_called_once_with("VerilogHierDown ")
        assert panel._pending_push_down == {
            "moduleName": "wrapper",
            "instancePath": "top/u_wrap",
            "apply": False,
        }

    def test_push_down_command_issues_lsp_request_with_new_names(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel._pending_push_down = {"moduleName": "top", "instancePath": "", "apply": False}

        panel.request_push_down_from_command("top_partition u_partition", apply_edit=False)

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        assert payload["arguments"] == [
            {
                "direction": "push_down",
                "selection": {"kind": "module", "moduleName": "top"},
                "newModuleName": "top_partition",
                "newInstanceName": "u_partition",
            }
        ]
        assert panel._pending_push_down is None

    def test_push_down_command_uses_instance_kind_when_pending_has_path(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel._pending_push_down = {
            "moduleName": "wrapper",
            "instancePath": "top/u_wrap",
            "apply": False,
        }

        panel.request_push_down_from_command("wrapper_core u_core", apply_edit=False)

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert payload["arguments"] == [
            {
                "direction": "push_down",
                "selection": {"kind": "instance", "instancePath": "top/u_wrap"},
                "newModuleName": "wrapper_core",
                "newInstanceName": "u_core",
            }
        ]

    def test_push_down_command_omits_instance_name_when_blank(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel._pending_push_down = {"moduleName": "top", "instancePath": "", "apply": True}

        panel.request_push_down_from_command("top_partition", apply_edit=True)

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert payload["arguments"] == [
            {
                "direction": "push_down",
                "selection": {"kind": "module", "moduleName": "top"},
                "newModuleName": "top_partition",
            }
        ]

    def test_push_down_command_without_pending_target_sets_status(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)

        panel.request_push_down_from_command("top_partition", apply_edit=False)

        api.set_status.assert_called_once()
        api.lsp.custom_request_to.assert_not_called()

    def test_push_down_range_command_issues_kind_range_request(self, tmp_path):
        api = _make_api()
        source = tmp_path / "top.v"
        source.write_text(
            "module top;\nassign y = a & b;\nassign z = a | b;\nendmodule\n",
            encoding="utf-8",
        )
        buffer = MagicMock()
        buffer.path = source
        buffer.get_line.side_effect = lambda line: source.read_text(encoding="utf-8").splitlines()[line]
        api.active_buffer.return_value = buffer
        api.active_window.return_value = MagicMock(cursor=(1, 0))
        api.active_mode = "normal"
        panel = VerilogHierarchyPanel(api)

        panel.request_push_down_range_from_command(
            "top_core u_core",
            line_range=(1, 2),
            apply_edit=False,
        )

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        request = payload["arguments"][0]
        assert request["direction"] == "push_down"
        assert request["selection"]["kind"] == "range"
        assert request["selection"]["textDocument"]["uri"] == Path(source).resolve().as_uri()
        assert request["selection"]["range"] == {
            "start": {"line": 1, "character": 0},
            "end": {"line": 2, "character": len("assign z = a | b;")},
        }
        assert request["newModuleName"] == "top_core"
        assert request["newInstanceName"] == "u_core"

    def test_push_down_range_command_omits_instance_name_when_blank(self, tmp_path):
        api = _make_api()
        source = tmp_path / "top.v"
        source.write_text("module top;\nassign y = a;\nendmodule\n", encoding="utf-8")
        buffer = MagicMock()
        buffer.path = source
        buffer.get_line.side_effect = lambda line: source.read_text(encoding="utf-8").splitlines()[line]
        api.active_buffer.return_value = buffer
        api.active_window.return_value = MagicMock(cursor=(1, 0))
        api.active_mode = "normal"
        panel = VerilogHierarchyPanel(api)

        panel.request_push_down_range_from_command(
            "top_core",
            line_range=(1, 1),
            apply_edit=True,
        )

        request = api.lsp.custom_request_to.call_args.args[1]["arguments"][0]
        assert request["newModuleName"] == "top_core"
        assert "newInstanceName" not in request

    def test_push_down_range_command_routes_response_to_extract_handler(self, tmp_path):
        api = _make_api()
        source = tmp_path / "top.v"
        source.write_text("module top;\nassign y = a;\nendmodule\n", encoding="utf-8")
        buffer = MagicMock()
        buffer.path = source
        buffer.get_line.side_effect = lambda line: source.read_text(encoding="utf-8").splitlines()[line]
        api.active_buffer.return_value = buffer
        api.active_window.return_value = MagicMock(cursor=(1, 0))
        api.active_mode = "normal"
        panel = VerilogHierarchyPanel(api)
        panel._on_extract_preview = MagicMock()

        panel.request_push_down_range_from_command("top_core", line_range=(1, 1), apply_edit=False)

        cb = api.lsp.custom_request_to.call_args.kwargs["cb"]
        result = {"ok": False, "preview": {"ok": False}, "details": {"ok": False}}
        cb(result)
        panel._on_extract_preview.assert_called_once_with(result, apply_edit=False)

    def test_push_down_range_command_without_module_name_sets_status(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)

        panel.request_push_down_range_from_command("", line_range=(1, 2), apply_edit=False)

        api.set_status.assert_called_once()
        api.lsp.custom_request_to.assert_not_called()

    def test_push_down_range_response_with_apply_ready_opens_review(self, monkeypatch):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        edit = {"changes": {"file:///top.v": []}}
        reviews = []
        monkeypatch.setattr(
            proposed_review, "open_proposed_edits", lambda _api, review_list: reviews.extend(review_list)
        )

        panel._on_push_down_preview(
            {
                "ok": True,
                "preview": {
                    "ok": True,
                    "applyReady": True,
                    "source": {"moduleName": "top"},
                    "metadata": {"pushDownMode": "range", "origin": "extract"},
                },
                "edit": edit,
                "review": {
                    "files": [
                        {
                            "currentLabel": "current top.v",
                            "proposedLabel": "proposed top.v",
                            "currentText": "module top; assign y = a; endmodule",
                            "proposedText": "module top; top_core u(); endmodule",
                        }
                    ]
                },
            },
            apply_edit=False,
        )

        assert len(reviews) == 1
        assert reviews[0].title == "Hier-down top"

    def test_prompt_push_down_range_stashes_range_and_opens_cmdline(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)

        panel.prompt_push_down_range((3, 7), apply_edit=False)

        api.open_cmdline.assert_called_once_with("VerilogHierDownRange ")
        assert panel._pending_push_down_range == (3, 7)

    def test_prompt_push_down_range_uppercase_alias_uses_same_command(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)

        panel.prompt_push_down_range((1, 4), apply_edit=True)

        api.open_cmdline.assert_called_once_with("VerilogHierDownRange ")
        assert panel._pending_push_down_range == (1, 4)

    def test_push_down_range_command_consumes_pending_range_when_unset(self, tmp_path):
        api = _make_api()
        source = tmp_path / "top.v"
        source.write_text(
            "module top;\nassign y = a & b;\nassign z = a | b;\nendmodule\n",
            encoding="utf-8",
        )
        buffer = MagicMock()
        buffer.path = source
        buffer.get_line.side_effect = lambda line: source.read_text(encoding="utf-8").splitlines()[line]
        api.active_buffer.return_value = buffer
        api.active_window.return_value = MagicMock(cursor=(0, 0))
        api.active_mode = "normal"
        panel = VerilogHierarchyPanel(api)
        panel._pending_push_down_range = (1, 2)

        panel.request_push_down_range_from_command("top_core u_core", apply_edit=False)

        request = api.lsp.custom_request_to.call_args.args[1]["arguments"][0]
        assert request["selection"]["range"] == {
            "start": {"line": 1, "character": 0},
            "end": {"line": 2, "character": len("assign z = a | b;")},
        }
        assert panel._pending_push_down_range is None

    def test_push_down_range_command_clears_pending_when_no_module_name(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel._pending_push_down_range = (3, 5)

        panel.request_push_down_range_from_command("", apply_edit=False)

        api.set_status.assert_called_once()
        api.lsp.custom_request_to.assert_not_called()
        assert panel._pending_push_down_range is None

    def test_push_down_apply_ready_preview_opens_proposed_review(self, monkeypatch):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        edit = {"changes": {"file:///top.v": []}}
        reviews = []
        monkeypatch.setattr(
            proposed_review, "open_proposed_edits", lambda _api, review_list: reviews.extend(review_list)
        )

        panel._on_push_down_preview(
            {
                "ok": True,
                "preview": {"ok": True, "applyReady": True, "source": {"moduleName": "top"}},
                "edit": edit,
                "review": {
                    "files": [
                        {
                            "currentLabel": "current top.v",
                            "proposedLabel": "proposed top.v",
                            "currentText": "module top; endmodule",
                            "proposedText": "module top; top_core u (); endmodule",
                        }
                    ]
                },
            },
            apply_edit=False,
        )

        assert len(reviews) == 1
        assert reviews[0].title == "Hier-down top"
        reviews[0].on_confirm()
        api.lsp.apply_workspace_edit.assert_called_once_with(edit)
        api.set_status.assert_called_with("Applied hier-down edit")

    def test_push_down_blocked_apply_sets_status(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)

        panel._on_push_down_preview(
            {
                "ok": False,
                "preview": {
                    "ok": False,
                    "diagnostics": [{"severity": "error", "code": "blocked", "message": "no"}],
                },
            },
            apply_edit=True,
        )

        api.lsp.apply_workspace_edit.assert_not_called()
        api.set_status.assert_called_with("Hier-down edit was not available")

    def test_graph_key_requests_hierarchy_graph(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)

        consumed = panel._on_key("g", self._SAFE_NODE)

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert consumed is True
        assert payload["command"] == "verilog/hierarchyGraph"
        assert payload["arguments"] == [{"format": "json"}]

    def test_candidate_graph_opens_picker(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        panel._on_candidate_graph(
            {
                "ok": True,
                "hierarchyGraph": {
                    "wrappers": [
                        {
                            "instancePath": "top/u_wrap",
                            "moduleName": "wrapper",
                            "wrapperClass": "pure_pass_through",
                            "confidence": "safe",
                            "refactorActions": ["previewCollapse"],
                        }
                    ]
                },
            }
        )

        api.ui.open_picker.assert_called_once()
        source = api.ui.open_picker.call_args.kwargs["source"]
        assert "top/u_wrap" in str(source[0])

    def test_format_collapse_preview_includes_diff_and_diagnostics(self):
        lines = _format_collapse_preview(
            {
                "operation": "collapseHierarchy",
                "instancePath": "top/u_wrap",
                "ok": False,
                "confidence": "blocked",
                "diagnostics": [{"severity": "error", "code": "bad", "message": "blocked"}],
                "diff": "--- before\n+++ after",
            }
        )

        text = "\n".join(lines)
        assert "top/u_wrap" in text
        assert "bad" in text
        assert "--- before" in text

    def test_boundary_move_preview_format_shows_pull_up_summary(self):
        lines = _format_boundary_move_preview(
            {
                "operation": "moveHierarchyBoundary",
                "direction": "pull_up",
                "ok": True,
                "applyReady": False,
                "confidence": "planning",
                "source": {"moduleName": "wrapper", "instancePath": "top/u_wrap", "instanceName": "u_wrap"},
                "parent": {"moduleName": "top", "instancePath": "top"},
                "movedItems": {"instances": ["u_core"], "nets": ["mid"], "alwaysBlocks": 1},
                "afterHierarchy": {"removedInstancePath": "top/u_wrap", "mergedIntoPath": "top"},
            }
        )

        text = "\n".join(lines)
        assert "Direction: hier-up" in text
        assert "path:   top/u_wrap" in text
        assert "instances: u_core" in text
        assert "Preview only" in text

    def test_boundary_move_preview_opens_side_by_side_review(self, monkeypatch):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        reviews = []
        monkeypatch.setattr(proposed_review, "open_proposed_edit", lambda _api, review: reviews.append(review))

        panel._on_boundary_move_preview(
            {
                "ok": True,
                "preview": {
                    "operation": "moveHierarchyBoundary",
                    "direction": "pull_up",
                    "ok": True,
                    "applyReady": False,
                    "confidence": "planning",
                    "source": {"moduleName": "wrapper", "instancePath": "top/u_wrap", "instanceName": "u_wrap"},
                    "parent": {"moduleName": "top", "instancePath": "top"},
                    "movedItems": {"instances": ["u_core"], "nets": ["mid"], "alwaysBlocks": 1},
                    "beforeHierarchy": {"selectedPath": "top/u_wrap", "parentPath": "top"},
                    "afterHierarchy": {"removedInstancePath": "top/u_wrap", "mergedIntoPath": "top"},
                },
            }
        )

        assert len(reviews) == 1
        assert reviews[0].current_label == "Current hierarchy"
        assert reviews[0].proposed_label == "Planned hierarchy (preview only)"
        assert "Before hierarchy" in reviews[0].current_text
        assert "After hierarchy" in reviews[0].proposed_text
        api.ui.open_float.assert_not_called()

    def test_apply_ready_boundary_move_opens_workspace_edit_review(self, monkeypatch):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        edit = {"changes": {"file:///top.v": []}}
        reviews = []
        monkeypatch.setattr(
            proposed_review, "open_proposed_edits", lambda _api, review_list: reviews.extend(review_list)
        )

        panel._on_boundary_move_preview(
            {
                "ok": True,
                "preview": {
                    "operation": "moveHierarchyBoundary",
                    "direction": "pull_up",
                    "ok": True,
                    "applyReady": True,
                    "source": {"moduleName": "wrapper", "instancePath": "top/u_wrap", "instanceName": "u_wrap"},
                },
                "edit": edit,
                "review": {
                    "files": [
                        {
                            "currentLabel": "current top.v",
                            "proposedLabel": "proposed top.v",
                            "currentText": "module top; wrapper u_wrap(); endmodule",
                            "proposedText": "module top; wire u_wrap__mid; endmodule",
                        }
                    ]
                },
            }
        )

        assert len(reviews) == 1
        assert reviews[0].title == "Hier-up top/u_wrap"
        reviews[0].on_confirm()
        api.lsp.apply_workspace_edit.assert_called_once_with(edit)
        api.set_status.assert_called_with("Applied hier-up edit")

    def test_new_preview_closes_previous_float(self):
        api = _make_api()
        first = MagicMock()
        second = MagicMock()
        api.ui.open_float.side_effect = [first, second]
        panel = VerilogHierarchyPanel(api)

        panel._show_boundary_move_preview({"ok": True, "direction": "pull_up"})
        panel._show_extract_preview({"ok": True})

        first.close.assert_called_once()
        assert panel._preview_float is second


class TestExtractActions:
    def _api_for_source(self, tmp_path):
        api = _make_api()
        source = tmp_path / "top.v"
        source.write_text("module top;\nassign y = a & b;\nendmodule\n", encoding="utf-8")
        buffer = MagicMock()
        buffer.path = source
        buffer.get_line.side_effect = lambda line: source.read_text(encoding="utf-8").splitlines()[line]
        window = MagicMock()
        window.cursor = (1, 4)
        api.active_buffer.return_value = buffer
        api.active_window.return_value = window
        api.active_mode = "normal"
        return api, source

    def test_preview_key_requests_extract_preview_for_active_line(self, tmp_path):
        api, source = self._api_for_source(tmp_path)
        panel = VerilogHierarchyPanel(api)

        consumed = panel._on_key("e", MagicMock(value={}))

        payload = api.lsp.custom_request_to.call_args.args[1]
        request = payload["arguments"][0]
        assert consumed is True
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        assert request["textDocument"]["uri"] == Path(source).resolve().as_uri()
        assert request["range"] == {
            "start": {"line": 1, "character": 0},
            "end": {"line": 1, "character": len("assign y = a & b;")},
        }
        assert request["extractedModuleName"] == "extracted_logic"

    def test_preview_key_on_instance_reuses_hierarchy_up_preview(self, tmp_path):
        api, _source = self._api_for_source(tmp_path)
        panel = VerilogHierarchyPanel(api)
        node = MagicMock(
            value={
                "instanceName": "u_wrap",
                "moduleName": "wrapper",
                "instancePath": "top/u_wrap",
                "wrapperClass": "structural_wrapper",
                "refactorActions": [],
            }
        )

        consumed = panel._on_key("e", node)

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert consumed is True
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        assert payload["arguments"] == [
            {
                "direction": "pull_up",
                "selection": {"kind": "instance", "instancePath": "top/u_wrap"},
                "targetParentPath": "top",
            }
        ]

    def test_preview_key_closes_existing_preview_float_before_request(self, tmp_path):
        api, _source = self._api_for_source(tmp_path)
        panel = VerilogHierarchyPanel(api)
        handle = MagicMock()
        panel._preview_float = handle

        panel._on_key("e", MagicMock(value={}))

        handle.close.assert_called_once()
        assert panel._preview_float is None

    def test_apply_key_requests_extract_review_preview(self, tmp_path):
        api, _source = self._api_for_source(tmp_path)
        panel = VerilogHierarchyPanel(api)

        consumed = panel._on_key("E", MagicMock(value={}))

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert consumed is True
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"

    def test_public_preview_method_requests_extract_preview(self, tmp_path):
        api, _source = self._api_for_source(tmp_path)
        panel = VerilogHierarchyPanel(api)

        panel.preview_extract_from_active_selection()

        payload = api.lsp.custom_request_to.call_args.args[1]
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"

    def test_pull_up_selection_requests_range_boundary_move(self, tmp_path):
        api = _make_api()
        source = tmp_path / "top.v"
        source.write_text(
            "module top;\nchild u_child (\n    .a(a)\n);\nendmodule\n",
            encoding="utf-8",
        )
        buffer = MagicMock()
        buffer.path = source
        buffer.get_line.side_effect = lambda line: source.read_text(encoding="utf-8").splitlines()[line]
        window = MagicMock()
        window.cursor = (1, 0)
        api.active_buffer.return_value = buffer
        api.active_window.return_value = window
        api.active_mode = "normal"
        panel = VerilogHierarchyPanel(api)

        panel.preview_pull_up_from_active_selection(line_range=(1, 3))

        payload = api.lsp.custom_request_to.call_args.args[1]
        request = payload["arguments"][0]
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        assert request["direction"] == "pull_up"
        assert request["selection"]["kind"] == "range"
        assert request["selection"]["textDocument"]["uri"] == Path(source).resolve().as_uri()
        assert request["selection"]["range"] == {
            "start": {"line": 1, "character": 0},
            "end": {"line": 3, "character": len(");")},
        }

    def test_visual_selection_request_uses_selected_line_span(self, tmp_path):
        api, source = self._api_for_source(tmp_path)
        source.write_text(
            "module top;\nassign mid = a & b;\nassign y = mid | c;\nendmodule\n",
            encoding="utf-8",
        )
        engine = MagicMock()
        engine.visual_selection_regions.return_value = [(1, 4, 2, 12)]
        api._engine = engine
        api.active_mode = SimpleNamespace(value="visual_char")
        panel = VerilogHierarchyPanel(api)

        panel.preview_extract_from_active_selection()

        request = api.lsp.custom_request_to.call_args.args[1]["arguments"][0]
        assert request["range"] == {
            "start": {"line": 1, "character": 0},
            "end": {"line": 2, "character": len("assign y = mid | c;")},
        }

    def test_apply_extract_result_applies_workspace_edit(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        edit = {"changes": {"file:///top.v": []}}

        panel._on_extract_preview({"ok": True, "preview": {"ok": True}, "edit": edit}, apply_edit=True)

        api.lsp.apply_workspace_edit.assert_called_once_with(edit)
        api.set_status.assert_called_once_with("Applied hier-down edit")

    def test_apply_ready_extract_preview_opens_proposed_review(self, monkeypatch):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        preview_handle = MagicMock()
        panel._preview_float = preview_handle
        edit = {"changes": {"file:///top.v": []}}
        reviews = []
        monkeypatch.setattr(
            proposed_review, "open_proposed_edits", lambda _api, review_list: reviews.extend(review_list)
        )

        panel._on_extract_preview(
            {
                "ok": True,
                "preview": {"ok": True, "extractedModuleName": "child"},
                "edit": edit,
                "review": {
                    "files": [
                        {
                            "currentLabel": "current top.v",
                            "proposedLabel": "proposed top.v",
                            "currentText": "module top; endmodule",
                            "proposedText": "module child; endmodule",
                        }
                    ]
                },
            },
            apply_edit=False,
        )

        assert len(reviews) == 1
        assert reviews[0].title == "Hier-down child"
        preview_handle.close.assert_called_once()
        assert panel._preview_float is None
        api.ui.open_float.assert_not_called()
        reviews[0].on_confirm()
        api.lsp.apply_workspace_edit.assert_called_once_with(edit)
        api.set_status.assert_called_with("Applied hier-down edit")

    def test_apply_ready_extract_opens_created_module_after_confirm(self, monkeypatch, tmp_path):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        edit = {"changes": {"file:///top.v": [], "file:///child.v": []}}
        child_path = str((tmp_path / "child.v").resolve())
        reviews = []
        monkeypatch.setattr(
            proposed_review, "open_proposed_edits", lambda _api, review_list: reviews.extend(review_list)
        )

        panel._on_extract_preview(
            {
                "ok": True,
                "preview": {"ok": True, "extractedModuleName": "child"},
                "edit": edit,
                "review": {
                    "files": [
                        {
                            "file": str((tmp_path / "top.v").resolve()),
                            "currentLabel": "current top.v",
                            "proposedLabel": "proposed top.v",
                            "currentText": "module top; endmodule",
                            "proposedText": "module top; child u_child(); endmodule",
                        },
                        {
                            "file": child_path,
                            "currentLabel": "current child.v",
                            "proposedLabel": "proposed child.v",
                            "currentText": "",
                            "proposedText": "module child; endmodule",
                        },
                    ]
                },
            },
            apply_edit=True,
        )

        reviews[0].on_confirm()

        api.lsp.apply_workspace_edit.assert_called_once_with(edit)
        api.open_buffer.assert_called_once_with(child_path)

    def test_apply_ready_preview_passes_all_review_files_to_proposed_review(self, monkeypatch):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        edit = {"changes": {"file:///top.v": [], "file:///child.v": []}}
        reviews = []
        monkeypatch.setattr(
            proposed_review, "open_proposed_edits", lambda _api, review_list: reviews.extend(review_list)
        )

        panel._on_extract_preview(
            {
                "ok": True,
                "preview": {"ok": True, "extractedModuleName": "child"},
                "edit": edit,
                "review": {
                    "files": [
                        {
                            "currentLabel": "current top.v",
                            "proposedLabel": "proposed top.v",
                            "currentText": "module top; endmodule",
                            "proposedText": "module top; child u_child(); endmodule",
                        },
                        {
                            "currentLabel": "current child.v",
                            "proposedLabel": "proposed child.v",
                            "currentText": "",
                            "proposedText": "module child; endmodule",
                        },
                    ]
                },
            },
            apply_edit=False,
        )

        assert [review.proposed_label for review in reviews] == ["proposed top.v", "proposed child.v"]

    def test_apply_ready_extract_opens_source_file_review_first(self, monkeypatch, tmp_path):
        api = _make_api()
        source_path = str((tmp_path / "top.v").resolve())
        child_path = str((tmp_path / "child.v").resolve())
        api.active_buffer.return_value.path = source_path
        panel = VerilogHierarchyPanel(api)
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            proposed_review,
            "open_proposed_edits_with_initial",
            lambda _api, review_list, initial_review: captured.update(
                reviews=list(review_list),
                initial=initial_review,
            ),
        )
        monkeypatch.setattr(
            proposed_review,
            "open_proposed_edits",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected file picker path")),
        )

        panel._on_extract_preview(
            {
                "ok": True,
                "preview": {"ok": True, "extractedModuleName": "child"},
                "edit": {"changes": {"file:///top.v": [], "file:///child.v": []}},
                "review": {
                    "files": [
                        {
                            "file": source_path,
                            "currentLabel": "current top.v",
                            "proposedLabel": "proposed top.v",
                            "currentText": "module top; endmodule",
                            "proposedText": "module top; child u_child(); endmodule",
                        },
                        {
                            "file": child_path,
                            "currentLabel": "current child.v",
                            "proposedLabel": "proposed child.v",
                            "currentText": "",
                            "proposedText": "module child; endmodule",
                        },
                    ]
                },
            },
            apply_edit=True,
        )

        assert [review.proposed_label for review in captured["reviews"]] == ["proposed top.v", "proposed child.v"]
        assert captured["initial"].proposed_label == "proposed top.v"

    def test_blocked_extract_result_does_not_apply_edit(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)

        panel._on_extract_preview(
            {
                "ok": False,
                "preview": {
                    "ok": False,
                    "diagnostics": [{"severity": "error", "code": "blocked", "message": "not safe"}],
                },
            },
            apply_edit=True,
        )

        api.lsp.apply_workspace_edit.assert_not_called()
        api.set_status.assert_called_once_with("Hier-down edit was not available")

    def test_partial_selection_opens_suggestion_picker(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)
        preview_handle = MagicMock()
        api.ui.open_float.return_value = preview_handle

        preview = {
            "ok": False,
            "diagnostics": [{"severity": "error", "code": "partial-selection", "message": "..."}],
            "metadata": {
                "selectionNormalization": {
                    "items": [],
                    "diagnostics": [{"code": "partial-selection"}],
                    "suggestions": [
                        {
                            "kind": "expand-to-node",
                            "label": "Expand selection to cover always_block 'seq' (lines 7-13)",
                            "startLine": 7,
                            "endLine": 13,
                            "range": {"start": {"line": 6, "character": 4}, "end": {"line": 12, "character": 7}},
                            "nodeKind": "always_block",
                            "nodeName": "seq",
                        }
                    ],
                }
            },
        }

        panel._on_extract_preview({"ok": False, "preview": preview}, apply_edit=False)

        api.ui.open_picker.assert_called_once()
        preview_handle.close.assert_called_once()
        assert panel._preview_float is None
        kwargs = api.ui.open_picker.call_args.kwargs
        title = kwargs.get("title")
        source = kwargs.get("source")
        on_confirm = kwargs.get("on_confirm")
        assert title == "Verilog: expand hier-down selection"
        assert len(source) == 1
        assert source[0].start_line == 7
        assert source[0].end_line == 13
        assert source[0].kind == "expand-to-node"
        assert "Expand selection" in source[0].label

        # Selecting a suggestion re-runs preview with the expanded line range.
        api.lsp.custom_request_to.reset_mock()
        on_confirm(source[0])
        payload = api.lsp.custom_request_to.call_args.args[1]
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"
        request = payload["arguments"][0]
        # Suggestion's 1-based 7-13 maps to 0-based LSP line range 6-12.
        assert request["range"]["start"]["line"] == 6
        assert request["range"]["end"]["line"] == 12

    def test_partial_selection_picker_routes_to_apply_when_apply_edit_set(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)

        preview = {
            "ok": False,
            "metadata": {
                "selectionNormalization": {
                    "suggestions": [
                        {
                            "kind": "expand-to-node",
                            "label": "Expand to always_block 'seq' (lines 7-13)",
                            "startLine": 7,
                            "endLine": 13,
                        }
                    ],
                }
            },
        }

        panel._on_extract_preview({"ok": False, "preview": preview}, apply_edit=True)

        on_confirm = api.ui.open_picker.call_args.kwargs["on_confirm"]
        source = api.ui.open_picker.call_args.kwargs["source"]
        api.lsp.custom_request_to.reset_mock()
        on_confirm(source[0])
        # apply_extract path also issues a previewExtractModule request first.
        payload = api.lsp.custom_request_to.call_args.args[1]
        assert payload["command"] == "verilog/previewHierarchyBoundaryMove"

    def test_partial_selection_without_suggestions_does_not_open_picker(self):
        api = _make_api()
        panel = VerilogHierarchyPanel(api)

        panel._on_extract_preview(
            {"ok": False, "preview": {"ok": False, "diagnostics": []}},
            apply_edit=False,
        )

        api.ui.open_picker.assert_not_called()

    def test_format_extract_preview_uses_presentation_sections_when_available(self):
        lines = _format_extract_preview(
            {
                "operation": "extractSubmodule",
                "moduleName": "top",
                "extractedModuleName": "extracted_logic",
                "ok": True,
                "confidence": "safe",
                "selection": {"file": "top.v", "startLine": 2, "endLine": 3},
                "presentation": {
                    "sections": [
                        {"kind": "selected-source", "title": "Selected source", "text": "assign mid = a & b;"},
                        {
                            "kind": "normalized-selection",
                            "title": "Normalized semantic nodes",
                            "text": "- continuous_assign: mid [supported] (supported)",
                        },
                        {"kind": "boundary", "title": "Boundary ports", "text": "Inputs: a, b\nOutputs: y"},
                        {
                            "kind": "generated-module",
                            "title": "Generated module: extracted_logic.v",
                            "text": "module extracted_logic;",
                        },
                        {
                            "kind": "parent-replacement",
                            "title": "Parent replacement",
                            "text": "extracted_logic u0 (...);",
                        },
                        {"kind": "diff", "title": "Unified diff", "text": "--- before\n+++ after"},
                    ]
                },
            }
        )

        text = "\n".join(lines)
        assert "Selection: top.v:2-3" in text
        assert "Selected source:" in text
        assert "assign mid = a & b;" in text
        assert "Normalized semantic nodes:" in text
        assert "- continuous_assign: mid [supported] (supported)" in text
        assert "Boundary ports:" in text
        assert "Inputs: a, b" in text
        assert "Generated module: extracted_logic.v:" in text
        assert "Parent replacement:" in text
        assert "Unified diff:" in text

    def test_format_extract_preview_includes_boundary_generated_module_and_diff(self):
        lines = _format_extract_preview(
            {
                "operation": "extractSubmodule",
                "moduleName": "top",
                "extractedModuleName": "extracted_logic",
                "ok": True,
                "confidence": "safe",
                "selection": {"file": "top.v", "startLine": 2, "endLine": 2},
                "metadata": {
                    "selectionNormalization": {
                        "items": [
                            {
                                "kind": "continuous_assign",
                                "name": "y",
                                "supported": True,
                                "support": "supported",
                            }
                        ]
                    }
                },
                "boundary": {"inputs": ["a", "b"], "outputs": ["y"], "internals": []},
                "generatedModule": "module extracted_logic;",
                "diff": "--- before\n+++ after",
            }
        )

        text = "\n".join(lines)
        assert "extracted_logic" in text
        assert "continuous_assign y (supported)" in text
        assert "inputs: a, b" in text
        assert "module extracted_logic" in text
        assert "--- before" in text


class TestPluginExtractIntegration:
    def test_keybindings_include_normal_and_visual_hier_up_and_hier_down(self):
        api = _make_api()

        plugin._register_keybindings(api)

        normal_keys = [call.args[0] for call in api.keymap.nmap.call_args_list]
        visual_keys = [call.args[0] for call in api.keymap.vmap.call_args_list]
        assert "<leader>ru" in normal_keys
        assert "<leader>rw" in normal_keys
        assert "<leader>ru" in visual_keys
        assert "<leader>rw" in visual_keys
        assert "<leader>re" not in normal_keys
        assert "<leader>rU" not in normal_keys
        assert "<leader>rW" not in normal_keys

    def test_visual_hier_down_mapping_forwards_visual_range(self):
        api = _make_api()
        active_window = MagicMock()
        active_window.buffer.return_value.filetype = "verilog"
        api.active_window.return_value = active_window
        panel = MagicMock()
        plugin._state["hierarchy_panel"] = panel

        try:
            plugin._register_keybindings(api)
            visual_preview = next(
                call.args[1] for call in api.keymap.vmap.call_args_list if call.args[0] == "<leader>rw"
            )
            visual_preview(SimpleNamespace(visual_range=(7, 9)))
        finally:
            plugin._state.pop("hierarchy_panel", None)

        panel.prompt_push_down_range.assert_called_once_with((7, 9), apply_edit=False)

    def test_commands_include_new_and_legacy_hierarchy_commands(self):
        api = _make_api()

        plugin._register_commands(api)

        command_names = [call.args[0] for call in api.commands.register.call_args_list]
        assert "VerilogHierUp" in command_names
        assert "VerilogHierDown" in command_names
        assert "VerilogHierDownRange" in command_names
        assert "VerilogExtractPreview" in command_names
        assert "VerilogExtractApply" in command_names
        assert "VerilogPushDownRange" in command_names
        assert "VerilogPushDownRangeApply" in command_names

    def test_plugin_push_down_range_routes_to_panel_with_visual_range(self):
        api = _make_api()
        panel = MagicMock()
        plugin._state["hierarchy_panel"] = panel
        cmd = parse_ex_command("'<,'>VerilogPushDownRange top_core u_core")
        ctx = SimpleNamespace(engine=SimpleNamespace(_last_visual_selection=(None, (2, 4), (4, 1))))

        try:
            plugin._push_down_range(api, cmd=cmd, ctx=ctx, apply_edit=False)
        finally:
            plugin._state.pop("hierarchy_panel", None)

        panel.request_push_down_range_from_command.assert_called_once_with(
            "top_core u_core",
            line_range=(2, 4),
            apply_edit=False,
        )

    def test_plugin_push_down_range_apply_routes_with_apply_edit_true(self):
        api = _make_api()
        panel = MagicMock()
        plugin._state["hierarchy_panel"] = panel
        cmd = parse_ex_command("5,9VerilogPushDownRangeApply top_core")

        try:
            plugin._push_down_range(api, cmd=cmd, ctx=None, apply_edit=True)
        finally:
            plugin._state.pop("hierarchy_panel", None)

        panel.request_push_down_range_from_command.assert_called_once_with(
            "top_core",
            line_range=(4, 8),
            apply_edit=True,
        )

    def test_plugin_prompt_push_down_range_routes_to_panel(self):
        api = _make_api()
        panel = MagicMock()
        plugin._state["hierarchy_panel"] = panel
        ctx = SimpleNamespace(visual_range=(2, 6))

        try:
            plugin._prompt_push_down_range(api, ctx=ctx, apply_edit=False)
        finally:
            plugin._state.pop("hierarchy_panel", None)

        panel.prompt_push_down_range.assert_called_once_with((2, 6), apply_edit=False)

    def test_plugin_prompt_push_down_range_apply_passes_apply_flag(self):
        api = _make_api()
        panel = MagicMock()
        plugin._state["hierarchy_panel"] = panel
        ctx = SimpleNamespace(visual_range=(0, 3))

        try:
            plugin._prompt_push_down_range(api, ctx=ctx, apply_edit=True)
        finally:
            plugin._state.pop("hierarchy_panel", None)

        panel.prompt_push_down_range.assert_called_once_with((0, 3), apply_edit=True)

    def test_plugin_extract_helpers_route_to_panel(self):
        api = _make_api()
        panel = MagicMock()
        plugin._state["hierarchy_panel"] = panel

        try:
            plugin.preview_extract(api)
            plugin.apply_extract(api)
        finally:
            plugin._state.pop("hierarchy_panel", None)

        panel.preview_extract_from_active_selection.assert_called_once_with(line_range=None)
        panel.apply_extract_from_active_selection.assert_called_once_with(line_range=None)

    def test_plugin_extract_helper_uses_visual_context_range(self):
        api = _make_api()
        panel = MagicMock()
        plugin._state["hierarchy_panel"] = panel
        ctx = SimpleNamespace(visual_range=(3, 5))

        try:
            plugin._preview_extract(api, ctx=ctx)
        finally:
            plugin._state.pop("hierarchy_panel", None)

        panel.preview_extract_from_active_selection.assert_called_once_with(line_range=(3, 5))

    def test_plugin_pull_up_helper_uses_visual_context_range(self):
        api = _make_api()
        panel = MagicMock()
        plugin._state["hierarchy_panel"] = panel
        ctx = SimpleNamespace(visual_range=(3, 5))

        try:
            plugin._preview_pull_up_selection(api, ctx=ctx)
        finally:
            plugin._state.pop("hierarchy_panel", None)

        panel.preview_pull_up_from_active_selection.assert_called_once_with(line_range=(3, 5))

    def test_plugin_extract_command_uses_last_visual_mark_range(self):
        api = _make_api()
        panel = MagicMock()
        plugin._state["hierarchy_panel"] = panel
        cmd = parse_ex_command("'<,'>VerilogExtractPreview")
        ctx = SimpleNamespace(engine=SimpleNamespace(_last_visual_selection=(None, (2, 4), (4, 1))))

        try:
            plugin._preview_extract(api, cmd=cmd, ctx=ctx)
        finally:
            plugin._state.pop("hierarchy_panel", None)

        panel.preview_extract_from_active_selection.assert_called_once_with(line_range=(2, 4))
