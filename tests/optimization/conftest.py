from __future__ import annotations

import pytest

from scheduler.domain import (
    Assignment,
    CandidatePlacement,
    MeetingEvent,
    ObjectiveProfile,
    ProblemInstance,
    TimeAtom,
)


def candidate(
    candidate_id: str,
    room_id: str,
    day_id: str,
    *atom_ids: str,
    preference_penalty: int = 0,
) -> CandidatePlacement:
    return CandidatePlacement(
        candidate_id=candidate_id,
        room_id=room_id,
        day_id=day_id,
        start_atom_id=atom_ids[0],
        occupied_atom_ids=tuple(atom_ids),
        preference_penalty=preference_penalty,
    )


@pytest.fixture
def atoms() -> tuple[TimeAtom, ...]:
    return tuple(
        TimeAtom(atom_id=f"{day}{order}", day_id=day, day_index=day_index, order=order)
        for day_index, day in enumerate(("MON", "TUE"))
        for order in range(4)
    )


@pytest.fixture
def balanced_problem(atoms: tuple[TimeAtom, ...]) -> ProblemInstance:
    events = (
        MeetingEvent(
            event_id="E1",
            offering_id="O1",
            duration_atoms=1,
            section_ids=("S1",),
            instructor_ids=("I1",),
            candidates=(
                candidate("E1-M0", "R1", "MON", "MON0"),
                candidate("E1-T0", "R1", "TUE", "TUE0"),
            ),
        ),
        MeetingEvent(
            event_id="E2",
            offering_id="O2",
            duration_atoms=1,
            section_ids=("S1",),
            instructor_ids=("I1",),
            candidates=(
                candidate("E2-M2", "R1", "MON", "MON2"),
                candidate("E2-T2", "R1", "TUE", "TUE2"),
            ),
        ),
    )
    return ProblemInstance(
        schema_version="1.0",
        term_revision_id="TERM-1-R1",
        time_atoms=atoms,
        events=events,
        objective_profile=ObjectiveProfile(
            profile_id="test-v1",
            preference_normalizer=2,
            section_gap_normalizer=1,
            instructor_gap_normalizer=1,
            load_imbalance_normalizer=8,
        ),
        metadata=(("source", "synthetic"),),
    )


@pytest.fixture
def conflicting_problem(atoms: tuple[TimeAtom, ...]) -> ProblemInstance:
    return ProblemInstance(
        schema_version="1.0",
        term_revision_id="CONFLICT-R1",
        time_atoms=atoms,
        events=(
            MeetingEvent(
                event_id="E1",
                duration_atoms=2,
                section_ids=("S1",),
                instructor_ids=("I1",),
                candidates=(candidate("E1-C", "R1", "MON", "MON0", "MON1"),),
            ),
            MeetingEvent(
                event_id="E2",
                duration_atoms=2,
                section_ids=("S1",),
                instructor_ids=("I1",),
                candidates=(candidate("E2-C", "R1", "MON", "MON0", "MON1"),),
            ),
        ),
    )


@pytest.fixture
def conflicting_assignments() -> tuple[Assignment, ...]:
    return (
        Assignment(event_id="E1", candidate_id="E1-C"),
        Assignment(event_id="E2", candidate_id="E2-C"),
    )
