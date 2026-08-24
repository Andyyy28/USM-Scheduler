"""Deterministic, nested scaling projections of immutable problem snapshots."""

from __future__ import annotations

import copy
import hashlib
import math
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from django.db import transaction
from django.db.models import Prefetch

from scheduler import models

SCALING_PERCENTAGES = (25, 50, 75, 100)
DEFAULT_SCALING_SEED = 20260824
SCALING_PROTOCOL_VERSION = "1.0"
_SCALING_METADATA_KEYS = {
    "scaling_actual_event_percentage",
    "scaling_actual_offering_percentage",
    "scaling_percentage",
    "scaling_protocol_version",
    "scaling_seed",
    "scaling_selection_hash",
    "scaling_source_snapshot_hash",
}


@dataclass(frozen=True, slots=True)
class ScalingLevelPlan:
    percentage: int
    target_offering_count: int
    target_event_count: int
    selected_event_count: int
    actual_event_percentage: float
    actual_offering_percentage: float
    selected_offering_count: int
    selected_offering_ids: tuple[str, ...]
    selected_event_ids: tuple[str, ...]
    selected_lock_count: int
    context_counts: tuple[tuple[str, int], ...]
    selection_hash: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["selected_offering_ids"] = list(self.selected_offering_ids)
        value["selected_event_ids"] = list(self.selected_event_ids)
        value["context_counts"] = dict(self.context_counts)
        return value


@dataclass(frozen=True, slots=True)
class ScalingPlan:
    source_snapshot_id: int
    source_snapshot_hash: str
    seed: int
    full_event_count: int
    full_offering_count: int
    applicable_context_counts: tuple[tuple[str, int], ...]
    offering_order: tuple[str, ...]
    levels: tuple[ScalingLevelPlan, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": SCALING_PROTOCOL_VERSION,
            "source_snapshot_id": self.source_snapshot_id,
            "source_snapshot_hash": self.source_snapshot_hash,
            "seed": self.seed,
            "full_event_count": self.full_event_count,
            "full_offering_count": self.full_offering_count,
            "applicable_context_counts": dict(self.applicable_context_counts),
            "offering_order": list(self.offering_order),
            "levels": [level.to_dict() for level in self.levels],
        }


def plan_scaling_snapshots(
    full_snapshot: models.ProblemSnapshot,
    *,
    seed: int = DEFAULT_SCALING_SEED,
) -> ScalingPlan:
    """Plan nested 25/50/75/100 projections without writing database rows."""

    if type(seed) is not int or seed < 0:
        raise ValueError("scaling seed must be a non-negative integer")
    events = tuple(full_snapshot.input_data.get("events", ()))
    if not events:
        raise ValueError("the full snapshot contains no events")
    event_offerings = _event_offering_map(full_snapshot, events)
    contexts = _offering_contexts(full_snapshot, set(event_offerings.values()))
    events_by_offering: dict[str, list[str]] = {
        offering_id: [] for offering_id in contexts
    }
    for event in events:
        event_id = _event_id(event)
        events_by_offering[event_offerings[event_id]].append(event_id)
    offering_order = _multilabel_stratified_order(contexts, seed)
    full_event_count = len(events)
    full_context_counts: Counter[str] = Counter(
        context for offering_contexts in contexts.values() for context in offering_contexts
    )
    locked_event_ids = {
        str(lock.get("event_id"))
        for lock in full_snapshot.input_data.get("locked_assignments", ())
    }

    levels: list[ScalingLevelPlan] = []
    previous_offering_count = 0
    for percentage in SCALING_PERCENTAGES:
        target_offering_count = math.ceil(len(offering_order) * percentage / 100)
        target_event_count = math.ceil(full_event_count * percentage / 100)
        selected_offerings = list(offering_order[:target_offering_count])
        if percentage == 100:
            selected_offerings = list(offering_order)
        if len(selected_offerings) < previous_offering_count:  # pragma: no cover - invariant guard
            raise AssertionError("scaling levels must be nested")
        previous_offering_count = len(selected_offerings)
        selected_set = set(selected_offerings)
        selected_event_ids = tuple(
            _event_id(event)
            for event in events
            if event_offerings[_event_id(event)] in selected_set
        )
        selected_context_counts: Counter[str] = Counter(
            context
            for offering_id in selected_offerings
            for context in contexts[offering_id]
        )
        selection_hash = models.canonical_sha256(
            {
                "protocol_version": SCALING_PROTOCOL_VERSION,
                "source_snapshot_hash": full_snapshot.snapshot_hash,
                "seed": seed,
                "percentage": percentage,
                "selected_offering_ids": selected_offerings,
                "selected_event_ids": selected_event_ids,
            }
        )
        levels.append(
            ScalingLevelPlan(
                percentage=percentage,
                target_offering_count=target_offering_count,
                target_event_count=target_event_count,
                selected_event_count=len(selected_event_ids),
                actual_event_percentage=round(
                    len(selected_event_ids) * 100 / full_event_count, 6
                ),
                actual_offering_percentage=round(
                    len(selected_offerings) * 100 / len(offering_order), 6
                ),
                selected_offering_count=len(selected_offerings),
                selected_offering_ids=tuple(selected_offerings),
                selected_event_ids=selected_event_ids,
                selected_lock_count=len(set(selected_event_ids) & locked_event_ids),
                context_counts=tuple(sorted(selected_context_counts.items())),
                selection_hash=selection_hash,
            )
        )
    return ScalingPlan(
        source_snapshot_id=full_snapshot.pk,
        source_snapshot_hash=full_snapshot.snapshot_hash,
        seed=seed,
        full_event_count=full_event_count,
        full_offering_count=len(offering_order),
        applicable_context_counts=tuple(sorted(full_context_counts.items())),
        offering_order=offering_order,
        levels=tuple(levels),
    )


@transaction.atomic
def create_scaling_snapshots(
    full_snapshot: models.ProblemSnapshot,
    created_by: models.User,
    *,
    seed: int = DEFAULT_SCALING_SEED,
) -> dict[int, models.ProblemSnapshot]:
    """Idempotently persist the planned projections; 100% is the source row."""

    if not created_by.is_active or (not created_by.is_superuser and created_by.role not in {
        models.UserRole.SYSTEM_ADMIN,
        models.UserRole.CENTRAL_SCHEDULER,
    }):
        raise ValueError("Only a central scheduler may create research scaling snapshots.")
    plan = plan_scaling_snapshots(full_snapshot, seed=seed)
    snapshots: dict[int, models.ProblemSnapshot] = {}
    for level in plan.levels:
        if level.percentage == 100:
            snapshots[level.percentage] = full_snapshot
            continue
        input_data, candidate_map = _project_snapshot_payload(full_snapshot, level, seed)
        values = {
            "revision": full_snapshot.revision,
            "objective_profile": full_snapshot.objective_profile,
            "schema_version": full_snapshot.schema_version,
            "input_data": input_data,
            "candidate_map": candidate_map,
            "event_count": level.selected_event_count,
            "candidate_count": sum(len(candidates) for candidates in candidate_map.values()),
            "preprocessing_seconds": 0.0,
            "created_by": created_by,
        }
        probe = models.ProblemSnapshot(**values)
        snapshot_hash = models.canonical_sha256(probe.hash_payload())
        snapshot, _ = models.ProblemSnapshot.objects.get_or_create(
            snapshot_hash=snapshot_hash,
            defaults=values,
        )
        snapshots[level.percentage] = snapshot
    models.AuditLog.objects.create(
        actor=created_by,
        action="problem_snapshot.scaling_created",
        entity_type="ProblemSnapshot",
        entity_id=str(full_snapshot.pk),
        details={
            "source_snapshot_hash": full_snapshot.snapshot_hash,
            "seed": seed,
            "snapshots": {
                str(percentage): snapshot.snapshot_hash
                for percentage, snapshot in sorted(snapshots.items())
            },
        },
    )
    return snapshots


def _event_offering_map(
    full_snapshot: models.ProblemSnapshot, events: tuple[dict[str, Any], ...]
) -> dict[str, str]:
    missing_ids = {_event_id(event) for event in events if not event.get("offering_id")}
    meeting_offerings = {
        str(stable_key): external_key
        for stable_key, external_key in models.MeetingRequirement.objects.filter(
            offering__revision=full_snapshot.revision,
            stable_key__in=missing_ids,
        ).values_list("stable_key", "offering__external_key")
    }
    result: dict[str, str] = {}
    for event in events:
        event_id = _event_id(event)
        offering_id = event.get("offering_id") or meeting_offerings.get(event_id)
        if not offering_id:
            raise ValueError(f"event {event_id!r} cannot be mapped to an active offering")
        result[event_id] = str(offering_id)
    return result


def _offering_contexts(
    full_snapshot: models.ProblemSnapshot, offering_ids: set[str]
) -> dict[str, tuple[str, ...]]:
    section_links = models.OfferingSection.objects.select_related("program_subject")
    offerings = (
        models.CourseOffering.objects.filter(
            revision=full_snapshot.revision,
            is_active=True,
            external_key__in=offering_ids,
        )
        .prefetch_related(Prefetch("section_links", queryset=section_links))
        .order_by("external_key")
    )
    contexts: dict[str, tuple[str, ...]] = {}
    for offering in offerings:
        labels = {
            f"{link.program_subject.authoritative_college_id}:{link.program_subject.classification}"
            for link in offering.section_links.all()
        }
        if not labels:
            raise ValueError(f"active offering {offering.external_key!r} has no contextual strata")
        contexts[offering.external_key] = tuple(sorted(labels))
    missing = sorted(offering_ids - set(contexts))
    if missing:
        raise ValueError(
            "snapshot events reference inactive or missing offerings: " + ", ".join(missing)
        )
    return contexts


def _multilabel_stratified_order(
    contexts: dict[str, tuple[str, ...]], seed: int
) -> tuple[str, ...]:
    """Greedily minimize proportional error over every applicable context."""

    total = len(contexts)
    frequencies: Counter[str] = Counter(
        context for labels in contexts.values() for context in labels
    )
    selected_counts: Counter[str] = Counter()
    remaining = set(contexts)
    order: list[str] = []
    stable_ranks = {
        offering_id: hashlib.sha256(
            f"{seed}\0{offering_id}".encode()
        ).hexdigest()
        for offering_id in contexts
    }
    while remaining:
        selected_total = len(order) + 1

        def proportional_error_delta(
            offering_id: str, selected_total: int = selected_total
        ) -> tuple[float, str, str]:
            delta = 0.0
            for context in contexts[offering_id]:
                target = selected_total * frequencies[context] / total
                current_error = selected_counts[context] - target
                delta += ((current_error + 1) ** 2 - current_error**2) / frequencies[context]
            return (round(delta, 15), stable_ranks[offering_id], offering_id)

        selected = min(remaining, key=proportional_error_delta)
        order.append(selected)
        selected_counts.update(contexts[selected])
        remaining.remove(selected)
    return tuple(order)


def _project_snapshot_payload(
    full_snapshot: models.ProblemSnapshot,
    level: ScalingLevelPlan,
    seed: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected_event_ids = set(level.selected_event_ids)
    input_data = copy.deepcopy(full_snapshot.input_data)
    input_data["events"] = [
        event
        for event in input_data.get("events", ())
        if _event_id(event) in selected_event_ids
    ]
    input_data["locked_assignments"] = [
        lock
        for lock in input_data.get("locked_assignments", ())
        if str(lock.get("event_id")) in selected_event_ids
    ]
    metadata = [
        list(item)
        for item in input_data.get("metadata", ())
        if item and str(item[0]) not in _SCALING_METADATA_KEYS
    ]
    metadata.extend(
        [
            ["scaling_actual_event_percentage", str(level.actual_event_percentage)],
            [
                "scaling_actual_offering_percentage",
                str(level.actual_offering_percentage),
            ],
            ["scaling_percentage", str(level.percentage)],
            ["scaling_protocol_version", SCALING_PROTOCOL_VERSION],
            ["scaling_seed", str(seed)],
            ["scaling_selection_hash", level.selection_hash],
            ["scaling_source_snapshot_hash", full_snapshot.snapshot_hash],
        ]
    )
    input_data["metadata"] = sorted(metadata, key=lambda item: item[0])
    candidate_map = {
        event_id: copy.deepcopy(candidates)
        for event_id, candidates in full_snapshot.candidate_map.items()
        if event_id in selected_event_ids
    }
    if set(candidate_map) != selected_event_ids:
        missing = sorted(selected_event_ids - set(candidate_map))
        raise ValueError("candidate map is missing selected events: " + ", ".join(missing))
    return input_data, candidate_map


def _event_id(event: dict[str, Any]) -> str:
    event_id = event.get("event_id", event.get("id"))
    if event_id in (None, ""):
        raise ValueError("every snapshot event must have an event_id")
    return str(event_id)
