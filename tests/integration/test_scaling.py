from __future__ import annotations

import io
import json

import pytest
from django.core.management import call_command

from scheduler import models
from scheduler.domain import (
    Assignment,
    CandidatePlacement,
    MeetingEvent,
    ProblemInstance,
    TimeAtom,
)
from scheduler.domain import ObjectiveProfile as DomainObjectiveProfile
from scheduler.services.scaling import (
    DEFAULT_SCALING_SEED,
    _project_instance_characteristics,
    create_scaling_snapshots,
    plan_scaling_snapshots,
)
from tests.integration.test_experiments import _experiment_graph

pytestmark = pytest.mark.django_db


def test_availability_density_uses_only_retained_instructors_and_full_room_pool() -> None:
    input_data = {
        "events": [
            {
                "event_id": "meeting-a",
                "offering_id": "offering-a",
                "instructor_ids": ["retained"],
                "section_ids": ["section-a"],
                "section_headcounts": [["section-a", 50]],
                "meeting_headcount": 50,
                "duration_atoms": 1,
            }
        ],
        "time_atoms": [{"atom_id": "a"}, {"atom_id": "b"}],
        "room_evidence": [
            {"room_id": "room-a", "available_atom_ids": ["a", "b"]},
            {"room_id": "room-b", "available_atom_ids": ["a"]},
        ],
        "instructor_evidence": [
            {"instructor_id": "retained", "available_atom_ids": ["a", "b"]},
            {"instructor_id": "not-retained", "available_atom_ids": []},
        ],
    }

    result = _project_instance_characteristics(input_data, {"meeting-a": [{}]})

    assert result["instructors"] == 1
    assert result["rooms"] == 2
    assert result["available_room_atoms"] == 3
    assert result["availability_density"] == pytest.approx((1 + 0.5 + 1) / 3)


def _full_scaling_snapshot(
    suffix: str,
    *,
    locked_event_indexes: tuple[int, ...] = (0,),
    add_second_meeting_to_first_offering: bool = False,
) -> dict[str, object]:
    graph = _experiment_graph(suffix)
    revision = graph["revision"]
    user = graph["user"]
    base_meeting = graph["meeting"]
    base_offering = base_meeting.offering
    base_link = base_offering.section_links.select_related(
        "section__program__department__college", "program_subject"
    ).get()
    first_program = base_link.section.program
    first_department = first_program.department
    first_college = first_department.college
    first_section = base_link.section
    first_instructor = base_offering.instructor_links.get().instructor

    second_college = models.College.objects.create(
        code=f"SC2-{suffix}", name="Second scaling college"
    )
    second_department = models.Department.objects.create(
        college=second_college, code=f"SD2-{suffix}", name="Second scaling department"
    )
    second_program = models.Program.objects.create(
        department=second_department, code=f"SP2-{suffix}", name="Second scaling program"
    )
    second_section = models.Section.objects.create(
        revision=revision,
        program=second_program,
        code=f"SECOND-{suffix}",
        year_level=1,
        cohort_status=models.CohortStatus.CONTINUING,
    )
    second_instructor = models.Instructor.objects.create(
        department=second_department,
        employee_code=f"SF2-{suffix}",
        display_name="Second scaling faculty",
    )
    contexts = [
        (first_college, first_department, first_program, first_section, first_instructor, models.SubjectClassification.MAJOR),
        (first_college, first_department, first_program, first_section, first_instructor, models.SubjectClassification.GENERAL_EDUCATION),
        (second_college, second_department, second_program, second_section, second_instructor, models.SubjectClassification.MAJOR),
        (second_college, second_department, second_program, second_section, second_instructor, models.SubjectClassification.GENERAL_EDUCATION),
    ]
    # The helper already created the first major offering. Add seven more so
    # every one of the four context labels has exactly two offerings.
    additions = [contexts[0], contexts[1], contexts[1], contexts[2], contexts[2], contexts[3], contexts[3]]
    meetings = [base_meeting]
    for index, (college, department, program, section, instructor, classification) in enumerate(
        additions, start=1
    ):
        subject = models.Subject.objects.create(
            code=f"SS-{suffix}-{index}", title=f"Scaling subject {index}"
        )
        program_subject = models.ProgramSubject.objects.create(
            program=program,
            subject=subject,
            curriculum_version="2026",
            classification=classification,
            authoritative_college=college,
            authoritative_department=department,
        )
        # Deliberately offer the final second-college curriculum subject through
        # the first college. Scaling strata must follow the offering department,
        # not the curriculum's authoritative college.
        offering_department = first_department if index == 7 else department
        offering = models.CourseOffering.objects.create(
            revision=revision,
            subject=subject,
            offering_department=offering_department,
            external_key=f"SCALE-{suffix}-{index}",
        )
        models.OfferingSection.objects.create(
            offering=offering,
            section=section,
            program_subject=program_subject,
        )
        models.OfferingInstructor.objects.create(offering=offering, instructor=instructor)
        meetings.append(
            models.MeetingRequirement.objects.create(
                offering=offering,
                component=models.MeetingComponent.LECTURE,
                occurrence_number=1,
                duration_atoms=1,
            )
        )
    if add_second_meeting_to_first_offering:
        meetings.append(
            models.MeetingRequirement.objects.create(
                offering=base_offering,
                component=models.MeetingComponent.LECTURE,
                occurrence_number=2,
                duration_atoms=1,
            )
        )

    slots = graph["slots"]
    room = graph["room"]
    atoms = tuple(
        TimeAtom(
            atom_id=f"slot:{slot.pk}",
            day_id=f"day:{slot.day}",
            day_index=slot.day,
            order=slot.sequence,
        )
        for slot in slots
    )
    events = []
    for meeting in meetings:
        offering = meeting.offering
        link = offering.section_links.get()
        instructor_id = offering.instructor_links.get().instructor_id
        event_id = str(meeting.stable_key)
        candidate = CandidatePlacement(
            candidate_id=f"{event_id}:{room.pk}:{slots[0].pk}",
            room_id=str(room.pk),
            day_id=f"day:{slots[0].day}",
            start_atom_id=f"slot:{slots[0].pk}",
            occupied_atom_ids=(f"slot:{slots[0].pk}",),
        )
        events.append(
            MeetingEvent(
                event_id=event_id,
                duration_atoms=1,
                section_ids=(str(link.section_id),),
                instructor_ids=(str(instructor_id),),
                candidates=(candidate,),
                offering_id=offering.external_key,
            )
        )
    problem = ProblemInstance(
        schema_version="1.0",
        term_revision_id=str(revision.pk),
        time_atoms=atoms,
        events=tuple(events),
        objective_profile=DomainObjectiveProfile(profile_id="approved-scaling-v1"),
        locked_assignments=tuple(
            Assignment(
                event_id=events[index].event_id,
                candidate_id=events[index].candidates[0].candidate_id,
            )
            for index in locked_event_indexes
        ),
        metadata=(("source", "scaling-test"),),
    )
    candidate_map = {
        event.event_id: [candidate.to_dict() for candidate in event.candidates]
        for event in problem.events
    }
    snapshot = models.ProblemSnapshot.objects.create(
        revision=revision,
        objective_profile=graph["snapshot"].objective_profile,
        schema_version=problem.schema_version,
        input_data=problem.to_dict(),
        candidate_map=candidate_map,
        event_count=len(events),
        candidate_count=len(events),
        created_by=user,
    )
    return {**graph, "full_snapshot": snapshot, "events": tuple(events)}


def test_scaling_plan_is_stable_nested_and_stratified_by_every_context() -> None:
    graph = _full_scaling_snapshot("plan")
    snapshot = graph["full_snapshot"]

    first = plan_scaling_snapshots(snapshot)
    second = plan_scaling_snapshots(snapshot, seed=DEFAULT_SCALING_SEED)

    assert first == second
    assert first.full_event_count == 8
    first_college_id = graph["meeting"].offering.offering_department.college_id
    second_college_id = models.College.objects.exclude(pk=first_college_id).get(
        code="SC2-plan"
    ).pk
    assert dict(first.applicable_context_counts) == {
        f"{first_college_id}:{models.SubjectClassification.GENERAL_EDUCATION}": 3,
        f"{first_college_id}:{models.SubjectClassification.MAJOR}": 2,
        f"{second_college_id}:{models.SubjectClassification.GENERAL_EDUCATION}": 1,
        f"{second_college_id}:{models.SubjectClassification.MAJOR}": 2,
    }
    assert [level.selected_event_count for level in first.levels] == [2, 4, 6, 8]
    selected_sets = [set(level.selected_event_ids) for level in first.levels]
    assert selected_sets[0] < selected_sets[1] < selected_sets[2] < selected_sets[3]
    assert selected_sets[-1] == {event.event_id for event in graph["events"]}
    assert all(len(level.selection_hash) == 64 for level in first.levels)
    assert all(sum(dict(level.context_counts).values()) == level.selected_offering_count for level in first.levels)


def test_scaling_retains_all_locked_offerings_and_reports_forced_overage() -> None:
    graph = _full_scaling_snapshot(
        "locks",
        locked_event_indexes=(0, 1, 2),
        add_second_meeting_to_first_offering=True,
    )
    snapshot = graph["full_snapshot"]

    plan = plan_scaling_snapshots(snapshot)
    locked_event_ids = {
        lock["event_id"] for lock in snapshot.input_data["locked_assignments"]
    }
    locked_offering_ids = {
        event["offering_id"]
        for event in snapshot.input_data["events"]
        if event["event_id"] in locked_event_ids
    }
    first_offering_id = graph["meeting"].offering.external_key
    first_offering_event_ids = {
        event["event_id"]
        for event in snapshot.input_data["events"]
        if event["offering_id"] == first_offering_id
    }

    assert plan.full_event_count == 9
    assert plan.full_locked_event_count == 3
    assert plan.full_locked_offering_count == 3
    assert set(plan.locked_offering_ids) == locked_offering_ids
    assert set(plan.offering_order[:3]) == locked_offering_ids
    assert plan.levels[0].target_offering_count == 2
    assert plan.levels[0].selected_offering_count == 3
    assert plan.levels[0].selected_event_count == 4
    assert plan.levels[0].actual_event_percentage == 44.444444
    assert plan.levels[0].actual_offering_percentage == 37.5
    assert plan.levels[0].forced_offering_overage == 1
    for level in plan.levels:
        assert level.locked_retention_satisfied is True
        assert level.locked_offering_count == 3
        assert level.retained_locked_offering_count == 3
        assert level.selected_lock_count == 3
        assert locked_offering_ids <= set(level.selected_offering_ids)
        assert first_offering_event_ids <= set(level.selected_event_ids)

    snapshots = create_scaling_snapshots(snapshot, graph["user"])
    metadata = dict(snapshots[25].input_data["metadata"])
    assert metadata["scaling_actual_event_percentage"] == "44.444444"
    assert metadata["scaling_actual_offering_percentage"] == "37.5"
    assert metadata["scaling_forced_offering_overage"] == "1"
    assert metadata["scaling_locked_event_count"] == "3"
    assert metadata["scaling_locked_offering_count"] == "3"
    assert metadata["scaling_retained_locked_offering_count"] == "3"
    assert metadata["scaling_locked_retention_satisfied"] == "true"


def test_scaling_snapshots_preserve_domains_locks_are_idempotent_and_reuse_full() -> None:
    graph = _full_scaling_snapshot("commit")
    full_snapshot = graph["full_snapshot"]
    initial_count = models.ProblemSnapshot.objects.count()

    first = create_scaling_snapshots(full_snapshot, graph["user"])
    after_first = models.ProblemSnapshot.objects.count()
    second = create_scaling_snapshots(full_snapshot, graph["user"])

    assert set(first) == {25, 50, 75, 100}
    assert first[100].pk == full_snapshot.pk
    assert after_first == initial_count + 3
    assert models.ProblemSnapshot.objects.count() == after_first
    assert {percentage: snapshot.pk for percentage, snapshot in first.items()} == {
        percentage: snapshot.pk for percentage, snapshot in second.items()
    }
    full_events = {event["event_id"]: event for event in full_snapshot.input_data["events"]}
    full_locks = {
        lock["event_id"]: lock for lock in full_snapshot.input_data["locked_assignments"]
    }
    for percentage in (25, 50, 75):
        scaled = first[percentage]
        selected_ids = {event["event_id"] for event in scaled.input_data["events"]}
        assert scaled.input_data["time_atoms"] == full_snapshot.input_data["time_atoms"]
        assert scaled.candidate_map == {
            event_id: full_snapshot.candidate_map[event_id] for event_id in selected_ids
        }
        assert scaled.input_data["events"] == [
            event for event_id, event in full_events.items() if event_id in selected_ids
        ]
        assert scaled.input_data["locked_assignments"] == [
            lock for event_id, lock in full_locks.items() if event_id in selected_ids
        ]
        metadata = dict(scaled.input_data["metadata"])
        assert metadata["scaling_percentage"] == str(percentage)
        assert metadata["scaling_source_snapshot_hash"] == full_snapshot.snapshot_hash
        assert len(metadata["scaling_selection_hash"]) == 64
        assert ProblemInstance.from_dict(scaled.input_data).canonical_hash


def test_scaling_command_is_plan_only_until_explicit_commit() -> None:
    graph = _full_scaling_snapshot("command")
    snapshot = graph["full_snapshot"]
    initial_count = models.ProblemSnapshot.objects.count()
    stdout = io.StringIO()

    call_command("create_scaling_snapshots", snapshot.pk, stdout=stdout)

    planned = json.loads(stdout.getvalue())
    assert planned["committed"] is False
    assert models.ProblemSnapshot.objects.count() == initial_count

    committed_stdout = io.StringIO()
    call_command(
        "create_scaling_snapshots",
        snapshot.pk,
        "--commit",
        "--user-id",
        graph["user"].pk,
        stdout=committed_stdout,
    )
    committed = json.loads(committed_stdout.getvalue())
    assert committed["committed"] is True
    assert committed["snapshots"]["100"]["id"] == snapshot.pk
    assert committed["snapshots"]["100"]["is_source_snapshot"] is True
