"""Repeatable profiling harness for representative editor workloads.

Usage examples:

  # Timing table for all workloads (3 repeats each):
  uv run python -m peovim.debug.profile_workloads --repeat 3

  # cProfile hotspot breakdown for the realistic two-window frame workload:
  uv run python -m peovim.debug.profile_workloads --profile frame_render

  # cProfile for a specific render workload, top-30 functions:
  uv run python -m peovim.debug.profile_workloads --profile render_decorated_python --top 30

  # Quick scaled-down pass for CI / smoke checks:
  uv run python -m peovim.debug.profile_workloads --scale 0.25
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean

from peovim.core.document import Document
from peovim.core.editor_state import EditorState
from peovim.core.persistence import atomic_write_text
from peovim.core.registers import RegisterStore
from peovim.core.style import Style
from peovim.core.window import Window
from peovim.core.workspace import Workspace
from peovim.modal.dispatcher import ActionDispatcher
from peovim.modal.engine import ModalEngine
from peovim.plugins.compare import compute_blocks
from peovim.syntax.engine import _parse_task
from peovim.syntax.themes import get_theme
from peovim.ui.backends.headless import HeadlessBackend
from peovim.ui.cell_grid import CellGrid
from peovim.ui.decorations import GhostText, HighlightRegion, OverlayChar, Sign, VirtualText
from peovim.ui.event_loop import EventLoop
from peovim.ui.layout import Rect
from peovim.ui.notify import NotifyManager
from peovim.ui.picker import PickerWidget
from peovim.ui.window_renderer import render_window


@dataclass(frozen=True)
class WorkloadResult:
    name: str
    category: str
    runs: int
    iterations: int
    mean_ms: float
    min_mean_ms: float
    max_mean_ms: float
    total_ms: float
    details: dict[str, object]


@dataclass(frozen=True)
class _WorkloadSpec:
    category: str
    default_iterations: int
    runner: Callable[[int], WorkloadResult]


def _scaled_iterations(default_iterations: int, scale: float) -> int:
    return max(1, int(round(default_iterations * scale)))


def _aggregate_results(results: Sequence[WorkloadResult]) -> WorkloadResult:
    first = results[0]
    mean_samples = [result.mean_ms for result in results]
    total_samples = [result.total_ms for result in results]
    return WorkloadResult(
        name=first.name,
        category=first.category,
        runs=len(results),
        iterations=first.iterations,
        mean_ms=mean(mean_samples),
        min_mean_ms=min(mean_samples),
        max_mean_ms=max(mean_samples),
        total_ms=sum(total_samples),
        details={**first.details, "repeat": len(results)},
    )


def _time_iterations(iterations: int, callback: Callable[[], None]) -> tuple[float, list[float]]:
    samples: list[float] = []
    started = time.perf_counter()
    for _ in range(iterations):
        tick = time.perf_counter()
        callback()
        samples.append((time.perf_counter() - tick) * 1000.0)
    total_ms = (time.perf_counter() - started) * 1000.0
    return total_ms, samples


def _python_text(blocks: int = 220) -> str:
    chunks: list[str] = []
    for index in range(blocks):
        chunks.append(
            f"def func_{index}(value: int) -> str:\n"
            f"    total = value + {index}\n"
            "    if total % 2 == 0:\n"
            "        return f'value={total}'\n"
            "    return 'odd'\n\n"
        )
    return "".join(chunks)


def _large_text(lines: int = 6000) -> str:
    return "".join(f"{line:05d} lorem ipsum dolor sit amet, consectetur adipiscing elit\n" for line in range(lines))


def _diff_text_pair(blocks: int = 180) -> tuple[str, str]:
    left_lines: list[str] = []
    right_lines: list[str] = []
    for index in range(blocks):
        base = f"item_{index:03d} value = {index}"
        left_lines.append(base)
        right_lines.append(base)
        mode = index % 6
        if mode == 0:
            left_lines.append(f"left_only_{index:03d} = remove_me({index})")
        elif mode == 1:
            right_lines.append(f"right_only_{index:03d} = added_value({index})")
        elif mode == 2:
            left_lines.append(f"change_target_{index:03d} = old_{index}")
            right_lines.append(f"change_target_{index:03d} = new_{index}")
        elif mode == 3:
            left_lines.extend([f"group_left_{index:03d}_{suffix}" for suffix in ("a", "b")])
            right_lines.extend([f"group_right_{index:03d}_{suffix}" for suffix in ("a", "b", "c")])
        elif mode == 4:
            left_lines.append(f"stable_context_{index:03d}")
            right_lines.append(f"stable_context_{index:03d}")
        else:
            left_lines.append(f"wrap_left_{index:03d} {'x' * 30}")
            right_lines.append(f"wrap_right_{index:03d} {'y' * 24}")
    return "\n".join(left_lines), "\n".join(right_lines)


def _make_snapshot(
    text: str,
    *,
    suffix: str,
    width: int,
    height: int,
    scroll_line: int = 0,
    cursor_line: int = 0,
    cursor_col: int = 0,
    options: dict | None = None,
):
    document = Document()
    document.load_string(text)
    document.path = Path(f"profile_workload{suffix}")
    window = Window(document, width=width, height=height)
    window.scroll_line = scroll_line
    window.cursor.move_to(cursor_line, cursor_col)
    if options:
        window.options.update(options)
    return window.snapshot()


def _decorated_python_decorations(line_count: int) -> list[object]:
    decorations: list[object] = []
    error_style = Style(fg="#f38ba8", bg="#3b1f2b")
    warn_style = Style(fg="#f9e2af", bg="#3a3326")
    hint_style = Style(fg="#94e2d5")
    sign_style = Style(fg="#89b4fa")
    overlay_style = Style(fg="#f5c2e7", attrs=1)
    for line in range(4, min(line_count, 180), 9):
        decorations.append(HighlightRegion(line, 4, line, 18, error_style, priority=20))
    for line in range(7, min(line_count, 180), 13):
        decorations.append(HighlightRegion(line, 8, line, 26, warn_style, priority=15))
    for line in range(2, min(line_count, 180), 10):
        decorations.append(VirtualText(line, "  ■ diag", hint_style, priority=5))
    for line in range(1, min(line_count, 180), 11):
        decorations.append(Sign(line, "●", sign_style, priority=5))
    for line in range(5, min(line_count, 180), 17):
        decorations.append(GhostText(line, 20, "  # pending fix", hint_style))
    for line in range(3, min(line_count, 180), 19):
        decorations.append(OverlayChar(line, 7, "→", overlay_style))
    return decorations


def _diff_decorations(left_lines: list[str], right_lines: list[str]) -> tuple[list[object], list[object], int]:
    left_decorations: list[object] = []
    right_decorations: list[object] = []
    blocks = compute_blocks(left_lines, right_lines)
    change_left = Style(bg="#614a1a")
    change_right = Style(bg="#465422")
    delete_style = Style(bg="#55222e")
    insert_style = Style(bg="#465422")
    hint_insert = Style(fg="#8cd67a")
    hint_delete = Style(fg="#f48fb1")

    for block in blocks:
        if block.kind in {"change", "delete"}:
            style = change_left if block.kind == "change" else delete_style
            for line in range(block.left_start, block.left_end):
                text = left_lines[line]
                left_decorations.append(
                    Sign(
                        line,
                        "~" if block.kind == "change" else "-",
                        Style(fg="#e8bd68" if block.kind == "change" else "#f48fb1"),
                        priority=10,
                    )
                )
                if text:
                    left_decorations.append(HighlightRegion(line, 0, line, len(text), style, priority=10))
        if block.kind in {"change", "insert"}:
            style = change_right if block.kind == "change" else insert_style
            for line in range(block.right_start, block.right_end):
                text = right_lines[line]
                right_decorations.append(
                    Sign(
                        line,
                        "~" if block.kind == "change" else "+",
                        Style(fg="#e8bd68" if block.kind == "change" else "#a6e3a1"),
                        priority=10,
                    )
                )
                if text:
                    right_decorations.append(HighlightRegion(line, 0, line, len(text), style, priority=10))
        if block.kind == "insert":
            anchor = min(block.left_start, max(0, len(left_lines) - 1))
            left_decorations.append(
                VirtualText(anchor, f"  ⟪ +{block.right_count} lines on right ⟫", hint_insert, priority=5)
            )
        elif block.kind == "delete":
            anchor = min(block.right_start, max(0, len(right_lines) - 1))
            right_decorations.append(
                VirtualText(anchor, f"  ⟪ -{block.left_count} lines on left ⟫", hint_delete, priority=5)
            )
    return left_decorations, right_decorations, len(blocks)


def _lsp_dense_decorations(line_count: int) -> list[object]:
    decorations: list[object] = []
    error_highlight = Style(bg="#4a1f2b")
    warn_highlight = Style(bg="#4a3b1f")
    hint_highlight = Style(bg="#1f3b4a")
    error_text = Style(fg="#f38ba8")
    warn_text = Style(fg="#f9e2af")
    hint_text = Style(fg="#89dceb")
    ghost_style = Style(fg="#6c7086")
    action_style = Style(fg="#f5c2e7", attrs=1)

    for line in range(2, min(line_count, 220), 5):
        decorations.append(Sign(line, "E", error_text, priority=20))
        decorations.append(HighlightRegion(line, 4, line, 28, error_highlight, priority=20))
        decorations.append(VirtualText(line, "  error: incompatible types", error_text, priority=10))
    for line in range(4, min(line_count, 220), 7):
        decorations.append(Sign(line, "W", warn_text, priority=15))
        decorations.append(HighlightRegion(line, 12, line, 36, warn_highlight, priority=15))
        decorations.append(VirtualText(line, "  warning: dead code", warn_text, priority=8))
    for line in range(1, min(line_count, 220), 6):
        decorations.append(OverlayChar(line, 0, "💡", action_style))
    for line in range(3, min(line_count, 220), 8):
        decorations.append(HighlightRegion(line, 30, line, 48, hint_highlight, priority=12))
        decorations.append(VirtualText(line, "  : int", hint_text, priority=6))
    for line in range(6, min(line_count, 220), 11):
        decorations.append(GhostText(line, 18, "  completion_candidate()", ghost_style))
    return decorations


def _picker_items(count: int = 320) -> list[str]:
    return [f"src/module_{index:03d}/feature_picker_case_{index % 17}.py" for index in range(count)]


def _picker_preview(item: str) -> list[tuple[str, Style]]:
    return [
        ("path: ", Style(fg="#6c7086")),
        (item, Style(fg="#cdd6f4")),
        ("  score=92", Style(fg="#a6e3a1")),
    ]


def _make_picker_widget() -> PickerWidget:
    picker = PickerWidget()
    picker.open(
        "Profile Picker",
        _picker_items(),
        multi_select=True,
        preview=lambda item: [_picker_preview(item)] * 12,
    )
    for key in ("f", "e", "a", "<Down>", "<Down>", "<Tab>", "<Down>"):
        picker.feed_key(key)
    return picker


def _make_profile_event_loop() -> tuple[EventLoop, HeadlessBackend, NotifyManager]:
    primary_doc = Document()
    primary_doc.load_string(_python_text(180))
    primary_doc.path = Path("profile_frame_primary.py")
    primary_window = Window(primary_doc, width=120, height=40)
    primary_window.cursor.move_to(24, 8)
    primary_window.scroll_line = 12
    primary_window.options.update({"number": True, "relativenumber": True, "signcolumn": "yes", "tabstop": 4})

    workspace = Workspace(primary_window)
    secondary_window = workspace.active_tab.split_vertical()
    secondary_doc = Document()
    secondary_doc.load_string(_large_text(2200))
    secondary_doc.path = Path("profile_frame_notes.txt")
    secondary_window.document = secondary_doc
    secondary_window.cursor.move_to(210, 16)
    secondary_window.scroll_line = 180
    secondary_window.options.update({"number": True, "signcolumn": "no", "tabstop": 4})
    workspace.active_tab.focus_window(primary_window)

    engine = ModalEngine()
    registers = RegisterStore()
    editor_state = EditorState()
    editor_state.message = "Indexed 2 files; ready"
    dispatcher = ActionDispatcher(
        engine,
        primary_window,
        registers,
        editor_state=editor_state,
        workspace=workspace,
    )
    backend = HeadlessBackend(cols=120, rows=40)
    notify_manager = NotifyManager()
    notify_manager.notify("Startup complete\n2 buffers restored", level="info", title="session", timeout=0)
    event_loop = EventLoop(
        backend,
        engine,
        dispatcher,
        workspace,
        editor_state=editor_state,
        notify_manager=notify_manager,
    )
    event_loop._syntax_engine.submit = lambda *args, **kwargs: None  # type: ignore[method-assign]
    event_loop._running = True
    return event_loop, backend, notify_manager


def _profile_render_python(iterations: int) -> WorkloadResult:
    snapshot = _make_snapshot(
        _python_text(),
        suffix=".py",
        width=120,
        height=40,
        scroll_line=80,
        cursor_line=90,
        cursor_col=8,
        options={"number": True, "relativenumber": True, "signcolumn": "yes", "tabstop": 4},
    )
    rect = Rect(0, 0, 120, 40)
    theme = get_theme("catppuccin")
    spans = _parse_task(snapshot.buffer_snapshot)

    total_ms, samples = _time_iterations(
        iterations,
        lambda: render_window(snapshot, rect, True, highlight_spans=spans, theme=theme),
    )
    return WorkloadResult(
        name="render_python",
        category="render",
        runs=1,
        iterations=iterations,
        mean_ms=mean(samples),
        min_mean_ms=mean(samples),
        max_mean_ms=mean(samples),
        total_ms=total_ms,
        details={
            "lines": len(snapshot.buffer_snapshot.line_offsets),
            "spans": len(spans),
            "viewport": [rect.width, rect.height],
        },
    )


def _profile_render_large_text(iterations: int) -> WorkloadResult:
    snapshot = _make_snapshot(
        _large_text(),
        suffix=".txt",
        width=140,
        height=45,
        scroll_line=1200,
        cursor_line=1210,
        cursor_col=24,
        options={"number": True, "signcolumn": "no", "tabstop": 4},
    )
    rect = Rect(0, 0, 140, 45)
    theme = get_theme("catppuccin")

    total_ms, samples = _time_iterations(iterations, lambda: render_window(snapshot, rect, True, theme=theme))
    return WorkloadResult(
        name="render_large_text",
        category="render",
        runs=1,
        iterations=iterations,
        mean_ms=mean(samples),
        min_mean_ms=mean(samples),
        max_mean_ms=mean(samples),
        total_ms=total_ms,
        details={"lines": len(snapshot.buffer_snapshot.line_offsets), "viewport": [rect.width, rect.height]},
    )


def _profile_render_decorated_python(iterations: int) -> WorkloadResult:
    snapshot = _make_snapshot(
        _python_text(260),
        suffix=".py",
        width=120,
        height=40,
        scroll_line=50,
        cursor_line=64,
        cursor_col=14,
        options={"number": True, "relativenumber": True, "signcolumn": "yes", "tabstop": 4},
    )
    rect = Rect(0, 0, 120, 40)
    theme = get_theme("catppuccin")
    spans = _parse_task(snapshot.buffer_snapshot)
    decorations = _decorated_python_decorations(len(snapshot.buffer_snapshot.line_offsets))

    total_ms, samples = _time_iterations(
        iterations,
        lambda: render_window(
            snapshot,
            rect,
            True,
            decorations=decorations,
            highlight_spans=spans,
            theme=theme,
        ),
    )
    return WorkloadResult(
        name="render_decorated_python",
        category="render",
        runs=1,
        iterations=iterations,
        mean_ms=mean(samples),
        min_mean_ms=mean(samples),
        max_mean_ms=mean(samples),
        total_ms=total_ms,
        details={
            "lines": len(snapshot.buffer_snapshot.line_offsets),
            "spans": len(spans),
            "decorations": len(decorations),
            "viewport": [rect.width, rect.height],
        },
    )


def _profile_render_multi_window(iterations: int) -> WorkloadResult:
    snapshots = [
        _make_snapshot(
            _python_text(120), suffix=".py", width=80, height=24, scroll_line=10, cursor_line=12, cursor_col=4
        ),
        _make_snapshot(
            _large_text(1800), suffix=".md", width=80, height=24, scroll_line=240, cursor_line=250, cursor_col=12
        ),
        _make_snapshot(
            _python_text(90), suffix=".py", width=80, height=24, scroll_line=30, cursor_line=35, cursor_col=2
        ),
    ]
    theme = get_theme("catppuccin")
    rect = Rect(0, 0, 80, 24)
    span_sets = [_parse_task(snapshot.buffer_snapshot) for snapshot in snapshots]

    def _render_all() -> None:
        for snapshot, spans in zip(snapshots, span_sets, strict=False):
            render_window(snapshot, rect, True, highlight_spans=spans, theme=theme)

    total_ms, samples = _time_iterations(iterations, _render_all)
    return WorkloadResult(
        name="render_multi_window",
        category="render",
        runs=1,
        iterations=iterations,
        mean_ms=mean(samples),
        min_mean_ms=mean(samples),
        max_mean_ms=mean(samples),
        total_ms=total_ms,
        details={"windows": len(snapshots), "viewport": [rect.width, rect.height]},
    )


def _profile_picker_render(iterations: int) -> WorkloadResult:
    picker = _make_picker_widget()

    def _render_picker() -> None:
        grid = CellGrid(120, 40)
        picker.render(grid)
        grid.flush()

    total_ms, samples = _time_iterations(iterations, _render_picker)
    return WorkloadResult(
        name="picker_render",
        category="ui",
        runs=1,
        iterations=iterations,
        mean_ms=mean(samples),
        min_mean_ms=mean(samples),
        max_mean_ms=mean(samples),
        total_ms=total_ms,
        details={
            "items": len(picker._items),
            "filtered": len(picker._filtered),
            "selection": picker._sel,
            "query": picker._query,
            "preview_lines": 12,
        },
    )


def _profile_frame_render(iterations: int) -> WorkloadResult:
    event_loop, backend, notify_manager = _make_profile_event_loop()

    def _render_frame() -> None:
        event_loop._grid = None
        backend.clear_ops()
        event_loop._render({"full"})

    total_ms, samples = _time_iterations(iterations, _render_frame)
    return WorkloadResult(
        name="frame_render",
        category="ui",
        runs=1,
        iterations=iterations,
        mean_ms=mean(samples),
        min_mean_ms=mean(samples),
        max_mean_ms=mean(samples),
        total_ms=total_ms,
        details={
            "windows": len(event_loop._workspace.active_tab.all_windows()),
            "notifications": len(notify_manager._queue),
            "backend_ops": len(backend.render_ops()),
            "backend_raw_bytes": len(backend.raw_bytes()),
            "message": bool(event_loop._editor_state and event_loop._editor_state.message),
        },
    )


def _profile_diff_render(iterations: int) -> WorkloadResult:
    left_text, right_text = _diff_text_pair()
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    left_snapshot = _make_snapshot(
        left_text,
        suffix="_left.py",
        width=58,
        height=32,
        scroll_line=20,
        cursor_line=28,
        cursor_col=4,
        options={"number": True, "signcolumn": "yes", "tabstop": 4},
    )
    right_snapshot = _make_snapshot(
        right_text,
        suffix="_right.py",
        width=58,
        height=32,
        scroll_line=20,
        cursor_line=28,
        cursor_col=4,
        options={"number": True, "signcolumn": "yes", "tabstop": 4},
    )
    theme = get_theme("catppuccin")
    rect = Rect(0, 0, 58, 32)
    left_spans = _parse_task(left_snapshot.buffer_snapshot)
    right_spans = _parse_task(right_snapshot.buffer_snapshot)
    left_decorations, right_decorations, block_count = _diff_decorations(left_lines, right_lines)

    def _render_pair() -> None:
        render_window(left_snapshot, rect, True, decorations=left_decorations, highlight_spans=left_spans, theme=theme)
        render_window(
            right_snapshot, rect, False, decorations=right_decorations, highlight_spans=right_spans, theme=theme
        )

    total_ms, samples = _time_iterations(iterations, _render_pair)
    return WorkloadResult(
        name="diff_render",
        category="render",
        runs=1,
        iterations=iterations,
        mean_ms=mean(samples),
        min_mean_ms=mean(samples),
        max_mean_ms=mean(samples),
        total_ms=total_ms,
        details={
            "blocks": block_count,
            "left_lines": len(left_lines),
            "right_lines": len(right_lines),
            "decorations": len(left_decorations) + len(right_decorations),
            "viewport": [rect.width, rect.height],
        },
    )


def _profile_lsp_decorations_render(iterations: int) -> WorkloadResult:
    snapshot = _make_snapshot(
        _python_text(320),
        suffix="_lsp.py",
        width=120,
        height=40,
        scroll_line=60,
        cursor_line=74,
        cursor_col=10,
        options={"number": True, "relativenumber": True, "signcolumn": "yes", "tabstop": 4},
    )
    rect = Rect(0, 0, 120, 40)
    theme = get_theme("catppuccin")
    spans = _parse_task(snapshot.buffer_snapshot)
    decorations = _lsp_dense_decorations(len(snapshot.buffer_snapshot.line_offsets))

    total_ms, samples = _time_iterations(
        iterations,
        lambda: render_window(
            snapshot,
            rect,
            True,
            decorations=decorations,
            highlight_spans=spans,
            theme=theme,
        ),
    )
    return WorkloadResult(
        name="lsp_decorations_render",
        category="render",
        runs=1,
        iterations=iterations,
        mean_ms=mean(samples),
        min_mean_ms=mean(samples),
        max_mean_ms=mean(samples),
        total_ms=total_ms,
        details={
            "lines": len(snapshot.buffer_snapshot.line_offsets),
            "spans": len(spans),
            "decorations": len(decorations),
            "viewport": [rect.width, rect.height],
        },
    )


def _profile_syntax_python(iterations: int) -> WorkloadResult:
    snapshot = _make_snapshot(_python_text(260), suffix=".py", width=120, height=40)
    total_ms, samples = _time_iterations(iterations, lambda: _parse_task(snapshot.buffer_snapshot))  # type: ignore[arg-type]
    last_spans = _parse_task(snapshot.buffer_snapshot)
    return WorkloadResult(
        name="syntax_python",
        category="syntax",
        runs=1,
        iterations=iterations,
        mean_ms=mean(samples),
        min_mean_ms=mean(samples),
        max_mean_ms=mean(samples),
        total_ms=total_ms,
        details={"lines": len(snapshot.buffer_snapshot.line_offsets), "spans": len(last_spans)},
    )


def _profile_edit_large_file(iterations: int) -> WorkloadResult:
    """Per-keystroke insert cost into a large document.

    Times only the insert operations (not document load) to isolate the cost
    of _rebuild_line_index and _find_piece on a large piece table.
    Each iteration resets to a fresh copy of the document.
    """
    large_text = _large_text(5000)  # ~300 KB, 5 000 lines
    doc_lines = large_text.count("\n") + 1
    doc_bytes = len(large_text.encode("utf-8"))
    edits_per_iter = 50
    insert_line = 2500  # midfile — worst case for line-index rebuild

    samples: list[float] = []
    total_start = time.perf_counter()
    for _ in range(iterations):
        doc = Document()
        doc.load_string(large_text)
        t0 = time.perf_counter()
        for i in range(edits_per_iter):
            doc.insert(insert_line, i, "x")
        samples.append((time.perf_counter() - t0) * 1000.0 / edits_per_iter)
    total_ms = (time.perf_counter() - total_start) * 1000.0

    return WorkloadResult(
        name="edit_large_file",
        category="edit",
        runs=1,
        iterations=iterations,
        mean_ms=mean(samples),
        min_mean_ms=min(samples),
        max_mean_ms=max(samples),
        total_ms=total_ms,
        details={
            "doc_lines": doc_lines,
            "doc_bytes": doc_bytes,
            "edits_per_iter": edits_per_iter,
            "insert_line": insert_line,
            "note": "mean_ms = per-insert cost (excludes doc load)",
        },
    )


def _profile_persistence_atomic_text(iterations: int) -> WorkloadResult:
    payload = _large_text(1500)
    with tempfile.TemporaryDirectory(prefix="peovim-profile-") as tmp_dir:
        target = Path(tmp_dir) / "stores" / "workload.json"
        total_ms, samples = _time_iterations(iterations, lambda: atomic_write_text(target, payload, encoding="utf-8"))
    return WorkloadResult(
        name="persistence_atomic_text",
        category="persistence",
        runs=1,
        iterations=iterations,
        mean_ms=mean(samples),
        min_mean_ms=mean(samples),
        max_mean_ms=mean(samples),
        total_ms=total_ms,
        details={"bytes": len(payload.encode("utf-8"))},
    )


WORKLOADS: dict[str, _WorkloadSpec] = {
    "edit_large_file": _WorkloadSpec("edit", 5, _profile_edit_large_file),
    "diff_render": _WorkloadSpec("render", 6, _profile_diff_render),
    "frame_render": _WorkloadSpec("ui", 6, _profile_frame_render),
    "lsp_decorations_render": _WorkloadSpec("render", 6, _profile_lsp_decorations_render),
    "picker_render": _WorkloadSpec("ui", 8, _profile_picker_render),
    "render_decorated_python": _WorkloadSpec("render", 6, _profile_render_decorated_python),
    "render_python": _WorkloadSpec("render", 8, _profile_render_python),
    "render_large_text": _WorkloadSpec("render", 8, _profile_render_large_text),
    "render_multi_window": _WorkloadSpec("render", 6, _profile_render_multi_window),
    "syntax_python": _WorkloadSpec("syntax", 10, _profile_syntax_python),
    "persistence_atomic_text": _WorkloadSpec("persistence", 12, _profile_persistence_atomic_text),
}


def run_workloads(names: Sequence[str] | None = None, *, scale: float = 1.0, repeat: int = 1) -> list[WorkloadResult]:
    requested = list(names) if names else list(WORKLOADS)
    unknown = [name for name in requested if name not in WORKLOADS]
    if unknown:
        raise ValueError(f"Unknown workloads: {', '.join(sorted(unknown))}")
    if repeat < 1:
        raise ValueError("repeat must be >= 1")

    results = [
        _aggregate_results(
            [
                WORKLOADS[name].runner(_scaled_iterations(WORKLOADS[name].default_iterations, scale))
                for _ in range(repeat)
            ]
        )
        for name in requested
    ]
    return sorted(results, key=lambda result: result.mean_ms, reverse=True)


def format_results(results: Sequence[WorkloadResult]) -> str:
    lines = ["name                  category      runs  iterations  mean_ms  min_ms  max_ms  total_ms", "-" * 92]
    for result in results:
        lines.append(
            f"{result.name:<21} {result.category:<12} {result.runs:>5} {result.iterations:>10} {result.mean_ms:>8.3f} {result.min_mean_ms:>7.3f} {result.max_mean_ms:>7.3f} {result.total_ms:>9.3f}"
        )
    return "\n".join(lines)


def profile_workload(name: str, *, top: int = 20, scale: float = 1.0) -> None:
    """Run cProfile on a single workload and print a hotspot table.

    A warmup pass runs outside the profiler first to absorb one-time import
    and initialisation costs (platformdirs, ctypes, etc.) so they don't pollute
    the results.  Items that still appear with very low call counts in the output
    are one-time per-run setup (e.g. document line-index builds) and can be
    ignored when looking for render-loop hotspots.
    """
    if name not in WORKLOADS:
        raise ValueError(f"Unknown workload: {name!r}. Available: {', '.join(sorted(WORKLOADS))}")
    spec = WORKLOADS[name]
    # Use enough iterations that the hot loop dominates over any per-call setup.
    # 30× the default gives ~180 frame_render iterations ≈ 180 ms of hot-loop
    # vs ~37 ms one-time setup, so setup is <20% of total time.
    iterations = max(100, _scaled_iterations(spec.default_iterations, scale) * 30)

    # Warmup: burns in module imports and any lazy one-time global inits
    spec.runner(2)

    pr = cProfile.Profile()
    pr.enable()
    spec.runner(iterations)
    pr.disable()

    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("tottime")
    ps.print_stats(top)
    print(f"\n--- cProfile: {name} ({iterations} iterations) ---")
    print(s.getvalue())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--workload", action="append", dest="workloads", help="Run only the named workload (repeatable)"
    )
    parser.add_argument("--scale", type=float, default=1.0, help="Scale iteration counts for quicker or longer runs")
    parser.add_argument(
        "--repeat", type=int, default=1, help="Repeat each workload this many times and aggregate the results"
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a text table")
    parser.add_argument(
        "--profile", metavar="WORKLOAD", help="Run cProfile on a single workload and print hotspot table"
    )
    parser.add_argument(
        "--top", type=int, default=20, help="Number of functions to show in --profile output (default: 20)"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.profile:
        profile_workload(args.profile, top=args.top, scale=args.scale)
        return 0

    results = run_workloads(args.workloads, scale=args.scale, repeat=args.repeat)
    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        print(format_results(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
