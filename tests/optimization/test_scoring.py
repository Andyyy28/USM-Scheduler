from __future__ import annotations

import dataclasses

import pytest

from scheduler.domain import Assignment, ProblemInstance, score_schedule


def test_scorer_counts_preferences_gaps_and_integer_load_deviation(
    balanced_problem: ProblemInstance,
) -> None:
    first_event = dataclasses.replace(
        balanced_problem.events[0],
        candidates=(
            dataclasses.replace(
                balanced_problem.events[0].candidates[0], preference_penalty=2
            ),
            balanced_problem.events[0].candidates[1],
        ),
    )
    problem = dataclasses.replace(
        balanced_problem,
        events=(first_event, balanced_problem.events[1]),
    )

    result = score_schedule(
        problem,
        (
            Assignment(event_id="E1", candidate_id="E1-M0"),
            Assignment(event_id="E2", candidate_id="E2-M2"),
        ),
    )

    assert result.preference_penalty == 2
    assert result.section_gap_atoms == 1
    assert result.instructor_gap_atoms == 1
    assert result.load_imbalance == 8
    assert result.weighted_total == 12
    assert result.quality_score == 0.0


def test_balanced_schedule_has_no_gap_or_load_penalty(balanced_problem: ProblemInstance) -> None:
    result = score_schedule(
        balanced_problem,
        (
            Assignment(event_id="E1", candidate_id="E1-M0"),
            Assignment(event_id="E2", candidate_id="E2-T2"),
        ),
    )

    assert result.weighted_total == 0
    assert result.quality_score == 100.0


def test_scorer_refuses_incomplete_or_out_of_domain_assignments(
    balanced_problem: ProblemInstance,
) -> None:
    with pytest.raises(ValueError, match="missing assignments"):
        score_schedule(
            balanced_problem,
            (Assignment(event_id="E1", candidate_id="E1-M0"),),
        )
    with pytest.raises(ValueError, match="invalid for event"):
        score_schedule(
            balanced_problem,
            (
                Assignment(event_id="E1", candidate_id="wrong"),
                Assignment(event_id="E2", candidate_id="E2-M2"),
            ),
        )
