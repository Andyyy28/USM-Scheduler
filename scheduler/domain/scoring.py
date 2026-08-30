"""The common integer soft-objective scorer used by both algorithms."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .contracts import (
    Assignment,
    CandidatePlacement,
    ObjectiveBreakdown,
    ProblemInstance,
)
from .prepared import PreparedProblem


def score_schedule(
    problem: ProblemInstance, assignments: tuple[Assignment, ...] | list[Assignment],
    *, prepared: PreparedProblem | None = None,
) -> ObjectiveBreakdown:
    """Return the shared lower-is-better soft objective.

    Assignments may contain cross-event conflicts (the GA scores those using a
    lexicographic hard-first fitness), but must select exactly one legal
    candidate for every event. Call :func:`validate_schedule` separately for
    hard feasibility.

    Load imbalance is kept integer by summing ``abs(day_load * D - weekly_load)``
    over every resource and each of the ``D`` configured teaching days. This is
    proportional to absolute deviation from the weekly-load-per-day target.
    """

    resolved = resolve_assignments(problem, assignments, prepared=prepared)
    preference_penalty = sum(candidate.preference_penalty for candidate in resolved.values())

    section_occupancy: dict[tuple[str, str], set[str]] = defaultdict(set)
    instructor_occupancy: dict[tuple[str, str], set[str]] = defaultdict(set)
    section_day_load: dict[tuple[str, str], int] = defaultdict(int)
    instructor_day_load: dict[tuple[str, str], int] = defaultdict(int)
    section_ids: set[str] = set()
    instructor_ids: set[str] = set()

    for event in problem.events:
        candidate = resolved[event.event_id]
        duration = len(candidate.occupied_atom_ids)
        for section_id in event.section_ids:
            section_ids.add(section_id)
            section_occupancy[(section_id, candidate.day_id)].update(candidate.occupied_atom_ids)
            section_day_load[(section_id, candidate.day_id)] += duration
        for instructor_id in event.instructor_ids:
            instructor_ids.add(instructor_id)
            instructor_occupancy[(instructor_id, candidate.day_id)].update(
                candidate.occupied_atom_ids
            )
            instructor_day_load[(instructor_id, candidate.day_id)] += duration

    section_gap_atoms = _count_internal_gaps(problem, section_occupancy, prepared=prepared)
    instructor_gap_atoms = _count_internal_gaps(problem, instructor_occupancy, prepared=prepared)
    day_ids = prepared.day_ids if prepared is not None else problem.day_ids
    load_imbalance = _load_imbalance(day_ids, section_ids, section_day_load)
    load_imbalance += _load_imbalance(day_ids, instructor_ids, instructor_day_load)

    profile = problem.objective_profile
    weighted_total = (
        preference_penalty * profile.preference_weight
        + section_gap_atoms * profile.section_gap_weight
        + instructor_gap_atoms * profile.instructor_gap_weight
        + load_imbalance * profile.load_imbalance_weight
    )
    normalized_penalty = (
        profile.preference_weight * preference_penalty / profile.preference_normalizer
        + profile.section_gap_weight * section_gap_atoms / profile.section_gap_normalizer
        + profile.instructor_gap_weight
        * instructor_gap_atoms
        / profile.instructor_gap_normalizer
        + profile.load_imbalance_weight
        * load_imbalance
        / profile.load_imbalance_normalizer
    )
    active_weight = sum(
        weight
        for weight in (
            profile.preference_weight,
            profile.section_gap_weight,
            profile.instructor_gap_weight,
            profile.load_imbalance_weight,
        )
        if weight > 0
    )
    quality_score = 100.0 if active_weight == 0 else 100.0 * (1.0 - normalized_penalty / active_weight)
    quality_score = round(min(100.0, max(0.0, quality_score)), 6)

    return ObjectiveBreakdown(
        preference_penalty=preference_penalty,
        section_gap_atoms=section_gap_atoms,
        instructor_gap_atoms=instructor_gap_atoms,
        load_imbalance=load_imbalance,
        weighted_total=weighted_total,
        quality_score=quality_score,
    )


def resolve_assignments(
    problem: ProblemInstance, assignments: Iterable[Assignment],
    *, prepared: PreparedProblem | None = None,
) -> dict[str, CandidatePlacement]:
    """Resolve a complete assignment vector or raise a precise ``ValueError``."""

    if prepared is not None:
        prepared.require_problem(problem)
    event_map = prepared.event_map if prepared is not None else problem.event_map
    selected: dict[str, CandidatePlacement] = {}
    for assignment in assignments:
        event = event_map.get(assignment.event_id)
        if event is None:
            raise ValueError(f"assignment references unknown event {assignment.event_id!r}")
        if assignment.event_id in selected:
            raise ValueError(f"event {assignment.event_id!r} has multiple assignments")
        candidates = prepared.candidates[event.event_id] if prepared is not None else event.candidate_map
        candidate = candidates.get(assignment.candidate_id)
        if candidate is None:
            raise ValueError(
                f"candidate {assignment.candidate_id!r} is invalid for event "
                f"{assignment.event_id!r}"
            )
        selected[assignment.event_id] = candidate

    missing = sorted(set(event_map) - set(selected))
    if missing:
        raise ValueError(f"missing assignments for events: {', '.join(missing)}")
    return selected


def _count_internal_gaps(
    problem: ProblemInstance, occupancy: dict[tuple[str, str], set[str]],
    *, prepared: PreparedProblem | None = None,
) -> int:
    if prepared is not None:
        total = 0
        for occupied_ids in occupancy.values():
            if len(occupied_ids) >= 2:
                positions = [prepared.atom_positions[atom_id] for atom_id in occupied_ids]
                total += max(positions) - min(positions) + 1 - len(positions)
        return total
    ordered_atoms: dict[str, list[str]] = defaultdict(list)
    for atom in sorted(problem.time_atoms, key=lambda item: (item.day_index, item.order, item.atom_id)):
        ordered_atoms[atom.day_id].append(atom.atom_id)

    total = 0
    for (_, day_id), occupied_ids in occupancy.items():
        if len(occupied_ids) < 2:
            continue
        positions = [
            position
            for position, atom_id in enumerate(ordered_atoms[day_id])
            if atom_id in occupied_ids
        ]
        if positions:
            total += sum(
                1
                for atom_id in ordered_atoms[day_id][min(positions) : max(positions) + 1]
                if atom_id not in occupied_ids
            )
    return total


def _load_imbalance(
    day_ids: tuple[str, ...],
    resource_ids: set[str],
    day_load: dict[tuple[str, str], int],
) -> int:
    day_count = len(day_ids)
    if day_count == 0:
        return 0
    total = 0
    for resource_id in resource_ids:
        weekly_load = sum(day_load[(resource_id, day_id)] for day_id in day_ids)
        total += sum(
            abs(day_load[(resource_id, day_id)] * day_count - weekly_load)
            for day_id in day_ids
        )
    return total
