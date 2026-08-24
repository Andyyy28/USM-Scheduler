from __future__ import annotations

import dataclasses

import pytest

from scheduler.domain import Assignment, ProblemInstance, SolverAlgorithm, SolverConfig, SolverStatus
from scheduler.solvers import GeneticAlgorithmSolver


def config(seed: int = 1001) -> SolverConfig:
    return SolverConfig(
        algorithm=SolverAlgorithm.GENETIC_ALGORITHM,
        seed=seed,
        time_limit_seconds=5,
        population_size=30,
        tournament_size=3,
        max_generations=12,
        repair_attempts=10,
    )


def test_ga_finds_feasible_known_instance(balanced_problem: ProblemInstance) -> None:
    result = GeneticAlgorithmSolver().solve(balanced_problem, config())

    assert result.status is SolverStatus.FEASIBLE
    assert result.validation.feasible
    assert result.first_feasible_seconds is not None
    assert result.objective is not None
    assert result.objective.weighted_total == 0
    assert result.problem_hash == balanced_problem.canonical_hash


def test_ga_is_seed_reproducible_excluding_clock_metrics(
    balanced_problem: ProblemInstance,
) -> None:
    first = GeneticAlgorithmSolver().solve(balanced_problem, config(seed=404))
    second = GeneticAlgorithmSolver().solve(balanced_problem, config(seed=404))

    assert first.assignments == second.assignments
    assert first.objective == second.objective
    assert first.validation == second.validation
    assert first.metrics == second.metrics


def test_ga_reports_no_solution_without_claiming_infeasibility(
    conflicting_problem: ProblemInstance,
) -> None:
    result = GeneticAlgorithmSolver().solve(conflicting_problem, config())

    assert result.status is SolverStatus.NO_SOLUTION
    assert not result.validation.feasible
    assert result.assignments
    assert "no feasible solution" in result.stopping_reason.lower()
    assert "proved" not in result.stopping_reason.lower()


def test_ga_honors_locked_gene(balanced_problem: ProblemInstance) -> None:
    problem = dataclasses.replace(
        balanced_problem,
        locked_assignments=(Assignment(event_id="E1", candidate_id="E1-T0"),),
    )

    result = GeneticAlgorithmSolver().solve(problem, config())

    selected = {assignment.event_id: assignment.candidate_id for assignment in result.assignments}
    assert selected["E1"] == "E1-T0"
    assert result.validation.feasible


def test_ga_rejects_wrong_algorithm_config(balanced_problem: ProblemInstance) -> None:
    wrong = dataclasses.replace(config(), algorithm=SolverAlgorithm.CP_SAT)
    with pytest.raises(ValueError, match="requires algorithm"):
        GeneticAlgorithmSolver().solve(balanced_problem, wrong)


def test_ga_rejects_parallel_worker_request(balanced_problem: ProblemInstance) -> None:
    parallel = dataclasses.replace(config(), worker_count=2)
    with pytest.raises(ValueError, match="single-threaded"):
        GeneticAlgorithmSolver().solve(balanced_problem, parallel)
