from __future__ import annotations

from datetime import date, time

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from scheduler import models

pytestmark = pytest.mark.django_db


def test_dataset_origin_defaults_to_not_recorded_for_historical_compatibility() -> None:
    user = models.User.objects.create_user(username="origin-default")
    term = models.AcademicTerm.objects.create(
        academic_year="2026-2027",
        semester=models.Semester.FIRST,
        campus="Origin Test",
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 12, 20),
    )
    revision = models.TermDatasetRevision.objects.create(
        term=term,
        revision_number=1,
        created_by=user,
    )
    batch = models.ImportBatch.objects.create(
        term=term,
        uploaded_by=user,
        original_filename="historical.xlsx",
        file_hash="0" * 64,
    )

    assert revision.data_origin == models.DatasetOrigin.UNKNOWN
    assert revision.get_data_origin_display() == "Not recorded"
    assert batch.data_origin == models.DatasetOrigin.UNKNOWN


def build_academic_graph(suffix: str = "") -> dict[str, object]:
    user = models.User.objects.create_user(
        username=f"scheduler{suffix}",
        password="test-password",
        role=models.UserRole.CENTRAL_SCHEDULER,
    )
    college = models.College.objects.create(code=f"C{suffix or '0'}", name=f"College {suffix or '0'}")
    department = models.Department.objects.create(
        college=college,
        code=f"D{suffix or '0'}",
        name=f"Department {suffix or '0'}",
    )
    program = models.Program.objects.create(
        department=department,
        code=f"P{suffix or '0'}",
        name=f"Program {suffix or '0'}",
    )
    subject = models.Subject.objects.create(code=f"SUB{suffix or '0'}", title="Scheduling")
    program_subject = models.ProgramSubject.objects.create(
        program=program,
        subject=subject,
        curriculum_version="2026",
        classification=models.SubjectClassification.MAJOR,
        authoritative_college=college,
        authoritative_department=department,
    )
    term = models.AcademicTerm.objects.create(
        academic_year="2026-2027",
        semester=models.Semester.FIRST,
        campus=f"Kabacan{suffix}",
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 12, 20),
    )
    revision = models.TermDatasetRevision.objects.create(
        term=term,
        revision_number=1,
        created_by=user,
    )
    section = models.Section.objects.create(
        revision=revision,
        program=program,
        code=f"BSCS-1A{suffix}",
        year_level=1,
        cohort_status=models.CohortStatus.INCOMING,
    )
    instructor = models.Instructor.objects.create(
        department=department,
        employee_code=f"FAC-{suffix or '0'}",
        display_name=f"Faculty {suffix or '0'}",
    )
    room = models.Room.objects.create(
        code=f"R-{suffix or '0'}",
        campus=term.campus,
        owning_college=college,
    )
    slot = models.TimeSlot.objects.create(
        revision=revision,
        day=models.Weekday.MONDAY,
        sequence=0,
        starts_at=time(8, 0),
        ends_at=time(8, 30),
    )
    offering = models.CourseOffering.objects.create(
        revision=revision,
        subject=subject,
        offering_department=department,
        external_key=f"OFFER-{suffix or '0'}",
    )
    models.OfferingSection.objects.create(
        offering=offering,
        section=section,
        program_subject=program_subject,
    )
    models.OfferingInstructor.objects.create(offering=offering, instructor=instructor)
    meeting = models.MeetingRequirement.objects.create(
        offering=offering,
        component=models.MeetingComponent.LECTURE,
        occurrence_number=1,
        duration_atoms=1,
    )
    objective = models.ObjectiveProfile.objects.create(name=f"Default {suffix}", version=1)
    snapshot = models.ProblemSnapshot.objects.create(
        revision=revision,
        objective_profile=objective,
        input_data={"events": [{"id": str(meeting.stable_key)}]},
        candidate_map={str(meeting.stable_key): [{"room": room.pk, "slot": slot.pk}]},
        event_count=1,
        candidate_count=1,
        created_by=user,
    )
    return {
        "user": user,
        "college": college,
        "department": department,
        "program": program,
        "subject": subject,
        "program_subject": program_subject,
        "term": term,
        "revision": revision,
        "section": section,
        "instructor": instructor,
        "room": room,
        "slot": slot,
        "offering": offering,
        "meeting": meeting,
        "objective": objective,
        "snapshot": snapshot,
    }


def test_user_roles_and_reviewer_college_scope_are_explicit() -> None:
    graph = build_academic_graph()
    reviewer = models.User.objects.create_user(
        username="reviewer",
        role=models.UserRole.COLLEGE_REVIEWER,
    )
    scope = models.UserCollegeScope(user=reviewer, college=graph["college"])
    scope.full_clean()
    scope.save()

    central_scope = models.UserCollegeScope(user=graph["user"], college=graph["college"])
    with pytest.raises(ValidationError, match="Only college reviewers"):
        central_scope.full_clean()


def test_student_model_contains_only_pseudonymous_identity() -> None:
    field_names = {field.name for field in models.Student._meta.fields}
    assert "pseudonymous_code" in field_names
    assert {"first_name", "last_name", "email", "student_number"}.isdisjoint(field_names)
    student = models.Student.objects.create(pseudonymous_code="anon-8cfe")
    assert str(student) == "anon-8cfe"


def test_cross_unit_and_room_owner_validation() -> None:
    graph = build_academic_graph()
    other_college = models.College.objects.create(code="OTHER", name="Other College")
    other_department = models.Department.objects.create(
        college=other_college,
        code="OTHER-DEPT",
        name="Other Department",
    )

    curriculum = graph["program_subject"]
    curriculum.authoritative_department = other_department
    with pytest.raises(ValidationError, match="must belong to the college"):
        curriculum.full_clean()

    ownerless = models.Room(code="NONE", campus=graph["term"].campus)
    with pytest.raises(ValidationError, match="exactly one"):
        ownerless.full_clean()

    two_owners = models.Room(
        code="TWO",
        campus=graph["term"].campus,
        owning_college=graph["college"],
        owning_department=graph["department"],
    )
    with pytest.raises(ValidationError, match="exactly one"):
        two_owners.full_clean()


def test_laboratory_profile_and_room_authorization_rules() -> None:
    graph = build_academic_graph()
    invalid_profile = models.LaboratoryProfile(
        room=graph["room"],
        laboratory_type="Computer",
    )
    with pytest.raises(ValidationError, match="laboratory room"):
        invalid_profile.full_clean()

    authorization = models.RoomAuthorization(
        revision=graph["revision"],
        room=graph["room"],
        classification=models.SubjectClassification.MAJOR,
        college=graph["college"],
    )
    authorization.full_clean()
    authorization.save()
    assert authorization.authorized_unit == graph["college"]

    authorization.department = graph["department"]
    with pytest.raises(ValidationError, match="exactly one"):
        authorization.full_clean()


def test_timeslot_duration_and_revision_consistency_validation() -> None:
    graph = build_academic_graph()
    backwards = models.TimeSlot(
        revision=graph["revision"],
        day=models.Weekday.TUESDAY,
        sequence=0,
        starts_at=time(9, 0),
        ends_at=time(8, 30),
    )
    with pytest.raises(ValidationError, match="after its start"):
        backwards.full_clean()

    other = build_academic_graph("B")
    profile = models.InstructorAvailabilityProfile.objects.create(
        revision=graph["revision"],
        instructor=graph["instructor"],
    )
    availability = models.InstructorAvailability(profile=profile, time_slot=other["slot"])
    with pytest.raises(ValidationError, match="same revision"):
        availability.full_clean()


def test_full_availability_requires_explicit_acknowledgement() -> None:
    graph = build_academic_graph()
    profile = models.InstructorAvailabilityProfile(
        revision=graph["revision"],
        instructor=graph["instructor"],
        assume_fully_available=True,
    )
    with pytest.raises(ValidationError, match="explicit acknowledgement"):
        profile.full_clean()

    profile.acknowledged_by = graph["user"]
    profile.full_clean()
    assert profile.acknowledged_at is not None


def test_offering_section_rejects_mismatched_revision_and_curriculum() -> None:
    graph = build_academic_graph()
    other = build_academic_graph("B")
    link = models.OfferingSection(
        offering=graph["offering"],
        section=other["section"],
        program_subject=graph["program_subject"],
    )
    with pytest.raises(ValidationError) as exc_info:
        link.full_clean()
    assert "section" in exc_info.value.message_dict
    assert "program_subject" in exc_info.value.message_dict


def test_committed_revision_content_is_immutable_but_can_be_superseded() -> None:
    graph = build_academic_graph()
    revision = graph["revision"]
    revision.content_hash = "a" * 64
    revision.status = models.RevisionStatus.COMMITTED
    revision.save()
    assert revision.committed_at is not None

    revision.label = "changed"
    with pytest.raises(ValidationError, match="immutable"):
        revision.save()

    revision.refresh_from_db()
    revision.status = models.RevisionStatus.SUPERSEDED
    revision.save()
    assert revision.status == models.RevisionStatus.SUPERSEDED


def test_objective_profile_has_canonical_hash_and_freezes_after_approval() -> None:
    graph = build_academic_graph()
    objective = graph["objective"]
    expected = models.canonical_sha256(objective.hash_payload())
    assert objective.profile_hash == expected

    objective.approved_by = graph["user"]
    objective.is_approved = True
    objective.save()
    objective.weights = {**objective.weights, "section_internal_gaps": 9}
    with pytest.raises(ValidationError, match="immutable"):
        objective.save()

    invalid = models.ObjectiveProfile(name="Invalid", weights={"gaps": 1.25})
    with pytest.raises(ValidationError, match="non-negative integer"):
        invalid.full_clean(exclude={"profile_hash"})


def test_problem_snapshot_digest_and_rows_are_immutable() -> None:
    graph = build_academic_graph()
    snapshot = graph["snapshot"]
    assert len(snapshot.snapshot_hash) == 64
    original_hash = snapshot.snapshot_hash

    snapshot.event_count = 99
    with pytest.raises(ValidationError, match="immutable"):
        snapshot.save()
    snapshot.refresh_from_db()
    assert snapshot.snapshot_hash == original_hash
    assert snapshot.event_count == 1


def test_ga_run_cannot_claim_exact_solver_proofs() -> None:
    graph = build_academic_graph()
    run = models.ScheduleRun(
        snapshot=graph["snapshot"],
        algorithm=models.SolverAlgorithm.GENETIC_ALGORITHM,
        seed=1001,
        status=models.RunStatus.OPTIMAL,
        requested_by=graph["user"],
    )
    with pytest.raises(ValidationError, match="cannot prove"):
        run.full_clean()

    run.status = models.RunStatus.NO_SOLUTION
    run.full_clean()


def test_experiment_run_is_bound_to_shared_snapshot_and_unique_seed() -> None:
    graph = build_academic_graph()
    experiment = models.ExperimentBatch.objects.create(
        name="Comparison",
        snapshot=graph["snapshot"],
        seeds=[1001, 1002],
        created_by=graph["user"],
    )
    models.ScheduleRun.objects.create(
        experiment_batch=experiment,
        snapshot=graph["snapshot"],
        algorithm=models.SolverAlgorithm.CP_SAT,
        seed=1001,
        requested_by=graph["user"],
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        models.ScheduleRun.objects.create(
            experiment_batch=experiment,
            snapshot=graph["snapshot"],
            algorithm=models.SolverAlgorithm.CP_SAT,
            seed=1001,
            requested_by=graph["user"],
        )


def test_atom_projection_prevents_room_instructor_and_section_conflicts() -> None:
    graph = build_academic_graph()
    schedule = models.ScheduleVersion.objects.create(
        term=graph["term"],
        revision=graph["revision"],
        snapshot=graph["snapshot"],
        version_number=1,
        name="Draft",
        source=models.ScheduleSource.CP_SAT,
        created_by=graph["user"],
    )
    first_assignment = models.ScheduleAssignment.objects.create(
        schedule=schedule,
        meeting_requirement=graph["meeting"],
        room=graph["room"],
        start_time_slot=graph["slot"],
    )
    second_meeting = models.MeetingRequirement.objects.create(
        offering=graph["offering"],
        component=models.MeetingComponent.LECTURE,
        occurrence_number=2,
        duration_atoms=1,
    )
    second_assignment = models.ScheduleAssignment.objects.create(
        schedule=schedule,
        meeting_requirement=second_meeting,
        room=graph["room"],
        start_time_slot=graph["slot"],
    )
    models.ScheduleRoomAllocation.objects.create(
        schedule=schedule,
        assignment=first_assignment,
        room=graph["room"],
        time_slot=graph["slot"],
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        models.ScheduleRoomAllocation.objects.create(
            schedule=schedule,
            assignment=second_assignment,
            room=graph["room"],
            time_slot=graph["slot"],
        )

    models.ScheduleInstructorAllocation.objects.create(
        schedule=schedule,
        assignment=first_assignment,
        instructor=graph["instructor"],
        time_slot=graph["slot"],
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        models.ScheduleInstructorAllocation.objects.create(
            schedule=schedule,
            assignment=second_assignment,
            instructor=graph["instructor"],
            time_slot=graph["slot"],
        )

    models.ScheduleSectionAllocation.objects.create(
        schedule=schedule,
        assignment=first_assignment,
        section=graph["section"],
        time_slot=graph["slot"],
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        models.ScheduleSectionAllocation.objects.create(
            schedule=schedule,
            assignment=second_assignment,
            section=graph["section"],
            time_slot=graph["slot"],
        )


def test_lock_must_match_meeting_revision() -> None:
    graph = build_academic_graph()
    other = build_academic_graph("B")
    lock = models.LockedAssignment(
        meeting_requirement=graph["meeting"],
        room=graph["room"],
        start_time_slot=other["slot"],
        locked_by=graph["user"],
        reason="Do not move",
    )
    with pytest.raises(ValidationError, match="dataset revision"):
        lock.full_clean()


def test_review_scope_and_approval_require_independent_feasibility() -> None:
    graph = build_academic_graph()
    schedule = models.ScheduleVersion.objects.create(
        term=graph["term"],
        revision=graph["revision"],
        snapshot=graph["snapshot"],
        version_number=1,
        name="Candidate",
        source=models.ScheduleSource.CP_SAT,
        created_by=graph["user"],
    )
    reviewer = models.User.objects.create_user(
        username="reviewer",
        role=models.UserRole.COLLEGE_REVIEWER,
    )
    unscoped_review = models.ScheduleReview(
        schedule=schedule,
        college=graph["college"],
        reviewer=reviewer,
        status=models.ReviewStatus.ENDORSED,
        comment="Checked",
    )
    with pytest.raises(ValidationError, match="not scoped"):
        unscoped_review.full_clean()

    models.UserCollegeScope.objects.create(user=reviewer, college=graph["college"])
    unscoped_review.full_clean()
    unscoped_review.save()

    schedule.status = models.ScheduleStatus.APPROVED
    schedule._allow_approval_transition = True
    schedule.save()
    approval = models.ScheduleApproval(schedule=schedule, approved_by=graph["user"])
    with pytest.raises(ValidationError, match="independent validation"):
        approval.full_clean()

    models.ValidationResult.objects.create(
        schedule_version=schedule,
        is_feasible=True,
        hard_violation_count=0,
        violations={},
    )
    approval.full_clean()
    approval.save()


def test_approved_schedule_is_unique_immutable_and_archivable() -> None:
    graph = build_academic_graph()
    schedule = models.ScheduleVersion(
        term=graph["term"],
        revision=graph["revision"],
        snapshot=graph["snapshot"],
        version_number=1,
        name="Approved baseline",
        source=models.ScheduleSource.CP_SAT,
        status=models.ScheduleStatus.APPROVED,
        created_by=graph["user"],
    )
    schedule._allow_approval_transition = True
    schedule.save()
    assert schedule.finalized_at is not None

    schedule.name = "Silent edit"
    with pytest.raises(ValidationError, match="immutable"):
        schedule.save()

    schedule.refresh_from_db()
    second = models.ScheduleVersion(
        term=graph["term"],
        revision=graph["revision"],
        snapshot=graph["snapshot"],
        version_number=2,
        name="Another approved version",
        source=models.ScheduleSource.GENETIC_ALGORITHM,
        status=models.ScheduleStatus.APPROVED,
        created_by=graph["user"],
    )
    second._allow_approval_transition = True
    with pytest.raises(IntegrityError), transaction.atomic():
        second.save()

    schedule.status = models.ScheduleStatus.ARCHIVED
    schedule.save()
    assert schedule.status == models.ScheduleStatus.ARCHIVED


def test_committed_revision_freezes_rows_and_referenced_catalog_semantics() -> None:
    graph = build_academic_graph()
    models.RoomAvailabilityProfile.objects.create(
        revision=graph["revision"],
        room=graph["room"],
        assume_fully_available=True,
        acknowledged_by=graph["user"],
    )
    revision = graph["revision"]
    revision.status = models.RevisionStatus.COMMITTED
    revision.content_hash = "a" * 64
    revision.save()

    section = graph["section"]
    section.code = "CHANGED"
    with pytest.raises(ValidationError, match="dataset revision are immutable"):
        section.save()

    room = graph["room"]
    room.name = "Changed room semantics"
    with pytest.raises(ValidationError, match="committed dataset revision"):
        room.save()

    term = graph["term"]
    term.campus = "Different campus"
    with pytest.raises(ValidationError, match="committed dataset revision"):
        term.save()

    term.refresh_from_db()
    term.status = models.TermStatus.ACTIVE
    term.save()
    assert term.status == models.TermStatus.ACTIVE


def test_experiment_protocol_and_terminal_run_evidence_are_immutable() -> None:
    graph = build_academic_graph()
    batch = models.ExperimentBatch.objects.create(
        name="Frozen experiment",
        snapshot=graph["snapshot"],
        seeds=[1001],
        order_seed=44,
        created_by=graph["user"],
    )
    run = models.ScheduleRun.objects.create(
        experiment_batch=batch,
        snapshot=graph["snapshot"],
        algorithm=models.SolverAlgorithm.CP_SAT,
        seed=1001,
        requested_by=graph["user"],
    )

    batch.time_limit_seconds = 1
    with pytest.raises(ValidationError, match="protocol is immutable"):
        batch.save()

    run.status = models.RunStatus.FEASIBLE
    run.save()
    run.seed = 1002
    with pytest.raises(ValidationError, match="run evidence is immutable"):
        run.save()
    with pytest.raises(ValidationError, match="cannot be deleted"):
        run.delete()


def test_audit_log_is_append_only() -> None:
    graph = build_academic_graph()
    event = models.AuditLog.objects.create(
        actor=graph["user"],
        action="IMPORT_PREVIEWED",
        entity_type="ImportBatch",
        entity_id="42",
        details={"errors": 0},
    )
    event.details = {"errors": 99}
    with pytest.raises(ValidationError, match="append-only"):
        event.save()
    with pytest.raises(ValidationError, match="append-only"):
        event.delete()
