from __future__ import annotations

from dataclasses import replace
from random import Random

import pytest

from scheduler.domain import Assignment, SolverAlgorithm, SolverConfig, score_schedule, validate_schedule
from scheduler.solvers import genetic
from scheduler.solvers.neighborhood import improve_feasible
from scripts.benchmark_ga import build_scenarios
from tests.optimization.test_ga_repair import _alias_problem, _independent_evaluation, _plateau_problem


def test_unprepared_recheck_finishing_late_cannot_admit_an_incumbent(balanced_problem, monkeypatch):
    clock = [0.0]
    original = genetic.validate_schedule
    checks = []

    def validate(problem, assignments, **kwargs):
        checked = original(problem, assignments, **kwargs)
        if not kwargs.get("prepared") and assignments:
            checks.append(assignments)
            clock[0] = 2.0
        return checked

    monkeypatch.setattr(genetic, "monotonic", lambda: clock[0])
    monkeypatch.setattr(genetic, "validate_schedule", validate)
    result = genetic.GeneticAlgorithmSolver().solve(balanced_problem, SolverConfig(
        algorithm=SolverAlgorithm.GENETIC_ALGORITHM, time_limit_seconds=1,
    ))
    assert len(checks) == 1
    assert result.assignments == ()
    assert result.first_feasible_seconds is None


def test_prepared_mismatch_fails_closed(balanced_problem, monkeypatch):
    original = genetic.score_schedule

    def incorrect_fast_score(problem, assignments, **kwargs):
        score = original(problem, assignments, **kwargs)
        return replace(score, weighted_total=score.weighted_total + 1) if kwargs.get("prepared") else score

    monkeypatch.setattr(genetic, "score_schedule", incorrect_fast_score)
    with pytest.raises(ValueError, match="independent incumbent check"):
        genetic.GeneticAlgorithmSolver().solve(balanced_problem, SolverConfig(
            algorithm=SolverAlgorithm.GENETIC_ALGORITHM, time_limit_seconds=5,
        ))


def test_feasible_pass_improves_shared_objective_and_keeps_lock(balanced_problem):
    problem = replace(balanced_problem, locked_assignments=(Assignment("E1", "E1-M0"),))
    observed = []

    def evaluate(chromosome):
        assert chromosome[0] == 0
        observed.append(chromosome)
        return _independent_evaluation(problem, chromosome)

    initial = evaluate((0, 0)).fitness
    counters = {}
    result = improve_feasible(problem.events, (0, 0), initial, {0: 0}, evaluate, Random(1), 1, counters, lambda: 0)
    assert observed
    assert evaluate(result).fitness[0] == 0
    assert evaluate(result).fitness < initial
    assert 0 < counters["feasible_improvement_max_requests"] <= 64


def test_feasible_pass_rejects_cheaper_conflicting_moves():
    problem = _plateau_problem()
    expensive = replace(problem.events[0].candidates[1], preference_penalty=10)
    first = replace(problem.events[0], candidates=(problem.events[0].candidates[0], expensive))
    problem = replace(problem, events=(first, *problem.events[1:]),
                      objective_profile=replace(problem.objective_profile, preference_weight=1))
    initial = (1, 0, 0)
    assert _independent_evaluation(problem, initial).fitness == (0, 10)
    assert _independent_evaluation(problem, (0, 0, 0)).fitness[1] == 0

    def evaluate(chromosome):
        assert chromosome[1] == 0
        return _independent_evaluation(problem, chromosome)

    result = improve_feasible(problem.events, initial, (0, 10), {1: 0}, evaluate, Random(2), 1, {}, lambda: 0)
    assert result == initial


def test_feasible_pass_can_swap_when_every_single_move_conflicts():
    problem = _plateau_problem()
    first = replace(problem.events[0], candidates=(
        replace(problem.events[0].candidates[0], preference_penalty=10),
        problem.events[0].candidates[1],
    ))
    second = replace(problem.events[1], candidates=(
        replace(first.candidates[1], candidate_id="E2-B", preference_penalty=10),
        replace(first.candidates[0], candidate_id="E2-A", preference_penalty=0),
    ))
    problem = replace(problem, events=(first, second), locked_assignments=(),
                      objective_profile=replace(problem.objective_profile, preference_weight=1))
    assert _independent_evaluation(problem, (0, 0)).fitness == (0, 20)
    assert all(_independent_evaluation(problem, move).fitness[0] > 0 for move in ((0, 1), (1, 0)))
    counters = {}
    result = improve_feasible(problem.events, (0, 0), (0, 20), {},
                              lambda chromosome: _independent_evaluation(problem, chromosome),
                              Random(2), 1, counters, lambda: 0)
    assert result == (1, 1)
    assert _independent_evaluation(problem, result).fitness == (0, 0)
    assert counters["feasible_improvements"] == 1


def test_feasible_pass_requests_are_bounded_and_late_improvement_is_discarded():
    problem = _alias_problem(100, increasing_penalties=True)
    problem = replace(problem, events=problem.events[:1])
    original = (99,)
    initial = _independent_evaluation(problem, original).fitness
    counters = {}
    result = improve_feasible(problem.events, original, initial, {},
                              lambda chromosome: _independent_evaluation(problem, chromosome),
                              Random(2), 1, counters, lambda: 0)
    assert _independent_evaluation(problem, result).fitness < initial
    assert counters["feasible_improvement_evaluations"] == 64
    assert counters["feasible_improvement_max_requests"] == 64
    clock = [0.0]

    def late_evaluate(chromosome):
        clock[0] = 2
        return _independent_evaluation(problem, chromosome)

    assert improve_feasible(problem.events, original, initial, {}, late_evaluate,
                            Random(2), 1, {}, lambda: clock[0]) == original


def test_timing_metrics_are_diagnostic_only_and_normal_metrics_are_reproducible(balanced_problem):
    config = SolverConfig(algorithm=SolverAlgorithm.GENETIC_ALGORITHM, time_limit_seconds=5,
                          max_generations=2, population_size=4)
    solver = genetic.GeneticAlgorithmSolver()
    first = solver.solve(balanced_problem, config)
    second = solver.solve(balanced_problem, config)
    assert first.metrics == second.metrics
    assert "repair_seconds" not in dict(first.metrics)
    diagnostic = dict(solver.solve(balanced_problem, replace(config, diagnostic_trace=True)).metrics)
    for key in ("initialization_seconds", "preparation_seconds", "validation_seconds", "scoring_seconds", "repair_seconds"):
        assert diagnostic[key] >= 0


@pytest.mark.parametrize("repair_expires", [False, True])
def test_quality_pass_runs_after_completed_generations_but_not_deadline_padding(
    balanced_problem, monkeypatch, repair_expires,
):
    clock = [0.0]
    initial = iter(((0, 0), (1, 1)))
    quality_calls = []
    monkeypatch.setattr(genetic, "monotonic", lambda: clock[0])
    monkeypatch.setattr(genetic, "_randomized_greedy", lambda *args, **kwargs: next(initial))
    monkeypatch.setattr(genetic, "_tournament", lambda population, *args: population[-1])

    def repair(events, chromosome, *args):
        if repair_expires:
            clock[0] = 2
        return chromosome

    monkeypatch.setattr(genetic, "_repair", repair)
    monkeypatch.setattr(genetic, "improve_feasible", lambda *args: quality_calls.append(args[1]))
    result = genetic.GeneticAlgorithmSolver().solve(balanced_problem, SolverConfig(
        algorithm=SolverAlgorithm.GENETIC_ALGORITHM, population_size=2, tournament_size=2,
        mutation_rate=0, crossover_rate=0, max_generations=1, time_limit_seconds=1,
    ))
    assert result.validation.feasible
    assert len(quality_calls) == (0 if repair_expires else 1)
    assert dict(result.metrics)["completed_generations"] == len(quality_calls)


def test_additional_unseen_variants_have_feasible_witnesses_and_distinct_hashes():
    scenarios = build_scenarios(include_holdouts=True)
    assert [len(case.problem.events) for case in scenarios] == [30, 48, 40, 60, 32]
    assert len({case.problem.canonical_hash for case in scenarios}) == 5
    for case in scenarios:
        assert validate_schedule(case.problem, case.witness).feasible
        assert score_schedule(case.problem, case.witness).weighted_total >= 0
