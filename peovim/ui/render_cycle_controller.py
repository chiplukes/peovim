"""Render-cycle helpers extracted from `EventLoop`."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from peovim.syntax.engine import HighlightSpan
from peovim.ui.cell_grid import CellGrid

if TYPE_CHECKING:
    from peovim.ui.event_loop import EventLoop


log = logging.getLogger(__name__)


def _index_syntax_spans_by_line(spans: tuple[HighlightSpan, ...]) -> dict[int, tuple[HighlightSpan, ...]]:
    if not spans:
        return {}

    spans_by_line: dict[int, list[HighlightSpan]] = {}
    for span in spans:
        for line in range(span.start_line, span.end_line + 1):
            spans_by_line.setdefault(line, []).append(span)
    return {line: tuple(line_spans) for line, line_spans in spans_by_line.items()}


class RenderCycleController:
    """Owns invalidation state and frame render-cycle execution for `EventLoop`."""

    def __init__(self, host: EventLoop) -> None:
        self._host = host

    def invalidate(self, reason: str = "full") -> None:
        host = self._host
        host._dirty = True
        host._pending_invalidation_reasons.add(reason)

    def invalidate_cmdline(self) -> None:
        self.invalidate("cmdline")

    def invalidate_message(self) -> None:
        self.invalidate("message")

    def consume_invalidation_reasons(self) -> set[str]:
        host = self._host
        reasons = set(host._pending_invalidation_reasons)
        host._pending_invalidation_reasons.clear()
        return reasons

    def render(self, reasons: set[str] | None = None) -> None:
        host = self._host
        cols, rows = host._backend.get_size()
        active_reasons = reasons or self.consume_invalidation_reasons() or {"full"}
        recreated_grid = False

        if host._grid is None or host._grid.width != cols or host._grid.height != rows:
            log.debug(
                "GRID-RECREATE: was (%s,%s) now (%d,%d)",
                host._grid.width if host._grid else None,
                host._grid.height if host._grid else None,
                cols,
                rows,
            )
            host._grid = CellGrid(cols, rows)
            recreated_grid = True

        grid = host._grid
        _dbg = log.isEnabledFor(logging.DEBUG)
        _t0 = time.perf_counter() if _dbg else 0.0
        try:
            if active_reasons <= {"message"}:
                host._frame_controller.render_cmdline_row(grid, cols, rows)
            else:
                host._render_body(grid, cols, rows, clear_grid=not recreated_grid)
        finally:
            _t1 = time.perf_counter() if _dbg else 0.0
            if hasattr(host._backend, "write_raw"):
                if recreated_grid:
                    host._backend.write_raw(b"\x1b[2J\x1b[H")
                grid.flush_ansi(host._ansi_buf)
                if host._ansi_buf:
                    host._backend.write_raw(host._ansi_buf)
            else:
                ops = grid.flush()
                if recreated_grid:
                    from peovim.ui.backend import ClearScreen, MoveCursor

                    ops = [ClearScreen(), MoveCursor(0, 0), *ops]
                if ops:
                    host._backend.write(ops)
            cursor_ops = host._build_terminal_cursor_ops()
            if cursor_ops:
                host._backend.write(cursor_ops)
            if _dbg:
                _t2 = time.perf_counter()
                body_ms = (_t1 - _t0) * 1000
                write_ms = (_t2 - _t1) * 1000
                total_ms = body_ms + write_ms
                if total_ms > 10:
                    log.debug(
                        "FRAME: body=%.1f ms  write=%.1f ms  total=%.1f ms  reasons=%s",
                        body_ms,
                        write_ms,
                        total_ms,
                        active_reasons,
                    )

    def on_syntax_done(self, buffer_id: int, spans: list[HighlightSpan]) -> None:
        host = self._host
        submitted_ver = host._syntax_submitted.get(buffer_id, 0)
        spans_tuple = tuple(spans)
        host._syntax_cache[buffer_id] = (submitted_ver, spans_tuple, _index_syntax_spans_by_line(spans_tuple))
        self.invalidate("full")

    def mark_dirty(self) -> None:
        self.invalidate("full")
