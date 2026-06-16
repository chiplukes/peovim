"""
Phase 4g — Folding: FoldStore, Window integration, dispatcher actions,
engine key bindings, and renderer fold indicators.
"""

from peovim.core.document import Document
from peovim.core.fold import FoldStore
from peovim.core.registers import RegisterStore
from peovim.core.window import Window
from peovim.modal.actions import (
    CloseAllFolds,
    CloseFold,
    CreateFold,
    DeleteFold,
    OpenAllFolds,
    OpenFold,
    ToggleFold,
)
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine

# ---------------------------------------------------------------------------
# FoldStore unit tests
# ---------------------------------------------------------------------------


class TestFoldStore:
    def test_create_fold(self):
        fs = FoldStore()
        fs.create(2, 5)
        assert len(fs) == 1
        assert fs.fold_header(2) is not None
        assert fs.fold_header(2).end_line == 5

    def test_fold_sorted_on_create(self):
        fs = FoldStore()
        fs.create(10, 15)
        fs.create(2, 5)
        folds = fs.closed_folds()
        assert folds[0] == (2, 5)
        assert folds[1] == (10, 15)

    def test_create_removes_overlapping(self):
        fs = FoldStore()
        fs.create(3, 4)  # fully inside next create
        fs.create(2, 5)
        assert len(fs) == 1

    def test_is_folded_body(self):
        fs = FoldStore()
        fs.create(2, 5)
        assert fs.is_folded(3)
        assert fs.is_folded(5)
        assert not fs.is_folded(2)  # header is not "folded body"
        assert not fs.is_folded(6)

    def test_fold_header_returns_none_for_body(self):
        fs = FoldStore()
        fs.create(2, 5)
        assert fs.fold_header(3) is None
        assert fs.fold_header(6) is None

    def test_open_fold(self):
        fs = FoldStore()
        fs.create(2, 5)
        assert fs.fold_header(2) is not None  # closed
        fs.open(2)
        assert fs.fold_header(2) is None  # open folds don't count as headers
        assert not fs.is_folded(3)

    def test_close_fold(self):
        fs = FoldStore()
        fs.create(2, 5)
        fs.open(2)
        fs.close(2)
        assert fs.fold_header(2) is not None

    def test_toggle_fold(self):
        fs = FoldStore()
        fs.create(2, 5)
        fs.toggle(2)  # open
        assert fs.fold_header(2) is None
        fs.toggle(2)  # close again
        assert fs.fold_header(2) is not None

    def test_open_all(self):
        fs = FoldStore()
        fs.create(2, 5)
        fs.create(10, 12)
        fs.open_all()
        assert fs.closed_folds() == []

    def test_close_all(self):
        fs = FoldStore()
        fs.create(2, 5)
        fs.create(10, 12)
        fs.open_all()
        fs.close_all()
        assert len(fs.closed_folds()) == 2

    def test_delete_fold(self):
        fs = FoldStore()
        fs.create(2, 5)
        fs.delete(3)  # delete by any line inside
        assert len(fs) == 0

    def test_delete_nonexistent_is_noop(self):
        fs = FoldStore()
        fs.delete(99)  # no-op

    def test_fold_at(self):
        fs = FoldStore()
        fs.create(2, 5)
        assert fs.fold_at(2) is not None
        assert fs.fold_at(3) is not None
        assert fs.fold_at(5) is not None
        assert fs.fold_at(6) is None

    def test_reversed_range_normalised(self):
        fs = FoldStore()
        fs.create(5, 2)  # reversed — should be stored as (2, 5)
        f = fs.fold_at(3)
        assert f is not None
        assert f.start_line == 2
        assert f.end_line == 5


# ---------------------------------------------------------------------------
# Window integration
# ---------------------------------------------------------------------------


class TestWindowFolds:
    def test_window_has_foldstore(self):
        doc = Document()
        doc.load_string("a\nb\nc")
        w = Window(doc)
        assert isinstance(w.folds, FoldStore)

    def test_snapshot_includes_closed_folds(self):
        doc = Document()
        doc.load_string("a\nb\nc\nd\ne")
        w = Window(doc)
        w.folds.create(1, 3)
        snap = w.snapshot()
        assert (1, 3) in snap.closed_folds

    def test_snapshot_excludes_open_folds(self):
        doc = Document()
        doc.load_string("a\nb\nc\nd\ne")
        w = Window(doc)
        w.folds.create(1, 3)
        w.folds.open(1)
        snap = w.snapshot()
        assert snap.closed_folds == ()


# ---------------------------------------------------------------------------
# Dispatcher action tests
# ---------------------------------------------------------------------------


def _make_session(content: str = ""):
    doc = Document()
    doc.load_string(content)
    w = Window(doc)
    reg = RegisterStore()
    engine = ModalEngine()
    engine.set_cursor(0, 0)
    engine.set_line_count(doc.line_count())
    dispatcher = ActionDispatcher(engine, w, reg)
    return doc, w, engine, dispatcher


class TestDispatcherFolds:
    def test_create_fold(self):
        doc, w, eng, disp = _make_session("a\nb\nc\nd\ne")
        disp.dispatch([CreateFold(1, 3)])
        assert w.folds.fold_header(1) is not None

    def test_open_fold(self):
        doc, w, eng, disp = _make_session("a\nb\nc\nd\ne")
        disp.dispatch([CreateFold(1, 3)])
        disp.dispatch([OpenFold(1)])
        assert w.folds.fold_header(1) is None  # now open

    def test_close_fold(self):
        doc, w, eng, disp = _make_session("a\nb\nc\nd\ne")
        disp.dispatch([CreateFold(1, 3)])
        disp.dispatch([OpenFold(1)])
        disp.dispatch([CloseFold(1)])
        assert w.folds.fold_header(1) is not None

    def test_toggle_fold(self):
        doc, w, eng, disp = _make_session("a\nb\nc\nd\ne")
        disp.dispatch([CreateFold(1, 3)])
        disp.dispatch([ToggleFold(1)])
        assert w.folds.fold_header(1) is None  # toggled open

    def test_open_all_folds(self):
        doc, w, eng, disp = _make_session("a\nb\nc\nd\ne")
        disp.dispatch([CreateFold(0, 1)])
        disp.dispatch([CreateFold(3, 4)])
        disp.dispatch([OpenAllFolds()])
        assert w.folds.closed_folds() == []

    def test_close_all_folds(self):
        doc, w, eng, disp = _make_session("a\nb\nc\nd\ne")
        disp.dispatch([CreateFold(0, 1)])
        disp.dispatch([CreateFold(3, 4)])
        disp.dispatch([OpenAllFolds()])
        disp.dispatch([CloseAllFolds()])
        assert len(w.folds.closed_folds()) == 2

    def test_delete_fold(self):
        doc, w, eng, disp = _make_session("a\nb\nc\nd\ne")
        disp.dispatch([CreateFold(1, 3)])
        disp.dispatch([DeleteFold(2)])
        assert len(w.folds) == 0


# ---------------------------------------------------------------------------
# Engine key binding tests
# ---------------------------------------------------------------------------


def _parse_keys(keys: str) -> list[str]:
    """Parse a key string like 'zf3j' into ['z', 'f', '3', 'j']."""
    result = []
    i = 0
    while i < len(keys):
        if keys[i] == "<":
            end = keys.index(">", i)
            result.append(keys[i : end + 1])
            i = end + 1
        else:
            result.append(keys[i])
            i += 1
    return result


class EditorSession:
    def __init__(self, content: str = "") -> None:
        self.doc = Document()
        self.doc.load_string(content)
        self.window = Window(self.doc)
        self.registers = RegisterStore()
        self.engine = ModalEngine()
        self.engine.set_cursor(0, 0)
        self.engine.set_line_count(self.doc.line_count())
        self.engine.set_document(self.doc)
        self.dispatcher = ActionDispatcher(self.engine, self.window, self.registers)

    def type(self, keys: str) -> None:
        for key in _parse_keys(keys):
            actions = self.engine.feed_key(key)
            self.dispatcher.dispatch(actions)

    def cursor(self) -> tuple[int, int]:
        return self.window.cursor.line, self.window.cursor.col


class TestEngineFoldBindings:
    def test_zf_creates_fold_current_line(self):
        s = EditorSession("a\nb\nc\nd\ne")
        s.window.cursor.move_to(1, 0)
        s.engine.set_cursor(1, 0)
        s.type("zf")
        # zf with count=1 creates fold [1, 1]
        assert s.window.folds.fold_header(1) is not None

    def test_zf_with_count(self):
        s = EditorSession("a\nb\nc\nd\ne")
        s.window.cursor.move_to(0, 0)
        s.engine.set_cursor(0, 0)
        s.type("3zf")
        # 3zf should create fold [0, 2]
        fold = s.window.folds.fold_header(0)
        assert fold is not None
        assert fold.end_line == 2

    def test_zo_opens_fold(self):
        s = EditorSession("a\nb\nc\nd\ne")
        s.window.cursor.move_to(0, 0)
        s.engine.set_cursor(0, 0)
        s.window.folds.create(0, 2)
        s.type("zo")
        assert s.window.folds.fold_header(0) is None

    def test_zc_closes_fold(self):
        s = EditorSession("a\nb\nc\nd\ne")
        s.window.cursor.move_to(0, 0)
        s.engine.set_cursor(0, 0)
        s.window.folds.create(0, 2)
        s.window.folds.open(0)
        s.type("zc")
        assert s.window.folds.fold_header(0) is not None

    def test_za_toggles_fold(self):
        s = EditorSession("a\nb\nc\nd\ne")
        s.window.cursor.move_to(0, 0)
        s.engine.set_cursor(0, 0)
        s.window.folds.create(0, 2)
        s.type("za")  # toggle open
        assert s.window.folds.fold_header(0) is None
        s.type("za")  # toggle closed — but cursor is on fold body now, fold_at(0) still exists
        assert s.window.folds.fold_header(0) is not None

    def test_zR_opens_all(self):
        s = EditorSession("a\nb\nc\nd\ne")
        s.window.folds.create(0, 1)
        s.window.folds.create(3, 4)
        s.type("zR")
        assert s.window.folds.closed_folds() == []

    def test_zM_closes_all(self):
        s = EditorSession("a\nb\nc\nd\ne")
        s.window.folds.create(0, 1)
        s.window.folds.create(3, 4)
        s.window.folds.open_all()
        s.type("zM")
        assert len(s.window.folds.closed_folds()) == 2

    def test_zd_deletes_fold(self):
        s = EditorSession("a\nb\nc\nd\ne")
        s.window.cursor.move_to(0, 0)
        s.engine.set_cursor(0, 0)
        s.window.folds.create(0, 2)
        s.type("zd")
        assert len(s.window.folds) == 0


# ---------------------------------------------------------------------------
# Renderer fold indicator tests
# ---------------------------------------------------------------------------


class TestRendererFolds:
    def test_fold_indicator_shown(self):
        from peovim.ui.layout import Rect
        from peovim.ui.window_renderer import render_window

        doc = Document()
        doc.load_string("line0\nline1\nline2\nline3\nline4")
        w = Window(doc, width=40, height=10)
        w.folds.create(1, 3)
        snap = w.snapshot()

        rect = Rect(0, 0, 40, 10)
        grid = render_window(snap, rect, is_active=True)

        # Row 0 = "line0", row 1 = fold indicator, row 2 = "line4"
        cells = grid._current
        row0_text = "".join(c[0] for c in cells[0]).strip()
        assert row0_text.startswith("line0")

        row1_text = "".join(c[0] for c in cells[1]).strip()
        assert row1_text.startswith("+--")

        row2_text = "".join(c[0] for c in cells[2]).strip()
        assert row2_text.startswith("line4")

    def test_no_fold_indicator_when_open(self):
        from peovim.ui.layout import Rect
        from peovim.ui.window_renderer import render_window

        doc = Document()
        doc.load_string("line0\nline1\nline2\nline3\nline4")
        w = Window(doc, width=40, height=10)
        w.folds.create(1, 3)
        w.folds.open(1)
        snap = w.snapshot()

        rect = Rect(0, 0, 40, 10)
        grid = render_window(snap, rect, is_active=True)

        row1_text = "".join(c[0] for c in grid._current[1]).strip()
        assert row1_text.startswith("line1")
