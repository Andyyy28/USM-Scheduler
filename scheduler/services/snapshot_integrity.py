"""Cross-check redundant frozen facts before they can become research evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def snapshot_consistency_issues(snapshot: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    def issue(code: str, message: str, field: str) -> None:
        issues.append({"code": code, "message": message, "field": field})

    data = snapshot.input_data
    if not isinstance(data, Mapping) or not isinstance(data.get("events"), list):
        return issues  # The structural preflight reports this separately.
    events = [row for row in data["events"] if isinstance(row, Mapping)]
    event_ids = [str(row.get("event_id", "")) for row in events]
    event_set = set(event_ids)
    if len(event_ids) != len(event_set):
        issue("DUPLICATE_FROZEN_EVENT", "Frozen meeting IDs must be unique.", "input_data")
    if snapshot.event_count != len(data["events"]):
        issue("FROZEN_EVENT_COUNT_MISMATCH", "event_count disagrees with the frozen meetings.", "event_count")
    if data.get("schema_version") != snapshot.schema_version:
        issue("FROZEN_SCHEMA_MISMATCH", "The stored schema and solver contract disagree.", "schema_version")
    if str(data.get("term_revision_id")) != str(snapshot.revision_id):
        issue("FROZEN_REVISION_MISMATCH", "The solver contract references another revision.", "input_data")

    candidate_map = snapshot.candidate_map if isinstance(snapshot.candidate_map, Mapping) else {}
    if set(candidate_map) != event_set:
        issue("FROZEN_CANDIDATE_KEYS_MISMATCH", "Candidate-map keys must exactly match frozen meeting IDs.", "candidate_map")
    candidate_count = sum(len(rows) for rows in candidate_map.values() if isinstance(rows, list))
    if snapshot.candidate_count != candidate_count:
        issue("FROZEN_CANDIDATE_COUNT_MISMATCH", "candidate_count disagrees with the frozen domains.", "candidate_count")

    sections = snapshot.section_headcounts if isinstance(snapshot.section_headcounts, Mapping) else {}
    meetings = snapshot.meeting_headcounts if isinstance(snapshot.meeting_headcounts, Mapping) else {}
    referenced_sections: set[str] = set()
    for event in events:
        event_id = str(event.get("event_id", ""))
        section_ids = {str(value) for value in event.get("section_ids", ())}
        referenced_sections.update(section_ids)
        embedded = event.get("section_headcounts", ())
        embedded_length = len(embedded) if isinstance(embedded, (list, tuple)) else -1
        try:
            embedded_counts = dict(embedded)
        except (TypeError, ValueError):
            embedded_counts = {}
        expected_counts = {section_id: sections.get(section_id) for section_id in section_ids}
        if len(embedded_counts) != embedded_length or embedded_counts != expected_counts:
            issue("FROZEN_SECTION_EVIDENCE_MISMATCH", f"Meeting {event_id} enrollment copies disagree.", "section_headcounts")
        if event.get("meeting_headcount") != meetings.get(event_id):
            issue("FROZEN_MEETING_EVIDENCE_MISMATCH", f"Meeting {event_id} headcount copies disagree.", "meeting_headcounts")
        if event.get("fixed_student_limit") != snapshot.fixed_student_limit:
            issue("FROZEN_LIMIT_EVIDENCE_MISMATCH", f"Meeting {event_id} fixed-limit copies disagree.", "fixed_student_limit")
        embedded_candidates = _candidate_rows(event.get("candidates"))
        mapped_candidates = _candidate_rows(candidate_map.get(event_id))
        if embedded_candidates is None or mapped_candidates is None or embedded_candidates != mapped_candidates:
            issue("FROZEN_CANDIDATE_EVIDENCE_MISMATCH", f"Meeting {event_id} domain copies disagree or contain duplicates.", "candidate_map")
    if set(sections) != referenced_sections:
        issue("FROZEN_SECTION_KEYS_MISMATCH", "Enrollment keys must exactly match attached sections.", "section_headcounts")
    if set(meetings) != event_set:
        issue("FROZEN_MEETING_KEYS_MISMATCH", "Headcount keys must exactly match frozen meetings.", "meeting_headcounts")

    try:
        metadata = dict(data.get("metadata", ()))
    except (TypeError, ValueError):
        metadata = {}
    expected_metadata = {
        "fixed_student_limit": str(snapshot.fixed_student_limit),
        "constraint_manifest_hash": snapshot.constraint_manifest_hash,
        "objective_profile_hash": snapshot.objective_profile.profile_hash,
    }
    if any(metadata.get(key) != value for key, value in expected_metadata.items()):
        issue("FROZEN_METADATA_MISMATCH", "The solver metadata does not match the frozen rule/objective/limit evidence.", "input_data")

    expected_daily: dict[str, dict[str, Any]] = {}
    instructors = data.get("instructor_evidence", ())
    for row in instructors if isinstance(instructors, list) else ():
        if isinstance(row, Mapping):
            expected_daily[str(row.get("instructor_id"))] = {
                "instructor_id": str(row.get("instructor_id")),
                "max_daily_teaching_atoms": row.get("max_daily_teaching_atoms"),
                "acknowledge_no_daily_limit": row.get("acknowledge_no_daily_limit", False),
                "policy_hash": row.get("daily_load_policy_hash"),
            }
    daily_rows = snapshot.instructor_daily_load_evidence
    actual_daily = {
        str(row.get("instructor_id")): dict(row)
        for row in daily_rows
        if isinstance(row, Mapping)
    } if isinstance(daily_rows, list) else {}
    daily_row_count = len(daily_rows) if isinstance(daily_rows, list) else -1
    if actual_daily != expected_daily or len(actual_daily) != daily_row_count:
        issue("FROZEN_DAILY_LOAD_EVIDENCE_MISMATCH", "Instructor daily-load evidence copies disagree.", "instructor_daily_load_evidence")
    return issues


def _candidate_rows(rows: Any) -> dict[str, dict[str, Any]] | None:
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        return None
    mapped = {str(row.get("candidate_id", "")): dict(row) for row in rows}
    return mapped if len(mapped) == len(rows) and "" not in mapped else None
