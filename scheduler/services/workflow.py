"""Schedule validation, review, approval, locking, and cancellation workflow."""

from __future__ import annotations

from collections.abc import Iterable

from celery import current_app
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from scheduler import models
from scheduler.domain import Assignment, score_schedule, validate_schedule
from scheduler.services.problem_builder import load_problem


def _is_central(user: models.User) -> bool:
    return bool(user.is_active) and (user.is_superuser or user.role in {
        models.UserRole.SYSTEM_ADMIN,
        models.UserRole.CENTRAL_SCHEDULER,
    })


def audit(
    *,
    actor: models.User | None,
    action: str,
    entity: object,
    details: dict | None = None,
    ip_address: str | None = None,
) -> models.AuditLog:
    return models.AuditLog.objects.create(
        actor=actor,
        action=action,
        entity_type=type(entity).__name__,
        entity_id=str(getattr(entity, "pk", "")),
        details=details or {},
        ip_address=ip_address,
    )


@transaction.atomic
def validate_schedule_version(
    schedule: models.ScheduleVersion,
    *,
    actor: models.User | None = None,
) -> models.ValidationResult:
    # Snapshot is nullable for imported/manual drafts. Joining it here would
    # make PostgreSQL apply ``FOR UPDATE`` to the nullable side of an outer
    # join, which PostgreSQL does not support. Lock the schedule row first and
    # load its snapshot lazily inside this transaction.
    schedule = models.ScheduleVersion.objects.select_for_update().get(pk=schedule.pk)
    if not schedule.snapshot_id:
        raise ValidationError("A schedule requires a canonical problem snapshot for independent validation.")
    problem = load_problem(schedule.snapshot)
    assignments = _persisted_assignments(schedule, problem)
    report = validate_schedule(problem, assignments)
    objective = score_schedule(problem, assignments) if report.feasible else None
    result, _ = models.ValidationResult.objects.update_or_create(
        schedule_version=schedule,
        defaults={
            "is_feasible": report.feasible,
            "hard_violation_count": report.hard_violation_count,
            "violations": report.to_dict(),
            "raw_soft_penalty": objective.weighted_total if objective else 0,
            "objective_breakdown": objective.to_dict() if objective else {},
            "normalized_quality_score": objective.quality_score if objective else None,
            "validator_version": "1.0",
            "validated_at": timezone.now(),
        },
    )
    audit(
        actor=actor,
        action="schedule.validated",
        entity=schedule,
        details={"feasible": report.feasible, "hard_violation_count": report.hard_violation_count},
    )
    return result


def _persisted_assignments(schedule: models.ScheduleVersion, problem) -> tuple[Assignment, ...]:
    """Resolve the persisted room/start fields against the frozen legal domains.

    Validation must not trust the solver's cached ``placement_data`` because a
    later administrative edit could otherwise leave a stale legal candidate ID
    attached to different physical room/time fields.
    """

    assignments: list[Assignment] = []
    for row in schedule.assignments.select_related("meeting_requirement"):
        event_id = str(row.meeting_requirement.stable_key)
        event = problem.event_map.get(event_id)
        candidate_id = f"persisted:{row.room_id}:{row.start_time_slot_id}"
        if event is not None:
            expected_start_atom = f"slot:{row.start_time_slot_id}"
            candidate = next(
                (
                    item
                    for item in event.candidates
                    if item.room_id == str(row.room_id)
                    and item.start_atom_id == expected_start_atom
                ),
                None,
            )
            if candidate is not None:
                candidate_id = candidate.candidate_id
        assignments.append(Assignment(event_id=event_id, candidate_id=candidate_id))
    return tuple(assignments)


@transaction.atomic
def submit_for_review(schedule: models.ScheduleVersion, actor: models.User) -> models.ScheduleVersion:
    if not _is_central(actor):
        raise PermissionDenied("Only a central scheduler may submit a schedule for review.")
    schedule = models.ScheduleVersion.objects.select_for_update().get(pk=schedule.pk)
    validation = validate_schedule_version(schedule, actor=actor)
    if not validation.is_feasible:
        raise ValidationError("Only a zero-hard-violation schedule can enter review.")
    if schedule.status != models.ScheduleStatus.DRAFT:
        raise ValidationError("Only a draft schedule can enter review.")
    schedule.status = models.ScheduleStatus.UNDER_REVIEW
    schedule.save(update_fields=["status", "updated_at"])
    audit(actor=actor, action="schedule.submitted_for_review", entity=schedule)
    return schedule


@transaction.atomic
def review_schedule(
    *,
    schedule: models.ScheduleVersion,
    college: models.College,
    reviewer: models.User,
    status: str,
    comment: str,
) -> models.ScheduleReview:
    schedule = models.ScheduleVersion.objects.select_for_update().get(pk=schedule.pk)
    if schedule.status != models.ScheduleStatus.UNDER_REVIEW:
        raise ValidationError("Reviews are accepted only while the schedule is under review.")
    if not reviewer.is_active:
        raise PermissionDenied("Inactive accounts cannot review schedules.")
    is_scoped_reviewer = reviewer.role == models.UserRole.COLLEGE_REVIEWER and reviewer.college_scopes.filter(
        college=college
    ).exists()
    if reviewer.role == models.UserRole.COLLEGE_REVIEWER and not is_scoped_reviewer:
        raise PermissionDenied("The reviewer is not scoped to this college.")
    if not _is_central(reviewer) and reviewer.role != models.UserRole.COLLEGE_REVIEWER:
        raise PermissionDenied("This user cannot review schedules.")
    if status not in models.ReviewStatus.values:
        raise ValidationError("Unsupported review status.")
    if status in {models.ReviewStatus.ENDORSED, models.ReviewStatus.CHANGES_REQUESTED} and not is_scoped_reviewer:
        raise PermissionDenied("Only a reviewer scoped to this college may record its decision.")
    review = models.ScheduleReview(
        schedule=schedule,
        college=college,
        reviewer=reviewer,
        status=status,
        comment=comment,
    )
    review.full_clean()
    review.save()
    audit(
        actor=reviewer,
        action=f"schedule.review.{status.lower()}",
        entity=schedule,
        details={"college_id": college.pk, "review_id": review.pk},
    )
    return review


def required_review_college_ids(schedule: models.ScheduleVersion) -> set[int]:
    return set(
        models.College.objects.filter(
            departments__programs__sections__offering_links__offering__meeting_requirements__schedule_assignments__schedule=schedule
        ).values_list("pk", flat=True)
    )


@transaction.atomic
def approve_schedule(
    schedule: models.ScheduleVersion,
    actor: models.User,
    *,
    notes: str = "",
) -> models.ScheduleApproval:
    if not _is_central(actor):
        raise PermissionDenied("Only a central scheduler may approve schedules.")
    schedule = models.ScheduleVersion.objects.select_for_update().get(pk=schedule.pk)
    if schedule.status != models.ScheduleStatus.UNDER_REVIEW:
        raise ValidationError("The schedule must be under review before approval.")
    validation = validate_schedule_version(schedule, actor=actor)
    if not validation.is_feasible:
        raise ValidationError("The schedule must pass independent validation before approval.")
    required = required_review_college_ids(schedule)
    latest_decisions: dict[int, str] = {}
    for college_id, decision in schedule.reviews.filter(
        status__in={models.ReviewStatus.ENDORSED, models.ReviewStatus.CHANGES_REQUESTED}
    ).order_by("college_id", "created_at", "pk").values_list("college_id", "status"):
        latest_decisions[college_id] = decision
    endorsed = {
        college_id
        for college_id, decision in latest_decisions.items()
        if decision == models.ReviewStatus.ENDORSED
    }
    missing = required - endorsed
    if missing:
        codes = list(models.College.objects.filter(pk__in=missing).values_list("code", flat=True))
        raise ValidationError(f"Missing college endorsement(s): {', '.join(sorted(codes))}")

    current_approved = models.ScheduleVersion.objects.select_for_update().filter(
        term=schedule.term,
        status=models.ScheduleStatus.APPROVED,
    )
    for previous in current_approved:
        previous.status = models.ScheduleStatus.ARCHIVED
        previous.save(update_fields=["status", "updated_at"])
    schedule.status = models.ScheduleStatus.APPROVED
    schedule.finalized_at = timezone.now()
    schedule._allow_approval_transition = True
    schedule.save(update_fields=["status", "finalized_at", "updated_at"])
    approval = models.ScheduleApproval(
        schedule=schedule,
        approved_by=actor,
        notes=notes,
    )
    approval.full_clean()
    approval.save()
    audit(actor=actor, action="schedule.approved", entity=schedule, details={"approval_id": approval.pk})
    return approval


@transaction.atomic
def lock_schedule_assignments(
    *,
    schedule: models.ScheduleVersion,
    assignment_ids: Iterable[int],
    actor: models.User,
    reason: str,
) -> list[models.LockedAssignment]:
    if not _is_central(actor):
        raise PermissionDenied("Only a central scheduler may lock assignments.")
    if schedule.status not in {models.ScheduleStatus.UNDER_REVIEW, models.ScheduleStatus.APPROVED}:
        raise ValidationError("Only reviewed or approved schedule assignments may be locked.")
    assignments = list(
        schedule.assignments.filter(pk__in=list(assignment_ids)).select_related(
            "meeting_requirement", "room", "start_time_slot"
        )
    )
    if not assignments:
        raise ValidationError("No schedule assignments were selected.")
    created: list[models.LockedAssignment] = []
    for assignment in assignments:
        lock, _ = models.LockedAssignment.objects.update_or_create(
            meeting_requirement=assignment.meeting_requirement,
            is_active=True,
            defaults={
                "room": assignment.room,
                "start_time_slot": assignment.start_time_slot,
                "source_schedule": schedule,
                "locked_by": actor,
                "reason": reason,
            },
        )
        created.append(lock)
    audit(
        actor=actor,
        action="schedule.assignments_locked",
        entity=schedule,
        details={"assignment_ids": [assignment.pk for assignment in assignments]},
    )
    return created


@transaction.atomic
def cancel_run(run: models.ScheduleRun, actor: models.User) -> models.ScheduleRun:
    if not _is_central(actor) and run.requested_by_id != actor.pk:
        raise PermissionDenied("This user cannot cancel the run.")
    run = models.ScheduleRun.objects.select_for_update().get(pk=run.pk)
    if run.is_terminal:
        return run
    revoke_error = ""
    if run.task_id:
        try:
            if run.status == models.RunStatus.RUNNING:
                # The deployment dedicates a single-concurrency prefork worker to
                # solver tasks, so terminating this task cannot kill a web process
                # or another benchmark run. Celery replaces the worker child.
                current_app.control.revoke(run.task_id, terminate=True, signal="SIGTERM")
            else:
                current_app.control.revoke(run.task_id, terminate=False)
        except Exception as exc:  # pragma: no cover - transport-specific
            # The database cancellation remains authoritative: late workers see
            # the terminal row and cannot persist solver output over it.
            revoke_error = type(exc).__name__
    run.status = models.RunStatus.CANCELLED
    run.finished_at = timezone.now()
    run.heartbeat_at = run.finished_at
    run.lease_expires_at = None
    run.stopping_reason = "Cancelled by user"
    run.failure_category = models.FailureCategory.USER_CANCELLATION
    run.failure_classified_by = actor
    run.failure_classified_at = run.finished_at
    run.save(
        update_fields=[
            "status",
            "finished_at",
            "heartbeat_at",
            "lease_expires_at",
            "stopping_reason",
            "failure_category",
            "failure_classified_by",
            "failure_classified_at",
            "updated_at",
        ]
    )
    audit(
        actor=actor,
        action="run.cancelled",
        entity=run,
        details={"task_revoke_error_type": revoke_error or None},
    )
    if run.experiment_batch_id:
        # A revoked queued task will never enter the Celery task's finalizer,
        # so refresh its parent lifecycle as part of the same durable change.
        from scheduler.services.runs import refresh_run_containers

        refresh_run_containers(run.pk)
    return run
