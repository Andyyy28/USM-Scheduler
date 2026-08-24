"""Reproducible statistics used by algorithm comparison reports.

The functions deliberately avoid hiding failed runs. Time-to-feasibility
summaries accept a success indicator and treat failed runs as censored at the
experiment deadline.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from random import Random
from statistics import fmean, median


@dataclass(frozen=True, slots=True)
class DescriptiveSummary:
    count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    standard_deviation: float | None
    median: float | None
    first_quartile: float | None
    third_quartile: float | None
    interquartile_range: float | None
    median_absolute_deviation: float | None

    def to_dict(self) -> dict[str, int | float | None]:
        return asdict(self)


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("quantile requires at least one value")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return float(sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction)


def describe(values: Iterable[float]) -> DescriptiveSummary:
    sample = sorted(float(value) for value in values)
    if not sample:
        return DescriptiveSummary(0, None, None, None, None, None, None, None, None, None)
    centre = float(median(sample))
    q1 = _quantile(sample, 0.25)
    q3 = _quantile(sample, 0.75)
    if len(sample) > 1:
        variance = sum((value - fmean(sample)) ** 2 for value in sample) / (len(sample) - 1)
        standard_deviation = math.sqrt(variance)
    else:
        standard_deviation = 0.0
    absolute_deviations = sorted(abs(value - centre) for value in sample)
    return DescriptiveSummary(
        count=len(sample),
        minimum=sample[0],
        maximum=sample[-1],
        mean=fmean(sample),
        standard_deviation=standard_deviation,
        median=centre,
        first_quartile=q1,
        third_quartile=q3,
        interquartile_range=q3 - q1,
        median_absolute_deviation=float(median(absolute_deviations)),
    )


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Return a two-sided Wilson score interval (95% by default)."""

    if total <= 0:
        raise ValueError("total must be positive")
    if successes < 0 or successes > total:
        raise ValueError("successes must be between zero and total")
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
    margin /= denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def vargha_delaney_a12(
    first: Sequence[float],
    second: Sequence[float],
    *,
    lower_is_better: bool = True,
) -> float:
    """Probability that a random first result is better than a second result.

    Ties contribute one half. A value of 0.5 indicates no stochastic
    dominance. For schedule penalty and runtime, ``lower_is_better`` remains
    true.
    """

    if not first or not second:
        raise ValueError("both samples must contain at least one value")
    wins = 0.0
    for left in first:
        for right in second:
            if left == right:
                wins += 0.5
            elif (left < right) if lower_is_better else (left > right):
                wins += 1.0
    return wins / (len(first) * len(second))


def normalized_hamming(
    first: Mapping[str, str | int], second: Mapping[str, str | int]
) -> float:
    """Compare complete placement maps using the union of event identifiers."""

    keys = set(first) | set(second)
    if not keys:
        return 0.0
    return sum(first.get(key) != second.get(key) for key in keys) / len(keys)


def restricted_mean_time_to_feasibility(
    times: Sequence[float], successes: Sequence[bool], deadline: float
) -> float:
    """Kaplan-Meier restricted mean time to feasibility up to ``deadline``."""

    if len(times) != len(successes):
        raise ValueError("times and successes must have equal lengths")
    if not times:
        raise ValueError("at least one observation is required")
    if deadline <= 0:
        raise ValueError("deadline must be positive")
    observations = sorted(
        (min(max(float(time), 0.0), deadline), bool(success))
        for time, success in zip(times, successes, strict=True)
    )
    survival = 1.0
    area = 0.0
    previous_time = 0.0
    at_risk = len(observations)
    index = 0
    while index < len(observations):
        time = observations[index][0]
        area += survival * (time - previous_time)
        events = 0
        censored = 0
        while index < len(observations) and observations[index][0] == time:
            if observations[index][1] and time < deadline:
                events += 1
            else:
                censored += 1
            index += 1
        if at_risk and events:
            survival *= 1 - events / at_risk
        at_risk -= events + censored
        previous_time = time
    if previous_time < deadline:
        area += survival * (deadline - previous_time)
    return area


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Return Holm-Bonferroni adjusted p-values in their original order."""

    count = len(p_values)
    if any(value < 0 or value > 1 for value in p_values):
        raise ValueError("p-values must be within [0, 1]")
    ranked = sorted(enumerate(p_values), key=lambda item: item[1])
    adjusted = [0.0] * count
    running_max = 0.0
    for rank, (original_index, value) in enumerate(ranked):
        candidate = min(1.0, (count - rank) * value)
        running_max = max(running_max, candidate)
        adjusted[original_index] = running_max
    return adjusted


def bootstrap_median_interval(
    values: Sequence[float],
    *,
    confidence: float = 0.95,
    resamples: int = 10_000,
    seed: int = 20260824,
) -> tuple[float, float]:
    """Return a deterministic percentile-bootstrap interval for the median."""

    sample = [float(value) for value in values]
    if not sample:
        raise ValueError("bootstrap requires at least one value")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one")
    if resamples < 100:
        raise ValueError("resamples must be at least 100")
    if any(not math.isfinite(value) for value in sample):
        raise ValueError("bootstrap values must be finite")
    randomizer = Random(seed)
    estimates = sorted(
        float(median(randomizer.choices(sample, k=len(sample))))
        for _ in range(resamples)
    )
    alpha = (1 - confidence) / 2
    return _quantile(estimates, alpha), _quantile(estimates, 1 - alpha)


def unpaired_permutation_test(
    first: Sequence[float],
    second: Sequence[float],
    *,
    statistic: str = "median",
    resamples: int = 10_000,
    seed: int = 20260824,
) -> dict[str, float | int | str]:
    """Two-sided, deterministic label-permutation test for independent samples."""

    left = [float(value) for value in first]
    right = [float(value) for value in second]
    if not left or not right:
        raise ValueError("both samples must contain at least one value")
    if any(not math.isfinite(value) for value in left + right):
        raise ValueError("permutation values must be finite")
    if statistic == "median":
        estimator = median
    elif statistic == "mean":
        estimator = fmean
    else:
        raise ValueError("statistic must be 'median' or 'mean'")
    if resamples < 100:
        raise ValueError("resamples must be at least 100")
    observed = float(estimator(left)) - float(estimator(right))
    combined = left + right
    left_size = len(left)
    randomizer = Random(seed)
    extreme = 0
    for _ in range(resamples):
        shuffled = randomizer.sample(combined, k=len(combined))
        permuted = float(estimator(shuffled[:left_size])) - float(estimator(shuffled[left_size:]))
        if abs(permuted) >= abs(observed) - 1e-15:
            extreme += 1
    return {
        "statistic": statistic,
        "observed_difference_first_minus_second": observed,
        "p_value_two_sided": (extreme + 1) / (resamples + 1),
        "resamples": resamples,
        "seed": seed,
    }
