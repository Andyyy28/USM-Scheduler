from __future__ import annotations

import csv
from io import StringIO
from xml.etree import ElementTree

from scheduler.services.evidence_bundle import _figures, _trials_csv
from scheduler.services.research_metrics import TrialObservation, analyze_study_trials
from tests.test_research_metrics import _complete_trials


def test_zero_penalty_and_zero_runtime_figures_are_valid_svg() -> None:
    rows = _complete_trials()
    for row in rows:
        row["raw_penalty"] = 0
        row["objective_components"] = {key: 0 for key in row["objective_components"]}
        row["metadata"] = {"execution_seconds": 0}
    summary = analyze_study_trials(rows, protocol_valid=True, resamples=100)
    figures = _figures(summary, [])
    for svg in figures.values():
        assert ElementTree.fromstring(svg).tag.endswith("svg")
        assert "NaN" not in svg


def test_trial_csv_includes_secondary_metrics_without_raw_placements() -> None:
    item = TrialObservation(
        scale_percentage=100, seed=1001, algorithm="GA", eligible=True,
        independently_feasible=True, raw_penalty=0, meeting_count=2,
        metadata={
            "shared_preprocessing_seconds": 0.2,
            "independent_validation_seconds": 0.03,
            "stopping_reason": "Search stagnated with a feasible incumbent.",
            "room_time_utilization": 0.5,
            "placement_diversity_mean_hamming": 0.25,
            "placement_diversity_peer_count": 29,
            "solver_diagnostics": {"repair_successes": 4, "mutation_operations": 12},
        },
    )
    content = _trials_csv([item])
    assert content == _trials_csv([item])
    row = next(csv.DictReader(StringIO(content.decode())))
    assert row["shared_preprocessing_seconds"] == "0.2"
    assert row["independent_validation_seconds"] == "0.03"
    assert row["room_time_utilization"] == "0.5"
    assert row["placement_diversity_peer_count"] == "29"
    assert '"repair_successes":4' in row["solver_diagnostics_json"]
    assert "placement_map" not in row
