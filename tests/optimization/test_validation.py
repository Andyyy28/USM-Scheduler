from __future__ import annotations

import dataclasses

from scheduler.domain import (
    Assignment,
    InstructorEvidence,
    MeetingEvent,
    ProblemInstance,
    RoomAuthorizationGrant,
    RoomAuthorizationRequirement,
    RoomEvidence,
    TimeAtom,
    ViolationCode,
    validate_schedule,
)

from .conftest import candidate


def _hard_rule_evidence_problem(
    *,
    duration_atoms: int = 1,
    room_available: tuple[str, ...] = ("MON0",),
    instructor_available: tuple[str, ...] = ("MON0",),
    room_kind: str = "LABORATORY",
    has_laboratory_profile: bool = True,
    capability_ids: tuple[str, ...] = ("CAP-LAB",),
    authorization_college_id: str = "COLLEGE-1",
    include_room_evidence: bool = True,
    include_instructor_evidence: bool = True,
) -> ProblemInstance:
    room_evidence = (
        (
            RoomEvidence(
                room_id="R1",
                room_kind=room_kind,
                available_atom_ids=room_available,
                capability_ids=capability_ids,
                authorization_grants=(
                    RoomAuthorizationGrant(
                        classification="MAJOR",
                        college_id=authorization_college_id,
                    ),
                ),
                has_laboratory_profile=has_laboratory_profile,
            ),
        )
        if include_room_evidence
        else ()
    )
    instructor_evidence = (
        (InstructorEvidence("I1", instructor_available),)
        if include_instructor_evidence
        else ()
    )
    return ProblemInstance(
        schema_version="1.1",
        term_revision_id="TERM-EVIDENCE",
        time_atoms=(TimeAtom("MON0", "MON", 0, 0),),
        events=(
            MeetingEvent(
                event_id="E1",
                duration_atoms=duration_atoms,
                section_ids=("S1",),
                instructor_ids=("I1",),
                candidates=(candidate("E1-C1", "R1", "MON", "MON0"),),
                required_capability_ids=("CAP-LAB",),
                requires_laboratory_room=True,
                authorization_requirements=(
                    RoomAuthorizationRequirement(
                        section_id="S1",
                        classification="MAJOR",
                        authoritative_college_id="COLLEGE-1",
                        authoritative_department_id="DEPARTMENT-1",
                        offering_college_id="COLLEGE-2",
                        offering_department_id="DEPARTMENT-2",
                    ),
                ),
            ),
        ),
        room_evidence=room_evidence,
        instructor_evidence=instructor_evidence,
    )


def test_conflicts_are_counted_once_per_resource_and_event_pair(
    conflicting_problem: ProblemInstance,
    conflicting_assignments: tuple[Assignment, ...],
) -> None:
    report = validate_schedule(conflicting_problem, conflicting_assignments)

    assert not report.feasible
    assert dict(report.counts) == {
        ViolationCode.INSTRUCTOR_CONFLICT.value: 1,
        ViolationCode.ROOM_CONFLICT.value: 1,
        ViolationCode.SECTION_CONFLICT.value: 1,
    }
    assert all(violation.atom_ids == ("MON0", "MON1") for violation in report.violations)


def test_exact_assignment_and_candidate_domain_are_validated(
    balanced_problem: ProblemInstance,
) -> None:
    report = validate_schedule(
        balanced_problem,
        (
            Assignment(event_id="E1", candidate_id="not-in-domain"),
            Assignment(event_id="E1", candidate_id="E1-M0"),
            Assignment(event_id="UNKNOWN", candidate_id="anything"),
        ),
    )

    assert dict(report.counts) == {
        ViolationCode.DUPLICATE_ASSIGNMENT.value: 1,
        ViolationCode.MISSING_ASSIGNMENT.value: 1,
        ViolationCode.UNKNOWN_EVENT.value: 1,
    }


def test_lock_mismatch_is_reported(balanced_problem: ProblemInstance) -> None:
    problem = dataclasses.replace(
        balanced_problem,
        locked_assignments=(Assignment(event_id="E1", candidate_id="E1-M0"),),
    )
    report = validate_schedule(
        problem,
        (
            Assignment(event_id="E1", candidate_id="E1-T0"),
            Assignment(event_id="E2", candidate_id="E2-M2"),
        ),
    )

    assert ViolationCode.LOCK_VIOLATION.value in dict(report.counts)


def test_distinct_day_group_is_independent_of_resource_conflicts(
    balanced_problem: ProblemInstance,
) -> None:
    first, second = balanced_problem.events
    events = (
        dataclasses.replace(first, distinct_day_group="OFFERING-1"),
        MeetingEvent(
            event_id=second.event_id,
            duration_atoms=second.duration_atoms,
            section_ids=("S2",),
            instructor_ids=("I2",),
            distinct_day_group="OFFERING-1",
            candidates=(
                candidate("E2-M2", "R2", "MON", "MON2"),
                candidate("E2-T2", "R2", "TUE", "TUE2"),
            ),
        ),
    )
    problem = dataclasses.replace(balanced_problem, events=events)

    same_day = validate_schedule(
        problem,
        (
            Assignment(event_id="E1", candidate_id="E1-M0"),
            Assignment(event_id="E2", candidate_id="E2-M2"),
        ),
    )
    different_days = validate_schedule(
        problem,
        (
            Assignment(event_id="E1", candidate_id="E1-M0"),
            Assignment(event_id="E2", candidate_id="E2-T2"),
        ),
    )

    assert dict(same_day.counts) == {ViolationCode.DISTINCT_DAY_CONFLICT.value: 1}
    assert different_days.feasible


def test_valid_shared_section_schedule_has_no_violations(balanced_problem: ProblemInstance) -> None:
    report = validate_schedule(
        balanced_problem,
        (
            Assignment(event_id="E1", candidate_id="E1-M0"),
            Assignment(event_id="E2", candidate_id="E2-T2"),
        ),
    )

    assert report.feasible
    assert report.hard_violation_count == 0
    assert not balanced_problem.supports_independent_hard_rule_validation


def test_schema_1_1_independently_revalidates_all_frozen_hard_rule_evidence() -> None:
    problem = _hard_rule_evidence_problem()
    restored = ProblemInstance.from_dict(problem.to_dict())

    report = validate_schedule(restored, (Assignment("E1", "E1-C1"),))

    assert restored == problem
    assert restored.supports_independent_hard_rule_validation
    assert report.feasible


def test_schema_1_1_rejects_corrupted_local_hard_rule_evidence() -> None:
    problem = _hard_rule_evidence_problem(
        duration_atoms=2,
        room_available=(),
        instructor_available=(),
        room_kind="CLASSROOM",
        has_laboratory_profile=False,
        capability_ids=(),
        authorization_college_id="COLLEGE-2",
    )

    report = validate_schedule(problem, (Assignment("E1", "E1-C1"),))

    assert dict(report.counts) == {
        ViolationCode.DURATION_MISMATCH.value: 1,
        ViolationCode.INSTRUCTOR_UNAVAILABLE.value: 1,
        ViolationCode.LABORATORY_ROOM_REQUIRED.value: 1,
        ViolationCode.ROOM_AUTHORIZATION_VIOLATION.value: 1,
        ViolationCode.ROOM_CAPABILITY_MISMATCH.value: 1,
        ViolationCode.ROOM_UNAVAILABLE.value: 1,
    }


def test_schema_1_1_rejects_missing_frozen_resource_evidence() -> None:
    problem = _hard_rule_evidence_problem(
        include_room_evidence=False,
        include_instructor_evidence=False,
    )

    report = validate_schedule(problem, (Assignment("E1", "E1-C1"),))

    assert dict(report.counts) == {
        ViolationCode.MISSING_INSTRUCTOR_EVIDENCE.value: 1,
        ViolationCode.MISSING_ROOM_EVIDENCE.value: 1,
    }


def test_schema_1_1_non_major_authorization_uses_raw_offering_unit() -> None:
    problem = _hard_rule_evidence_problem(authorization_college_id="COLLEGE-2")
    event = problem.events[0]
    requirement = dataclasses.replace(
        event.authorization_requirements[0],
        classification="GE",
    )
    problem = dataclasses.replace(
        problem,
        events=(dataclasses.replace(event, authorization_requirements=(requirement,)),),
        room_evidence=(
            dataclasses.replace(
                problem.room_evidence[0],
                authorization_grants=(
                    dataclasses.replace(
                        problem.room_evidence[0].authorization_grants[0],
                        classification="GE",
                    ),
                ),
            ),
        ),
    )

    report = validate_schedule(problem, (Assignment("E1", "E1-C1"),))

    assert report.feasible


def test_schema_1_1_rejects_missing_section_authorization_evidence() -> None:
    problem = _hard_rule_evidence_problem()
    problem = dataclasses.replace(
        problem,
        events=(
            dataclasses.replace(
                problem.events[0],
                authorization_requirements=(),
            ),
        ),
    )

    report = validate_schedule(problem, (Assignment("E1", "E1-C1"),))

    assert dict(report.counts) == {
        ViolationCode.MISSING_AUTHORIZATION_EVIDENCE.value: 1,
    }
