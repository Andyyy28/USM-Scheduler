"""Translate a committed term revision into the canonical solver contracts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from statistics import mean, median
from time import perf_counter
from typing import Any

from django.db import transaction
from django.db.models import Prefetch

from scheduler import models
from scheduler.domain import (
    Assignment,
    CandidatePlacement,
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
from scheduler.domain import ObjectiveProfile as DomainObjectiveProfile


@dataclass(frozen=True, slots=True)
class BuildIssue:
    code: str
    message: str
    entity_type: str = ""
    entity_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class ProblemBuildError(ValueError):
    def __init__(self, issues: Iterable[BuildIssue]):
        self.issues = tuple(issues)
        summary = "; ".join(issue.message for issue in self.issues[:5])
        if len(self.issues) > 5:
            summary += f"; and {len(self.issues) - 5} more issue(s)"
        super().__init__(summary or "The term revision cannot be converted to a scheduling problem.")


@dataclass(frozen=True, slots=True)
class ProblemBuildResult:
    problem: ProblemInstance
    preprocessing_seconds: float
    candidate_count: int
    constraint_manifest_hash: str
    rule_manifest: dict[str, Any]
    section_headcounts: dict[str, int]
    meeting_headcounts: dict[str, int]
    reserved_block_evidence: tuple[dict[str, Any], ...]
    instructor_daily_load_evidence: tuple[dict[str, Any], ...]
    instance_characteristics: dict[str, Any]


def _domain_objective_profile(profile: models.ObjectiveProfile) -> DomainObjectiveProfile:
    weights = profile.weights or {}
    normalizers = profile.normalization_denominators or {}
    return DomainObjectiveProfile(
        profile_id=f"{profile.name}-v{profile.version}-{profile.profile_hash[:12]}",
        preference_weight=int(weights.get("instructor_preference", 0)),
        section_gap_weight=int(weights.get("section_internal_gaps", 0)),
        instructor_gap_weight=int(weights.get("instructor_internal_gaps", 0)),
        load_imbalance_weight=int(weights.get("daily_load_imbalance", 0)),
        preference_normalizer=max(1, int(normalizers.get("instructor_preference", 1))),
        section_gap_normalizer=max(1, int(normalizers.get("section_internal_gaps", 1))),
        instructor_gap_normalizer=max(1, int(normalizers.get("instructor_internal_gaps", 1))),
        load_imbalance_normalizer=max(1, int(normalizers.get("daily_load_imbalance", 1))),
    )


def _contiguous_windows(slots: list[models.TimeSlot], duration: int) -> Iterable[list[models.TimeSlot]]:
    for start in range(0, len(slots) - duration + 1):
        window = slots[start : start + duration]
        if all(
            right.sequence == left.sequence + 1 and right.starts_at == left.ends_at
            for left, right in zip(window, window[1:], strict=False)
        ):
            yield window


def _available_atom_ids_for_instructor(
    profile: models.InstructorAvailabilityProfile,
    all_atom_ids: set[int],
) -> set[int]:
    if profile.assume_fully_available:
        return set(all_atom_ids)
    prefetched_rows = getattr(profile, "prefetched_available_rows", None)
    if prefetched_rows is not None:
        return {
            row.time_slot_id
            for row in prefetched_rows
            if row.is_available and row.time_slot_id in all_atom_ids
        }
    return set(
        profile.availability_rows.filter(is_available=True, time_slot_id__in=all_atom_ids).values_list(
            "time_slot_id", flat=True
        )
    )


def _available_atom_ids_for_room(
    profile: models.RoomAvailabilityProfile,
    all_atom_ids: set[int],
) -> set[int]:
    if profile.assume_fully_available:
        return set(all_atom_ids)
    prefetched_rows = getattr(profile, "prefetched_available_rows", None)
    if prefetched_rows is not None:
        return {
            row.time_slot_id
            for row in prefetched_rows
            if row.is_available and row.time_slot_id in all_atom_ids
        }
    return set(
        profile.availability_rows.filter(is_available=True, time_slot_id__in=all_atom_ids).values_list(
            "time_slot_id", flat=True
        )
    )


def _authorization_matches(
    authorization_rows: list[models.RoomAuthorization],
    link: models.OfferingSection,
    offering: models.CourseOffering,
) -> bool:
    classification = link.program_subject.classification
    if classification == models.SubjectClassification.MAJOR:
        college_id = link.program_subject.authoritative_college_id
        department_id = link.program_subject.authoritative_department_id
    else:
        department_id = offering.offering_department_id
        college_id = offering.offering_department.college_id
    return any(
        authorization.classification == classification
        and (
            (authorization.department_id is not None and authorization.department_id == department_id)
            or (authorization.college_id is not None and authorization.college_id == college_id)
        )
        for authorization in authorization_rows
    )


def _authorization_requirement(
    link: models.OfferingSection,
    offering: models.CourseOffering,
) -> RoomAuthorizationRequirement:
    return RoomAuthorizationRequirement(
        section_id=str(link.section_id),
        classification=link.program_subject.classification,
        authoritative_college_id=str(link.program_subject.authoritative_college_id),
        authoritative_department_id=(
            str(link.program_subject.authoritative_department_id)
            if link.program_subject.authoritative_department_id is not None
            else None
        ),
        offering_college_id=str(offering.offering_department.college_id),
        offering_department_id=str(offering.offering_department_id),
    )


def _policy_evidence(policy: models.ConstraintPolicyVersion) -> dict[str, Any]:
    return {
        "rule_code": policy.rule_code,
        "version": policy.version,
        "policy_hash": policy.policy_hash,
        "classification": policy.classification,
        "owner_office": policy.owner_office,
        "source": policy.source,
        "effective_term_id": policy.effective_term_id,
        "parameters": policy.parameters,
        "is_approved": policy.is_approved,
        "approved_by_id": policy.approved_by_id,
        "approved_at": policy.approved_at.isoformat() if policy.approved_at else None,
    }


def _build_rule_manifest(
    revision: models.TermDatasetRevision,
    referenced_policies: Iterable[models.ConstraintPolicyVersion],
) -> tuple[dict[str, Any], str]:
    policies = {
        (policy.rule_code, policy.version, policy.pk): policy
        for policy in referenced_policies
    }
    fixed_policy = (
        models.ConstraintPolicyVersion.objects.filter(
            effective_term=revision.term,
            rule_code="FIXED_STUDENT_LIMIT_50",
        )
        .order_by("-version", "-pk")
        .first()
    )
    if fixed_policy is not None:
        policies[(fixed_policy.rule_code, fixed_policy.version, fixed_policy.pk)] = fixed_policy
        fixed_rule = {
            "rule_code": fixed_policy.rule_code,
            "limit": 50,
            "policy_hash": fixed_policy.policy_hash,
            "approved": fixed_policy.is_approved,
        }
    else:
        fixed_rule = {
            "rule_code": "FIXED_STUDENT_LIMIT_50",
            "limit": 50,
            "policy_hash": "",
            "approved": False,
        }
    policy_rows = [
        _policy_evidence(policy)
        for policy in sorted(
            policies.values(),
            key=lambda item: (item.rule_code, item.version, item.pk),
        )
    ]
    manifest = {
        "schema": "constraint-manifest-v2",
        "fixed_student_rule": fixed_rule,
        "policies": policy_rows,
        "policy_hashes": sorted(row["policy_hash"] for row in policy_rows),
    }
    return manifest, models.canonical_sha256(manifest)


def _reserved_block_applies(
    block: models.ReservedTimeBlock,
    meeting: models.MeetingRequirement,
    section_links: list[models.OfferingSection],
) -> bool:
    if block.scope == models.ReservedBlockScope.INSTITUTION:
        return True
    if block.scope == models.ReservedBlockScope.COLLEGE:
        return bool(
            block.college_id == meeting.offering.offering_department.college_id
            or any(
                link.section.program.department.college_id == block.college_id
                for link in section_links
            )
        )
    if block.scope == models.ReservedBlockScope.DEPARTMENT:
        return bool(
            block.department_id == meeting.offering.offering_department_id
            or any(
                link.section.program.department_id == block.department_id
                for link in section_links
            )
        )
    if block.scope == models.ReservedBlockScope.PROGRAM:
        return any(link.section.program_id == block.program_id for link in section_links)
    if block.scope == models.ReservedBlockScope.SECTION:
        return any(link.section_id == block.section_id for link in section_links)
    return False


def _instance_characteristics(problem: ProblemInstance) -> dict[str, Any]:
    candidate_counts = [len(event.candidates) for event in problem.events]
    section_ids = sorted({section_id for event in problem.events for section_id in event.section_ids})
    instructor_ids = sorted(
        {instructor_id for event in problem.events for instructor_id in event.instructor_ids}
    )
    headcount_by_section = {
        section_id: count
        for event in problem.events
        for section_id, count in event.section_headcounts
    }
    headcounts = list(headcount_by_section.values())
    required_atoms = sum(event.duration_atoms for event in problem.events)
    available_room_atoms = sum(
        len(evidence.available_atom_ids) for evidence in problem.room_evidence
    )
    potential_domains = (
        len(problem.events) * len(problem.room_evidence) * len(problem.time_atoms)
    )
    availability_ratios = [
        len(evidence.available_atom_ids) / max(1, len(problem.time_atoms))
        for evidence in (
            *problem.room_evidence,
            *(
                row
                for row in problem.instructor_evidence
                if row.instructor_id in instructor_ids
            ),
        )
    ]
    return {
        "offerings": len({event.offering_id for event in problem.events}),
        "meetings": len(problem.events),
        "sections": len(section_ids),
        "instructors": len(instructor_ids),
        "rooms": len(problem.room_evidence),
        "time_atoms": len(problem.time_atoms),
        "locks": len(problem.locked_assignments),
        "candidates": {
            "total": sum(candidate_counts),
            "min": min(candidate_counts, default=0),
            "median": median(candidate_counts) if candidate_counts else 0,
            "mean": mean(candidate_counts) if candidate_counts else 0,
            "max": max(candidate_counts, default=0),
        },
        "required_meeting_atoms": required_atoms,
        "available_room_atoms": available_room_atoms,
        "candidate_domain_density": (
            sum(candidate_counts) / potential_domains if potential_domains else 0
        ),
        "room_time_demand_pressure": (
            required_atoms / available_room_atoms if available_room_atoms else None
        ),
        "availability_density": mean(availability_ratios) if availability_ratios else 0,
        "section_headcounts": {
            "min": min(headcounts, default=0),
            "median": median(headcounts) if headcounts else 0,
            "mean": mean(headcounts) if headcounts else 0,
            "max": max(headcounts, default=0),
        },
        "meetings_approaching_50": sum(
            1 for event in problem.events if (event.meeting_headcount or 0) >= 45
        ),
    }


def build_problem(
    revision: models.TermDatasetRevision,
    objective_profile: models.ObjectiveProfile,
) -> ProblemBuildResult:
    started = perf_counter()
    issues: list[BuildIssue] = []
    source_import_summary = (
        models.ImportBatch.objects.filter(committed_revision=revision)
        .values_list("summary", flat=True)
        .first()
    )
    is_legacy_import = bool(
        isinstance(source_import_summary, dict)
        and str(source_import_summary.get("schema_version")) == "1.0"
    )
    if revision.status not in {models.RevisionStatus.VALIDATED, models.RevisionStatus.COMMITTED}:
        issues.append(
            BuildIssue(
                "REVISION_NOT_VALIDATED",
                "Only a validated or committed term revision can be scheduled.",
                "TermDatasetRevision",
                str(revision.pk),
            )
        )
    if objective_profile.term_id and objective_profile.term_id != revision.term_id:
        issues.append(
            BuildIssue(
                "OBJECTIVE_TERM_MISMATCH",
                "The objective profile belongs to a different academic term.",
                "ObjectiveProfile",
                str(objective_profile.pk),
            )
        )

    slots = list(
        revision.time_slots.filter(is_active=True, is_break=False).order_by("day", "sequence")
    )
    if not slots:
        issues.append(BuildIssue("NO_TIME_SLOTS", "The revision has no active schedulable time slots."))
    slots_by_day: dict[int, list[models.TimeSlot]] = {}
    for slot in slots:
        slots_by_day.setdefault(slot.day, []).append(slot)
    all_slot_ids = {slot.pk for slot in slots}
    atom_id = lambda value: f"slot:{value}"  # noqa: E731 - concise stable identifier adapter

    instructor_profiles = {
        profile.instructor_id: profile
        for profile in revision.instructor_availability_profiles.select_related(
            "instructor", "daily_load_policy_version"
        ).prefetch_related(
            Prefetch(
                "availability_rows",
                queryset=models.InstructorAvailability.objects.filter(is_available=True),
                to_attr="prefetched_available_rows",
            ),
            Prefetch(
                "preferences",
                queryset=models.InstructorPreference.objects.select_related("time_slot"),
                to_attr="prefetched_preferences",
            ),
        )
    }
    room_profiles = {
        profile.room_id: profile
        for profile in revision.room_availability_profiles.select_related("room").prefetch_related(
            Prefetch(
                "availability_rows",
                queryset=models.RoomAvailability.objects.filter(is_available=True),
                to_attr="prefetched_available_rows",
            )
        )
    }

    meetings = list(
        models.MeetingRequirement.objects.filter(
            offering__revision=revision,
            offering__is_active=True,
            is_active=True,
        )
        .select_related("offering", "offering__offering_department", "offering__offering_department__college")
        .prefetch_related(
            Prefetch("required_capabilities", to_attr="prefetched_required_capabilities"),
            Prefetch(
                "offering__section_links",
                queryset=models.OfferingSection.objects.select_related(
                    "section",
                    "section__program__department__college",
                    "program_subject",
                    "program_subject__authoritative_college",
                    "program_subject__authoritative_department",
                ),
                to_attr="prefetched_section_links",
            ),
            Prefetch(
                "offering__instructor_links",
                queryset=models.OfferingInstructor.objects.select_related("instructor"),
                to_attr="prefetched_instructor_links",
            ),
        )
        .order_by("stable_key")
    )
    if not meetings:
        issues.append(BuildIssue("NO_MEETINGS", "The revision has no active meeting requirements."))

    rooms = list(
        models.Room.objects.filter(is_active=True, campus=revision.term.campus)
        .select_related("laboratory_profile")
        .prefetch_related(
            Prefetch("capability_links", to_attr="prefetched_capability_links"),
            Prefetch(
                "authorizations",
                queryset=models.RoomAuthorization.objects.filter(revision=revision),
                to_attr="prefetched_authorizations",
            ),
        )
        .order_by("code")
    )
    if not rooms:
        issues.append(BuildIssue("NO_ROOMS", "The term campus has no active rooms."))

    reserved_blocks = list(
        revision.reserved_time_blocks.filter(is_active=True)
        .select_related(
            "college",
            "department",
            "program",
            "section",
            "policy_version",
        )
        .prefetch_related(
            Prefetch(
                "slot_links",
                queryset=models.ReservedTimeBlockSlot.objects.select_related("time_slot"),
                to_attr="prefetched_slot_links",
            )
        )
        .order_by("scope", "label", "pk")
    )
    for block in reserved_blocks:
        if not block.prefetched_slot_links:
            issues.append(
                BuildIssue(
                    "RESERVED_BLOCK_WITHOUT_SLOTS",
                    f"Reserved block {block.label!r} has no time atoms.",
                    "ReservedTimeBlock",
                    str(block.pk),
                )
            )
        if not block.policy_version.is_approved:
            issues.append(
                BuildIssue(
                    "UNAPPROVED_RESERVED_BLOCK_POLICY",
                    f"Reserved block {block.label!r} references an unapproved policy.",
                    "ReservedTimeBlock",
                    str(block.pk),
                )
            )

    room_capability_ids = {
        room.pk: {link.capability_id for link in room.prefetched_capability_links}
        for room in rooms
    }
    room_authorizations = {
        room.pk: list(room.prefetched_authorizations) for room in rooms
    }
    room_available = {}
    for room in rooms:
        profile = room_profiles.get(room.pk)
        if profile is None:
            issues.append(
                BuildIssue(
                    "MISSING_ROOM_AVAILABILITY_PROFILE",
                    f"Room {room.code} has no availability profile or full-availability acknowledgement.",
                    "Room",
                    str(room.pk),
                )
            )
            continue
        room_available[room.pk] = _available_atom_ids_for_room(profile, all_slot_ids)

    avoid_penalties: dict[tuple[int, int], int] = {}
    preferred_rewards: dict[tuple[int, int], int] = {}
    preferred_ceiling: dict[int, int] = {}
    instructor_available: dict[int, set[int]] = {}
    relevant_instructor_ids = {
        link.instructor_id
        for meeting in meetings
        for link in meeting.offering.prefetched_instructor_links
    }
    for instructor_id in relevant_instructor_ids:
        profile = instructor_profiles.get(instructor_id)
        if profile is None:
            issues.append(
                BuildIssue(
                    "MISSING_INSTRUCTOR_AVAILABILITY_PROFILE",
                    f"Instructor {instructor_id} has no availability profile or full-availability acknowledgement.",
                    "Instructor",
                    str(instructor_id),
                )
            )
            continue
        instructor_available[instructor_id] = _available_atom_ids_for_instructor(profile, all_slot_ids)
        if (
            profile.max_daily_teaching_atoms is None
            and not profile.acknowledge_no_daily_limit
            and not is_legacy_import
        ):
            issues.append(
                BuildIssue(
                    "MISSING_INSTRUCTOR_DAILY_LOAD_LIMIT",
                    (
                        f"Instructor {profile.instructor.employee_code} requires either a "
                        "positive maximum daily teaching-atom limit or an approved no-limit "
                        "acknowledgement."
                    ),
                    "Instructor",
                    str(instructor_id),
                )
            )
        if profile.daily_load_policy_version_id is None:
            if not is_legacy_import:
                issues.append(
                    BuildIssue(
                        "MISSING_INSTRUCTOR_DAILY_LOAD_POLICY",
                        f"Instructor {profile.instructor.employee_code} has no daily-load policy reference.",
                        "Instructor",
                        str(instructor_id),
                    )
                )
        elif not profile.daily_load_policy_version.is_approved:
            issues.append(
                BuildIssue(
                    "UNAPPROVED_INSTRUCTOR_DAILY_LOAD_POLICY",
                    f"Instructor {profile.instructor.employee_code} references an unapproved daily-load policy.",
                    "Instructor",
                    str(instructor_id),
                )
            )
        for preference in profile.prefetched_preferences:
            if preference.level == models.PreferenceLevel.AVOID:
                avoid_penalties[(instructor_id, preference.time_slot_id)] = preference.weight
            elif preference.level == models.PreferenceLevel.PREFERRED:
                preferred_rewards[(instructor_id, preference.time_slot_id)] = preference.weight
                preferred_ceiling[instructor_id] = max(
                    preferred_ceiling.get(instructor_id, 0), preference.weight
                )

    room_hard_rule_evidence = tuple(
        RoomEvidence(
            room_id=str(room.pk),
            room_kind=room.kind,
            available_atom_ids=tuple(
                sorted(atom_id(slot_id) for slot_id in room_available[room.pk])
            ),
            capability_ids=tuple(
                sorted(str(capability_id) for capability_id in room_capability_ids[room.pk])
            ),
            authorization_grants=tuple(
                RoomAuthorizationGrant(
                    classification=authorization.classification,
                    college_id=(
                        str(authorization.college_id)
                        if authorization.college_id is not None
                        else None
                    ),
                    department_id=(
                        str(authorization.department_id)
                        if authorization.department_id is not None
                        else None
                    ),
                )
                for authorization in room_authorizations[room.pk]
            ),
            has_laboratory_profile=hasattr(room, "laboratory_profile"),
        )
        for room in rooms
        if room.pk in room_available
    )
    instructor_hard_rule_evidence = tuple(
        InstructorEvidence(
            instructor_id=str(instructor_id),
            available_atom_ids=tuple(
                sorted(atom_id(slot_id) for slot_id in instructor_available[instructor_id])
            ),
            max_daily_teaching_atoms=instructor_profiles[instructor_id].max_daily_teaching_atoms,
            acknowledge_no_daily_limit=(
                instructor_profiles[instructor_id].acknowledge_no_daily_limit
            ),
            daily_load_policy_hash=(
                instructor_profiles[instructor_id].daily_load_policy_version.policy_hash
                if instructor_profiles[instructor_id].daily_load_policy_version_id
                else None
            ),
        )
        for instructor_id in sorted(relevant_instructor_ids)
        if instructor_id in instructor_available
    )
    reserved_block_evidence = tuple(
        {
            "block_id": str(block.pk),
            "label": block.label,
            "scope": block.scope,
            "scope_target_id": (
                str(block.scope_target.pk) if block.scope_target is not None else None
            ),
            "atom_ids": sorted(
                atom_id(link.time_slot_id) for link in block.prefetched_slot_links
            ),
            "policy_hash": block.policy_version.policy_hash,
        }
        for block in reserved_blocks
    )
    instructor_daily_load_evidence = tuple(
        {
            "instructor_id": evidence.instructor_id,
            "max_daily_teaching_atoms": evidence.max_daily_teaching_atoms,
            "acknowledge_no_daily_limit": evidence.acknowledge_no_daily_limit,
            "policy_hash": evidence.daily_load_policy_hash,
        }
        for evidence in instructor_hard_rule_evidence
    )
    referenced_policies = [block.policy_version for block in reserved_blocks]
    referenced_policies.extend(
        profile.daily_load_policy_version
        for profile in instructor_profiles.values()
        if profile.daily_load_policy_version_id is not None
    )
    rule_manifest, constraint_manifest_hash = _build_rule_manifest(
        revision,
        referenced_policies,
    )

    events: list[MeetingEvent] = []
    locks: list[Assignment] = []
    active_locks = {
        lock.meeting_requirement_id: lock
        for lock in models.LockedAssignment.objects.filter(
            meeting_requirement__offering__revision=revision,
            is_active=True,
        ).select_related("room", "start_time_slot")
    }

    # Window construction depends only on the day and meeting duration.  Build
    # each ordered sequence once, then reuse it for every room/event while
    # retaining the legacy day/start ordering used by candidate generation.
    windows_by_duration_day = {
        duration: {
            day: tuple(tuple(window) for window in _contiguous_windows(day_slots, duration))
            for day, day_slots in slots_by_day.items()
        }
        for duration in {meeting.duration_atoms for meeting in meetings}
    }

    for meeting in meetings:
        event_id = str(meeting.stable_key)
        section_links = list(meeting.offering.prefetched_section_links)
        instructor_links = list(meeting.offering.prefetched_instructor_links)
        unique_section_rows = {
            str(link.section_id): link.section for link in section_links
        }
        frozen_section_headcounts: dict[str, int] = {}
        for section_id, section in sorted(unique_section_rows.items()):
            enrollment = section.expected_enrollment
            if enrollment is None:
                if not is_legacy_import:
                    issues.append(
                        BuildIssue(
                            "MISSING_SECTION_EXPECTED_ENROLLMENT",
                            (
                                f"Section {section.code} attached to meeting {event_id} has no "
                                "expected enrollment; enter a value from 1 to 50."
                            ),
                            "Section",
                            section_id,
                        )
                    )
            elif not 1 <= enrollment <= 50:
                issues.append(
                    BuildIssue(
                        "SECTION_EXPECTED_ENROLLMENT_OUT_OF_RANGE",
                        (
                            f"Section {section.code} attached to meeting {event_id} has "
                            f"expected enrollment {enrollment}; the fixed range is 1 to 50."
                        ),
                        "Section",
                        section_id,
                    )
                )
            else:
                frozen_section_headcounts[section_id] = enrollment
        meeting_headcount = (
            sum(frozen_section_headcounts.values())
            if len(frozen_section_headcounts) == len(unique_section_rows)
            else None
        )
        if meeting_headcount is not None and meeting_headcount > 50:
            section_labels = ", ".join(
                f"{unique_section_rows[section_id].code}={count}"
                for section_id, count in sorted(frozen_section_headcounts.items())
            )
            issues.append(
                BuildIssue(
                    "MEETING_HEADCOUNT_EXCEEDS_FIXED_LIMIT",
                    (
                        f"Meeting {event_id} combines {meeting_headcount} students "
                        f"({section_labels}); the fixed maximum is 50."
                    ),
                    "MeetingRequirement",
                    event_id,
                )
            )
        applicable_reserved_blocks = [
            block
            for block in reserved_blocks
            if _reserved_block_applies(block, meeting, section_links)
        ]
        reserved_slot_ids = {
            link.time_slot_id
            for block in applicable_reserved_blocks
            for link in block.prefetched_slot_links
        }
        if not section_links:
            issues.append(
                BuildIssue("MEETING_WITHOUT_SECTION", f"{meeting} has no attached section.", "MeetingRequirement", event_id)
            )
        if not instructor_links:
            issues.append(
                BuildIssue(
                    "MEETING_WITHOUT_INSTRUCTOR",
                    f"{meeting} has no preassigned instructor.",
                    "MeetingRequirement",
                    event_id,
                )
            )
        for link in section_links:
            if not link.section.is_active:
                issues.append(
                    BuildIssue(
                        "INACTIVE_SECTION",
                        f"{meeting} is attached to inactive section {link.section.code}.",
                        "Section",
                        str(link.section_id),
                    )
                )
        for link in instructor_links:
            if not link.instructor.is_active:
                issues.append(
                    BuildIssue(
                        "INACTIVE_INSTRUCTOR",
                        f"{meeting} is assigned to inactive instructor {link.instructor.employee_code}.",
                        "Instructor",
                        str(link.instructor_id),
                    )
                )
        required_capabilities = {
            capability.pk for capability in meeting.prefetched_required_capabilities
        }
        candidates: list[CandidatePlacement] = []
        meeting_lock = active_locks.get(meeting.pk)

        for room in rooms:
            if room.pk not in room_available:
                continue
            if meeting.component == models.MeetingComponent.LABORATORY and (
                room.kind != models.RoomKind.LABORATORY
                or not hasattr(room, "laboratory_profile")
            ):
                continue
            if not required_capabilities.issubset(room_capability_ids[room.pk]):
                continue
            if any(
                not _authorization_matches(room_authorizations[room.pk], link, meeting.offering)
                for link in section_links
            ):
                continue
            for day in slots_by_day:
                for window in windows_by_duration_day[meeting.duration_atoms][day]:
                    window_ids = {slot.pk for slot in window}
                    if window_ids & reserved_slot_ids:
                        continue
                    if not window_ids.issubset(room_available[room.pk]):
                        continue
                    instructor_ids = [link.instructor_id for link in instructor_links]
                    if any(
                        instructor_id not in instructor_available
                        or not window_ids.issubset(instructor_available[instructor_id])
                        for instructor_id in instructor_ids
                    ):
                        continue
                    if meeting_lock and (
                        meeting_lock.room_id != room.pk
                        or meeting_lock.start_time_slot_id != window[0].pk
                    ):
                        continue
                    preference_penalty = sum(
                        avoid_penalties.get((instructor_id, slot.pk), 0)
                        + preferred_ceiling.get(instructor_id, 0)
                        - preferred_rewards.get((instructor_id, slot.pk), 0)
                        for instructor_id in instructor_ids
                        for slot in window
                    )
                    candidate_id = f"{event_id}:{room.pk}:{window[0].pk}"
                    candidates.append(
                        CandidatePlacement(
                            candidate_id=candidate_id,
                            room_id=str(room.pk),
                            day_id=f"day:{day}",
                            start_atom_id=atom_id(window[0].pk),
                            occupied_atom_ids=tuple(atom_id(slot.pk) for slot in window),
                            preference_penalty=preference_penalty,
                            eligibility_metadata=(
                                ("room_code", room.code),
                                ("start_slot_id", str(window[0].pk)),
                            ),
                        )
                    )

        if not candidates:
            issues.append(
                BuildIssue(
                    "EMPTY_CANDIDATE_DOMAIN",
                    f"{meeting} has no legal room/time placement.",
                    "MeetingRequirement",
                    event_id,
                )
            )
            continue
        event = MeetingEvent(
            event_id=event_id,
            duration_atoms=meeting.duration_atoms,
            section_ids=tuple(sorted(unique_section_rows)),
            instructor_ids=tuple(sorted(str(link.instructor_id) for link in instructor_links)),
            candidates=tuple(sorted(candidates, key=lambda item: item.candidate_id)),
            distinct_day_group=(
                f"{meeting.offering.external_key}:{meeting.distinct_day_group}"
                if meeting.distinct_day_group
                else None
            ),
            offering_id=meeting.offering.external_key,
            required_capability_ids=tuple(
                sorted(str(capability_id) for capability_id in required_capabilities)
            ),
            requires_laboratory_room=(
                meeting.component == models.MeetingComponent.LABORATORY
            ),
            authorization_requirements=tuple(
                sorted(
                    (
                        _authorization_requirement(link, meeting.offering)
                        for link in section_links
                    ),
                    key=lambda item: item.section_id,
                )
            ),
            section_headcounts=tuple(sorted(frozen_section_headcounts.items())),
            meeting_headcount=meeting_headcount,
            fixed_student_limit=50,
            reserved_atom_ids=tuple(
                sorted(atom_id(slot_id) for slot_id in reserved_slot_ids)
            ),
        )
        events.append(event)
        if meeting_lock:
            locks.append(Assignment(event_id=event_id, candidate_id=event.candidates[0].candidate_id))

    if issues:
        raise ProblemBuildError(issues)

    time_atoms = tuple(
        TimeAtom(
            atom_id=atom_id(slot.pk),
            day_id=f"day:{slot.day}",
            day_index=slot.day,
            order=slot.sequence,
        )
        for slot in slots
    )
    problem = ProblemInstance(
        schema_version="1.1" if is_legacy_import else "1.2",
        term_revision_id=str(revision.pk),
        time_atoms=time_atoms,
        events=tuple(events),
        objective_profile=_domain_objective_profile(objective_profile),
        room_evidence=room_hard_rule_evidence,
        instructor_evidence=instructor_hard_rule_evidence,
        locked_assignments=tuple(locks),
        metadata=(
            ("academic_year", revision.term.academic_year),
            ("campus", revision.term.campus),
            ("constraint_manifest_hash", constraint_manifest_hash),
            ("fixed_student_limit", "50"),
            ("objective_profile_hash", objective_profile.profile_hash),
            ("revision_number", str(revision.revision_number)),
            ("semester", revision.term.semester),
        ),
    )
    lock_conflict_codes = {
        ViolationCode.ROOM_CONFLICT,
        ViolationCode.INSTRUCTOR_CONFLICT,
        ViolationCode.SECTION_CONFLICT,
        ViolationCode.DISTINCT_DAY_CONFLICT,
    }
    for violation in validate_schedule(problem, tuple(locks)).violations:
        if violation.code in lock_conflict_codes:
            issues.append(
                BuildIssue(
                    "CONFLICTING_LOCKS",
                    violation.message,
                    "LockedAssignment",
                    ",".join(violation.event_ids),
                )
            )
    if issues:
        raise ProblemBuildError(issues)
    section_headcounts = {
        section_id: count
        for event in problem.events
        for section_id, count in event.section_headcounts
    }
    meeting_headcounts = {
        event.event_id: event.meeting_headcount
        for event in problem.events
        if event.meeting_headcount is not None
    }
    return ProblemBuildResult(
        problem=problem,
        preprocessing_seconds=perf_counter() - started,
        candidate_count=sum(len(event.candidates) for event in problem.events),
        constraint_manifest_hash=constraint_manifest_hash,
        rule_manifest=rule_manifest,
        section_headcounts=section_headcounts,
        meeting_headcounts=meeting_headcounts,
        reserved_block_evidence=reserved_block_evidence,
        instructor_daily_load_evidence=instructor_daily_load_evidence,
        instance_characteristics=_instance_characteristics(problem),
    )


@transaction.atomic
def build_and_store_snapshot(
    revision: models.TermDatasetRevision,
    objective_profile: models.ObjectiveProfile,
    created_by: models.User,
) -> tuple[models.ProblemSnapshot, ProblemBuildResult]:
    result = build_problem(revision, objective_profile)
    candidate_map = {
        event.event_id: [candidate.to_dict() for candidate in event.candidates]
        for event in result.problem.events
    }
    values = {
        "revision": revision,
        "objective_profile": objective_profile,
        "schema_version": result.problem.schema_version,
        "input_data": result.problem.to_dict(),
        "candidate_map": candidate_map,
        "constraint_manifest_hash": result.constraint_manifest_hash,
        "rule_manifest": result.rule_manifest,
        "fixed_student_limit": 50,
        "section_headcounts": result.section_headcounts,
        "meeting_headcounts": result.meeting_headcounts,
        "reserved_block_evidence": list(result.reserved_block_evidence),
        "instructor_daily_load_evidence": list(result.instructor_daily_load_evidence),
        "instance_characteristics": result.instance_characteristics,
        "event_count": len(result.problem.events),
        "candidate_count": result.candidate_count,
        "preprocessing_seconds": result.preprocessing_seconds,
        "created_by": created_by,
    }
    probe = models.ProblemSnapshot(**values)
    snapshot_hash = models.canonical_sha256(probe.hash_payload())
    snapshot, created = models.ProblemSnapshot.objects.get_or_create(
        snapshot_hash=snapshot_hash,
        defaults=values,
    )
    models.AuditLog.objects.create(
        actor=created_by,
        action="problem_snapshot.created" if created else "problem_snapshot.reused",
        entity_type="ProblemSnapshot",
        entity_id=str(snapshot.pk),
        details={
            "revision_id": revision.pk,
            "objective_profile_id": objective_profile.pk,
            "snapshot_hash": snapshot.snapshot_hash,
            "event_count": snapshot.event_count,
            "candidate_count": snapshot.candidate_count,
        },
    )
    return snapshot, result


def load_problem(snapshot: models.ProblemSnapshot) -> ProblemInstance:
    return ProblemInstance.from_dict(snapshot.input_data)
