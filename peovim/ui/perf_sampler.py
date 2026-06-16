"""
ui.perf_sampler — Lightweight ring-buffer frame timing sampler.

Attached to EventLoop._perf_sampler when the perf_panel plugin is loaded.
Zero overhead when unattached (attribute is None, the check is a single
branch that Python can optimize away).

Each frame the render loop pushes one PerfSample with four durations:
  maintenance  — time spent in _run_render_maintenance (LSP drain, blink, etc.)
  render       — time spent in _render_if_dirty (cell grid build + flush)
  idle         — time spent in asyncio.sleep (CPU doing nothing for us)
  total        — wall-clock time for the whole tick (maintenance + render + idle)
  rendered     — True if the frame actually triggered a render pass
  lsp_queue    — depth of the LSP callback queue at the start of the tick
  gc_delta     — number of GC collection cycles that fired during this tick
"""

from __future__ import annotations

import gc as _gc
from collections import deque
from typing import NamedTuple


class PerfSample(NamedTuple):
    maintenance: float
    render: float
    idle: float
    total: float
    rendered: bool
    lsp_queue: int
    gc_delta: int


# Zero-allocation GC collection counter.  Incremented by a gc callback instead
# of calling gc.get_stats() (which allocates a list+3 dicts) every frame.
_gc_collections: int = sum(s["collections"] for s in _gc.get_stats())


def _gc_count_callback(phase: str, info: object) -> None:
    global _gc_collections
    if phase == "stop":
        _gc_collections += 1


_gc.callbacks.append(_gc_count_callback)


class PerfSampler:
    """Collects per-frame timing samples in a fixed-size ring buffer.

    Pushed by EventLoop._render_loop each tick; read by PerfTab for display.
    Thread-safe for single-reader/single-writer on CPython (deque append/read).
    """

    def __init__(self, maxlen: int = 120) -> None:
        self._buf: deque[PerfSample] = deque(maxlen=maxlen)

    def push(
        self,
        *,
        maintenance: float,
        render: float,
        idle: float,
        total: float,
        rendered: bool,
        lsp_queue: int = 0,
        gc_delta: int = 0,
    ) -> None:
        self._buf.append(PerfSample(maintenance, render, idle, total, rendered, lsp_queue, gc_delta))

    def stats(self) -> dict:
        """Return a snapshot dict of computed stats, or {} if no samples yet."""
        n = len(self._buf)
        if n == 0:
            return {}

        sum_total = sum_render = sum_maint = sum_idle = sum_lsp = 0.0
        max_total = max_render = max_maint = max_lsp = 0.0
        sum_gc = render_count = 0
        for s in self._buf:
            t = s.total
            r = s.render
            m = s.maintenance
            lq = s.lsp_queue
            sum_total += t
            sum_render += r
            sum_maint += m
            sum_idle += s.idle
            sum_lsp += lq
            sum_gc += s.gc_delta
            if t > max_total:
                max_total = t
            if r > max_render:
                max_render = r
            if m > max_maint:
                max_maint = m
            if lq > max_lsp:
                max_lsp = lq
            if s.rendered:
                render_count += 1

        return {
            "n": n,
            "fps": n / sum_total if sum_total > 0 else 0.0,
            "avg_frame": sum_total / n,
            "max_frame": max_total,
            "avg_render": sum_render / n,
            "max_render": max_render,
            "avg_maintenance": sum_maint / n,
            "max_maintenance": max_maint,
            "avg_idle": sum_idle / n,
            "render_pct": render_count / n,
            "max_lsp_queue": int(max_lsp),
            "avg_lsp_queue": sum_lsp / n,
            "gc_collections": sum_gc,
        }
