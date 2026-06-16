from __future__ import annotations

import json

import pytest

from peovim.debug.profile_workloads import format_results, main, run_workloads


def test_run_workloads_returns_ranked_subset_results() -> None:
    results = run_workloads(["persistence_atomic_text", "render_decorated_python"], scale=0.01)

    assert [result.name for result in results]
    assert {result.name for result in results} == {"persistence_atomic_text", "render_decorated_python"}
    assert results == sorted(results, key=lambda result: result.mean_ms, reverse=True)
    assert all(result.iterations >= 1 for result in results)
    assert all(result.runs == 1 for result in results)
    decorated = next(result for result in results if result.name == "render_decorated_python")
    assert decorated.details["decorations"]


def test_run_workloads_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="Unknown workloads"):
        run_workloads(["missing-workload"], scale=0.01)


def test_run_workloads_rejects_repeat_below_one() -> None:
    with pytest.raises(ValueError, match="repeat must be >= 1"):
        run_workloads(["persistence_atomic_text"], scale=0.01, repeat=0)


def test_format_results_includes_header() -> None:
    results = run_workloads(["persistence_atomic_text"], scale=0.01)

    table = format_results(results)

    assert "name" in table
    assert "runs" in table
    assert "mean_ms" in table
    assert "persistence_atomic_text" in table


def test_run_workloads_aggregates_repeated_runs() -> None:
    results = run_workloads(["persistence_atomic_text"], scale=0.01, repeat=2)

    assert len(results) == 1
    result = results[0]
    assert result.runs == 2
    assert result.details["repeat"] == 2
    assert result.min_mean_ms <= result.mean_ms <= result.max_mean_ms


def test_picker_workload_reports_filtered_state() -> None:
    results = run_workloads(["picker_render"], scale=0.01)

    assert len(results) == 1
    result = results[0]
    assert result.name == "picker_render"
    assert result.details["items"]
    assert result.details["filtered"]
    assert result.details["query"] == "fea"


def test_frame_workload_reports_windows_and_output() -> None:
    results = run_workloads(["frame_render"], scale=0.01)

    assert len(results) == 1
    result = results[0]
    assert result.name == "frame_render"
    assert result.details["windows"] == 2
    assert result.details["notifications"] == 1
    assert result.details["backend_ops"] or result.details["backend_raw_bytes"]
    assert result.details["message"] is True


def test_diff_workload_reports_blocks_and_decorations() -> None:
    results = run_workloads(["diff_render"], scale=0.01)

    assert len(results) == 1
    result = results[0]
    assert result.name == "diff_render"
    assert result.details["blocks"]
    assert result.details["decorations"]
    assert result.details["left_lines"] != result.details["right_lines"]


def test_lsp_workload_reports_dense_decorations() -> None:
    results = run_workloads(["lsp_decorations_render"], scale=0.01)

    assert len(results) == 1
    result = results[0]
    assert result.name == "lsp_decorations_render"
    assert result.details["decorations"]
    assert result.details["lines"]


def test_main_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--workload", "persistence_atomic_text", "--scale", "0.01", "--repeat", "2", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["name"] == "persistence_atomic_text"
    assert payload[0]["runs"] == 2
