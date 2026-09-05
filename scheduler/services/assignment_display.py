"""Resolve timetable display data from persisted placements and frozen domains."""

from scheduler import models
from scheduler.services.problem_builder import load_problem


def prepare_assignments(schedule, assignments):
    """Batch-load end times and exact active locks without trusting cached allocations."""
    if not assignments or not schedule.snapshot_id:
        return assignments
    problem = load_problem(schedule.snapshot)
    events, atoms = problem.event_map, problem.atom_map
    ends = dict(models.TimeSlot.objects.filter(revision_id=schedule.revision_id).values_list("pk", "ends_at"))
    locks = set(models.LockedAssignment.objects.filter(
        meeting_requirement__offering__revision_id=schedule.revision_id, is_active=True,
    ).values_list("meeting_requirement_id", "room_id", "start_time_slot_id"))
    for row in assignments:
        event = events.get(str(row.meeting_requirement.stable_key))
        candidate = next((candidate for candidate in event.candidates
                          if candidate.room_id == str(row.room_id)
                          and candidate.start_atom_id == f"slot:{row.start_time_slot_id}"), None) if event else None
        row.resolved_end_time = None
        if candidate:
            last = max(candidate.occupied_atom_ids, key=lambda atom: atoms[atom].order)
            row.resolved_end_time = ends.get(int(last.removeprefix("slot:")))
        row.resolved_locked = (row.meeting_requirement_id, row.room_id, row.start_time_slot_id) in locks
    return assignments
