from __future__ import annotations

import dataclasses
import json
import random

import pytest

from scheduler.domain import (
    Assignment,
    CandidatePlacement,
    MeetingEvent,
    ProblemInstance,
    SolverAlgorithm,
    SolverConfig,
    canonical_json,
)


def test_problem_round_trip_is_lossless_and_json_serializable(balanced_problem: ProblemInstance) -> None:
    encoded = balanced_problem.to_dict()
    json.dumps(encoded)

    restored = ProblemInstance.from_dict(encoded)

    assert restored == balanced_problem
    assert restored.canonical_hash == balanced_problem.canonical_hash


def test_contracts_are_frozen(balanced_problem: ProblemInstance) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        balanced_problem.term_revision_id = "CHANGED"  # type: ignore[misc]


def test_semantically_unordered_collections_have_same_hash(
    balanced_problem: ProblemInstance,
) -> None:
    reversed_events = tuple(reversed(balanced_problem.events))
    events = tuple(
        MeetingEvent(
            event_id=event.event_id,
            duration_atoms=event.duration_atoms,
            section_ids=tuple(reversed(event.section_ids)),
            instructor_ids=tuple(reversed(event.instructor_ids)),
            candidates=tuple(reversed(event.candidates)),
            distinct_day_group=event.distinct_day_group,
            offering_id=event.offering_id,
        )
        for event in reversed_events
    )
    reordered = ProblemInstance(
        schema_version=balanced_problem.schema_version,
        term_revision_id=balanced_problem.term_revision_id,
        time_atoms=tuple(reversed(balanced_problem.time_atoms)),
        events=events,
        objective_profile=balanced_problem.objective_profile,
        metadata=tuple(reversed(balanced_problem.metadata)),
    )

    assert reordered.canonical_hash == balanced_problem.canonical_hash
    assert canonical_json(reordered) == canonical_json(balanced_problem)


def test_hash_is_stable_across_many_random_input_permutations(
    balanced_problem: ProblemInstance,
) -> None:
    expected = balanced_problem.canonical_hash
    rng = random.Random(9173)
    for _ in range(30):
        atoms = list(balanced_problem.time_atoms)
        events = list(balanced_problem.events)
        rng.shuffle(atoms)
        rng.shuffle(events)
        permuted = dataclasses.replace(
            balanced_problem,
            time_atoms=tuple(atoms),
            events=tuple(events),
        )
        assert permuted.canonical_hash == expected


def test_problem_rejects_candidate_with_unknown_atom(balanced_problem: ProblemInstance) -> None:
    bad_event = MeetingEvent(
        event_id="BAD",
        duration_atoms=1,
        section_ids=("S",),
        instructor_ids=("I",),
        candidates=(
            CandidatePlacement(
                candidate_id="BAD-C",
                room_id="R",
                day_id="MON",
                start_atom_id="UNKNOWN",
                occupied_atom_ids=("UNKNOWN",),
            ),
        ),
    )
    with pytest.raises(ValueError, match="unknown time atom"):
        dataclasses.replace(balanced_problem, events=(bad_event,))


def test_problem_rejects_multi_atom_candidate_crossing_a_break(
    balanced_problem: ProblemInstance,
) -> None:
    bad_event = MeetingEvent(
        event_id="BAD",
        duration_atoms=2,
        section_ids=("S",),
        instructor_ids=("I",),
        candidates=(
            CandidatePlacement(
                candidate_id="BAD-C",
                room_id="R",
                day_id="MON",
                start_atom_id="MON0",
                occupied_atom_ids=("MON0", "MON2"),
            ),
        ),
    )
    with pytest.raises(ValueError, match="contiguous"):
        dataclasses.replace(balanced_problem, events=(bad_event,))


def test_lock_must_reference_legal_candidate(balanced_problem: ProblemInstance) -> None:
    with pytest.raises(ValueError, match="invalid candidate"):
        dataclasses.replace(
            balanced_problem,
            locked_assignments=(Assignment(event_id="E1", candidate_id="not-legal"),),
        )


def test_solver_config_round_trip_and_hash() -> None:
    config = SolverConfig(
        algorithm=SolverAlgorithm.GENETIC_ALGORITHM,
        seed=1001,
        time_limit_seconds=300,
        max_generations=25,
    )

    restored = SolverConfig.from_dict(config.to_dict())

    assert restored == config
    assert restored.canonical_hash == config.canonical_hash
