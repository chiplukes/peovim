"""
ui.alloc_tracer — tracemalloc-based allocation hotspot logger.

Starts tracemalloc on import and piggybacks on gc.callbacks to take
snapshots periodically.  Every `snapshot_every` gen0 collections it
logs the top allocation sites (by object count diff since last snapshot).

Usage — no init.py changes needed:

    uv run peovim myfile.py --log --log-modules peovim.ui.alloc_tracer

Then scroll / hold j|k for a few seconds.  Log lines look like:

    ALLOC +342  asyncio/tasks.py:645
    ALLOC +120  asyncio/futures.py:89
    ALLOC  +60  peovim/ui/perf_sampler.py:26

Tunable at runtime:
    from peovim.ui import alloc_tracer
    alloc_tracer.snapshot_every = 30   # collections between snapshots (default 20)
    alloc_tracer.top_n          = 15   # lines per snapshot (default 15)
    alloc_tracer.min_diff       = 5    # suppress sites with fewer new objects (default 5)
"""

from __future__ import annotations

import gc
import logging
import tracemalloc

log = logging.getLogger(__name__)

snapshot_every: int = 1
top_n: int = 15
min_diff: int = 5

tracemalloc.start(1)
log.info("alloc_tracer: tracemalloc started, gc callback registered")

_call_count: int = 0
_prev_snapshot: tracemalloc.Snapshot | None = None

_FILTERS = (
    tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
    tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
    tracemalloc.Filter(False, "<unknown>"),
)


def _gc_callback(phase: str, info: object) -> None:
    global _call_count, _prev_snapshot
    if phase != "stop" or not log.isEnabledFor(logging.DEBUG):
        return
    _call_count += 1
    if _call_count % snapshot_every != 0:
        return
    snap = tracemalloc.take_snapshot().filter_traces(_FILTERS)
    if _prev_snapshot is None:
        _prev_snapshot = snap
        return
    stats = snap.compare_to(_prev_snapshot, "lineno")
    _prev_snapshot = snap
    lines: list[str] = []
    for s in stats[:top_n]:
        if s.count_diff < min_diff:
            break
        lines.append(f"  {s.count_diff:+5d} obj  {s.traceback[0]}")
    if lines:
        log.debug("=== ALLOC DIFF (every %d gc, top %d) ===\n%s", snapshot_every, top_n, "\n".join(lines))


gc.callbacks.append(_gc_callback)
