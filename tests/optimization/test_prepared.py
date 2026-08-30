from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from random import Random

import pytest

from scheduler.domain import Assignment, PreparedProblem, score_schedule, validate_schedule
from scripts.benchmark_ga import build_scenarios
from tests.optimization.test_thesis_v2_rules import _problem


def assert_equivalent(problem, assignments):
    prepared = PreparedProblem(problem)
    assert validate_schedule(problem, assignments, prepared=prepared) == validate_schedule(problem, assignments)
    try:
        expected = score_schedule(problem, assignments)
    except ValueError as exc:
        with pytest.raises(ValueError) as actual:
            score_schedule(problem, assignments, prepared=prepared)
        assert str(actual.value) == str(exc)
    else:
        assert score_schedule(problem, assignments, prepared=prepared) == expected


def test_prepared_matches_complete_conflicting_and_malformed_assignments():
    rng = Random(175)
    for scenario in build_scenarios():
        problem = scenario.problem
        assert_equivalent(problem, scenario.witness)
        for _ in range(20):
            assignments = tuple(Assignment(event.event_id, rng.choice(event.candidates).candidate_id)
                                for event in problem.events)
            assert_equivalent(problem, assignments)
        assert_equivalent(problem, scenario.witness[:-1])
        assert_equivalent(problem, (*scenario.witness, scenario.witness[0]))
        assert_equivalent(problem, (Assignment("UNKNOWN", "UNKNOWN"), *scenario.witness))
        assert_equivalent(problem, (Assignment(problem.events[0].event_id, "INVALID"), *scenario.witness[1:]))


@pytest.mark.parametrize("change", ["authorization", "availability", "daily", "enrollment", "reserved"])
def test_prepared_does_not_trust_candidate_membership_for_policy_checks(change):
    problem = _problem(two_events=True, daily_limit=2, no_daily_limit=False)
    if change == "authorization":
        problem = replace(problem, room_evidence=(replace(problem.room_evidence[0], authorization_grants=()),))
    elif change == "availability":
        problem = replace(problem, room_evidence=(replace(problem.room_evidence[0], available_atom_ids=()),))
    elif change == "daily":
        problem = replace(problem, instructor_evidence=(replace(problem.instructor_evidence[0], max_daily_teaching_atoms=1),))
    elif change == "enrollment":
        problem = replace(problem, events=(replace(problem.events[0], section_headcounts=(), meeting_headcount=None), *problem.events[1:]))
    else:
        problem = replace(problem, events=(replace(problem.events[0], reserved_atom_ids=("MON0",)), *problem.events[1:]))
    assignments = tuple(Assignment(event.event_id, event.candidates[0].candidate_id) for event in problem.events)
    assert not validate_schedule(problem, assignments).feasible
    assert_equivalent(problem, assignments)


def test_prepared_rejects_equal_but_distinct_problem_and_is_immutable(balanced_problem):
    context = PreparedProblem(balanced_problem)
    other = replace(balanced_problem)
    for evaluator in (validate_schedule, score_schedule):
        with pytest.raises(ValueError, match="different problem"):
            evaluator(other, (), prepared=context)
    with pytest.raises(TypeError):
        context.candidates["E1"]["fake"] = balanced_problem.events[0].candidates[0]
    with pytest.raises(TypeError):
        context.atom_positions["MON0"] = 999
    with pytest.raises(FrozenInstanceError):
        context.problem = other


def test_preparation_deadline_checks_during_candidate_indexing(balanced_problem):
    calls = 0

    def clock():
        nonlocal calls
        calls += 1
        return 0.0 if calls < 3 else 2.0

    with pytest.raises(TimeoutError, match="deadline"):
        PreparedProblem(balanced_problem, deadline=1, clock=clock)


@pytest.mark.parametrize("schema", ["1.0", "1.1"])
def test_prepared_uses_grid_positions_with_sparse_orders_and_reordered_atoms(balanced_problem, schema):
    problem = replace(balanced_problem, schema_version=schema, time_atoms=tuple(
        replace(atom, order=atom.order * 3) for atom in reversed(balanced_problem.time_atoms)
    ))
    assignments = tuple(Assignment(event.event_id, event.candidates[0].candidate_id) for event in problem.events)
    assert score_schedule(problem, assignments).section_gap_atoms == 1
    assert_equivalent(problem, assignments)
