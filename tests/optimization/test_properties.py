from __future__ import annotations

from math import comb

from hypothesis import given
from hypothesis import strategies as st

from scheduler.domain import (
    Assignment,
    CandidatePlacement,
    MeetingEvent,
    ProblemInstance,
    TimeAtom,
    ViolationCode,
    validate_schedule,
)


@given(
    event_count=st.integers(min_value=2, max_value=6),
    duration_atoms=st.integers(min_value=1, max_value=8),
)
def test_resource_conflicts_are_pair_based_not_atom_based(
    event_count: int,
    duration_atoms: int,
) -> None:
    """Longer meetings must not inflate one conflicting event pair's count."""

    atom_ids = tuple(f"MON-{index}" for index in range(duration_atoms))
    atoms = tuple(
        TimeAtom(atom_id=atom_id, day_id="MON", day_index=0, order=index)
        for index, atom_id in enumerate(atom_ids)
    )
    events = tuple(
        MeetingEvent(
            event_id=f"E{index}",
            duration_atoms=duration_atoms,
            section_ids=("S1",),
            instructor_ids=("I1",),
            candidates=(
                CandidatePlacement(
                    candidate_id=f"E{index}-C",
                    room_id="R1",
                    day_id="MON",
                    start_atom_id=atom_ids[0],
                    occupied_atom_ids=atom_ids,
                ),
            ),
        )
        for index in range(event_count)
    )
    problem = ProblemInstance(
        schema_version="1.0",
        term_revision_id="PROPERTY-R1",
        time_atoms=atoms,
        events=events,
    )
    assignments = tuple(
        Assignment(event_id=event.event_id, candidate_id=event.candidates[0].candidate_id)
        for event in events
    )

    report = validate_schedule(problem, assignments)

    pair_count = comb(event_count, 2)
    assert dict(report.counts) == {
        ViolationCode.INSTRUCTOR_CONFLICT.value: pair_count,
        ViolationCode.ROOM_CONFLICT.value: pair_count,
        ViolationCode.SECTION_CONFLICT.value: pair_count,
    }
    assert report.hard_violation_count == pair_count * 3
    assert all(violation.atom_ids == atom_ids for violation in report.violations)
