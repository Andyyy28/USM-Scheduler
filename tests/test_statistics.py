import pytest

from scheduler.services.statistics import (
    bootstrap_median_interval,
    describe,
    holm_adjust,
    normalized_hamming,
    restricted_mean_time_to_feasibility,
    unpaired_permutation_test,
    vargha_delaney_a12,
    wilson_interval,
)


def test_describe_reports_distribution() -> None:
    summary = describe([1, 2, 3, 4])
    assert summary.count == 4
    assert summary.mean == 2.5
    assert summary.median == 2.5
    assert summary.interquartile_range == 1.5


def test_wilson_interval_contains_observed_proportion() -> None:
    lower, upper = wilson_interval(24, 30)
    assert lower < 0.8 < upper
    assert wilson_interval(0, 30)[0] == 0.0


def test_a12_uses_lower_values_as_better_by_default() -> None:
    assert vargha_delaney_a12([1, 2], [4, 5]) == 1.0
    assert vargha_delaney_a12([1], [1]) == 0.5


def test_hamming_uses_union_and_detects_missing_assignments() -> None:
    assert normalized_hamming({"a": 1, "b": 2}, {"a": 1, "b": 3}) == 0.5
    assert normalized_hamming({"a": 1}, {}) == 1.0


def test_restricted_mean_retains_timeouts() -> None:
    result = restricted_mean_time_to_feasibility([1, 10], [True, False], deadline=10)
    assert result == pytest.approx(5.5)


def test_holm_adjust_preserves_original_order_and_monotonicity() -> None:
    assert holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def test_bootstrap_median_interval_is_seeded_and_contains_centre() -> None:
    first = bootstrap_median_interval([1, 2, 3, 4, 5], resamples=500, seed=7)
    second = bootstrap_median_interval([1, 2, 3, 4, 5], resamples=500, seed=7)
    assert first == second
    assert first[0] <= 3 <= first[1]


def test_unpaired_permutation_test_reports_direction_and_reproducible_p_value() -> None:
    first = unpaired_permutation_test([1, 1, 2, 2], [8, 9, 9, 10], resamples=500, seed=9)
    second = unpaired_permutation_test([1, 1, 2, 2], [8, 9, 9, 10], resamples=500, seed=9)
    assert first == second
    assert first["observed_difference_first_minus_second"] < 0
    assert 0 <= first["p_value_two_sided"] <= 1
