"""Tests for the signal trace picker helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from peovim.plugins.verilog_lsp.signal_trace import (
    TraceItem,
    _highlight_preview,
    _make_item,
    open_trace_picker,
)

# ── TraceItem ─────────────────────────────────────────────────────────────────


class TestTraceItem:
    def test_str_returns_label(self):
        item = TraceItem(label="foo.v:42")
        assert str(item) == "foo.v:42"

    def test_header_item(self):
        item = TraceItem(label="── Drivers ──", is_header=True)
        assert item.is_header is True
        assert item.file == ""


# ── _make_item ────────────────────────────────────────────────────────────────


class TestMakeItem:
    def _entry(self, **kwargs):
        base = {
            "file": "file:///project/adder.v",
            "range": {"start": {"line": 4, "character": 0}, "end": {"line": 4, "character": 20}},
            "kind": "assign",
            "label": "assign sum = a + b",
            "instancePath": "top.u_add",
        }
        base.update(kwargs)
        return base

    def test_label_includes_filename(self):
        item = _make_item(self._entry())
        assert "adder.v" in item.label

    def test_label_includes_line_number(self):
        item = _make_item(self._entry())
        assert "5" in item.label  # 0-based line 4 → display 5

    def test_label_includes_instance_path(self):
        item = _make_item(self._entry())
        assert "top.u_add" in item.label

    def test_file_path_set(self):
        item = _make_item(self._entry())
        assert item.file.endswith("adder.v")

    def test_range_preserved(self):
        item = _make_item(self._entry())
        assert item.range["start"]["line"] == 4

    def test_signal_chain_is_preserved_for_preview_highlighting(self):
        item = _make_item(self._entry(signalChain=["add_out", "child_port", "renamed_bus"]))
        assert item.signal_names == ("add_out", "child_port", "renamed_bus")


class TestHighlightPreview:
    def test_highlights_all_signal_aliases(self):
        lines = _highlight_preview(
            "assign add_out = child_port ^ renamed_bus;",
            ("add_out", "child_port", "renamed_bus"),
        )
        highlighted = {text for line in lines for text, style in line if getattr(style, "fg", None) == (255, 230, 80)}
        assert {"add_out", "child_port", "renamed_bus"} <= highlighted


# ── open_trace_picker ─────────────────────────────────────────────────────────


class TestOpenTracePicker:
    _DRIVER = {
        "file": "file:///project/top.v",
        "range": {"start": {"line": 6, "character": 4}, "end": {"line": 6, "character": 30}},
        "kind": "assign",
        "label": "assign result = add_out",
        "instancePath": "top",
    }

    def _result(self, *, include_driver: bool = True, include_load: bool = False):
        return {
            "signal": {"name": "add_out", "width": "[7:0]"},
            "drivers": [self._DRIVER] if include_driver else [],
            "loads": [] if not include_load else [],
        }

    def test_opens_picker_with_title(self):
        api = MagicMock()
        open_trace_picker(api, self._result())
        api.ui.open_picker.assert_called_once()
        title = api.ui.open_picker.call_args.kwargs.get("title", "")
        assert "add_out" in title

    def test_picker_source_has_header_and_item(self):
        api = MagicMock()
        open_trace_picker(api, self._result())
        source = api.ui.open_picker.call_args.kwargs.get("source", [])
        assert any(i.is_header for i in source)
        assert any(not i.is_header for i in source)

    def test_no_items_sets_status(self):
        api = MagicMock()
        open_trace_picker(api, self._result(include_driver=False))
        api.set_status.assert_called_once()
        api.ui.open_picker.assert_not_called()

    def test_preview_highlights_boundary_alias_names(self):
        api = MagicMock()
        result = self._result()
        result["drivers"] = [
            {
                **self._DRIVER,
                "style": "boundary",
                "preview": "assign add_out = child_port ^ renamed_bus;",
                "signalChain": ["add_out", "child_port", "renamed_bus"],
            }
        ]

        open_trace_picker(api, result)

        item = next(item for item in api.ui.open_picker.call_args.kwargs["source"] if not item.is_header)
        preview = api.ui.open_picker.call_args.kwargs["preview"](item)
        highlighted = {text for line in preview for text, style in line if getattr(style, "fg", None) == (255, 230, 80)}
        assert {"add_out", "child_port", "renamed_bus"} <= highlighted
