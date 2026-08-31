from __future__ import annotations

from django.db.models import Count, IntegerField, OuterRef, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce

from scheduler import models


def _related_count(model, revision_lookup: str):  # type: ignore[no-untyped-def]
    return Coalesce(
        Subquery(
            model.objects.filter(**{revision_lookup: OuterRef("pk")})
            .order_by()
            .values(revision_lookup)
            .annotate(total=Count("pk"))
            .values("total")[:1],
            output_field=IntegerField(),
        ),
        Value(0),
    )


def with_dataset_counts(queryset: QuerySet) -> QuerySet:
    """Add independent revision counts without a multi-relation join product."""

    return queryset.annotate(
        section_count=_related_count(models.Section, "revision_id"),
        meeting_count=_related_count(
            models.MeetingRequirement,
            "offering__revision_id",
        ),
        room_count=_related_count(models.RoomAvailabilityProfile, "revision_id"),
        instructor_count=_related_count(
            models.InstructorAvailabilityProfile,
            "revision_id",
        ),
    )

