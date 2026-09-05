from __future__ import annotations

from types import SimpleNamespace

import pytest

from scheduler.services.secondary_metrics import secondary_trial_metadata


def _run(run_id: int, *, alternate: bool = False, algorithm: str = "CP_SAT", included: bool = True):
    candidates = {
        "meeting-a": [
            {"candidate_id": "a1", "room_id": "room-1", "occupied_atom_ids": ["period-1"]},
            {"candidate_id": "a2", "room_id": "room-2", "occupied_atom_ids": ["period-1"]},
        ],
        "meeting-b": [
            {"candidate_id": "b1", "room_id": "room-1", "occupied_atom_ids": ["period-2"]},
        ],
    }
    snapshot = SimpleNamespace(
        snapshot_hash="frozen-problem",
        preprocessing_seconds=0.2,
        event_count=2,
        candidate_map=candidates,
        input_data={
            "events": [{"event_id": "meeting-a"}, {"event_id": "meeting-b"}],
            "room_evidence": [
                {"room_id": "room-1", "available_atom_ids": ["period-1", "period-2"]},
                {"room_id": "room-2", "available_atom_ids": ["period-1", "period-2"]},
            ],
        },
    )
    return SimpleNamespace(
        pk=run_id,
        algorithm=algorithm,
        purpose="MEASURED",
        included_in_analysis=included,
        status="FEASIBLE",
        failure_category="",
        snapshot=snapshot,
        diagnostics={"metrics": {"independent_validation_seconds": 0.03}},
        validation_result=SimpleNamespace(is_feasible=True, hard_violation_count=0),
        result_data={
            "assignments": [
                {"event_id": "meeting-a", "candidate_id": "a2" if alternate else "a1"},
                {"event_id": "meeting-b", "candidate_id": "b1"},
            ]
        },
    )


def test_secondary_metrics_use_frozen_periods_and_eligible_same_algorithm_peers() -> None:
    rows = [
        _run(1),
        _run(2, alternate=True),
        _run(3, algorithm="GA"),
        _run(4, included=False),
    ]
    result = secondary_trial_metadata(rows, formal=True)

    assert result[1]["shared_preprocessing_seconds"] == 0.2
    assert result[1]["independent_validation_seconds"] == 0.03
    assert result[1]["room_time_utilization"] == 0.5
    assert result[1]["occupied_room_atoms"] == 2
    assert result[1]["available_room_atoms"] == 4
    assert result[1]["placement_diversity_mean_hamming"] == pytest.approx(0.5)
    assert result[1]["placement_diversity_peer_count"] == 1
    assert result[1]["placement_signature"] != result[2]["placement_signature"]
    assert result[3]["placement_diversity_mean_hamming"] is None
    assert result[4]["placement_diversity_peer_count"] == 0
    assert all("placement_map" not in value for value in result.values())


def test_secondary_metrics_leave_missing_or_infeasible_evidence_unestimated() -> None:
    missing = _run(1)
    missing.snapshot.input_data.pop("room_evidence")
    infeasible = _run(2)
    infeasible.validation_result.is_feasible = False
    result = secondary_trial_metadata([missing, infeasible], formal=True)

    assert result[1]["room_time_utilization"] is None
    assert result[1]["available_room_atoms"] is None
    assert result[2]["room_time_utilization"] is None
    assert result[2]["placement_signature"] is None
    assert result[1]["placement_diversity_peer_count"] == 0
