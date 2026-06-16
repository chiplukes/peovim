from __future__ import annotations

from peovim.core.document import Document
from peovim.core.window import Window
from peovim.ui.cell_grid import CellGrid
from peovim.ui.layout import Rect
from peovim.ui.render_jobs import (
    RenderExecutionPolicy,
    RenderJobExecutor,
    WindowRenderResult,
    create_window_render_job,
    execute_render_strategy,
    merge_window_render_results,
    parallel_render_pool_size,
    parallel_render_requested,
    parallel_render_supported,
    parallel_render_worker_count,
    render_runtime_diagnostics,
    render_window_job,
    render_window_jobs,
    render_window_jobs_parallel,
    render_window_jobs_sequential_into,
    resolve_render_strategy,
)


def _make_snapshot(text: str = "hello\n", width: int = 12, height: int = 3):
    document = Document()
    document.load_string(text)
    window = Window(document, width=width, height=height)
    return window.snapshot(), Rect(0, 0, width, height)


def _grid_text(grid: CellGrid, row: int) -> str:
    return "".join(cell[0] for cell in grid._current[row])


def test_create_window_render_job_materializes_iterables() -> None:
    snapshot, rect = _make_snapshot()
    decorations = [object(), object()]
    spans = []

    job = create_window_render_job(snapshot, rect, True, decorations=decorations, highlight_spans=spans)

    assert isinstance(job.decorations, tuple)
    assert job.decorations == tuple(decorations)
    assert isinstance(job.highlight_spans, tuple)


def test_render_window_job_returns_grid_for_rect() -> None:
    snapshot, rect = _make_snapshot("hello\nworld\n")
    job = create_window_render_job(snapshot, rect, True)

    result = render_window_job(job)

    assert result.rect == rect
    assert result.grid.width == rect.width
    assert _grid_text(result.grid, 0).startswith("hello")


def test_render_window_jobs_preserves_job_order() -> None:
    first_snapshot, first_rect = _make_snapshot("one\n")
    second_snapshot, second_rect = _make_snapshot("two\n")
    second_rect = Rect(5, 0, second_rect.width, second_rect.height)
    jobs = [
        create_window_render_job(first_snapshot, first_rect, True),
        create_window_render_job(second_snapshot, second_rect, False),
    ]

    results = render_window_jobs(jobs, allow_parallel=True)

    assert [result.rect for result in results] == [first_rect, second_rect]


def test_merge_window_render_results_blits_sub_grids_by_rect() -> None:
    target = CellGrid(8, 2)
    left = CellGrid(2, 1)
    left.write_str(0, 0, "AB")
    right = CellGrid(2, 1)
    right.write_str(0, 0, "CD")
    results = [
        WindowRenderResult(Rect(0, 0, 2, 1), left),
        WindowRenderResult(Rect(4, 0, 2, 1), right),
    ]

    merge_window_render_results(target, results)

    assert _grid_text(target, 0)[:6] == "AB  CD"


def test_render_window_jobs_sequential_into_blits_without_result_list() -> None:
    target = CellGrid(8, 2)
    first_snapshot, first_rect = _make_snapshot("one\n")
    second_snapshot, second_rect = _make_snapshot("two\n")
    second_rect = Rect(4, 0, second_rect.width, second_rect.height)
    jobs = [
        create_window_render_job(first_snapshot, first_rect, True),
        create_window_render_job(second_snapshot, second_rect, False),
    ]

    render_window_jobs_sequential_into(target, jobs)

    assert _grid_text(target, 0)[:7] == "one two"


def test_parallel_render_supported_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ED_ENABLE_PARALLEL_RENDER", raising=False)

    assert parallel_render_supported() is False


def test_parallel_render_supported_requires_opt_in_and_capability(monkeypatch) -> None:
    monkeypatch.setenv("ED_ENABLE_PARALLEL_RENDER", "1")
    monkeypatch.setattr(
        "peovim.ui.render_jobs.sysconfig.get_config_var", lambda name: 1 if name == "Py_GIL_DISABLED" else None
    )

    assert parallel_render_supported() is True


def test_parallel_render_requested_policy_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("ED_ENABLE_PARALLEL_RENDER", "1")

    assert parallel_render_requested(RenderExecutionPolicy(parallel_enabled=False)) is False


def test_parallel_render_supported_uses_policy_without_env(monkeypatch) -> None:
    monkeypatch.delenv("ED_ENABLE_PARALLEL_RENDER", raising=False)
    monkeypatch.setattr(
        "peovim.ui.render_jobs.sysconfig.get_config_var", lambda name: 1 if name == "Py_GIL_DISABLED" else None
    )

    assert parallel_render_supported(policy=RenderExecutionPolicy(parallel_enabled=True)) is True


def test_render_runtime_diagnostics_reports_free_threaded_reason(monkeypatch) -> None:
    monkeypatch.setenv("ED_ENABLE_PARALLEL_RENDER", "1")
    monkeypatch.delenv("ED_PARALLEL_RENDER_WORKERS", raising=False)
    monkeypatch.setattr("peovim.ui.render_jobs.os.cpu_count", lambda: 6)
    monkeypatch.setattr(
        "peovim.ui.render_jobs.sysconfig.get_config_var", lambda name: 0 if name == "Py_GIL_DISABLED" else None
    )

    diagnostics = render_runtime_diagnostics()

    assert diagnostics.requested is True
    assert diagnostics.runtime_supported is False
    assert diagnostics.effective_parallelism is False
    assert diagnostics.free_threaded is False
    assert diagnostics.worker_count == 6
    assert diagnostics.worker_source == "cpu"
    assert diagnostics.reason == "Python build is not free-threaded (Py_GIL_DISABLED=0)"


def test_render_runtime_diagnostics_reports_single_worker_sequential_reason(monkeypatch) -> None:
    monkeypatch.setattr(
        "peovim.ui.render_jobs.sysconfig.get_config_var", lambda name: 1 if name == "Py_GIL_DISABLED" else None
    )

    diagnostics = render_runtime_diagnostics(RenderExecutionPolicy(parallel_enabled=True, max_workers=1))

    assert diagnostics.requested is True
    assert diagnostics.runtime_supported is True
    assert diagnostics.effective_parallelism is False
    assert diagnostics.worker_count == 1
    assert diagnostics.worker_source == "policy"
    assert diagnostics.reason == "effective worker count is 1, so rendering remains sequential"


def test_parallel_render_worker_count_uses_cpu_count_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ED_PARALLEL_RENDER_WORKERS", raising=False)
    monkeypatch.setattr("peovim.ui.render_jobs.os.cpu_count", lambda: 8)

    assert parallel_render_worker_count(3) == 3
    assert parallel_render_worker_count(12) == 8


def test_parallel_render_worker_count_honors_env_override(monkeypatch) -> None:
    monkeypatch.setenv("ED_PARALLEL_RENDER_WORKERS", "2")

    assert parallel_render_worker_count(5) == 2


def test_parallel_render_worker_count_invalid_env_falls_back_to_one(monkeypatch) -> None:
    monkeypatch.setenv("ED_PARALLEL_RENDER_WORKERS", "invalid")

    assert parallel_render_worker_count(5) == 1


def test_parallel_render_pool_size_uses_cpu_count_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ED_PARALLEL_RENDER_WORKERS", raising=False)
    monkeypatch.setattr("peovim.ui.render_jobs.os.cpu_count", lambda: 6)

    assert parallel_render_pool_size() == 6


def test_parallel_render_pool_size_honors_env_override(monkeypatch) -> None:
    monkeypatch.setenv("ED_PARALLEL_RENDER_WORKERS", "4")

    assert parallel_render_pool_size() == 4


def test_parallel_render_pool_size_invalid_env_falls_back_to_one(monkeypatch) -> None:
    monkeypatch.setenv("ED_PARALLEL_RENDER_WORKERS", "invalid")

    assert parallel_render_pool_size() == 1


def test_parallel_render_pool_size_uses_policy_override() -> None:
    assert parallel_render_pool_size(policy=RenderExecutionPolicy(max_workers=5)) == 5


def test_resolve_render_strategy_defaults_to_sequential(monkeypatch) -> None:
    monkeypatch.delenv("ED_ENABLE_PARALLEL_RENDER", raising=False)

    assert resolve_render_strategy(allow_parallel=True) == "sequential"


def test_resolve_render_strategy_uses_parallel_when_supported(monkeypatch) -> None:
    monkeypatch.setattr("peovim.ui.render_jobs.parallel_render_supported", lambda *, policy=None: True)

    assert resolve_render_strategy(allow_parallel=True) == "parallel"


def test_execute_render_strategy_dispatches_to_parallel_helper(monkeypatch) -> None:
    snapshot, rect = _make_snapshot("one\n")
    jobs = [create_window_render_job(snapshot, rect, True)]
    called: list[str] = []

    def fake_parallel(window_jobs, *, executor=None, policy=None):
        called.append("parallel")
        return []

    monkeypatch.setattr("peovim.ui.render_jobs.render_window_jobs_parallel", fake_parallel)

    assert execute_render_strategy(jobs, strategy="parallel") == []
    assert called == ["parallel"]


def test_execute_render_strategy_dispatches_to_sequential_helper(monkeypatch) -> None:
    snapshot, rect = _make_snapshot("one\n")
    jobs = [create_window_render_job(snapshot, rect, True)]
    called: list[str] = []

    def fake_sequential(window_jobs):
        called.append("sequential")
        return []

    monkeypatch.setattr("peovim.ui.render_jobs.render_window_jobs_sequential", fake_sequential)

    assert execute_render_strategy(jobs, strategy="sequential") == []
    assert called == ["sequential"]


def test_render_window_jobs_parallel_uses_executor_and_preserves_order(monkeypatch) -> None:
    first_snapshot, first_rect = _make_snapshot("one\n")
    second_snapshot, second_rect = _make_snapshot("two\n")
    jobs = [
        create_window_render_job(first_snapshot, first_rect, True),
        create_window_render_job(second_snapshot, second_rect, False),
    ]
    seen_max_workers: list[int] = []

    class FakeExecutor:
        def __init__(self, *, max_workers: int, thread_name_prefix: str) -> None:
            seen_max_workers.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def map(self, func, window_jobs):
            return [func(job) for job in window_jobs]

    monkeypatch.setattr("peovim.ui.render_jobs.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr("peovim.ui.render_jobs.parallel_render_worker_count", lambda count, *, policy=None: 2)
    monkeypatch.setattr("peovim.ui.render_jobs.parallel_render_pool_size", lambda *, policy=None: 4)

    results = render_window_jobs_parallel(jobs)

    assert seen_max_workers == [4]
    assert [result.rect for result in results] == [first_rect, second_rect]


def test_render_window_jobs_parallel_falls_back_for_single_job(monkeypatch) -> None:
    snapshot, rect = _make_snapshot("one\n")
    jobs = [create_window_render_job(snapshot, rect, True)]
    called: list[str] = []

    def fake_sequential(window_jobs):
        called.append("sequential")
        return []

    monkeypatch.setattr("peovim.ui.render_jobs.render_window_jobs_sequential", fake_sequential)

    assert render_window_jobs_parallel(jobs) == []
    assert called == ["sequential"]


def test_render_job_executor_reuses_pool_for_same_worker_count(monkeypatch) -> None:
    first_snapshot, first_rect = _make_snapshot("one\n")
    second_snapshot, second_rect = _make_snapshot("two\n")
    jobs = [
        create_window_render_job(first_snapshot, first_rect, True),
        create_window_render_job(second_snapshot, second_rect, False),
    ]
    created: list[FakeExecutor] = []

    class FakeExecutor:
        def __init__(self, *, max_workers: int, thread_name_prefix: str) -> None:
            self.max_workers = max_workers
            self.thread_name_prefix = thread_name_prefix
            self.shutdown_calls: list[bool] = []
            created.append(self)

        def shutdown(self, *, wait: bool = False) -> None:
            self.shutdown_calls.append(wait)

        def map(self, func, window_jobs):
            return [func(job) for job in window_jobs]

    monkeypatch.setattr("peovim.ui.render_jobs.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr("peovim.ui.render_jobs.parallel_render_worker_count", lambda count, *, policy=None: 2)
    monkeypatch.setattr("peovim.ui.render_jobs.parallel_render_supported", lambda *, policy=None: True)

    executor = RenderJobExecutor()

    first_results = executor.render_jobs(jobs, allow_parallel=True)
    second_results = executor.render_jobs(jobs, allow_parallel=True)
    executor.shutdown(wait=False)

    assert len(created) == 1
    assert [result.rect for result in first_results] == [first_rect, second_rect]
    assert [result.rect for result in second_results] == [first_rect, second_rect]
    assert created[0].shutdown_calls == [False]


def test_render_job_executor_reuses_pool_when_job_count_changes(monkeypatch) -> None:
    first_snapshot, first_rect = _make_snapshot("one\n")
    second_snapshot, second_rect = _make_snapshot("two\n")
    two_jobs = [
        create_window_render_job(first_snapshot, first_rect, True),
        create_window_render_job(second_snapshot, second_rect, False),
    ]
    one_job = [create_window_render_job(first_snapshot, first_rect, True)]
    created: list[FakeExecutor] = []

    class FakeExecutor:
        def __init__(self, *, max_workers: int, thread_name_prefix: str) -> None:
            self.max_workers = max_workers
            self.shutdown_calls: list[bool] = []
            created.append(self)

        def shutdown(self, *, wait: bool = False) -> None:
            self.shutdown_calls.append(wait)

        def map(self, func, window_jobs):
            return [func(job) for job in window_jobs]

    monkeypatch.setattr("peovim.ui.render_jobs.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr("peovim.ui.render_jobs.parallel_render_worker_count", lambda count, *, policy=None: count)
    monkeypatch.setattr("peovim.ui.render_jobs.parallel_render_pool_size", lambda *, policy=None: 4)
    monkeypatch.setattr("peovim.ui.render_jobs.parallel_render_supported", lambda *, policy=None: True)

    executor = RenderJobExecutor()
    executor.render_jobs(two_jobs, allow_parallel=True)
    executor.render_jobs(one_job, allow_parallel=True)
    executor.render_jobs(two_jobs, allow_parallel=True)

    assert len(created) == 1
    assert created[0].shutdown_calls == []


def test_render_job_executor_shutdown_is_idempotent(monkeypatch) -> None:
    snapshot, rect = _make_snapshot("one\n")
    jobs = [create_window_render_job(snapshot, rect, True), create_window_render_job(snapshot, rect, False)]
    shutdown_calls: list[bool] = []

    class FakeExecutor:
        def __init__(self, *, max_workers: int, thread_name_prefix: str) -> None:
            self.max_workers = max_workers

        def shutdown(self, *, wait: bool = False) -> None:
            shutdown_calls.append(wait)

        def map(self, func, window_jobs):
            return [func(job) for job in window_jobs]

    monkeypatch.setattr("peovim.ui.render_jobs.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr("peovim.ui.render_jobs.parallel_render_worker_count", lambda count, *, policy=None: 2)
    monkeypatch.setattr("peovim.ui.render_jobs.parallel_render_pool_size", lambda *, policy=None: 4)
    monkeypatch.setattr("peovim.ui.render_jobs.parallel_render_supported", lambda *, policy=None: True)

    executor = RenderJobExecutor()
    executor.render_jobs(jobs, allow_parallel=True)
    executor.shutdown(wait=False)
    executor.shutdown(wait=True)

    assert shutdown_calls == [False]
