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
from scheduler.solvers.genetic import _cache_capacity, _Evaluation, _randomized_greedy, _repair


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
    assert metrics["implementation_version"] == "ga-v7"


def test_ga_does_not_evaluate_when_construction_starts_after_deadline(
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

    assert metrics["initial_population_size"] == 0
    assert metrics["evaluated_chromosomes"] == 0
    assert result.assignments == ()
    assert result.first_feasible_seconds is None
    assert not result.validation.feasible


def test_ga_rejects_initial_evaluation_scored_after_deadline(
    balanced_problem: ProblemInstance, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scheduler.solvers import genetic

    clock = [0.0]
    original_score = genetic.score_schedule

    def late_score(problem, assignments, **kwargs):
        score = original_score(problem, assignments, **kwargs)
        clock[0] = 2.0
        return score

    monkeypatch.setattr(genetic, "monotonic", lambda: clock[0])
    monkeypatch.setattr(genetic, "score_schedule", late_score)
    result = GeneticAlgorithmSolver().solve(
        balanced_problem, dataclasses.replace(config(), time_limit_seconds=1),
    )
    assert dict(result.metrics)["evaluated_chromosomes"] == 1
    assert result.assignments == ()
    assert result.first_feasible_seconds is None
    assert not result.validation.feasible


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


def test_ga_records_observed_repair_success_and_failure() -> None:
    event = MeetingEvent(
        event_id="repair-event", duration_atoms=1, section_ids=("S1",), instructor_ids=("I1",),
        candidates=(_candidate("bad", "MON", "MON0"), _candidate("good", "TUE", "TUE0")),
    )

    def evaluate(chromosome):
        return _Evaluation((int(chromosome[0] == 0), 0), (0,))

    success: dict[str, int] = {}
    assert _repair((event,), (0,), {}, 1, evaluate, Random(1), float("inf"), success) == (1,)
    assert success["repair_needed"] == success["repair_successes"] == 1
    assert success["repair_candidate_evaluations"] == 1
    failure: dict[str, int] = {}
    assert _repair((event,), (0,), {}, 0, evaluate, Random(1), float("inf"), failure) == (0,)
    assert failure["repair_failures"] == 1


def test_ga_records_actual_mutated_genes(balanced_problem: ProblemInstance) -> None:
    observed = GeneticAlgorithmSolver().solve(
        balanced_problem,
        dataclasses.replace(config(), population_size=2, tournament_size=2, max_generations=1, mutation_rate=1),
    )
    metrics = dict(observed.metrics)
    assert metrics["mutation_operations"] >= metrics["mutated_offspring"] > 0
    assert metrics["repair_calls"] > 0


def test_repair_prioritizes_removing_a_conflict_over_an_earlier_soft_improvement(
    balanced_problem: ProblemInstance,
) -> None:
    from scheduler.domain import score_schedule, validate_schedule

    events = (
        MeetingEvent(
            "E1", 1, (), (),
            (
                _candidate("E1-EXPENSIVE", "MON", "MON0", preference_penalty=10),
                _candidate("E1-CHEAP", "MON", "MON0"),
            ),
        ),
        MeetingEvent(
            "E2", 1, (), (),
            (
                _candidate("E2-CONFLICT", "MON", "MON0"),
                _candidate("E2-FREE", "TUE", "TUE0", preference_penalty=100),
            ),
        ),
    )
    problem = dataclasses.replace(
        balanced_problem,
        events=events,
        objective_profile=dataclasses.replace(
            balanced_problem.objective_profile,
            preference_weight=1, section_gap_weight=0, instructor_gap_weight=0,
            load_imbalance_weight=0,
        ),
    )

    def evaluate(chromosome):
        assignments = tuple(
            Assignment(event.event_id, event.candidates[gene].candidate_id)
            for event, gene in zip(events, chromosome, strict=True)
        )
        validation = validate_schedule(problem, assignments)
        return _Evaluation(
            (validation.hard_violation_count, score_schedule(problem, assignments).weighted_total),
            (0, 1) if not validation.feasible else (),
        )

    assert evaluate((0, 0)).fitness == (1, 10)
    assert evaluate((1, 0)).fitness == (1, 0)
    repaired = _repair(events, (0, 0), {}, 1, evaluate, Random(1), float("inf"))
    assert evaluate(repaired).fitness == (0, 110)


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
