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
    TimeAtom,
    ViolationCode,
    validate_schedule,
)
from scheduler.solvers import GeneticAlgorithmSolver
from scheduler.solvers.genetic import (
    _incremental_conflict_count,
    _occupy,
    _randomized_greedy,
)
from tests.optimization.test_thesis_v2_rules import _problem


def _candidate(
    candidate_id: str,
    day_id: str,
    *atom_ids: str,
    room_id: str = "R1",
    preference_penalty: int = 0,
) -> CandidatePlacement:
    return CandidatePlacement(
        candidate_id=candidate_id,
        room_id=room_id,
        day_id=day_id,
        start_atom_id=atom_ids[0],
        occupied_atom_ids=atom_ids,
        preference_penalty=preference_penalty,
    )


def _locked_team_events() -> tuple[MeetingEvent, ...]:
    return (
        MeetingEvent(
            event_id="LOCKED-TEAM",
            duration_atoms=2,
            section_ids=("S1",),
            instructor_ids=("I1", "I2"),
            candidates=(_candidate("TEAM-MON", "MON", "MON0", "MON1"),),
        ),
        MeetingEvent(
            event_id="LOCKED-SOLO",
            duration_atoms=1,
            section_ids=("S2",),
            instructor_ids=("I1",),
            candidates=(_candidate("SOLO-MON", "MON", "MON2"),),
        ),
        MeetingEvent(
            event_id="MUTABLE-TEAM",
            duration_atoms=1,
            section_ids=("S3",),
            instructor_ids=("I1", "I2"),
            candidates=(
                _candidate("TEAM-PREFERRED", "MON", "MON3"),
                _candidate("TEAM-WITHIN-LIMIT", "TUE", "TUE0", preference_penalty=10),
            ),
        ),
    )


@pytest.mark.parametrize("daily_limits", [{"I1": 3}, {"I2": 2}, {"I1": 3, "I2": 2}])
def test_greedy_respects_locked_multi_atom_load_for_each_team_instructor(
    daily_limits: dict[str, int],
) -> None:
    # No room, instructor, or section overlaps distinguish these choices. Only
    # the teaching load accumulated by the locks makes Monday unacceptable.
    chromosome = _randomized_greedy(
        _locked_team_events(),
        {0: 0, 1: 0},
        Random(17),
        daily_limits=daily_limits,
    )

    assert chromosome == (0, 0, 1)


@pytest.mark.parametrize("daily_limits", [None, {}, {"UNRELATED-INSTRUCTOR": 1}])
def test_greedy_keeps_preferred_nonoverlapping_day_without_an_applicable_limit(
    daily_limits: dict[str, int] | None,
) -> None:
    events = _locked_team_events()
    default_result = _randomized_greedy(events, {0: 0, 1: 0}, Random(17))
    explicit_result = _randomized_greedy(
        events,
        {0: 0, 1: 0},
        Random(17),
        daily_limits=daily_limits,
    )

    assert default_result == explicit_result == (0, 0, 0)


def test_zero_preference_weight_ignores_raw_candidate_penalties_with_the_same_seed() -> None:
    event = _locked_team_events()[2]
    reversed_preferences = dataclasses.replace(
        event,
        candidates=(
            dataclasses.replace(event.candidates[0], preference_penalty=100),
            dataclasses.replace(event.candidates[1], preference_penalty=0),
        ),
    )
    selected = set()

    for seed in range(32):
        original = _randomized_greedy((event,), {}, Random(seed), preference_weight=0)
        changed = _randomized_greedy(
            (reversed_preferences,), {}, Random(seed), preference_weight=0
        )
        assert original == changed
        selected.add(original)

    # Zero-weight preferences leave both placements eligible for random ties.
    assert selected == {(0,), (1,)}


@pytest.mark.parametrize("duration_atoms", [2, 4, 8])
def test_incremental_conflicts_match_independent_pair_counts_across_multiple_atoms(
    duration_atoms: int,
) -> None:
    atom_ids = tuple(f"MON{index}" for index in range(duration_atoms))
    events = tuple(
        MeetingEvent(
            event_id=f"E{index}",
            duration_atoms=duration_atoms,
            section_ids=("SHARED", "SECOND"),
            instructor_ids=("SHARED", "SECOND"),
            distinct_day_group="SHARED",
            candidates=(_candidate(f"C{index}", "MON", *atom_ids, room_id="SHARED"),),
        )
        for index in range(3)
    )
    room_occupancy: dict[tuple[str, str], set[str]] = {}
    instructor_occupancy: dict[tuple[str, str], set[str]] = {}
    section_occupancy: dict[tuple[str, str], set[str]] = {}
    distinct_days: dict[tuple[str, str], set[str]] = {}
    for event in events[:-1]:
        _occupy(
            event,
            event.candidates[0],
            room_occupancy,
            instructor_occupancy,
            section_occupancy,
            distinct_days,
        )

    incremental = _incremental_conflict_count(
        events[-1],
        events[-1].candidates[0],
        room_occupancy,
        instructor_occupancy,
        section_occupancy,
        distinct_days,
    )
    problem = ProblemInstance(
        schema_version="1.0",
        term_revision_id="GREEDY-CONFLICT-REGRESSION",
        time_atoms=tuple(
            TimeAtom(atom_id, "MON", 0, order) for order, atom_id in enumerate(atom_ids)
        ),
        events=events,
    )
    assignments = tuple(Assignment(event.event_id, event.candidates[0].candidate_id) for event in events)
    before = validate_schedule(dataclasses.replace(problem, events=events[:-1]), assignments[:-1])
    after = validate_schedule(problem, assignments)

    # Two existing meetings each share one room, two instructors, two sections,
    # and a distinct-day group, regardless of the number of overlapping atoms.
    assert incremental == after.hard_violation_count - before.hard_violation_count == 12


def test_greedy_returns_none_when_the_deadline_is_already_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scheduler.solvers.genetic.monotonic", lambda: 2.0)

    assert _randomized_greedy(
        _locked_team_events(), {0: 0, 1: 0}, Random(17), deadline=1.0
    ) is None


def test_greedy_discards_construction_when_the_deadline_expires_midway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def clock() -> float:
        nonlocal calls
        calls += 1
        return 0.0 if calls == 1 else 2.0

    monkeypatch.setattr("scheduler.solvers.genetic.monotonic", clock)

    chromosome = _randomized_greedy(_locked_team_events(), {}, Random(17), deadline=1.0)

    assert calls >= 2
    assert chromosome is None


def test_ga_initialization_receives_the_frozen_schema_1_2_daily_limit() -> None:
    problem = _problem(two_events=True, daily_limit=2, no_daily_limit=False)
    atoms = (*problem.time_atoms, TimeAtom("MON2", "MON", 0, 2))
    mutable = dataclasses.replace(
        problem.events[1],
        duration_atoms=1,
        candidates=(
            _candidate("E2-MON-LATE", "MON", "MON2"),
            _candidate("E2-TUE", "TUE", "TUE0", preference_penalty=10),
        ),
    )
    problem = dataclasses.replace(
        problem,
        time_atoms=atoms,
        events=(problem.events[0], mutable),
        locked_assignments=(Assignment("E1", "E1-MON"),),
        room_evidence=tuple(
            dataclasses.replace(evidence, available_atom_ids=tuple(atom.atom_id for atom in atoms))
            for evidence in problem.room_evidence
        ),
        instructor_evidence=tuple(
            dataclasses.replace(evidence, available_atom_ids=tuple(atom.atom_id for atom in atoms))
            for evidence in problem.instructor_evidence
        ),
    )
    monday = (Assignment("E1", "E1-MON"), Assignment("E2", "E2-MON-LATE"))
    assert dict(validate_schedule(problem, monday).counts) == {
        ViolationCode.INSTRUCTOR_DAILY_LOAD_EXCEEDED.value: 1
    }

    result = GeneticAlgorithmSolver().solve(
        problem,
        SolverConfig(
            algorithm=SolverAlgorithm.GENETIC_ALGORITHM,
            seed=17,
            time_limit_seconds=2,
            population_size=2,
            tournament_size=2,
            mutation_rate=0,
            crossover_rate=0,
            repair_attempts=0,
            max_generations=1,
        ),
    )

    # Evolution and repair cannot introduce the Tuesday candidate here: it must
    # already have been selected by construction using the frozen daily policy.
    assert result.validation.feasible
    assert result.assignments == (Assignment("E1", "E1-MON"), Assignment("E2", "E2-TUE"))
