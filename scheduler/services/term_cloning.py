"""Semester-to-semester cloning for editable scheduling inputs."""

from __future__ import annotations

from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from scheduler import models


def _ensure_central(actor: models.User) -> None:
    if not actor.is_active or (not actor.is_superuser and actor.role not in {
        models.UserRole.SYSTEM_ADMIN,
        models.UserRole.CENTRAL_SCHEDULER,
    }):
        raise PermissionDenied("Only a system administrator or central scheduler may clone a term.")


def _save_validated(instance):  # type: ignore[no-untyped-def]
    instance.full_clean()
    instance.save()
    return instance


@transaction.atomic
def clone_term_revision(
    source_revision: models.TermDatasetRevision,
    *,
    academic_year: str,
    semester: str,
    starts_on: date,
    ends_on: date,
    actor: models.User,
    label: str | None = None,
) -> models.TermDatasetRevision:
    """Clone revision-scoped inputs into a new editable semester.

    Organization, catalog, instructor, room, capability, and pseudonymous
    student records are shared. Every revision-local row receives a new primary
    key and all through-table references are remapped. Solver runs, snapshots,
    schedules, validations, approvals, reviews, and locked assignments are
    intentionally excluded.
    """

    _ensure_central(actor)
    source = (
        models.TermDatasetRevision.objects.select_related("term")
        .select_for_update()
        .get(pk=source_revision.pk)
    )
    if source.status not in {models.RevisionStatus.COMMITTED, models.RevisionStatus.SUPERSEDED}:
        raise ValidationError("Only a committed or superseded dataset revision can be cloned.")
    if semester not in models.Semester.values:
        raise ValidationError({"semester": "Choose a supported semester value."})

    new_term = models.AcademicTerm(
        academic_year=academic_year,
        semester=semester,
        campus=source.term.campus,
        starts_on=starts_on,
        ends_on=ends_on,
        status=models.TermStatus.DRAFT,
    )
    _save_validated(new_term)
    revision = models.TermDatasetRevision(
        term=new_term,
        revision_number=1,
        status=models.RevisionStatus.DRAFT,
        label=label or (
            f"Clone of {source.term.academic_year} {source.get_status_display()} "
            f"revision {source.revision_number}"
        ),
        content_hash="",
        data_origin=source.data_origin,
        created_by=actor,
    )
    _save_validated(revision)

    policy_map: dict[int, models.ConstraintPolicyVersion] = {}
    for original in source.term.constraint_policy_versions.order_by("rule_code", "version", "pk"):
        clone = models.ConstraintPolicyVersion(
            rule_code=original.rule_code,
            version=original.version,
            title=original.title,
            definition=original.definition,
            classification=original.classification,
            owner_office=original.owner_office,
            source=original.source,
            effective_term=new_term,
            parameters=original.parameters,
            is_approved=original.is_approved,
            approved_by=original.approved_by,
            approved_at=original.approved_at,
        )
        _save_validated(clone)
        policy_map[original.pk] = clone

    section_map: dict[int, models.Section] = {}
    for original in source.sections.select_related("program").order_by("pk"):
        clone = models.Section(
            revision=revision,
            program=original.program,
            code=original.code,
            year_level=original.year_level,
            cohort_status=original.cohort_status,
            expected_enrollment=original.expected_enrollment,
            is_active=original.is_active,
        )
        _save_validated(clone)
        section_map[original.pk] = clone

    slot_map: dict[int, models.TimeSlot] = {}
    for original in source.time_slots.order_by("day", "sequence", "pk"):
        clone = models.TimeSlot(
            revision=revision,
            day=original.day,
            sequence=original.sequence,
            starts_at=original.starts_at,
            ends_at=original.ends_at,
            is_break=original.is_break,
            is_active=original.is_active,
        )
        _save_validated(clone)
        slot_map[original.pk] = clone

    for original in source.room_authorizations.select_related("room", "college", "department").order_by("pk"):
        clone = models.RoomAuthorization(
            revision=revision,
            room=original.room,
            classification=original.classification,
            college=original.college,
            department=original.department,
            notes=original.notes,
        )
        _save_validated(clone)

    for original in source.instructor_availability_profiles.select_related(
        "instructor", "daily_load_policy_version"
    ).order_by("pk"):
        acknowledged = bool(original.acknowledged_by_id or original.assume_fully_available)
        clone = models.InstructorAvailabilityProfile(
            revision=revision,
            instructor=original.instructor,
            assume_fully_available=original.assume_fully_available,
            max_daily_teaching_atoms=original.max_daily_teaching_atoms,
            acknowledge_no_daily_limit=original.acknowledge_no_daily_limit,
            daily_load_policy_version=(
                policy_map[original.daily_load_policy_version_id]
                if original.daily_load_policy_version_id is not None
                else None
            ),
            acknowledged_by=actor if acknowledged else None,
            acknowledged_at=timezone.now() if acknowledged else None,
            notes=original.notes,
        )
        _save_validated(clone)
        for row in original.availability_rows.select_related("time_slot").order_by("pk"):
            availability = models.InstructorAvailability(
                profile=clone,
                time_slot=slot_map[row.time_slot_id],
                is_available=row.is_available,
            )
            _save_validated(availability)
        for row in original.preferences.select_related("time_slot").order_by("pk"):
            preference = models.InstructorPreference(
                profile=clone,
                time_slot=slot_map[row.time_slot_id],
                level=row.level,
                weight=row.weight,
            )
            _save_validated(preference)

    for original in source.room_availability_profiles.select_related("room").order_by("pk"):
        acknowledged = bool(original.acknowledged_by_id or original.assume_fully_available)
        clone = models.RoomAvailabilityProfile(
            revision=revision,
            room=original.room,
            assume_fully_available=original.assume_fully_available,
            acknowledged_by=actor if acknowledged else None,
            acknowledged_at=timezone.now() if acknowledged else None,
            notes=original.notes,
        )
        _save_validated(clone)
        for row in original.availability_rows.select_related("time_slot").order_by("pk"):
            availability = models.RoomAvailability(
                profile=clone,
                time_slot=slot_map[row.time_slot_id],
                is_available=row.is_available,
            )
            _save_validated(availability)

    offering_map: dict[int, models.CourseOffering] = {}
    for original in source.course_offerings.select_related("subject", "offering_department").order_by("pk"):
        clone = models.CourseOffering(
            revision=revision,
            subject=original.subject,
            offering_department=original.offering_department,
            external_key=original.external_key,
            is_active=original.is_active,
        )
        _save_validated(clone)
        offering_map[original.pk] = clone
        for link in original.section_links.select_related("section", "program_subject").order_by("pk"):
            cloned_link = models.OfferingSection(
                offering=clone,
                section=section_map[link.section_id],
                program_subject=link.program_subject,
            )
            _save_validated(cloned_link)
        for link in original.instructor_links.select_related("instructor").order_by("pk"):
            cloned_link = models.OfferingInstructor(
                offering=clone,
                instructor=link.instructor,
            )
            _save_validated(cloned_link)

    meeting_map: dict[int, models.MeetingRequirement] = {}
    meetings = models.MeetingRequirement.objects.filter(offering__revision=source).select_related("offering").order_by("pk")
    for original in meetings:
        clone = models.MeetingRequirement(
            offering=offering_map[original.offering_id],
            component=original.component,
            occurrence_number=original.occurrence_number,
            duration_atoms=original.duration_atoms,
            distinct_day_group=original.distinct_day_group,
            is_active=original.is_active,
        )
        _save_validated(clone)
        meeting_map[original.pk] = clone
        for capability in original.required_capabilities.order_by("pk"):
            cloned_link = models.MeetingRequiredCapability(
                meeting_requirement=clone,
                capability=capability,
            )
            _save_validated(cloned_link)

    memberships = models.StudentSectionMembership.objects.filter(
        section__revision=source
    ).select_related("student", "section").order_by("pk")
    for original in memberships:
        clone = models.StudentSectionMembership(
            student=original.student,
            section=section_map[original.section_id],
        )
        _save_validated(clone)

    reserved_block_map: dict[int, models.ReservedTimeBlock] = {}
    for original in source.reserved_time_blocks.select_related(
        "college", "department", "program", "section", "policy_version"
    ).order_by("scope", "label", "pk"):
        clone = models.ReservedTimeBlock(
            revision=revision,
            scope=original.scope,
            college=original.college,
            department=original.department,
            program=original.program,
            section=(
                section_map[original.section_id]
                if original.section_id is not None
                else None
            ),
            policy_version=policy_map[original.policy_version_id],
            label=original.label,
            reason=original.reason,
            is_active=original.is_active,
        )
        _save_validated(clone)
        reserved_block_map[original.pk] = clone
        for row in original.slot_links.select_related("time_slot").order_by("pk"):
            slot_link = models.ReservedTimeBlockSlot(
                block=clone,
                time_slot=slot_map[row.time_slot_id],
            )
            _save_validated(slot_link)

    models.AuditLog.objects.create(
        actor=actor,
        action="term.revision_cloned",
        entity_type="TermDatasetRevision",
        entity_id=str(revision.pk),
        details={
            "source_revision_id": source.pk,
            "source_term_id": source.term_id,
            "new_term_id": new_term.pk,
            "section_count": len(section_map),
            "offering_count": len(offering_map),
            "meeting_count": len(meeting_map),
            "constraint_policy_count": len(policy_map),
            "reserved_block_count": len(reserved_block_map),
        },
    )
    for source_profile in source.term.objective_profiles.order_by("name", "version"):
        models.ObjectiveProfile.objects.create(
            name=source_profile.name,
            version=source_profile.version,
            term=new_term,
            weights=source_profile.weights,
            definitions=source_profile.definitions,
            normalization_denominators=source_profile.normalization_denominators,
            is_approved=False,
        )
    return revision


__all__ = ["clone_term_revision"]
