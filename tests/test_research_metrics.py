from __future__ import annotations

import pytest

from scheduler.services.research_metrics import (
    NO_FORMAL_CONCLUSION,
    NO_MEANINGFUL_ADVANTAGE,
    analyze_study_trials,
)


def _complete_trials(*, cp_feasible: bool = True, ga_feasible: bool = True):
    rows = []
    for scale in (25, 50, 75, 100):
        for seed in range(1001, 1031):
            for algorithm, feasible, first_time, penalty in (
                ("CP_SAT", cp_feasible, 15.0, 100.0),
                ("GA", ga_feasible, 30.0, 110.0),
            ):
                rows.append(
                    {
                        "scale_percentage": scale,
                        "seed": seed,
                        "algorithm": algorithm,
                        "eligible": True,
                        "independently_feasible": feasible,
                        "first_feasible_seconds": first_time if feasible else None,
                        "raw_penalty": penalty if feasible else None,
                        "meeting_count": max(1, scale),
                        "objective_components": {
                            "preference_penalty": 25,
                            "section_gap_atoms": 25,
                            "instructor_gap_atoms": 25,
                            "load_imbalance": 25,
                        },
                        "comparison_signature": f"scale-{scale}",
                        "purpose": "MEASURED",
                        "status": "FEASIBLE" if feasible else "TIMEOUT",
                    }
                )
    return rows


def test_incomplete_or_exploratory_evidence_never_claims_a_winner() -> None:
    report = analyze_study_trials(
        _complete_trials()[:20],
        mode="EXPLORATORY",
        study_status="COMPLETED",
        protocol_valid=True,
        resamples=100,
    )

    assert report["formal_conclusion"] == NO_FORMAL_CONCLUSION
    assert report["winner_decision"]["claimable"] is False


@pytest.mark.parametrize("first_time,expected_successes", [(300, 30), (300.001, 0), (None, 0)])
def test_formal_deadline_qualifies_all_three_primary_outcomes(first_time, expected_successes) -> None:
    rows = _complete_trials()
    for row in rows:
        row["first_feasible_seconds"] = first_time
    report = analyze_study_trials(rows, protocol_valid=True, resamples=100)
    full = report["scales"]["100"]["primary_outcomes"]
    assert full["feasibility"]["by_algorithm"]["GA"]["independently_feasible"] == expected_successes
    assert full["time_to_feasibility"]["by_algorithm"]["GA"]["right_censored_trials"] == 30 - expected_successes
    quality = full["schedule_quality"]["by_algorithm"]["GA"]["raw_weighted_soft_penalty"]
    assert quality["count"] == expected_successes


def test_complete_analysis_reports_wilson_holm_km_rmst_and_censoring() -> None:
    rows = _complete_trials()
    # One CP-SAT observation is an eligible algorithm timeout and must stay in
    # the denominator and be right-censored at 300 seconds.
    row = next(
        item
        for item in rows
        if item["scale_percentage"] == 100
        and item["seed"] == 1001
        and item["algorithm"] == "CP_SAT"
    )
    row.update(
        independently_feasible=False,
        first_feasible_seconds=None,
        raw_penalty=None,
        status="TIMEOUT",
    )

    report = analyze_study_trials(
        rows,
        protocol_valid=True,
        resamples=100,
    )
    full = report["scales"]["100"]["primary_outcomes"]

    assert report["integrity"]["complete"] is True
    assert full["feasibility"]["by_algorithm"]["CP_SAT"]["eligible_trials"] == 30
    assert full["feasibility"]["by_algorithm"]["CP_SAT"]["independently_feasible"] == 29
    assert len(full["feasibility"]["by_algorithm"]["CP_SAT"]["wilson_95"]) == 2
    assert full["time_to_feasibility"]["by_algorithm"]["CP_SAT"]["right_censored_trials"] == 1
    assert full["time_to_feasibility"]["by_algorithm"]["CP_SAT"]["kaplan_meier"][-1][
        "time_seconds"
    ] == 300.0
    assert report["scales"]["100"]["holm_family"]["family_size"] == 3


def test_all_infeasible_complete_study_reports_no_meaningful_advantage() -> None:
    report = analyze_study_trials(
        _complete_trials(cp_feasible=False, ga_feasible=False),
        protocol_valid=True,
        resamples=100,
    )

    full_quality = report["scales"]["100"]["primary_outcomes"]["schedule_quality"]
    assert full_quality["comparison"]["available"] is False
    assert report["formal_conclusion"] == NO_MEANINGFUL_ADVANTAGE


@pytest.mark.parametrize(("cp_penalty", "ga_penalty", "cp_time", "ga_time", "outcome"), [
    (96, 100, 100, 100, None),
    (95, 100, 100, 100, "schedule_quality"),
    (100, 100, 91, 100, None),
    (100, 100, 90, 100, "time_to_feasibility"),
    (0, 0, 10, 10, None),
])
def test_preregistered_practical_thresholds_and_zero_penalties(
    cp_penalty, ga_penalty, cp_time, ga_time, outcome,
) -> None:
    rows = _complete_trials()
    for row in rows:
        is_cp = row["algorithm"] == "CP_SAT"
        penalty = cp_penalty if is_cp else ga_penalty
        # Integer-valued spread preserves the exact median reduction while
        # avoiding a two-point tie distribution in the median permutation test.
        row["raw_penalty"] = int(penalty * 100 + (row["seed"] - 1015.5) * 2) if penalty else 0
        row["first_feasible_seconds"] = cp_time if is_cp else ga_time
    report = analyze_study_trials(rows, protocol_valid=True, resamples=100)
    assert report["winner_decision"]["deciding_outcome"] == outcome
    assert report == analyze_study_trials(rows, protocol_valid=True, resamples=100)


@pytest.mark.parametrize("status", ["DRAFT", "RUNNING", "INVALID", "CANCELLED"])
def test_noncomplete_states_never_produce_a_formal_conclusion(status: str) -> None:
    report = analyze_study_trials(
        _complete_trials(), study_status=status, protocol_valid=True, resamples=100
    )
    assert report["formal_conclusion"] == NO_FORMAL_CONCLUSION


def test_blank_provenance_and_unclassified_failure_block_inference() -> None:
    rows = _complete_trials()
    rows[0]["comparison_signature"] = ""
    report = analyze_study_trials(rows, protocol_valid=True, resamples=100)
    assert report["formal_conclusion"] == NO_FORMAL_CONCLUSION
    rows[0]["comparison_signature"] = "scale-25"
    rows[0]["failure_category"] = "UNCLASSIFIED"
    report = analyze_study_trials(rows, protocol_valid=True, resamples=100)
    assert report["formal_conclusion"] == NO_FORMAL_CONCLUSION


def test_excluded_original_pair_does_not_inflate_replacement_denominators() -> None:
    rows = _complete_trials()
    originals = []
    for row in rows[:2]:
        originals.append({**row, "eligible": False, "pair_attempt": 1, "exclusion_reason": "Superseded pair"})
        row["pair_attempt"] = 2
    report = analyze_study_trials(rows + originals, protocol_valid=True, resamples=100)
    assert report["integrity"]["matrix"]["eligible_measured_trials"] == 240
    assert report["scales"]["25"]["primary_outcomes"]["feasibility"]["by_algorithm"]["CP_SAT"]["eligible_trials"] == 30
