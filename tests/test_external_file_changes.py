"""Tests for external file change detection (runtime_controller.check_external_changes)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from peovim.core.document import Document
from peovim.ui.runtime_controller import EventLoopRuntimeController

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_host(docs: list[Document], active_doc: Document) -> MagicMock:
    """Build a minimal EventLoop-shaped mock for EventLoopRuntimeController."""
    host = MagicMock()
    host._editor_state = SimpleNamespace(message="")
    host._workspace.documents = docs
    host._workspace.active_window.document = active_doc
    host._syntax_submitted = {id(d): 0 for d in docs}
    host._syntax_cache = {id(d): object() for d in docs}
    return host


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCheckExternalChanges:
    def test_auto_reloads_clean_buffer(self, tmp_path: Path) -> None:
        f = tmp_path / "file.py"
        f.write_text("original\n", encoding="utf-8")
        doc = Document()
        doc.load(f)

        f.write_text("updated\n", encoding="utf-8")

        host = _make_host([doc], doc)
        ctrl = EventLoopRuntimeController(host)
        ctrl._do_check_external_changes()

        assert doc.get_text() == "updated\n"
        assert not doc.has_external_changes()
        assert "reloaded" in host._editor_state.message
        host._invalidate.assert_called()

    def test_clears_syntax_caches_after_reload(self, tmp_path: Path) -> None:
        f = tmp_path / "file.py"
        f.write_text("original\n", encoding="utf-8")
        doc = Document()
        doc.load(f)

        f.write_text("updated\n", encoding="utf-8")

        host = _make_host([doc], doc)
        ctrl = EventLoopRuntimeController(host)
        ctrl._do_check_external_changes()

        assert id(doc) not in host._syntax_submitted
        assert id(doc) not in host._syntax_cache

    def test_warns_once_for_dirty_buffer(self, tmp_path: Path) -> None:
        f = tmp_path / "file.py"
        f.write_text("original\n", encoding="utf-8")
        doc = Document()
        doc.load(f)
        doc.insert(0, 0, "# edit\n")  # make dirty

        f.write_text("external change\n", encoding="utf-8")

        host = _make_host([doc], doc)
        ctrl = EventLoopRuntimeController(host)
        ctrl._do_check_external_changes()

        assert "W12" in host._editor_state.message
        assert id(doc) in ctrl._file_check_warned
        assert doc.get_text() != "external change\n"  # not auto-reloaded

    def test_dirty_warn_fires_only_once(self, tmp_path: Path) -> None:
        f = tmp_path / "file.py"
        f.write_text("original\n", encoding="utf-8")
        doc = Document()
        doc.load(f)
        doc.insert(0, 0, "# edit\n")
        f.write_text("external\n", encoding="utf-8")

        host = _make_host([doc], doc)
        ctrl = EventLoopRuntimeController(host)

        ctrl._do_check_external_changes()
        first_call_count = host._invalidate.call_count

        host._editor_state.message = ""
        ctrl._do_check_external_changes()

        # Second check: already in warned set, so no new invalidate
        assert host._invalidate.call_count == first_call_count
        assert host._editor_state.message == ""

    def test_no_action_when_file_unchanged(self, tmp_path: Path) -> None:
        f = tmp_path / "file.py"
        f.write_text("hello\n", encoding="utf-8")
        doc = Document()
        doc.load(f)

        host = _make_host([doc], doc)
        ctrl = EventLoopRuntimeController(host)
        ctrl._do_check_external_changes()

        host._invalidate.assert_not_called()
        assert host._editor_state.message == ""

    def test_rate_limit_skips_check_before_interval(self, tmp_path: Path) -> None:
        f = tmp_path / "file.py"
        f.write_text("original\n", encoding="utf-8")
        doc = Document()
        doc.load(f)
        f.write_text("updated\n", encoding="utf-8")

        host = _make_host([doc], doc)
        ctrl = EventLoopRuntimeController(host)
        # Simulate that we just ran a check
        ctrl._last_file_check = 1000.0

        ctrl.check_external_changes(1000.5)  # only 0.5 s later — should skip

        host._invalidate.assert_not_called()

    def test_force_check_ignores_rate_limit(self, tmp_path: Path) -> None:
        f = tmp_path / "file.py"
        f.write_text("original\n", encoding="utf-8")
        doc = Document()
        doc.load(f)
        f.write_text("updated\n", encoding="utf-8")

        host = _make_host([doc], doc)
        ctrl = EventLoopRuntimeController(host)
        ctrl._last_file_check = 1000.0  # simulate recent check

        ctrl.force_check_external_changes()

        assert doc.get_text() == "updated\n"

    def test_background_doc_reloaded_without_message(self, tmp_path: Path) -> None:
        f1 = tmp_path / "active.py"
        f2 = tmp_path / "bg.py"
        f1.write_text("active\n", encoding="utf-8")
        f2.write_text("bg_original\n", encoding="utf-8")
        active = Document()
        active.load(f1)
        bg = Document()
        bg.load(f2)

        f2.write_text("bg_updated\n", encoding="utf-8")

        host = _make_host([active, bg], active)
        ctrl = EventLoopRuntimeController(host)
        ctrl._do_check_external_changes()

        assert bg.get_text() == "bg_updated\n"
        assert host._editor_state.message == ""  # no message for non-active doc
        host._invalidate.assert_called()
