"""
plugins.perf_panel — Live performance meter bottom panel tab.

Shows per-frame timing breakdown (render, maintenance, idle) and FPS.
Useful for catching slow init.py callbacks, heavy plugins, or event
handlers that cause dropped frames.

Load in init.py:
    plugins.load("peovim.plugins.perf_panel")

Show it:
    keymap.nmap("<leader>tp", lambda: api.ui.show_bottom_tab("perf"), desc="Perf panel")

Or just open the bottom panel (<A-p>) and switch to the "perf" tab with > / <.

Reading the display
-------------------
Each bar spans 0–16.7ms (one full 60fps frame budget):
  green  < 25% of budget (< 4.2ms)
  yellow  25–60% of budget (4.2–10ms)
  red     > 60% of budget (> 10ms)

render   — time spent building and flushing the cell grid
maintnce — time spent in maintenance (LSP queue drain, autosave, blink, …)
idle     — time asyncio.sleep() kept the CPU away from editor work

A healthy editor at 60fps shows render + maintnce well under 5ms combined
and idle around 16ms. If render or maintnce is yellow/red the bars will
tell you which subsystem to investigate.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from peovim.ui.perf_sampler import PerfSampler

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI
    from peovim.ui.cell_grid import CellGrid

# Bar scale: 16.67ms == 100% (one frame at 60fps)
_BUDGET_MS = 1000.0 / 60.0

# Colours
_BG = (22, 22, 28)
_DIM = (80, 82, 105)
_HDR_OK = (70, 200, 90)
_HDR_WARN = (200, 160, 40)
_HDR_BAD = (210, 65, 65)
_HINT = (52, 52, 68)
_BAR_EMPTY = (42, 42, 56)
_BAR_OK = (55, 185, 80)
_BAR_WARN = (200, 155, 40)
_BAR_BAD = (210, 65, 65)


def _bar_color(ratio: float) -> tuple[int, int, int]:
    if ratio < 0.25:
        return _BAR_OK
    if ratio < 0.60:
        return _BAR_WARN
    return _BAR_BAD


class PerfTab:
    """Bottom panel tab that shows live frame timing bar meters."""

    title = "perf"

    def __init__(self, sampler: PerfSampler, request_redraw: Any) -> None:
        self._sampler = sampler
        self._request_redraw = request_redraw
        self._refresh_task: asyncio.Task | None = None
        self._active = False

    # ------------------------------------------------------------------
    # Lifecycle hooks (called by PanelHost)
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        self._active = True
        with contextlib.suppress(RuntimeError):
            self._refresh_task = asyncio.get_event_loop().create_task(self._refresh_loop())

    def on_hide(self) -> None:
        self._active = False
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            self._refresh_task = None

    async def _refresh_loop(self) -> None:
        """Trigger a redraw every 0.5s so meters update without user input."""
        while self._active:
            await asyncio.sleep(0.5)
            if self._active:
                self._request_redraw()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def feed_key(self, key: str) -> bool:
        return False

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self, grid: CellGrid) -> None:
        for r in range(grid.height):
            grid.fill(r, 0, grid.width, bg=_BG)

        stats = self._sampler.stats()
        if not stats:
            if grid.height > 0:
                grid.write_str(0, 2, "  waiting for samples...", fg=_DIM, bg=_BG)
            return

        avg_frame_ms = stats["avg_frame"] * 1000
        max_frame_ms = stats["max_frame"] * 1000
        avg_render_ms = stats["avg_render"] * 1000
        max_render_ms = stats["max_render"] * 1000
        avg_maint_ms = stats["avg_maintenance"] * 1000
        max_maint_ms = stats["max_maintenance"] * 1000
        avg_idle_ms = stats["avg_idle"] * 1000
        fps = stats["fps"]
        dirty_pct = stats["render_pct"] * 100
        n = stats["n"]
        max_lsp_q = stats["max_lsp_queue"]
        avg_lsp_q = stats["avg_lsp_queue"]
        gc_collections = stats["gc_collections"]

        row = 0

        # ── Row 0: header ────────────────────────────────────────────
        if row < grid.height:
            fps_color = _HDR_OK if fps >= 55 else _HDR_WARN if fps >= 30 else _HDR_BAD
            header = (
                f"  fps {fps:5.1f}   "
                f"frame {avg_frame_ms:5.2f}ms avg  {max_frame_ms:5.2f}ms max   "
                f"dirty {dirty_pct:.0f}%   [{n} samples]"
            )
            grid.write_padded(row, 0, header, grid.width, fg=fps_color, bg=_BG)
            row += 1

        # ── Row 1: LSP queue + GC ─────────────────────────────────────
        # Python 3.13+ redesigned the GC to run incremental "safe-point"
        # collections at the end of each asyncio event-loop tick.  At 60fps
        # the baseline is ~60-120 collections per 120-frame window — this is
        # normal and each collection sweeps ~20 objects in <1ms.  Only flag
        # values well above this baseline as unusual GC pressure.
        if row < grid.height:
            lsp_color = _HDR_OK if max_lsp_q == 0 else _HDR_WARN if max_lsp_q < 10 else _HDR_BAD
            gc_per_frame = gc_collections / n if n > 0 else 0.0
            gc_color = _HDR_OK if gc_per_frame <= 2.0 else _HDR_WARN if gc_per_frame <= 5.0 else _HDR_BAD
            lsp_str = f"  lsp queue  avg {avg_lsp_q:4.1f}  max {max_lsp_q:3d}"
            gc_str = f"   gc cycles {gc_collections:3d} / {n} frames  ({gc_per_frame:.1f}/frame)"
            grid.write_str(row, 0, lsp_str, fg=lsp_color, bg=_BG)
            grid.write_str(row, len(lsp_str), gc_str, fg=gc_color, bg=_BG)
            row += 1

        # ── Bar rows ─────────────────────────────────────────────────
        # Bar width: squeeze or expand to fill available space
        bar_w = max(10, min(28, grid.width - 50))

        metrics: list[tuple[str, float, float | None]] = [
            ("render", avg_render_ms, max_render_ms),
            ("maintnce", avg_maint_ms, max_maint_ms),
            ("idle", avg_idle_ms, None),
        ]

        for label, avg_ms, max_ms in metrics:
            if row >= grid.height:
                break
            self._write_bar_row(grid, row, label, avg_ms, max_ms, bar_w)
            row += 1

        # ── Hint row ─────────────────────────────────────────────────
        if row < grid.height:
            grid.write_str(
                row,
                0,
                "  bars = % of 16.7ms budget (60fps)  │  green < 25%  yellow < 60%  red > 60%",
                fg=_HINT,
                bg=_BG,
            )

    def _write_bar_row(
        self,
        grid: CellGrid,
        row: int,
        label: str,
        avg_ms: float,
        max_ms: float | None,
        bar_w: int,
    ) -> None:
        ratio = min(avg_ms / _BUDGET_MS, 1.0)
        filled = round(ratio * bar_w)
        fill_color = _bar_color(ratio)

        col = 0

        # label
        label_str = f"  {label:<9}"
        grid.write_str(row, col, label_str, fg=_DIM, bg=_BG)
        col += len(label_str)

        # filled portion
        if filled:
            grid.write_str(row, col, "█" * filled, fg=fill_color, bg=_BG)
            col += filled

        # empty portion
        empty = bar_w - filled
        if empty:
            grid.write_str(row, col, "░" * empty, fg=_BAR_EMPTY, bg=_BG)
            col += empty

        # numeric stats
        stats_str = f"   {avg_ms:6.3f}ms avg  {max_ms:6.3f}ms max" if max_ms is not None else f"   {avg_ms:6.3f}ms avg"
        if col + len(stats_str) <= grid.width:
            grid.write_str(row, col, stats_str, fg=_DIM, bg=_BG)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def setup(api: EditorAPI) -> None:
    sampler = PerfSampler()

    # _event_loop is wired into EditorAPI after init.py runs, so we defer
    # the sampler attachment until editor_ready fires (by which point it's set).
    def _attach(**_kw: Any) -> None:
        event_loop = getattr(api, "_event_loop", None)
        if event_loop is not None:
            event_loop._perf_sampler = sampler

    api.events.on("editor_ready", _attach)

    def _request_redraw() -> None:
        bp = getattr(api.ui, "_bottom_panel", None)
        if bp is not None:
            bp._needs_full_redraw = True

    api.ui.register_bottom_tab("perf", PerfTab(sampler, _request_redraw))
