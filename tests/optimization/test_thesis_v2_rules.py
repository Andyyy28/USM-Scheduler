from __future__ import annotations

import json

import pytest

from scheduler.domain import (
    Assignment,
    CandidatePlacement,
    InstructorEvidence,
    MeetingEvent,
    ObjectiveProfile,
    ProblemInstance,
    RoomAuthorizationGrant,
    RoomAuthorizationRequirement,
    RoomEvidence,
    SolverAlgorithm,
    SolverConfig,
    TimeAtom,
    ViolationCode,
    validate_schedule,
)
from scheduler.solvers import (
    CpSatSolver,
    GeneticAlgorithmSolver,
    is_ortools_available,
)

ATOMS = (
    TimeAtom("MON0", "MON", 0, 0),
    TimeAtom("MON1", "MON", 0, 1),
    TimeAtom("TUE0", "TUE", 1, 0),
    TimeAtom("TUE1", "TUE", 1, 1),
)


def _candidate(candidate_id: str, day: str, *atoms: str) -> CandidatePlacement:
    return CandidatePlacement(
        candidate_id=candidate_id,
        room_id="R1",
        day_id=day,
        start_atom_id=atoms[0],
        occupied_atom_ids=tuple(atoms),
    )


def _authorization(section_id: str) -> RoomAuthorizationRequirement:
    return RoomAuthorizationRequirement(
        section_id=section_id,
        classification="MAJOR",
        authoritative_college_id="C1",
        authoritative_department_id="D1",
        offering_college_id="C1",
        offering_department_id="D1",
    )


def _problem(
    *,
    room_kind: str = "CLASSROOM",
    section_headcounts: tuple[tuple[str, int], ...] = (("S1", 50),),
    reserved_atom_ids: tuple[str, ...] = (),
    daily_limit: int | None = None,
    no_daily_limit: bool = True,
    two_events: bool = False,
) -> ProblemInstance:
    section_ids = tuple(section_id for section_id, _ in section_headcounts)
    event = MeetingEvent(
        event_id="E1",
        offering_id="O1",
        duration_atoms=2 if two_events else 1,
        section_ids=section_ids,
        instructor_ids=("I1",),
        candidates=(
            _candidate("E1-MON", "MON", "MON0", "MON1")
            if two_events
            else _candidate("E1-MON", "MON", "MON0"),
            *(
                (_candidate("E1-TUE", "TUE", "TUE0", "TUE1"),)
                if two_events
                else ()
            ),
        ),
        authorization_requirements=tuple(_authorization(value) for value in section_ids),
        section_headcounts=section_headcounts,
        meeting_headcount=sum(value for _, value in section_headcounts),
        fixed_student_limit=50,
        reserved_atom_ids=reserved_atom_ids,
    )
    events = [event]
    if two_events:
        events.append(
            MeetingEvent(
                event_id="E2",
                offering_id="O2",
                duration_atoms=2,
                section_ids=("S2",),
                instructor_ids=("I1",),
                candidates=(
                    _candidate("E2-MON", "MON", "MON0", "MON1"),
                    _candidate("E2-TUE", "TUE", "TUE0", "TUE1"),
                ),
                authorization_requirements=(_authorization("S2"),),
                section_headcounts=(("S2", 20),),
                meeting_headcount=20,
                fixed_student_limit=50,
            )
        )
    return ProblemInstance(
        schema_version="1.2",
        term_revision_id="THESIS-V2-R1",
        time_atoms=ATOMS,
        events=tuple(events),
        objective_profile=ObjectiveProfile(
            profile_id="approved-study-objective-v1",
            preference_normalizer=10,
            section_gap_normalizer=10,
            instructor_gap_normalizer=10,
            load_imbalance_normalizer=10,
        ),
        room_evidence=(
            RoomEvidence(
                room_id="R1",
                room_kind=room_kind,
                available_atom_ids=tuple(atom.atom_id for atom in ATOMS),
                authorization_grants=(
                    RoomAuthorizationGrant(classification="MAJOR", college_id="C1"),
                ),
                has_laboratory_profile=room_kind == "LABORATORY",
            ),
        ),
        instructor_evidence=(
            InstructorEvidence(
                instructor_id="I1",
                available_atom_ids=tuple(atom.atom_id for atom in ATOMS),
                max_daily_teaching_atoms=daily_limit,
                acknowledge_no_daily_limit=no_daily_limit,
                daily_load_policy_hash="d" * 64,
            ),
        ),
    )


@pytest.mark.parametrize("room_kind", ["CLASSROOM", "LABORATORY", "SPECIAL"])
def test_fixed_fifty_rule_accepts_exact_limit_for_every_room_kind(room_kind: str) -> None:
    problem = _problem(room_kind=room_kind, section_headcounts=(("S1", 20), ("S2", 30)))

    report = validate_schedule(problem, (Assignment("E1", "E1-MON"),))

    assert report.feasible


@pytest.mark.parametrize("room_kind", ["CLASSROOM", "LABORATORY", "SPECIAL"])
def test_fixed_fifty_rule_rejects_combined_51_for_every_room_kind(room_kind: str) -> None:
    problem = _problem(room_kind=room_kind, section_headcounts=(("S1", 1), ("S2", 50)))

    report = validate_schedule(problem, (Assignment("E1", "E1-MON"),))

    assert dict(report.counts) == {ViolationCode.FIXED_STUDENT_LIMIT_EXCEEDED.value: 1}
    assert "51 students" in report.violations[0].message


def test_approved_reserved_atom_is_independently_rejected() -> None:
    problem = _problem(reserved_atom_ids=("MON0",))

    report = validate_schedule(problem, (Assignment("E1", "E1-MON"),))

    assert dict(report.counts) == {ViolationCode.RESERVED_BLOCK_VIOLATION.value: 1}
    assert report.violations[0].atom_ids == ("MON0",)


def test_daily_teaching_atom_limit_accepts_boundary_and_rejects_excess() -> None:
    assignments = (Assignment("E1", "E1-MON"), Assignment("E2", "E2-TUE"))
    at_boundary = _problem(two_events=True, daily_limit=2, no_daily_limit=False)
    above_limit = _problem(two_events=True, daily_limit=1, no_daily_limit=False)

    assert validate_schedule(at_boundary, assignments).feasible
    report = validate_schedule(above_limit, assignments)
    assert dict(report.counts) == {
        ViolationCode.INSTRUCTOR_DAILY_LOAD_EXCEEDED.value: 2
    }


def test_snapshot_contract_contains_no_physical_capacity_inputs() -> None:
    payload = json.dumps(_problem().to_dict(), sort_keys=True).casefold()

    for prohibited in (
        "chair_count",
        "seat_count",
        "room_capacity",
        "floor_space",
        "floor_area",
        "physical_dimensions",
    ):
        assert prohibited not in payload


@pytest.mark.skipif(not is_ortools_available(), reason="OR-Tools is not installed")
def test_both_solvers_receive_same_problem_and_obey_daily_limit() -> None:
    problem = _problem(two_events=True, daily_limit=2, no_daily_limit=False)
    shared_budget = {"seed": 1001, "time_limit_seconds": 2, "worker_count": 1}
    cp_config = SolverConfig(algorithm=SolverAlgorithm.CP_SAT, **shared_budget)
    ga_config = SolverConfig(
        algorithm=SolverAlgorithm.GENETIC_ALGORITHM,
        population_size=20,
        tournament_size=3,
        repair_attempts=20,
        max_generations=20,
        **shared_budget,
    )

    cp_result = CpSatSolver().solve(problem, cp_config)
    ga_result = GeneticAlgorithmSolver().solve(problem, ga_config)

    assert cp_config.time_limit_seconds == ga_config.time_limit_seconds
    assert cp_config.worker_count == ga_config.worker_count == 1
    assert cp_result.problem_hash == ga_result.problem_hash == problem.canonical_hash
    assert cp_result.validation.feasible
    assert ga_result.validation.feasible
    for result in (cp_result, ga_result):
        selected_days = {
            problem.event_map[item.event_id].candidate_map[item.candidate_id].day_id
            for item in result.assignments
        }
        assert selected_days == {"MON", "TUE"}
