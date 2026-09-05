from __future__ import annotations

import dataclasses
import json
from xml.etree import ElementTree

import pytest

from scheduler.domain.contracts import SolverAlgorithm, SolverConfig
from scheduler.services.convergence import convergence_svg, parse_trace
from scheduler.solvers.cp_sat import CpSatSolver
from scheduler.solvers.genetic import GeneticAlgorithmSolver
from scheduler.solvers.tracing import IncumbentTrace


@pytest.mark.parametrize("solver,algorithm", [
    (CpSatSolver(), SolverAlgorithm.CP_SAT),
    (GeneticAlgorithmSolver(), SolverAlgorithm.GENETIC_ALGORITHM),
])
def test_traces_are_opt_in_and_only_record_budget_qualified_incumbents(
    balanced_problem, solver, algorithm,
) -> None:
    config = SolverConfig(algorithm=algorithm, seed=9001, time_limit_seconds=5, max_generations=10)
    assert "diagnostic_trace" not in config.to_dict()
    routine = solver.solve(balanced_problem, config)
    assert "convergence_trace_json" not in dict(routine.metrics)
    diagnostic_config = dataclasses.replace(config, diagnostic_trace=True)
    assert config.canonical_hash != diagnostic_config.canonical_hash
    diagnostic = solver.solve(balanced_problem, diagnostic_config)
    raw = dict(diagnostic.metrics)["convergence_trace_json"]
    points = parse_trace(raw, 5)
    assert points
    assert points[-1]["raw_penalty"] == diagnostic.objective.weighted_total
    assert all(point["elapsed_seconds"] <= 5 for point in points)
    svg = convergence_svg(25, [{"algorithm": algorithm.value, "points": points}], 5)
    assert ElementTree.fromstring(svg).tag.endswith("svg")


def test_trace_storage_is_bounded_and_preserves_endpoints() -> None:
    trace = IncumbentTrace(True)
    for index in range(2000):
        trace.observe(index / 10, (0, 2000 - index))
    points = json.loads(dict(trace.metrics())["convergence_trace_json"])
    assert len(points) <= 512
    assert points[0]["raw_penalty"] == 2000
    assert points[-1]["raw_penalty"] == 1
    assert parse_trace(points, 300) == points


@pytest.mark.parametrize("point", [
    {"elapsed_seconds": -0.1, "hard_violations": 0, "raw_penalty": 0},
    {"elapsed_seconds": 301, "hard_violations": 0, "raw_penalty": 0},
    {"elapsed_seconds": float("nan"), "hard_violations": 0, "raw_penalty": 0},
    {"elapsed_seconds": 1, "hard_violations": 0, "raw_penalty": "<script>"},
    {"elapsed_seconds": 1, "hard_violations": 1, "raw_penalty": 0},
])
def test_invalid_trace_is_not_rendered(point) -> None:
    assert parse_trace([point], 300) == []


def test_empty_and_zero_penalty_traces_have_valid_distinct_rendering() -> None:
    empty = convergence_svg(100, [], 300)
    zero = convergence_svg(100, [{"algorithm": "GA", "points": [
        {"elapsed_seconds": 0, "hard_violations": 0, "raw_penalty": 0},
    ]}], 300)
    assert "No recorded incumbent" in empty
    assert "No recorded incumbent" not in zero
    assert "NaN" not in zero
    ElementTree.fromstring(zero)


def test_cp_sat_does_not_accept_a_post_deadline_incumbent(balanced_problem, monkeypatch) -> None:
    calls = 0

    def clock():
        nonlocal calls
        calls += 1
        return 0.0 if calls == 1 else 2.0

    monkeypatch.setattr("scheduler.solvers.cp_sat.monotonic", clock)
    result = CpSatSolver().solve(balanced_problem, SolverConfig(
        algorithm=SolverAlgorithm.CP_SAT, time_limit_seconds=1,
    ))
    assert result.assignments == ()
    assert result.first_feasible_seconds is None
    assert not result.validation.feasible
