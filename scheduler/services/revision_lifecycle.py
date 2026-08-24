"""Validation and freezing for semester revisions edited after cloning."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from scheduler import models
from scheduler.services.problem_builder import ProblemBuildError, build_problem


def _ensure_central(actor: models.User) -> None:
    if not actor.is_active or (not actor.is_superuser and actor.role not in {
        models.UserRole.SYSTEM_ADMIN,
        models.UserRole.CENTRAL_SCHEDULER,
    }):
        raise PermissionDenied("Only a central scheduler may finalize a term revision.")


@transaction.atomic
def validate_and_commit_revision(
    revision: models.TermDatasetRevision,
    objective_profile: models.ObjectiveProfile,
    actor: models.User,
) -> models.TermDatasetRevision:
    """Preflight an editable clone and atomically freeze it for scheduling."""

    _ensure_central(actor)
    locked = models.TermDatasetRevision.objects.select_for_update().select_related("term").get(
        pk=revision.pk
    )
    if locked.status != models.RevisionStatus.DRAFT:
        raise ValidationError("Only a DRAFT cloned revision can be finalized.")
    if not objective_profile.is_approved:
        raise ValidationError("Finalize with an approved objective profile.")
    if objective_profile.term_id and objective_profile.term_id != locked.term_id:
        raise ValidationError("The objective profile belongs to a different academic term.")

    # build_problem accepts VALIDATED inputs. The surrounding transaction rolls
    # this transition back if any availability, authorization, lock, or empty-
    # domain preflight issue is raised.
    locked.status = models.RevisionStatus.VALIDATED
    locked.save(update_fields=["status", "updated_at"])
    try:
        build_result = build_problem(locked, objective_profile)
    except ProblemBuildError:
        raise

    problem_payload = build_result.problem.to_dict()
    # A term revision hash represents academic/resource input. Objective policy
    # and operational locks have their own hashes and snapshot provenance.
    problem_payload.pop("objective_profile", None)
    problem_payload.pop("locked_assignments", None)
    locked.content_hash = models.canonical_sha256(problem_payload)
    locked.status = models.RevisionStatus.COMMITTED
    locked.committed_at = timezone.now()
    locked.save(
        update_fields=["content_hash", "status", "committed_at", "updated_at"]
    )
    models.AuditLog.objects.create(
        actor=actor,
        action="term.revision_finalized",
        entity_type="TermDatasetRevision",
        entity_id=str(locked.pk),
        details={
            "content_hash": locked.content_hash,
            "objective_profile_id": objective_profile.pk,
            "event_count": len(build_result.problem.events),
            "candidate_count": build_result.candidate_count,
        },
    )
    return locked


__all__ = ["validate_and_commit_revision"]
