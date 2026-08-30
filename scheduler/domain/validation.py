"""Algorithm-independent hard-constraint validation."""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from .contracts import (
    Assignment,
    CandidatePlacement,
    ProblemInstance,
    RoomAuthorizationGrant,
    RoomAuthorizationRequirement,
    ValidationReport,
    Violation,
    ViolationCode,
)
from .prepared import PreparedProblem


def validate_schedule(
    problem: ProblemInstance, assignments: tuple[Assignment, ...] | list[Assignment],
    *, prepared: PreparedProblem | None = None,
) -> ValidationReport:
    """Validate a schedule without trusting either optimization engine.

    Conflict counts are based on unique ``(resource, event pair)`` keys. A
    two-hour collision is therefore one violation rather than four violations
    when the atom size is thirty minutes.
    """

    if prepared is not None:
        prepared.require_problem(problem)
    event_map = prepared.event_map if prepared is not None else problem.event_map
    by_event: dict[str, list[Assignment]] = defaultdict(list)
    violations: list[Violation] = []

    for assignment in assignments:
        if assignment.event_id not in event_map:
            violations.append(
                Violation(
                    code=ViolationCode.UNKNOWN_EVENT,
                    message=f"Assignment references unknown event {assignment.event_id!r}.",
                    event_ids=(assignment.event_id,),
                )
            )
            continue
        by_event[assignment.event_id].append(assignment)

    resolved: dict[str, CandidatePlacement] = {}
    for event in prepared.events if prepared is not None else sorted(problem.events, key=lambda item: item.event_id):
        event_assignments = by_event.get(event.event_id, [])
        if not event_assignments:
            violations.append(
                Violation(
                    code=ViolationCode.MISSING_ASSIGNMENT,
                    message=f"Event {event.event_id!r} has no assignment.",
                    event_ids=(event.event_id,),
                )
            )
            continue
        if len(event_assignments) > 1:
            violations.append(
                Violation(
                    code=ViolationCode.DUPLICATE_ASSIGNMENT,
                    message=f"Event {event.event_id!r} has {len(event_assignments)} assignments.",
                    event_ids=(event.event_id,),
                )
            )
            continue
        assignment = event_assignments[0]
        candidates = prepared.candidates[event.event_id] if prepared is not None else event.candidate_map
        candidate = candidates.get(assignment.candidate_id)
        if candidate is None:
            violations.append(
                Violation(
                    code=ViolationCode.INVALID_PLACEMENT,
                    message=(
                        f"Candidate {assignment.candidate_id!r} is not in the legal domain "
                        f"for event {event.event_id!r}."
                    ),
                    event_ids=(event.event_id,),
                )
            )
            continue
        resolved[event.event_id] = candidate

    for event_id, locked_candidate_id in sorted(problem.lock_map.items()):
        selected = resolved.get(event_id)
        if selected is None or selected.candidate_id != locked_candidate_id:
            violations.append(
                Violation(
                    code=ViolationCode.LOCK_VIOLATION,
                    message=(
                        f"Event {event_id!r} must use locked candidate "
                        f"{locked_candidate_id!r}."
                    ),
                    event_ids=(event_id,),
                )
            )

    if problem.supports_independent_hard_rule_validation:
        _append_local_hard_rule_violations(problem, resolved, violations)
    _append_resource_conflicts(problem, resolved, violations)
    _append_distinct_day_conflicts(problem, resolved, violations)
    if problem.supports_thesis_v2_rules:
        _append_daily_load_violations(problem, resolved, violations)

    ordered = tuple(sorted(violations, key=_violation_sort_key))
    return ValidationReport(feasible=not ordered, violations=ordered)


def _append_local_hard_rule_violations(
    problem: ProblemInstance,
    resolved: dict[str, CandidatePlacement],
    violations: list[Violation],
) -> None:
    """Recheck schema-1.1 placement facts without trusting candidate filtering."""

    room_evidence = problem.room_evidence_map
    instructor_evidence = problem.instructor_evidence_map
    for event in sorted(problem.events, key=lambda item: item.event_id):
        if problem.supports_thesis_v2_rules:
            expected_sections = set(event.section_ids)
            evidenced_sections = {section_id for section_id, _ in event.section_headcounts}
            if (
                event.fixed_student_limit != 50
                or event.meeting_headcount is None
                or expected_sections != evidenced_sections
            ):
                violations.append(
                    Violation(
                        code=ViolationCode.MISSING_ENROLLMENT_EVIDENCE,
                        message=(
                            f"Event {event.event_id!r} lacks complete frozen section-enrollment "
                            "evidence for the fixed 50-student rule."
                        ),
                        event_ids=(event.event_id,),
                    )
                )
            elif event.meeting_headcount > 50:
                violations.append(
                    Violation(
                        code=ViolationCode.FIXED_STUDENT_LIMIT_EXCEEDED,
                        message=(
                            f"Event {event.event_id!r} combines {event.meeting_headcount} "
                            "students; the fixed maximum is 50."
                        ),
                        event_ids=(event.event_id,),
                    )
                )
        candidate = resolved.get(event.event_id)
        if candidate is None:
            continue
        occupied = set(candidate.occupied_atom_ids)
        if problem.supports_thesis_v2_rules:
            reserved_atoms = occupied & set(event.reserved_atom_ids)
            if reserved_atoms:
                violations.append(
                    Violation(
                        code=ViolationCode.RESERVED_BLOCK_VIOLATION,
                        message=(
                            f"Event {event.event_id!r} uses an approved recurring reserved "
                            "teaching block."
                        ),
                        event_ids=(event.event_id,),
                        atom_ids=tuple(sorted(reserved_atoms)),
                    )
                )
        requirement_sections = {
            requirement.section_id for requirement in event.authorization_requirements
        }
        for section_id in sorted(set(event.section_ids) - requirement_sections):
            violations.append(
                Violation(
                    code=ViolationCode.MISSING_AUTHORIZATION_EVIDENCE,
                    message=(
                        f"Section {section_id!r} has no frozen room-authorization "
                        f"requirement for event {event.event_id!r}."
                    ),
                    event_ids=(event.event_id,),
                    resource_id=section_id,
                )
            )
        if len(candidate.occupied_atom_ids) != event.duration_atoms:
            violations.append(
                Violation(
                    code=ViolationCode.DURATION_MISMATCH,
                    message=(
                        f"Event {event.event_id!r} requires {event.duration_atoms} atoms but "
                        f"candidate {candidate.candidate_id!r} occupies "
                        f"{len(candidate.occupied_atom_ids)}."
                    ),
                    event_ids=(event.event_id,),
                    atom_ids=candidate.occupied_atom_ids,
                )
            )

        room = room_evidence.get(candidate.room_id)
        if room is None:
            violations.append(
                Violation(
                    code=ViolationCode.MISSING_ROOM_EVIDENCE,
                    message=(
                        f"Room {candidate.room_id!r} has no frozen hard-rule evidence in "
                        "this schema-1.1 snapshot."
                    ),
                    event_ids=(event.event_id,),
                    resource_id=candidate.room_id,
                    atom_ids=candidate.occupied_atom_ids,
                )
            )
        else:
            unavailable_atoms = occupied - set(room.available_atom_ids)
            if unavailable_atoms:
                violations.append(
                    Violation(
                        code=ViolationCode.ROOM_UNAVAILABLE,
                        message=(
                            f"Room {candidate.room_id!r} is unavailable for part of event "
                            f"{event.event_id!r}."
                        ),
                        event_ids=(event.event_id,),
                        resource_id=candidate.room_id,
                        atom_ids=tuple(sorted(unavailable_atoms)),
                    )
                )

            missing_capabilities = set(event.required_capability_ids) - set(
                room.capability_ids
            )
            if missing_capabilities:
                violations.append(
                    Violation(
                        code=ViolationCode.ROOM_CAPABILITY_MISMATCH,
                        message=(
                            f"Room {candidate.room_id!r} lacks required capabilities "
                            f"{', '.join(sorted(missing_capabilities))} for event "
                            f"{event.event_id!r}."
                        ),
                        event_ids=(event.event_id,),
                        resource_id=candidate.room_id,
                    )
                )

            if event.requires_laboratory_room and (
                room.room_kind != "LABORATORY" or not room.has_laboratory_profile
            ):
                violations.append(
                    Violation(
                        code=ViolationCode.LABORATORY_ROOM_REQUIRED,
                        message=(
                            f"Event {event.event_id!r} requires a laboratory room with a "
                            f"laboratory profile; room {candidate.room_id!r} does not qualify."
                        ),
                        event_ids=(event.event_id,),
                        resource_id=candidate.room_id,
                    )
                )

            for requirement in event.authorization_requirements:
                if not any(
                    _authorization_grant_matches(requirement, grant)
                    for grant in room.authorization_grants
                ):
                    violations.append(
                        Violation(
                            code=ViolationCode.ROOM_AUTHORIZATION_VIOLATION,
                            message=(
                                f"Room {candidate.room_id!r} is not authorized for section "
                                f"{requirement.section_id!r} under classification "
                                f"{requirement.classification!r}."
                            ),
                            event_ids=(event.event_id,),
                            resource_id=candidate.room_id,
                        )
                    )

        for instructor_id in event.instructor_ids:
            instructor = instructor_evidence.get(instructor_id)
            if instructor is None:
                violations.append(
                    Violation(
                        code=ViolationCode.MISSING_INSTRUCTOR_EVIDENCE,
                        message=(
                            f"Instructor {instructor_id!r} has no frozen availability "
                            "evidence in this schema-1.1 snapshot."
                        ),
                        event_ids=(event.event_id,),
                        resource_id=instructor_id,
                        atom_ids=candidate.occupied_atom_ids,
                    )
                )
                continue
            unavailable_atoms = occupied - set(instructor.available_atom_ids)
            if unavailable_atoms:
                violations.append(
                    Violation(
                        code=ViolationCode.INSTRUCTOR_UNAVAILABLE,
                        message=(
                            f"Instructor {instructor_id!r} is unavailable for part of event "
                            f"{event.event_id!r}."
                        ),
                        event_ids=(event.event_id,),
                        resource_id=instructor_id,
                        atom_ids=tuple(sorted(unavailable_atoms)),
                    )
                )


def _authorization_grant_matches(
    requirement: RoomAuthorizationRequirement,
    grant: RoomAuthorizationGrant,
) -> bool:
    if grant.classification != requirement.classification:
        return False
    if grant.department_id is not None:
        return grant.department_id == requirement.applicable_department_id
    return grant.college_id == requirement.applicable_college_id


def _append_resource_conflicts(
    problem: ProblemInstance,
    resolved: dict[str, CandidatePlacement],
    violations: list[Violation],
) -> None:
    event_map = problem.event_map
    buckets: dict[tuple[ViolationCode, str, str], set[str]] = defaultdict(set)

    for event_id, candidate in resolved.items():
        event = event_map[event_id]
        for atom_id in candidate.occupied_atom_ids:
            buckets[(ViolationCode.ROOM_CONFLICT, candidate.room_id, atom_id)].add(event_id)
            for instructor_id in event.instructor_ids:
                buckets[(ViolationCode.INSTRUCTOR_CONFLICT, instructor_id, atom_id)].add(event_id)
            for section_id in event.section_ids:
                buckets[(ViolationCode.SECTION_CONFLICT, section_id, atom_id)].add(event_id)

    conflicts: dict[tuple[ViolationCode, str, tuple[str, str]], set[str]] = defaultdict(set)
    for (code, resource_id, atom_id), event_ids in buckets.items():
        for left, right in combinations(sorted(event_ids), 2):
            conflicts[(code, resource_id, (left, right))].add(atom_id)

    resource_names = {
        ViolationCode.ROOM_CONFLICT: "Room",
        ViolationCode.INSTRUCTOR_CONFLICT: "Instructor",
        ViolationCode.SECTION_CONFLICT: "Section",
    }
    for (code, resource_id, event_pair), atom_ids in sorted(
        conflicts.items(),
        key=lambda item: (item[0][0].value, item[0][1], item[0][2]),
    ):
        violations.append(
            Violation(
                code=code,
                message=(
                    f"{resource_names[code]} {resource_id!r} is used by events "
                    f"{event_pair[0]!r} and {event_pair[1]!r} at the same time."
                ),
                event_ids=event_pair,
                resource_id=resource_id,
                atom_ids=tuple(sorted(atom_ids)),
            )
        )


def _append_distinct_day_conflicts(
    problem: ProblemInstance,
    resolved: dict[str, CandidatePlacement],
    violations: list[Violation],
) -> None:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for event in problem.events:
        if event.distinct_day_group and event.event_id in resolved:
            groups[(event.distinct_day_group, resolved[event.event_id].day_id)].append(event.event_id)

    for (group_id, day_id), event_ids in sorted(groups.items()):
        for left, right in combinations(sorted(event_ids), 2):
            violations.append(
                Violation(
                    code=ViolationCode.DISTINCT_DAY_CONFLICT,
                    message=(
                        f"Events {left!r} and {right!r} in distinct-day group "
                        f"{group_id!r} are both assigned to day {day_id!r}."
                    ),
                    event_ids=(left, right),
                    resource_id=group_id,
                )
            )


def _append_daily_load_violations(
    problem: ProblemInstance,
    resolved: dict[str, CandidatePlacement],
    violations: list[Violation],
) -> None:
    """Enforce each instructor's frozen positive daily atom limit."""

    instructor_evidence = problem.instructor_evidence_map
    loads: dict[tuple[str, str], int] = defaultdict(int)
    events_by_day: dict[tuple[str, str], set[str]] = defaultdict(set)
    atoms_by_day: dict[tuple[str, str], set[str]] = defaultdict(set)
    used_instructors: set[str] = set()

    for event in problem.events:
        candidate = resolved.get(event.event_id)
        if candidate is None:
            continue
        for instructor_id in event.instructor_ids:
            used_instructors.add(instructor_id)
            key = (instructor_id, candidate.day_id)
            loads[key] += len(candidate.occupied_atom_ids)
            events_by_day[key].add(event.event_id)
            atoms_by_day[key].update(candidate.occupied_atom_ids)

    for instructor_id in sorted(used_instructors):
        evidence = instructor_evidence.get(instructor_id)
        if evidence is None:
            # The schema-1.1 availability check already reports this omission.
            continue
        if (
            evidence.max_daily_teaching_atoms is None
            and not evidence.acknowledge_no_daily_limit
        ):
            violations.append(
                Violation(
                    code=ViolationCode.MISSING_DAILY_LOAD_EVIDENCE,
                    message=(
                        f"Instructor {instructor_id!r} has neither a positive approved "
                        "daily teaching-atom limit nor an approved no-limit acknowledgement."
                    ),
                    resource_id=instructor_id,
                )
            )
            continue
        if evidence.max_daily_teaching_atoms is None:
            continue
        for (resource_id, day_id), atom_count in sorted(loads.items()):
            if resource_id != instructor_id or atom_count <= evidence.max_daily_teaching_atoms:
                continue
            key = (resource_id, day_id)
            violations.append(
                Violation(
                    code=ViolationCode.INSTRUCTOR_DAILY_LOAD_EXCEEDED,
                    message=(
                        f"Instructor {resource_id!r} is assigned {atom_count} teaching "
                        f"atoms on day {day_id!r}; the approved maximum is "
                        f"{evidence.max_daily_teaching_atoms}."
                    ),
                    event_ids=tuple(sorted(events_by_day[key])),
                    resource_id=resource_id,
                    atom_ids=tuple(sorted(atoms_by_day[key])),
                )
            )


def _violation_sort_key(violation: Violation) -> tuple[object, ...]:
    return (
        violation.code.value,
        violation.resource_id or "",
        violation.event_ids,
        violation.atom_ids,
        violation.message,
    )
