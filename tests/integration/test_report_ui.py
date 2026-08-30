from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from scheduler import models


def _algorithm_summary() -> dict[str, object]:
    return {
        "success_rate": None,
        "feasible_runs": 0,
        "observed_runs": 0,
        "status_counts": {},
        "feasible_soft_penalty": {"median": None},
        "rmst_time_to_feasibility_seconds": None,
        "hard_violation_vector": {},
        "feasible_soft_penalty_median_bootstrap_95": None,
        "shared_preprocessing_seconds": {"median": None},
        "execution_seconds": {"median": None},
        "independent_validation_seconds": {"median": None},
        "end_to_end_processing_seconds": {"median": None},
        "feasible_penalty_per_meeting": {"median": None},
        "feasible_normalized_quality_score": {"median": None},
        "feasible_objective_components": {},
        "solver_configuration_by_run": {},
    }


def _metric_set(*, available: bool, penalty: float = 12.0) -> dict[str, object]:
    return {
        "feasibility_rate": {
            "available": available,
            "value": 0.5 if available else None,
            "wilson_95": [0.095, 0.905] if available else [None, None],
            "feasible_runs": 1 if available else 0,
            "observed_runs": 2 if available else 0,
            "planned_runs": 2,
            "unavailable_reason": None if available else "No terminal runs have been observed.",
        },
        "median_feasible_raw_penalty": {
            "available": available,
            "value": penalty if available else None,
            "bootstrap_95": [penalty, penalty] if available else None,
            "feasible_runs": 1 if available else 0,
            "unavailable_reason": (
                None
                if available
                else "No feasible runs with a raw penalty have been observed."
            ),
        },
        "rmst_time_to_feasibility_seconds": {
            "available": available,
            "value": 1.25 if available else None,
            "deadline_seconds": 2.0,
            "observed_runs": 2 if available else 0,
            "censored_runs": 1 if available else 0,
            "unavailable_reason": None if available else "No terminal runs have been observed.",
        },
    }


def _summary(state: str, state_message: str) -> dict[str, object]:
    cp_available = state in {"preliminary", "complete"}
    ga_available = state == "complete"
    cp = {"algorithm": "CP_SAT", "label": "CP-SAT", **_metric_set(available=cp_available)}
    ga = {
        "algorithm": "GA",
        "label": "Genetic Algorithm",
        **_metric_set(available=ga_available, penalty=18.0),
    }
    protocol_issues = (
        [{"code": "PROTOCOL_MISMATCH", "message": "A run used a different protocol."}]
        if state == "invalid"
        else []
    )
    return {
        "batch": {
            "status": "DRAFT",
            "seeds": [1, 2],
            "time_limit_seconds": 2,
            "snapshot_hash": "snapshot-hash",
            "objective_profile": {"hash": "objective-hash"},
            "cpu_limit": 1,
            "memory_limit_mb": 2048,
            "requested_run_configuration": {},
        },
        "algorithms": {"CP_SAT": _algorithm_summary(), "GA": _algorithm_summary()},
        "benchmark": {
            "schema_version": "1.0",
            "state": state,
            "state_message": state_message,
            "comparable": state == "complete",
            "comparability_reasons": [],
            "protocol_integrity": {
                "valid": state != "invalid",
                "issues": protocol_issues,
            },
            "algorithm_ids": ["CP_SAT", "GA"],
            "by_algorithm": {"CP_SAT": cp, "GA": ga},
        },
        "primary_engine_decision": {"winner": None, "rationale": "No winner is forced."},
        "quality_metric_policy": {
            "normalizer_review": {"requires_stakeholder_review": False}
        },
        "comparative_tests": {"pairing_assumption": "Paired seeds.", "outcomes": {}},
        "objective_weight_sensitivity": {"available": False},
    }


def _render_report(summary: dict[str, object]) -> str:
    request = RequestFactory().get("/experiments/1/")
    request.user = models.User(username="report-user")
    return render_to_string(
        "scheduler/experiment_detail.html",
        {
            "request": request,
            "batch": SimpleNamespace(id=1, name="UI benchmark"),
            "summary": summary,
            "experiment_id": "1",
            "section": "runs",
        },
        request=request,
    )


@pytest.mark.parametrize(
    ("state", "message", "charts_expected"),
    [
        (
            "unavailable",
            "Benchmark unavailable: no terminal runs have been observed.",
            False,
        ),
        (
            "preliminary",
            "Preliminary benchmark: both algorithms need at least one terminal run before comparison.",
            True,
        ),
        (
            "complete",
            "Benchmark complete: all planned CP-SAT and GA runs are terminal and protocol-compatible.",
            True,
        ),
        (
            "invalid",
            "Benchmark invalid: controlled-experiment protocol integrity checks failed.",
            False,
        ),
    ],
)
def test_benchmark_report_renders_accessible_state_and_only_safe_charts(
    state: str,
    message: str,
    charts_expected: bool,
) -> None:
    html = _render_report(_summary(state, message))

    assert f'data-benchmark-state="{state}"' in html
    assert message in html
    assert html.count("data-benchmark-chart") == (3 if charts_expected else 0)
    if charts_expected:
        assert "Feasibility rate" in html
        assert "Feasible raw penalty" in html
        assert "RMST time to feasibility" in html
        assert "No mixed-unit overall score is calculated." in html
    if state == "invalid":
        assert "PROTOCOL_MISMATCH" in html
        assert "A run used a different protocol." in html


def test_benchmark_report_preserves_zero_penalties_and_table_fallbacks() -> None:
    summary = _summary(
        "complete",
        "Benchmark complete: all planned CP-SAT and GA runs are terminal and protocol-compatible.",
    )
    benchmark = deepcopy(summary["benchmark"])
    benchmark["by_algorithm"]["CP_SAT"]["median_feasible_raw_penalty"]["value"] = 0.0
    benchmark["by_algorithm"]["CP_SAT"]["median_feasible_raw_penalty"]["bootstrap_95"] = [
        0.0,
        0.0,
    ]
    benchmark["by_algorithm"]["GA"]["median_feasible_raw_penalty"]["value"] = 0.0
    benchmark["by_algorithm"]["GA"]["median_feasible_raw_penalty"]["bootstrap_95"] = [
        0.0,
        0.0,
    ]
    summary["benchmark"] = benchmark

    html = _render_report(summary)

    assert html.count('data-benchmark-value="0.0"') == 2
    assert html.count("0.000") >= 4
    assert "Detailed CP-SAT and Genetic Algorithm benchmark evidence" in html
    assert "Median wall-clock timing evidence by algorithm" in html
    assert "Feasible-schedule quality measures by algorithm" in html


def test_benchmark_report_handles_partial_and_all_infeasible_evidence() -> None:
    partial_message = "Preliminary benchmark: results may change while planned runs are pending."
    partial = _summary("preliminary", partial_message)
    partial_benchmark = deepcopy(partial["benchmark"])
    partial_benchmark["by_algorithm"]["GA"].update(_metric_set(available=True, penalty=18.0))
    partial["benchmark"] = partial_benchmark

    partial_html = _render_report(partial)

    assert partial_message in partial_html
    assert partial_html.count("data-benchmark-chart") == 3
    assert "No terminal runs have been observed." not in partial_html

    infeasible = _summary(
        "complete",
        "Benchmark complete: all planned CP-SAT and GA runs are terminal and protocol-compatible.",
    )
    infeasible_benchmark = deepcopy(infeasible["benchmark"])
    for algorithm in ("CP_SAT", "GA"):
        metrics = infeasible_benchmark["by_algorithm"][algorithm]
        metrics["feasibility_rate"].update({"value": 0.0, "feasible_runs": 0})
        metrics["median_feasible_raw_penalty"].update(
            {
                "available": False,
                "value": None,
                "bootstrap_95": None,
                "feasible_runs": 0,
                "unavailable_reason": "No feasible runs with a raw penalty have been observed.",
            }
        )
    infeasible["benchmark"] = infeasible_benchmark

    infeasible_html = _render_report(infeasible)

    assert infeasible_html.count("No feasible runs with a raw penalty have been observed.") == 2
    assert infeasible_html.count("data-benchmark-chart") == 3
    assert infeasible_html.count('data-benchmark-value="0.0"') == 2
