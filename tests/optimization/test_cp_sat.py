from __future__ import annotations

import pytest

from scheduler.domain import ProblemInstance, SolverAlgorithm, SolverConfig, SolverStatus
from scheduler.solvers import CpSatSolver, is_ortools_available

pytestmark = pytest.mark.skipif(not is_ortools_available(), reason="OR-Tools is not installed")


def config(seed: int = 1001) -> SolverConfig:
    return SolverConfig(
        algorithm=SolverAlgorithm.CP_SAT,
        seed=seed,
        time_limit_seconds=5,
        worker_count=1,
    )


def test_cp_sat_finds_and_proves_known_zero_penalty_optimum(
    balanced_problem: ProblemInstance,
) -> None:
    result = CpSatSolver().solve(balanced_problem, config())

    assert result.status is SolverStatus.OPTIMAL
    assert result.validation.feasible
    assert result.objective is not None
    assert result.objective.weighted_total == 0
    assert result.first_feasible_seconds is not None
    assert result.problem_hash == balanced_problem.canonical_hash
    assert dict(result.metrics)["objective_value"] == 0


def test_cp_sat_proves_global_resource_conflict_infeasible(
    conflicting_problem: ProblemInstance,
) -> None:
    result = CpSatSolver().solve(conflicting_problem, config())

    assert result.status is SolverStatus.INFEASIBLE
    assert not result.validation.feasible
    assert result.objective is None
    assert result.assignments == ()
    assert "proved" in result.stopping_reason.lower()


def test_cp_sat_honors_lock(balanced_problem: ProblemInstance) -> None:
    import dataclasses

    from scheduler.domain import Assignment

    problem = dataclasses.replace(
        balanced_problem,
        locked_assignments=(Assignment(event_id="E1", candidate_id="E1-M0"),),
    )

    result = CpSatSolver().solve(problem, config())

    selected = {assignment.event_id: assignment.candidate_id for assignment in result.assignments}
    assert selected["E1"] == "E1-M0"
    assert result.validation.feasible


def test_cp_sat_encoded_soft_components_equal_independent_scorer(
    balanced_problem: ProblemInstance,
) -> None:
    import dataclasses

    from scheduler.domain import Assignment

    problem = dataclasses.replace(
        balanced_problem,
        locked_assignments=(
            Assignment(event_id="E1", candidate_id="E1-M0"),
            Assignment(event_id="E2", candidate_id="E2-M2"),
        ),
    )

    result = CpSatSolver().solve(problem, config())

    assert result.status is SolverStatus.OPTIMAL
    assert result.objective is not None
    assert result.objective.section_gap_atoms == 1
    assert result.objective.instructor_gap_atoms == 1
    assert result.objective.load_imbalance == 8
    assert result.objective.weighted_total == 10
    assert dict(result.metrics)["objective_value"] == 10


def test_cp_sat_rejects_wrong_algorithm_config(balanced_problem: ProblemInstance) -> None:
    wrong = dataclasses_replace_algorithm(config(), SolverAlgorithm.GENETIC_ALGORITHM)
    with pytest.raises(ValueError, match="requires algorithm"):
        CpSatSolver().solve(balanced_problem, wrong)


def dataclasses_replace_algorithm(
    value: SolverConfig, algorithm: SolverAlgorithm
) -> SolverConfig:
    import dataclasses

    return dataclasses.replace(value, algorithm=algorithm)
