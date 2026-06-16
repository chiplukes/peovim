"""
ui.render_jobs — per-window render job and merge helpers

Formalizes the worker contract for window rendering without enabling parallel
execution yet. Jobs use immutable snapshots and return standalone sub-grids,
so a future executor-backed path can share the same inputs and outputs.
"""

from __future__ import annotations

import os
import sysconfig
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from peovim.ui.cell_grid import CellGrid
from peovim.ui.layout import Rect
from peovim.ui.window_renderer import render_window

if TYPE_CHECKING:
    from collections.abc import Iterable

    from peovim.core.snapshot import WindowSnapshot
    from peovim.syntax.engine import HighlightSpan
    from peovim.syntax.themes import Theme


@dataclass(frozen=True)
class WindowRenderJob:
    """Immutable input for rendering one window into its own sub-grid."""

    snapshot: WindowSnapshot
    rect: Rect
    is_active: bool
    decorations: tuple[object, ...] = ()
    highlight_spans: tuple[HighlightSpan, ...] = ()
    theme: Theme | None = None
    sign_registry: object | None = None


@dataclass(frozen=True)
class WindowRenderResult:
    """Rendered sub-grid paired with the rect where it should be merged."""

    rect: Rect
    grid: CellGrid


RenderStrategy = Literal["sequential", "parallel"]


@dataclass(frozen=True)
class RenderExecutionPolicy:
    """Runtime policy controlling whether parallel rendering is requested."""

    parallel_enabled: bool | None = None
    max_workers: int | None = None


@dataclass(frozen=True)
class RenderRuntimeDiagnostics:
    """Resolved runtime diagnostics for the gated parallel render path."""

    requested: bool
    runtime_supported: bool
    effective_parallelism: bool
    free_threaded: bool
    gil_disabled_value: object
    worker_count: int
    worker_source: str
    reason: str


def render_execution_policy_from_values(
    parallel_mode: object,
    worker_setting: object,
) -> RenderExecutionPolicy:
    parallel_enabled = None
    if parallel_mode == "on":
        parallel_enabled = True
    elif parallel_mode == "off":
        parallel_enabled = False
    max_workers = worker_setting if isinstance(worker_setting, int) and worker_setting > 0 else None
    return RenderExecutionPolicy(parallel_enabled=parallel_enabled, max_workers=max_workers)


class RenderJobExecutor:
    """Owns the optional worker pool for parallel window rendering."""

    def __init__(self) -> None:
        self._executor: ThreadPoolExecutor | None = None
        self._max_workers: int = 0

    def render_jobs(
        self,
        jobs: list[WindowRenderJob],
        *,
        allow_parallel: bool = False,
        policy: RenderExecutionPolicy | None = None,
    ) -> list[WindowRenderResult]:
        strategy = resolve_render_strategy(allow_parallel=allow_parallel, policy=policy)
        return execute_render_strategy(jobs, strategy=strategy, executor=self, policy=policy)

    def render_parallel(
        self,
        jobs: list[WindowRenderJob],
        *,
        policy: RenderExecutionPolicy | None = None,
    ) -> list[WindowRenderResult]:
        if len(jobs) <= 1:
            return render_window_jobs_sequential(jobs)

        worker_count = parallel_render_worker_count(len(jobs), policy=policy)
        if worker_count <= 1:
            return render_window_jobs_sequential(jobs)

        executor = self._ensure_executor(parallel_render_pool_size(policy=policy))
        return list(executor.map(render_window_job, jobs))

    def shutdown(self, *, wait: bool = False) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=wait)
            self._executor = None
            self._max_workers = 0

    def _ensure_executor(self, max_workers: int) -> ThreadPoolExecutor:
        if self._executor is None or self._max_workers != max_workers:
            self.shutdown(wait=False)
            self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="peovim-render")
            self._max_workers = max_workers
        return self._executor


def create_window_render_job(
    snapshot: WindowSnapshot,
    rect: Rect,
    is_active: bool,
    decorations: Iterable[object] | None = None,
    highlight_spans: Iterable[HighlightSpan] | None = None,
    theme: Theme | None = None,
    sign_registry: object | None = None,
) -> WindowRenderJob:
    return WindowRenderJob(
        snapshot=snapshot,
        rect=rect,
        is_active=is_active,
        decorations=tuple(decorations or ()),
        highlight_spans=tuple(highlight_spans or ()),
        theme=theme,
        sign_registry=sign_registry,
    )


def render_window_job(job: WindowRenderJob, grid: CellGrid | None = None) -> WindowRenderResult:
    result = render_window(
        job.snapshot,
        job.rect,
        is_active=job.is_active,
        decorations=job.decorations or None,
        highlight_spans=job.highlight_spans or None,
        theme=job.theme,
        sign_registry=job.sign_registry,
        grid=grid,
    )
    return WindowRenderResult(rect=job.rect, grid=result)


def resolve_render_strategy(
    *,
    allow_parallel: bool = False,
    policy: RenderExecutionPolicy | None = None,
) -> RenderStrategy:
    if allow_parallel and parallel_render_supported(policy=policy):
        return "parallel"
    return "sequential"


def render_window_jobs_sequential(jobs: list[WindowRenderJob]) -> list[WindowRenderResult]:
    return [render_window_job(job) for job in jobs]


def render_window_jobs_sequential_into(grid: CellGrid, jobs: list[WindowRenderJob]) -> None:
    for job in jobs:
        result = render_window_job(job)
        grid.blit(result.grid, dest_x=result.rect.x, dest_y=result.rect.y)


def parallel_render_worker_count(
    job_count: int,
    *,
    policy: RenderExecutionPolicy | None = None,
) -> int:
    return max(1, min(job_count, parallel_render_pool_size(policy=policy)))


def parallel_render_pool_size(*, policy: RenderExecutionPolicy | None = None) -> int:
    worker_count, _worker_source = _resolve_parallel_render_worker_config(policy)
    return max(1, worker_count)


def render_window_jobs_parallel(
    jobs: list[WindowRenderJob],
    *,
    executor: RenderJobExecutor | None = None,
    policy: RenderExecutionPolicy | None = None,
) -> list[WindowRenderResult]:
    if len(jobs) <= 1:
        return render_window_jobs_sequential(jobs)

    if executor is not None:
        return executor.render_parallel(jobs, policy=policy)

    worker_count = parallel_render_worker_count(len(jobs), policy=policy)
    if worker_count <= 1:
        return render_window_jobs_sequential(jobs)

    with ThreadPoolExecutor(
        max_workers=parallel_render_pool_size(policy=policy), thread_name_prefix="peovim-render"
    ) as executor:
        return list(executor.map(render_window_job, jobs))


def execute_render_strategy(
    jobs: list[WindowRenderJob],
    *,
    strategy: RenderStrategy,
    executor: RenderJobExecutor | None = None,
    policy: RenderExecutionPolicy | None = None,
) -> list[WindowRenderResult]:
    if strategy == "parallel":
        return render_window_jobs_parallel(jobs, executor=executor, policy=policy)
    return render_window_jobs_sequential(jobs)


def render_window_jobs(
    jobs: list[WindowRenderJob],
    *,
    allow_parallel: bool = False,
    executor: RenderJobExecutor | None = None,
    policy: RenderExecutionPolicy | None = None,
) -> list[WindowRenderResult]:
    strategy = resolve_render_strategy(allow_parallel=allow_parallel, policy=policy)
    return execute_render_strategy(jobs, strategy=strategy, executor=executor, policy=policy)


def merge_window_render_results(grid: CellGrid, results: list[WindowRenderResult]) -> None:
    for result in results:
        grid.blit(result.grid, dest_x=result.rect.x, dest_y=result.rect.y)


def parallel_render_requested(policy: RenderExecutionPolicy | None = None) -> bool:
    if policy is not None and policy.parallel_enabled is not None:
        return policy.parallel_enabled
    enabled = os.getenv("ED_ENABLE_PARALLEL_RENDER", "").strip().lower()
    return enabled in {"1", "true", "yes", "on"}


def render_runtime_diagnostics(policy: RenderExecutionPolicy | None = None) -> RenderRuntimeDiagnostics:
    worker_count, worker_source = _resolve_parallel_render_worker_config(policy)
    gil_disabled_value = sysconfig.get_config_var("Py_GIL_DISABLED")
    free_threaded = gil_disabled_value == 1
    requested = parallel_render_requested(policy)
    runtime_supported = requested and free_threaded
    effective_parallelism = runtime_supported and worker_count > 1

    if not requested:
        reason = "parallel rendering not requested"
    elif not free_threaded:
        reason = f"Python build is not free-threaded (Py_GIL_DISABLED={gil_disabled_value!r})"
    elif worker_count <= 1:
        reason = "effective worker count is 1, so rendering remains sequential"
    else:
        reason = "parallel rendering available"

    return RenderRuntimeDiagnostics(
        requested=requested,
        runtime_supported=runtime_supported,
        effective_parallelism=effective_parallelism,
        free_threaded=free_threaded,
        gil_disabled_value=gil_disabled_value,
        worker_count=worker_count,
        worker_source=worker_source,
        reason=reason,
    )


def parallel_render_supported(*, policy: RenderExecutionPolicy | None = None) -> bool:
    return render_runtime_diagnostics(policy).runtime_supported


def _resolve_parallel_render_worker_config(policy: RenderExecutionPolicy | None = None) -> tuple[int, str]:
    if policy is not None and policy.max_workers is not None:
        return max(1, policy.max_workers), "policy"

    configured = os.getenv("ED_PARALLEL_RENDER_WORKERS", "").strip()
    if configured:
        try:
            return max(1, int(configured)), "environment"
        except ValueError:
            return 1, "environment"

    return max(1, os.cpu_count() or 1), "cpu"
