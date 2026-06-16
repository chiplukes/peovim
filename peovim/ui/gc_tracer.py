"""
ui.gc_tracer — GC collection diagnostic logger.

Registers a gc.callbacks hook automatically on import.  The hook does nothing
unless DEBUG logging is enabled for this module, so the overhead when disabled
is one `log.isEnabledFor(DEBUG)` check per GC collection (negligible).

Usage — no init.py changes needed:

    uv run peovim myfile.py --log --log-modules peovim.ui.gc_tracer

Then open the log (:LogView or tail ~/.config/peovim/peovim.log) and hold j/k.
Each GC collection logs two lines like:

    GC start gen0  count=(712,3,0)
    GC stop  gen0  count=(12,3,0)  collected=47  uncollectable=0

The "count" tuple is (gen0, gen1, gen2) from gc.get_count().
The delta between stop-count[0] and the next start-count[0] shows exactly
how many new GC-tracked objects were created between collections.

On "start" phase, also logs the top object types in the collecting generation:

    GC start gen0  count=(712,3,0)  top: list×18  dict×12  tuple×9

Tune output:
    top_n:     max types to show (default 10)
    min_count: suppress types with fewer objects (default 3)
    log_types: set False to skip the type analysis (default True)
"""

from __future__ import annotations

import gc
import logging
from collections import Counter
from typing import Any

log = logging.getLogger(__name__)

top_n: int = 10
min_count: int = 3
log_types: bool = True


def _gc_callback(phase: str, info: dict[str, Any]) -> None:
    if not log.isEnabledFor(logging.DEBUG):
        return
    generation = info.get("generation", "?")
    count = gc.get_count()
    if phase == "start":
        if log_types:
            objs = gc.get_objects(generation)
            if objs:
                counts: Counter[str] = Counter(type(o).__name__ for o in objs)
                top = [(name, n) for name, n in counts.most_common(top_n) if n >= min_count]
                summary = "  ".join(f"{name}\u00d7{n}" for name, n in top)
                log.debug("GC start gen%s  count=%r  top: %s", generation, count, summary)
            else:
                log.debug("GC start gen%s  count=%r  (empty)", generation, count)
        else:
            log.debug("GC start gen%s  count=%r", generation, count)
    else:  # "stop"
        collected = info.get("collected", "?")
        uncollectable = info.get("uncollectable", "?")
        log.debug(
            "GC stop  gen%s  count=%r  collected=%s  uncollectable=%s", generation, count, collected, uncollectable
        )


gc.callbacks.append(_gc_callback)
