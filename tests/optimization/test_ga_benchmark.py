from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from scheduler.domain import Assignment, ProblemInstance, ViolationCode, validate_schedule
from scripts.benchmark_ga import EVIDENCE_CLASS, build_scenarios, run_benchmark


def test_benchmark_cases_are_deterministic_replayable_and_known_feasible() -> None:
    first = build_scenarios()
    second = build_scenarios()
    assert [len(scenario.problem.events) for scenario in first] == [30, 48, 40]
    for left, right in zip(first, second, strict=True):
        assert left.problem.canonical_hash == right.problem.canonical_hash
        assert left.witness == right.witness
        assert validate_schedule(left.problem, left.witness).feasible
        replay = ProblemInstance.from_dict(left.problem.to_dict())
        assert replay.canonical_hash == left.problem.canonical_hash
        assert validate_schedule(replay, left.witness).feasible
        assert dict(replay.metadata)["evidence_class"] == EVIDENCE_CLASS
        assert all(len(event.candidates) >= 20 for event in replay.events)


def test_daily_stress_frozen_evidence_detects_resource_disjoint_overload() -> None:
    scenario = build_scenarios()[2]
    problem = scenario.problem
    # I2 teaches one two-atom class each day and has a two-atom daily cap.
    # Move its D1 meeting into an unused D0 block: no concurrent resource
    # collision is introduced, but the independent daily-load rule must fire.
    event = problem.events[10]
    assert event.instructor_ids == ("SYN-I2",)
    moved = Assignment(event.event_id, f"{event.event_id}-D0-A2-R2")
    assignments = tuple(moved if item.event_id == event.event_id else item for item in scenario.witness)
    validation = validate_schedule(problem, assignments)
    assert dict(validation.counts) == {ViolationCode.INSTRUCTOR_DAILY_LOAD_EXCEEDED.value: 1}
    assert problem.locked_assignments
    assert sum(len(event.instructor_ids) > 1 for event in problem.events) == 10
    assert sum(len(event.section_ids) > 1 for event in problem.events) == 10


def test_benchmark_evidence_is_used_by_independent_validator() -> None:
    scenario = build_scenarios()[0]
    problem = scenario.problem
    room = problem.room_evidence[0]
    tampered = replace(problem, room_evidence=(replace(room, authorization_grants=()), *problem.room_evidence[1:]))
    validation = validate_schedule(tampered, scenario.witness)
    assert ViolationCode.ROOM_AUTHORIZATION_VIOLATION.value in dict(validation.counts)


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan")])
def test_benchmark_rejects_invalid_budgets_before_writing(tmp_path: Path, seconds: float) -> None:
    output = tmp_path / "report.json"
    with pytest.raises(ValueError, match="finite and positive"):
        run_benchmark(output, seconds=seconds)
    assert not output.exists()
