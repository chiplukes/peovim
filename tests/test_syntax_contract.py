from __future__ import annotations

from concurrent.futures import Future

from peovim.core.document import Document
from peovim.core.snapshot import BufferSnapshot
from peovim.syntax.engine import HighlightSpan, SyntaxEngine


class RecordingLoop:
    def __init__(self) -> None:
        self.calls: list[tuple[object, tuple[object, ...]]] = []

    def call_soon_threadsafe(self, callback, *args) -> None:
        self.calls.append((callback, args))


class ControlledExecutor:
    def __init__(self) -> None:
        self.futures: list[Future] = []
        self.submissions: list[tuple] = []

    def submit(self, fn, *args):
        future: Future = Future()
        self.submissions.append((fn, *args))
        self.futures.append(future)
        return future


def _make_snapshot(text: str, *, filetype: str = "python") -> BufferSnapshot:
    doc = Document()
    doc.load_string(text)
    doc.filetype = filetype
    return doc.snapshot()


def test_submit_passes_buffer_snapshot_to_worker() -> None:
    executor = ControlledExecutor()
    engine = SyntaxEngine(executor)
    loop = RecordingLoop()
    snapshot = _make_snapshot("x = 1\n")

    engine.submit(snapshot, 7, lambda _buf_id, _spans: None, loop)

    assert len(executor.submissions) == 1
    _fn, worker_snapshot, *_rest = executor.submissions[0]
    assert isinstance(worker_snapshot, BufferSnapshot)
    assert worker_snapshot is snapshot
    assert not isinstance(worker_snapshot, Document)


def test_submit_marshals_completion_through_call_soon_threadsafe() -> None:
    executor = ControlledExecutor()
    engine = SyntaxEngine(executor)
    loop = RecordingLoop()
    delivered: list[tuple[int, list[HighlightSpan]]] = []
    spans = [HighlightSpan(0, 0, 0, 3, "keyword")]

    engine.submit(
        _make_snapshot("def x():\n    pass\n"),
        11,
        lambda buffer_id, result: delivered.append((buffer_id, result)),
        loop,
    )

    executor.futures[0].set_result(spans)

    assert delivered == []
    assert len(loop.calls) == 1

    callback, args = loop.calls[0]
    callback(*args)

    assert delivered == [(11, spans)]


def test_stale_syntax_results_are_discarded_when_newer_snapshot_submitted() -> None:
    executor = ControlledExecutor()
    engine = SyntaxEngine(executor)
    loop = RecordingLoop()
    delivered: list[tuple[int, list[HighlightSpan]]] = []
    old_spans = [HighlightSpan(0, 0, 0, 1, "old")]
    new_spans = [HighlightSpan(0, 0, 0, 1, "new")]

    doc = Document()
    doc.load_string("a\n")
    doc.filetype = "python"
    old_snapshot = doc.snapshot()

    def on_done(buffer_id: int, result: list[HighlightSpan]) -> None:
        delivered.append((buffer_id, result))

    engine.submit(old_snapshot, 3, on_done, loop)

    doc.insert(0, 1, "b")
    new_snapshot = doc.snapshot()
    engine.submit(new_snapshot, 3, on_done, loop)

    executor.futures[0].set_result(old_spans)
    assert loop.calls == []
    assert delivered == []

    executor.futures[1].set_result(new_spans)
    assert len(loop.calls) == 1

    callback, args = loop.calls[0]
    callback(*args)

    assert delivered == [(3, new_spans)]
