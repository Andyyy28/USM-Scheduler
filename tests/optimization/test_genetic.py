from __future__ import annotations

import dataclasses
from random import Random

import pytest

from scheduler.domain import (
    Assignment,
    CandidatePlacement,
    MeetingEvent,
    ProblemInstance,
    SolverAlgorithm,
    SolverConfig,
    SolverStatus,
)
from scheduler.solvers import GeneticAlgorithmSolver
from scheduler.solvers.genetic import _cache_capacity, _randomized_greedy


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


def test_ga_seeds_locked_occupancy_before_ranking_mutable_events() -> None:
    locked = MeetingEvent(
        event_id="E1",
        duration_atoms=1,
        section_ids=("S1",),
        instructor_ids=("I1",),
        candidates=(_candidate("E1-M0", "MON", "MON0"),),
    )
    mutable = MeetingEvent(
        event_id="E2",
        duration_atoms=1,
        section_ids=("S1",),
        instructor_ids=("I1",),
        candidates=(
            _candidate("E2-M0", "MON", "MON0"),
            _candidate("E2-T0", "TUE", "TUE0", preference_penalty=10),
        ),
    )

    chromosome = _randomized_greedy((locked, mutable), {0: 0}, Random(8))

    assert chromosome == (0, 1)


def test_ga_default_mutation_uses_only_mutable_events_and_caps_at_one(
    balanced_problem: ProblemInstance,
) -> None:
    problem = dataclasses.replace(
        balanced_problem,
        locked_assignments=(Assignment(event_id="E1", candidate_id="E1-M0"),),
    )

    result = GeneticAlgorithmSolver().solve(problem, config())
    metrics = dict(result.metrics)

    assert metrics["mutable_event_count"] == 1
    assert metrics["mutation_rate"] == 1.0
    assert metrics["implementation_version"] == "ga-v2"


def test_ga_allows_one_initial_incumbent_then_honors_tiny_deadline(
    balanced_problem: ProblemInstance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def expired_after_start() -> float:
        nonlocal calls
        calls += 1
        return 0.0 if calls == 1 else 2.0

    monkeypatch.setattr("scheduler.solvers.genetic.monotonic", expired_after_start)
    tiny = dataclasses.replace(config(), time_limit_seconds=1, max_generations=None)

    result = GeneticAlgorithmSolver().solve(balanced_problem, tiny)
    metrics = dict(result.metrics)

    assert metrics["initial_population_size"] == 1
    assert metrics["evaluated_chromosomes"] == 1
    assert result.assignments


def test_ga_exhausts_a_cacheable_small_search_space_without_proof_claim(
    balanced_problem: ProblemInstance,
) -> None:
    event = MeetingEvent(
        event_id="ONLY",
        duration_atoms=1,
        section_ids=("S1",),
        instructor_ids=("I1",),
        candidates=(
            _candidate("BEST", "MON", "MON0"),
            _candidate("OTHER", "TUE", "TUE0", preference_penalty=1),
        ),
    )
    problem = dataclasses.replace(
        balanced_problem,
        events=(event,),
        locked_assignments=(),
    )
    small = dataclasses.replace(
        config(),
        population_size=4,
        tournament_size=2,
        mutation_rate=None,
        repair_attempts=0,
        max_generations=20,
    )

    result = GeneticAlgorithmSolver().solve(problem, small)
    metrics = dict(result.metrics)

    assert result.status is SolverStatus.FEASIBLE
    assert metrics["evaluated_chromosomes"] == 2
    assert metrics["search_space_exhausted"] is True
    assert metrics["duplicates_suppressed"] > 0
    assert "without an optimality claim" in result.stopping_reason.lower()


def test_ga_cache_capacity_tracks_five_million_genes_with_safe_bounds() -> None:
    assert _cache_capacity(1) == 100_000
    assert _cache_capacity(100) == 50_000
    assert _cache_capacity(50_000) == 100
    assert _cache_capacity(10_000_000) == 100


def _candidate(
    candidate_id: str,
    day_id: str,
    atom_id: str,
    *,
    preference_penalty: int = 0,
) -> CandidatePlacement:
    return CandidatePlacement(
        candidate_id=candidate_id,
        room_id="R1",
        day_id=day_id,
        start_atom_id=atom_id,
        occupied_atom_ids=(atom_id,),
        preference_penalty=preference_penalty,
    )
