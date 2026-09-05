"""Secondary scheduling evidence derived from frozen, independently valid results.

Room-time utilization concerns scheduling periods only. Placement diversity is
the mean normalized Hamming distance to other eligible feasible schedules from
the same algorithm and frozen problem; a singleton has no diversity estimate.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from statistics import mean
from typing import Any

_TERMINAL = {"FEASIBLE", "OPTIMAL", "INFEASIBLE", "NO_SOLUTION", "TIMEOUT", "FAILED", "CANCELLED"}
_EXCLUDED_FAILURES = {"INFRASTRUCTURE", "USER_CANCELLATION", "UNCLASSIFIED"}
_DIAGNOSTIC_METRICS = (
    "evaluated_chromosomes", "generations", "duplicates_suppressed", "mutation_rate",
    "mutation_operations", "mutated_offspring", "repair_calls", "repair_needed",
    "repair_iterations", "repair_candidate_evaluations", "repair_improvements",
    "repair_successes", "repair_failures", "repair_deadline_skips", "stagnation_generations",
    "branches", "conflicts", "model_constraint_count", "model_variable_count",
    "best_objective_bound", "relative_gap",
)


def secondary_trial_metadata(runs: Sequence[Any], *, formal: bool) -> dict[int, dict[str, Any]]:
    """Return safe scalar metadata without publishing raw placement identifiers."""

    output: dict[int, dict[str, Any]] = {}
    peer_groups: dict[tuple[str, str], list[tuple[int, dict[str, str]]]] = defaultdict(list)
    for run in runs:
        metrics = _metrics(run)
        values = {
            "shared_preprocessing_seconds": _finite_nonnegative(
                metrics.get("shared_preprocessing_seconds", run.snapshot.preprocessing_seconds)
            ),
            "independent_validation_seconds": _finite_nonnegative(
                metrics.get("independent_validation_seconds")
            ),
            "room_time_utilization": None,
            "occupied_room_atoms": None,
            "available_room_atoms": None,
            "placement_signature": None,
            "placement_diversity_mean_hamming": None,
            "placement_diversity_peer_count": 0,
            "solver_diagnostics": {
                name: metrics[name]
                for name in _DIAGNOSTIC_METRICS
                if name in metrics and _finite_nonnegative(metrics[name]) is not None
            },
        }
        placements = _placement_map(run) if _independently_feasible(run) else {}
        if placements:
            values["placement_signature"] = hashlib.sha256(
                json.dumps(placements, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            values.update(_room_time_metadata(run.snapshot, placements))
            if _eligible(run, formal=formal):
                key = (str(run.algorithm), str(run.snapshot.snapshot_hash))
                peer_groups[key].append((run.pk, placements))
        output[run.pk] = values

    for group in peer_groups.values():
        for run_id, placements in group:
            distances = [
                sum(placements[event_id] != other[event_id] for event_id in placements)
                / len(placements)
                for other_id, other in group
                if other_id != run_id and set(other) == set(placements)
            ]
            output[run_id]["placement_diversity_peer_count"] = len(distances)
            output[run_id]["placement_diversity_mean_hamming"] = (
                mean(distances) if distances else None
            )
    return output


def _metrics(run: Any) -> Mapping[str, Any]:
    diagnostics = getattr(run, "diagnostics", {})
    if isinstance(diagnostics, Mapping) and isinstance(diagnostics.get("metrics"), Mapping):
        return diagnostics["metrics"]
    result = getattr(run, "result_data", {})
    if isinstance(result, Mapping) and isinstance(result.get("metrics"), Mapping):
        return result["metrics"]
    return {}


def _finite_nonnegative(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _independently_feasible(run: Any) -> bool:
    try:
        validation = run.validation_result
    except AttributeError:
        return False
    return bool(validation and validation.is_feasible and validation.hard_violation_count == 0)


def _eligible(run: Any, *, formal: bool) -> bool:
    purposes = {"MEASURED"} if formal else {"MEASURED", "ROUTINE"}
    return bool(
        str(getattr(run, "purpose", "ROUTINE")) in purposes
        and getattr(run, "included_in_analysis", True)
        and str(getattr(run, "status", "")) in _TERMINAL
        and str(getattr(run, "failure_category", "")) not in _EXCLUDED_FAILURES
    )


def _placement_map(run: Any) -> dict[str, str]:
    result = getattr(run, "result_data", {})
    if not isinstance(result, Mapping):
        return {}
    rows = result.get("assignments", ())
    if not isinstance(rows, (list, tuple)):
        return {}
    placements: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("event_id") is None or row.get("candidate_id") is None:
            return {}
        event_id = str(row["event_id"])
        if event_id in placements:
            return {}
        placements[event_id] = str(row["candidate_id"])
    expected = {
        str(row["event_id"])
        for row in run.snapshot.input_data.get("events", ())
        if isinstance(row, Mapping) and row.get("event_id") is not None
    }
    if not placements or set(placements) != expected or len(placements) != run.snapshot.event_count:
        return {}
    return placements


def _room_time_metadata(snapshot: Any, placements: Mapping[str, str]) -> dict[str, Any]:
    available = {
        (str(row["room_id"]), str(atom_id))
        for row in snapshot.input_data.get("room_evidence", ())
        if isinstance(row, Mapping) and row.get("room_id") is not None
        for atom_id in row.get("available_atom_ids", ())
    }
    if not available:
        return {}
    occupied: set[tuple[str, str]] = set()
    for event_id, candidate_id in placements.items():
        candidate = next(
            (
                row
                for row in snapshot.candidate_map.get(event_id, ())
                if isinstance(row, Mapping) and str(row.get("candidate_id")) == candidate_id
            ),
            None,
        )
        if candidate is None or candidate.get("room_id") is None:
            return {}
        occupied.update(
            (str(candidate["room_id"]), str(atom_id))
            for atom_id in candidate.get("occupied_atom_ids", ())
        )
    if not occupied or not occupied <= available:
        return {}
    return {
        "occupied_room_atoms": len(occupied),
        "available_room_atoms": len(available),
        "room_time_utilization": len(occupied) / len(available),
    }
