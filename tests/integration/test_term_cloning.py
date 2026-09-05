from __future__ import annotations

import json
from datetime import date
from io import StringIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command

from scheduler import models
from scheduler.services.problem_builder import ProblemBuildError, build_and_store_snapshot
from scheduler.services.revision_lifecycle import validate_and_commit_revision
from scheduler.services.term_cloning import clone_term_revision

pytestmark = pytest.mark.django_db


def seed_source() -> tuple[dict[str, int], models.TermDatasetRevision, models.User]:
    output = StringIO()
    call_command("seed_demo", stdout=output)
    identifiers = json.loads(output.getvalue().strip().splitlines()[-1])
    revision = models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"])
    actor = models.User.objects.get(pk=identifiers["central_user_id"])
    return identifiers, revision, actor


def test_clone_remaps_every_revision_input_and_excludes_solver_artifacts_and_locks() -> None:
    identifiers, source, actor = seed_source()
    source_instructor_profile = source.instructor_availability_profiles.order_by("pk").first()
    source_room_profile = source.room_availability_profiles.order_by("pk").first()
    source_slot = source.time_slots.order_by("day", "sequence").first()
    source_meeting = models.MeetingRequirement.objects.filter(offering__revision=source).order_by("pk").first()
    source_room = models.Room.objects.get(code="CSM-101", campus=source.term.campus)
    models.LockedAssignment.objects.create(
        meeting_requirement=source_meeting,
        room=source_room,
        start_time_slot=source_slot,
        locked_by=actor,
        reason="Source-only lock",
    )
    objective = models.ObjectiveProfile.objects.get(pk=identifiers["objective_profile_id"])
    source_snapshot, _ = build_and_store_snapshot(source, objective, actor)
    models.ScheduleRun.objects.create(
        snapshot=source_snapshot,
        algorithm=models.SolverAlgorithm.CP_SAT,
        seed=77,
        requested_by=actor,
    )
    models.ScheduleVersion.objects.create(
        term=source.term,
        revision=source,
        snapshot=source_snapshot,
        version_number=1,
        name="Source-only manual schedule",
        source=models.ScheduleSource.MANUAL,
        created_by=actor,
    )

    clone = clone_term_revision(
        source,
        academic_year="2027-2028",
        semester=models.Semester.SECOND,
        starts_on=date(2028, 1, 5),
        ends_on=date(2028, 5, 20),
        actor=actor,
        label="Editable semester planning base",
    )

    assert clone.pk != source.pk
    assert clone.term_id != source.term_id
    assert clone.status == models.RevisionStatus.DRAFT
    assert clone.content_hash == ""
    assert clone.label == "Editable semester planning base"
    assert clone.term.status == models.TermStatus.DRAFT
    assert clone.term.academic_year == "2027-2028"
    assert clone.term.semester == models.Semester.SECOND
    assert clone.term.campus == source.term.campus
    assert clone.created_by == actor
    assert clone.data_origin == source.data_origin == models.DatasetOrigin.SYNTHETIC

    assert clone.sections.count() == source.sections.count()
    assert clone.time_slots.count() == source.time_slots.count()
    assert clone.room_authorizations.count() == source.room_authorizations.count()
    assert clone.instructor_availability_profiles.count() == source.instructor_availability_profiles.count()
    assert clone.room_availability_profiles.count() == source.room_availability_profiles.count()
    assert clone.course_offerings.count() == source.course_offerings.count()

    source_section = source.sections.get(code="BSCS-1A")
    cloned_section = clone.sections.get(code="BSCS-1A")
    assert cloned_section.pk != source_section.pk
    assert cloned_section.program_id == source_section.program_id
    source_slot_ids = set(source.time_slots.values_list("pk", flat=True))
    clone_slot_ids = set(clone.time_slots.values_list("pk", flat=True))
    assert source_slot_ids.isdisjoint(clone_slot_ids)

    source_offerings = {row.external_key: row for row in source.course_offerings.all()}
    clone_offerings = {row.external_key: row for row in clone.course_offerings.all()}
    assert set(clone_offerings) == set(source_offerings)
    assert all(clone_offerings[key].pk != source_offerings[key].pk for key in source_offerings)
    for key, cloned_offering in clone_offerings.items():
        source_offering = source_offerings[key]
        assert cloned_offering.subject_id == source_offering.subject_id
        assert cloned_offering.offering_department_id == source_offering.offering_department_id
        assert list(cloned_offering.section_links.values_list("section_id", flat=True)) == [
            cloned_section.pk
        ]
        assert set(cloned_offering.instructor_links.values_list("instructor_id", flat=True)) == set(
            source_offering.instructor_links.values_list("instructor_id", flat=True)
        )

    source_meetings = list(
        models.MeetingRequirement.objects.filter(offering__revision=source).order_by("offering__external_key")
    )
    clone_meetings = list(
        models.MeetingRequirement.objects.filter(offering__revision=clone).order_by("offering__external_key")
    )
    assert len(clone_meetings) == len(source_meetings)
    assert {row.stable_key for row in source_meetings}.isdisjoint(
        {row.stable_key for row in clone_meetings}
    )
    assert [set(row.required_capabilities.values_list("code", flat=True)) for row in clone_meetings] == [
        set(row.required_capabilities.values_list("code", flat=True)) for row in source_meetings
    ]

    cloned_instructor_profile = clone.instructor_availability_profiles.get(
        instructor=source_instructor_profile.instructor
    )
    cloned_room_profile = clone.room_availability_profiles.get(room=source_room_profile.room)
    assert cloned_instructor_profile.pk != source_instructor_profile.pk
    assert cloned_room_profile.pk != source_room_profile.pk
    assert cloned_instructor_profile.acknowledged_by_id == source_instructor_profile.acknowledged_by_id
    assert cloned_room_profile.acknowledged_by_id == source_room_profile.acknowledged_by_id
    assert cloned_instructor_profile.preferences.count() == source_instructor_profile.preferences.count()
    assert cloned_instructor_profile.availability_rows.count() == 2
    assert cloned_room_profile.availability_rows.count() == 2
    assert set(
        cloned_instructor_profile.availability_rows.values_list("time_slot_id", flat=True)
    ).issubset(clone_slot_ids)
    assert set(
        cloned_room_profile.availability_rows.values_list("time_slot_id", flat=True)
    ).issubset(clone_slot_ids)

    source_membership = models.StudentSectionMembership.objects.get(section=source_section)
    clone_membership = models.StudentSectionMembership.objects.get(section=cloned_section)
    assert clone_membership.pk != source_membership.pk
    assert clone_membership.student_id == source_membership.student_id

    assert not models.LockedAssignment.objects.filter(
        meeting_requirement__offering__revision=clone
    ).exists()
    assert not clone.problem_snapshots.exists()
    assert not clone.schedule_versions.exists()
    assert not models.ScheduleRun.objects.filter(snapshot__revision=clone).exists()
    assert models.AuditLog.objects.filter(
        action="term.revision_cloned",
        entity_id=str(clone.pk),
    ).exists()

    cloned_section.code = "BSCS-1B"
    cloned_section.cohort_status = models.CohortStatus.CONTINUING
    cloned_section.save()
    source_section.refresh_from_db()
    assert source_section.code == "BSCS-1A"
    assert source_section.cohort_status == models.CohortStatus.INCOMING


def test_clone_permission_validation_and_transactional_rollback() -> None:
    identifiers, source, actor = seed_source()
    reviewer = models.User.objects.get(pk=identifiers["reviewer_user_id"])
    with pytest.raises(PermissionDenied):
        clone_term_revision(
            source,
            academic_year="2027-2028",
            semester=models.Semester.FIRST,
            starts_on=date(2027, 8, 1),
            ends_on=date(2027, 12, 20),
            actor=reviewer,
        )
    assert models.AcademicTerm.objects.count() == 1

    with pytest.raises(ValidationError):
        clone_term_revision(
            source,
            academic_year="2027-2028",
            semester=models.Semester.FIRST,
            starts_on=date(2027, 12, 20),
            ends_on=date(2027, 8, 1),
            actor=actor,
        )
    assert models.AcademicTerm.objects.count() == 1
    assert models.TermDatasetRevision.objects.count() == 1

    editable_source = models.TermDatasetRevision.objects.create(
        term=source.term,
        revision_number=2,
        status=models.RevisionStatus.DRAFT,
        created_by=actor,
    )
    with pytest.raises(ValidationError, match="committed or superseded"):
        clone_term_revision(
            editable_source,
            academic_year="2027-2028",
            semester=models.Semester.FIRST,
            starts_on=date(2027, 8, 1),
            ends_on=date(2027, 12, 20),
            actor=actor,
        )


def test_clone_term_management_command_outputs_new_editable_revision() -> None:
    _, source, actor = seed_source()
    output = StringIO()
    call_command(
        "clone_term",
        source.pk,
        academic_year="2027-2028",
        semester=models.Semester.FIRST,
        starts_on="2027-08-01",
        ends_on="2027-12-20",
        actor=actor.username,
        label="Command-created planning term",
        stdout=output,
    )
    payload = json.loads(output.getvalue().strip())
    clone = models.TermDatasetRevision.objects.get(pk=payload["revision_id"])
    assert payload["source_revision_id"] == source.pk
    assert payload["status"] == models.RevisionStatus.DRAFT
    assert payload["sections"] == source.sections.count()
    assert payload["offerings"] == source.course_offerings.count()
    assert payload["meetings"] == models.MeetingRequirement.objects.filter(
        offering__revision=source
    ).count()
    assert clone.label == "Command-created planning term"
    assert clone.term.academic_year == "2027-2028"


def test_edited_clone_can_be_preflighted_and_committed_atomically() -> None:
    _, source, actor = seed_source()
    clone = clone_term_revision(
        source,
        academic_year="2027-2028",
        semester=models.Semester.FIRST,
        starts_on=date(2027, 8, 1),
        ends_on=date(2027, 12, 20),
        actor=actor,
    )
    cloned_objective = clone.term.objective_profiles.get()
    assert cloned_objective.is_approved is False
    cloned_objective.is_approved = True
    cloned_objective.approved_by = actor
    cloned_objective.save()

    finalized = validate_and_commit_revision(clone, cloned_objective, actor)

    assert finalized.status == models.RevisionStatus.COMMITTED
    assert len(finalized.content_hash) == 64
    assert finalized.committed_at is not None
    snapshot, result = build_and_store_snapshot(finalized, cloned_objective, actor)
    assert snapshot.event_count == len(result.problem.events) == 2
    assert models.AuditLog.objects.filter(
        action="term.revision_finalized", entity_id=str(finalized.pk)
    ).exists()


def test_failed_clone_preflight_rolls_status_back_to_draft() -> None:
    _, source, actor = seed_source()
    clone = clone_term_revision(
        source,
        academic_year="2028-2029",
        semester=models.Semester.FIRST,
        starts_on=date(2028, 8, 1),
        ends_on=date(2028, 12, 20),
        actor=actor,
    )
    objective = clone.term.objective_profiles.get()
    objective.is_approved = True
    objective.approved_by = actor
    objective.save()
    clone.room_authorizations.all().delete()

    with pytest.raises(ProblemBuildError):
        validate_and_commit_revision(clone, objective, actor)

    clone.refresh_from_db()
    assert clone.status == models.RevisionStatus.DRAFT
    assert clone.content_hash == ""
