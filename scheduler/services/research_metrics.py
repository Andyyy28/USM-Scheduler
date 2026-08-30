"""Preregistered analysis for the CP-SAT versus Genetic Algorithm study.

This module deliberately accepts plain trial records as well as Django model
instances.  The plain-record boundary keeps the statistical analysis
reproducible and independently testable, while :func:`analyze_experiment_study`
is the adapter used by the web/API layer.

Only independently validated schedules count as feasible.  Infrastructure
failures, user cancellations, unclassified failures, warm-ups, tuning runs,
and diagnostics never enter the measured denominators.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from random import Random
from statistics import median
from typing import Any

from scheduler.services.statistics import (
    bootstrap_median_interval,
    holm_adjust,
    restricted_mean_time_to_feasibility,
    unpaired_permutation_test,
    vargha_delaney_a12,
    wilson_interval,
)

CP_SAT = "CP_SAT"
GENETIC_ALGORITHM = "GA"
ALGORITHMS = (CP_SAT, GENETIC_ALGORITHM)
PRIMARY_OUTCOMES = ("feasibility", "schedule_quality", "time_to_feasibility")
NO_FORMAL_CONCLUSION = "No formal conclusion available."
NO_MEANINGFUL_ADVANTAGE = "Neither algorithm demonstrated a meaningful overall advantage."
FORMAL_SCALES = (25, 50, 75, 100)
FORMAL_SEEDS = tuple(range(1001, 1031))
DEFAULT_RANDOM_SEED = 20260824

_EXCLUDED_FAILURE_CATEGORIES = {
    "INFRASTRUCTURE",
    "USER_CANCELLATION",
    "UNCLASSIFIED",
}
_TERMINAL_STATUSES = {
    "FEASIBLE",
    "OPTIMAL",
    "INFEASIBLE",
    "NO_SOLUTION",
    "TIMEOUT",
    "CANCELLED",
    "FAILED",
}
_COMPONENT_ALIASES = {
    "faculty_preference_penalty": (
        "faculty_preference_penalty",
        "preference_penalty",
        "instructor_preference",
    ),
    "section_gaps": ("section_gaps", "section_gap_atoms", "section_internal_gaps"),
    "instructor_gaps": (
        "instructor_gaps",
        "instructor_gap_atoms",
        "instructor_internal_gaps",
    ),
    "daily_load_imbalance": (
        "daily_load_imbalance",
        "load_imbalance",
    ),
}


@dataclass(frozen=True, slots=True)
class TrialObservation:
    """Algorithm-independent measured evidence for one solver attempt."""

    scale_percentage: int
    seed: int
    algorithm: str
    eligible: bool
    independently_feasible: bool
    first_feasible_seconds: float | None = None
    raw_penalty: float | None = None
    meeting_count: int = 0
    objective_components: Mapping[str, float] = field(default_factory=dict)
    hard_violation_categories: Mapping[str, int] = field(default_factory=dict)
    validator_present: bool = True
    purpose: str = "MEASURED"
    pair_attempt: int = 1
    status: str = ""
    failure_category: str = ""
    exclusion_reason: str = ""
    comparison_signature: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def pair_key(self) -> tuple[int, int]:
        return self.scale_percentage, self.seed

    @property
    def penalty_per_meeting(self) -> float | None:
        if self.raw_penalty is None or self.meeting_count <= 0:
            return None
        return self.raw_penalty / self.meeting_count

    def to_public_dict(self) -> dict[str, Any]:
        """Return the research fields used by reports and evidence exports."""

        return {
            "scale_percentage": self.scale_percentage,
            "seed": self.seed,
            "algorithm": self.algorithm,
            "eligible": self.eligible,
            "independently_feasible": self.independently_feasible,
            "first_feasible_seconds": self.first_feasible_seconds,
            "raw_penalty": self.raw_penalty,
            "penalty_per_meeting": self.penalty_per_meeting,
            "meeting_count": self.meeting_count,
            "objective_components": dict(sorted(self.objective_components.items())),
            "hard_violation_categories": dict(
                sorted(self.hard_violation_categories.items())
            ),
            "validator_present": self.validator_present,
            "purpose": self.purpose,
            "pair_attempt": self.pair_attempt,
            "status": self.status,
            "failure_category": self.failure_category,
            "exclusion_reason": self.exclusion_reason,
            "comparison_signature": self.comparison_signature,
            **dict(self.metadata),
        }


def _within_budget_observation(item: TrialObservation, deadline: float) -> TrialObservation:
    if item.independently_feasible and not (
        item.first_feasible_seconds is not None
        and math.isfinite(item.first_feasible_seconds)
        and 0 <= item.first_feasible_seconds <= deadline
        and item.status in {"FEASIBLE", "OPTIMAL"}
    ):
        return replace(item, independently_feasible=False, raw_penalty=None, objective_components={})
    return item


def analyze_study_trials(
    trials: Iterable[TrialObservation | Mapping[str, Any]],
    *,
    deadline_seconds: float = 300,
    mode: str = "FORMAL",
    study_status: str = "COMPLETED",
    protocol_valid: bool = False,
    expected_scales: Sequence[int] = FORMAL_SCALES,
    expected_seeds: Sequence[int] = FORMAL_SEEDS,
    resamples: int = 10_000,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> dict[str, Any]:
    """Analyze all scales under the frozen three-outcome protocol.

    The function is deterministic for identical inputs.  ``protocol_valid`` is
    an explicit gate supplied by the study-integrity validator; this statistics
    layer never infers institutional approval merely from observed outcomes.
    """

    if deadline_seconds <= 0 or not math.isfinite(float(deadline_seconds)):
        raise ValueError("deadline_seconds must be a finite positive number")
    if resamples < 100:
        raise ValueError("resamples must be at least 100")

    observations = tuple(_coerce_trial(item) for item in trials)
    if str(mode).upper() == "FORMAL":
        observations = tuple(
            _within_budget_observation(item, float(deadline_seconds)) for item in observations
        )
    scales = tuple(sorted(set(int(scale) for scale in expected_scales)))
    if not scales:
        scales = tuple(sorted({item.scale_percentage for item in observations}))
    seeds = tuple(sorted(set(int(seed) for seed in expected_seeds)))
    eligible = tuple(item for item in observations if item.eligible)
    _assert_unique_eligible_cells(eligible)

    by_scale: dict[str, Any] = {}
    for scale in scales:
        scale_trials = tuple(item for item in eligible if item.scale_percentage == scale)
        by_scale[str(scale)] = _analyze_scale(
            scale_trials,
            deadline=float(deadline_seconds),
            resamples=resamples,
            random_seed=_derived_seed(random_seed, f"scale:{scale}"),
        )

    matrix = _matrix_integrity(eligible, scales=scales, seeds=seeds)
    has_unclassified = any(
        item.purpose == "MEASURED" and item.failure_category == "UNCLASSIFIED"
        for item in observations
    )
    missing_validator = any(not item.validator_present for item in eligible)
    signatures_match = all(
        not detail["comparison_signature_mismatch"]
        for detail in matrix["by_scale"].values()
    )
    formal_mode = str(mode).upper() == "FORMAL"
    complete = (
        formal_mode
        and str(study_status).upper() == "COMPLETED"
        and matrix["complete"]
    )
    effective_protocol_valid = bool(
        protocol_valid
        and not has_unclassified
        and not missing_validator
        and signatures_match
    )
    winner_decision = _winner_decision(
        by_scale.get("100"),
        formal_mode=formal_mode,
        study_complete=complete,
        protocol_valid=effective_protocol_valid,
    )

    return {
        "analysis_protocol": {
            "version": "formal-v2",
            "deadline_seconds": float(deadline_seconds),
            "primary_outcomes": list(PRIMARY_OUTCOMES),
            "multiple_comparison_adjustment": (
                "Holm adjustment across exactly the three primary outcomes within each scale"
            ),
            "quality_population": "independently feasible eligible trials only",
            "time_population": "all eligible trials with non-feasible trials right-censored",
            "random_seed": random_seed,
            "resamples": resamples,
        },
        "integrity": {
            "formal_mode": formal_mode,
            "study_status": str(study_status).upper(),
            "matrix_complete": matrix["complete"],
            "complete": complete,
            "declared_protocol_valid": bool(protocol_valid),
            "effective_protocol_valid": effective_protocol_valid,
            "has_unclassified_failure": has_unclassified,
            "missing_independent_validator_evidence": missing_validator,
            "pair_comparison_signatures_match": signatures_match,
            "matrix": matrix,
        },
        "scales": by_scale,
        "winner_decision": winner_decision,
        "formal_conclusion": winner_decision["conclusion"],
    }


def analyze_experiment_study(
    study: Any,
    *,
    protocol_valid: bool | None = None,
    resamples: int = 10_000,
) -> dict[str, Any]:
    """Load Django study evidence and run the preregistered analysis."""

    records = trial_observations_from_study(study)
    integrity = study.protocol_integrity if isinstance(study.protocol_integrity, dict) else {}
    if protocol_valid is None:
        protocol_valid = bool(
            integrity.get("valid")
            or integrity.get("is_valid")
            or integrity.get("protocol_valid")
            or integrity.get("formal_eligible")
        )
    terminal_audit = None
    if str(study.mode).upper() == "FORMAL" and str(study.status).upper() == "COMPLETED":
        from scheduler.services.formal_studies import terminal_formal_integrity

        terminal_audit = terminal_formal_integrity(study)
        protocol_valid = bool(protocol_valid and terminal_audit["valid"] and not study.invalid_reason)
    report = analyze_study_trials(
        records,
        deadline_seconds=float(study.deadline_seconds),
        mode=str(study.mode),
        study_status=str(study.status),
        protocol_valid=protocol_valid,
        expected_scales=tuple(study.scale_percentages),
        expected_seeds=tuple(study.seeds),
        resamples=resamples,
        random_seed=int(study.order_seed),
    )
    if terminal_audit is not None:
        report["integrity"]["terminal_audit"] = terminal_audit
    return report


def trial_observations_from_study(study: Any) -> tuple[TrialObservation, ...]:
    """Create de-identified observations from persisted experiment runs."""

    from scheduler.services.secondary_metrics import secondary_trial_metadata

    formal = str(study.mode).upper() == "FORMAL"
    observations: list[TrialObservation] = []
    batches = study.batches.select_related(
        "snapshot", "snapshot__objective_profile"
    ).order_by("planned_scale_percentage", "pk")
    for batch in batches:
        scale = int(batch.planned_scale_percentage or 100)
        runs = list(batch.runs.select_related("validation_result", "snapshot").order_by(
            "seed", "algorithm", "pair_attempt", "pk"
        ))
        secondary = secondary_trial_metadata(runs, formal=formal)
        for run in runs:
            purpose = str(getattr(run, "purpose", "ROUTINE"))
            failure_category = str(getattr(run, "failure_category", "") or "")
            terminal = bool(getattr(run, "is_terminal", run.status in _TERMINAL_STATUSES))
            included = bool(getattr(run, "included_in_analysis", True))
            measured_purpose = purpose == "MEASURED" if formal else purpose in {
                "MEASURED",
                "ROUTINE",
            }
            is_eligible = bool(
                included
                and measured_purpose
                and terminal
                and failure_category not in _EXCLUDED_FAILURE_CATEGORIES
            )
            try:
                validation = run.validation_result
            except Exception as exc:  # Django's RelatedObjectDoesNotExist subclasses AttributeError.
                if exc.__class__.__name__ != "RelatedObjectDoesNotExist" and not isinstance(
                    exc, AttributeError
                ):
                    raise
                validation = None
            feasible = bool(
                validation
                and validation.is_feasible
                and validation.hard_violation_count == 0
            )
            validator_feasible = feasible
            if formal:
                first_time = _optional_finite(run.first_feasible_seconds)
                feasible = bool(feasible and first_time is not None and 0 <= first_time <= float(
                    run.configuration.get("time_limit_seconds", batch.time_limit_seconds)
                ) and run.status in {"FEASIBLE", "OPTIMAL"})
            raw_penalty = (
                float(validation.raw_soft_penalty)
                if validation is not None and feasible
                else None
            )
            components = _normalized_components(
                validation.objective_breakdown if validation is not None else {}
            )
            violations = _violation_counts(
                validation.violations if validation is not None else {}
            )
            snapshot = run.snapshot
            objective_hash = getattr(snapshot.objective_profile, "profile_hash", "")
            comparison_signature = _comparison_signature(
                snapshot_hash=str(snapshot.snapshot_hash),
                rule_manifest_hash=str(snapshot.constraint_manifest_hash),
                objective_hash=str(objective_hash),
                deadline_seconds=float(run.configuration.get("time_limit_seconds", batch.time_limit_seconds)),
                worker_count=int(run.configuration.get("worker_count", batch.cpu_limit)),
                source_commit=str(getattr(run, "source_commit", "") or ""),
                container_image=str(getattr(run, "container_image", "") or ""),
                dependency_versions=getattr(run, "dependency_versions", {}),
                configuration_signature=study.protocol_manifest.get("solver_profiles", batch.configuration),
            )
            if formal and not all((
                run.source_commit,
                run.container_image,
                run.dependency_versions,
                snapshot.constraint_manifest_hash,
                objective_hash,
                study.protocol_manifest.get("solver_profiles"),
            )):
                comparison_signature = ""
            observations.append(
                TrialObservation(
                    scale_percentage=scale,
                    seed=int(run.seed),
                    algorithm=_normalize_algorithm(str(run.algorithm)),
                    eligible=is_eligible,
                    independently_feasible=feasible,
                    first_feasible_seconds=(
                        float(run.first_feasible_seconds)
                        if feasible and run.first_feasible_seconds is not None
                        else None
                    ),
                    raw_penalty=raw_penalty,
                    meeting_count=int(snapshot.event_count),
                    objective_components=components,
                    hard_violation_categories=violations,
                    validator_present=validation is not None,
                    purpose=purpose,
                    pair_attempt=int(getattr(run, "pair_attempt", 1)),
                    status=str(run.status),
                    failure_category=failure_category,
                    exclusion_reason=str(getattr(run, "exclusion_reason", "") or ""),
                    comparison_signature=comparison_signature,
                    metadata={
                        **secondary[run.pk],
                        "validator_feasible": validator_feasible,
                        "budget_feasible": feasible,
                        "planned_order": getattr(run, "planned_order", None),
                        "actual_order": getattr(run, "actual_order", None),
                        "execution_seconds": _optional_finite(run.execution_seconds),
                        "process_cpu_seconds": _optional_finite(
                            getattr(run, "process_cpu_seconds", None)
                        ),
                        "peak_rss_mb": _optional_finite(getattr(run, "peak_rss_mb", None)),
                        "stopping_reason": str(run.stopping_reason or ""),
                        "snapshot_hash": str(snapshot.snapshot_hash),
                        "rule_manifest_hash": str(
                            getattr(snapshot, "constraint_manifest_hash", "") or ""
                        ),
                        "objective_profile_hash": str(objective_hash),
                        "configuration_hash": str(
                            getattr(run, "configuration_hash", "") or ""
                        ),
                        "source_commit": str(getattr(run, "source_commit", "") or ""),
                        "container_image": str(
                            getattr(run, "container_image", "") or ""
                        ),
                        "dependency_versions": getattr(run, "dependency_versions", {})
                        if isinstance(getattr(run, "dependency_versions", {}), dict)
                        else {},
                        "deadline_seconds": float(batch.time_limit_seconds),
                    },
                )
            )
    return tuple(observations)


def kaplan_meier_coordinates(
    times: Sequence[float],
    events: Sequence[bool],
    *,
    deadline: float,
) -> list[dict[str, int | float]]:
    """Return auditable Kaplan-Meier step coordinates through ``deadline``."""

    if len(times) != len(events):
        raise ValueError("times and events must have equal lengths")
    if deadline <= 0:
        raise ValueError("deadline must be positive")
    observations = sorted(
        (min(max(float(time), 0.0), deadline), bool(event) and float(time) < deadline)
        for time, event in zip(times, events, strict=True)
    )
    points: list[dict[str, int | float]] = [
        {
            "time_seconds": 0.0,
            "survival_probability": 1.0,
            "at_risk": len(observations),
            "events": 0,
            "censored": 0,
        }
    ]
    at_risk = len(observations)
    survival = 1.0
    index = 0
    while index < len(observations):
        time_value = observations[index][0]
        event_count = 0
        censored_count = 0
        while index < len(observations) and observations[index][0] == time_value:
            if observations[index][1]:
                event_count += 1
            else:
                censored_count += 1
            index += 1
        before = at_risk
        if before and event_count:
            survival *= 1 - event_count / before
        points.append(
            {
                "time_seconds": time_value,
                "survival_probability": survival,
                "at_risk": before,
                "events": event_count,
                "censored": censored_count,
            }
        )
        at_risk -= event_count + censored_count
    if not points or float(points[-1]["time_seconds"]) < deadline:
        points.append(
            {
                "time_seconds": float(deadline),
                "survival_probability": survival,
                "at_risk": at_risk,
                "events": 0,
                "censored": 0,
            }
        )
    return points


def _analyze_scale(
    trials: Sequence[TrialObservation],
    *,
    deadline: float,
    resamples: int,
    random_seed: int,
) -> dict[str, Any]:
    by_algorithm = {
        algorithm: tuple(item for item in trials if item.algorithm == algorithm)
        for algorithm in ALGORITHMS
    }
    feasibility = _feasibility_outcome(by_algorithm, random_seed=random_seed)
    quality = _quality_outcome(
        by_algorithm,
        resamples=resamples,
        random_seed=_derived_seed(random_seed, "quality"),
    )
    time_result = _time_outcome(
        by_algorithm,
        deadline=deadline,
        resamples=resamples,
        random_seed=_derived_seed(random_seed, "time"),
    )
    outcomes = {
        "feasibility": feasibility,
        "schedule_quality": quality,
        "time_to_feasibility": time_result,
    }
    raw_p_values = [
        float(outcomes[name]["comparison"].get("p_value_two_sided", 1.0))
        if outcomes[name]["comparison"].get("available")
        else 1.0
        for name in PRIMARY_OUTCOMES
    ]
    adjusted = holm_adjust(raw_p_values)
    for name, adjusted_value in zip(PRIMARY_OUTCOMES, adjusted, strict=True):
        outcomes[name]["comparison"]["p_value_holm_adjusted"] = adjusted_value
    hard_categories = {
        algorithm: dict(
            sorted(
                sum(
                    (Counter(item.hard_violation_categories) for item in records),
                    Counter(),
                ).items()
            )
        )
        for algorithm, records in by_algorithm.items()
    }
    return {
        "eligible_trials": len(trials),
        "eligible_by_algorithm": {
            algorithm: len(by_algorithm[algorithm]) for algorithm in ALGORITHMS
        },
        "primary_outcomes": outcomes,
        "holm_family": {
            "outcomes": list(PRIMARY_OUTCOMES),
            "family_size": 3,
            "raw_p_values": dict(zip(PRIMARY_OUTCOMES, raw_p_values, strict=True)),
            "adjusted_p_values": dict(zip(PRIMARY_OUTCOMES, adjusted, strict=True)),
            "alpha": 0.05,
        },
        "hard_violation_categories": hard_categories,
    }


def _feasibility_outcome(
    by_algorithm: Mapping[str, Sequence[TrialObservation]], *, random_seed: int
) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for algorithm in ALGORITHMS:
        records = by_algorithm[algorithm]
        numerator = sum(item.independently_feasible for item in records)
        denominator = len(records)
        rate = numerator / denominator if denominator else None
        summaries[algorithm] = {
            "independently_feasible": numerator,
            "eligible_trials": denominator,
            "rate": rate,
            "percentage": rate * 100 if rate is not None else None,
            "wilson_95": list(wilson_interval(numerator, denominator))
            if denominator
            else None,
        }
    pairs = _paired_records(by_algorithm)
    cp_values = [int(left.independently_feasible) for left, _ in pairs]
    ga_values = [int(right.independently_feasible) for _, right in pairs]
    comparison = _paired_binary_exact_test(cp_values, ga_values)
    comparison.update(
        {
            "paired_seed_blocks": len(pairs),
            "percentage_point_difference_cp_sat_minus_ga": (
                (summaries[CP_SAT]["rate"] - summaries[GENETIC_ALGORITHM]["rate"]) * 100
                if summaries[CP_SAT]["rate"] is not None
                and summaries[GENETIC_ALGORITHM]["rate"] is not None
                else None
            ),
            "random_seed": random_seed,
        }
    )
    return {"by_algorithm": summaries, "comparison": comparison}


def _quality_outcome(
    by_algorithm: Mapping[str, Sequence[TrialObservation]],
    *,
    resamples: int,
    random_seed: int,
) -> dict[str, Any]:
    feasible = {
        algorithm: tuple(item for item in by_algorithm[algorithm] if item.independently_feasible)
        for algorithm in ALGORITHMS
    }
    summaries: dict[str, Any] = {}
    for algorithm in ALGORITHMS:
        records = feasible[algorithm]
        penalties = [item.raw_penalty for item in records if item.raw_penalty is not None]
        per_meeting = [
            value for item in records if (value := item.penalty_per_meeting) is not None
        ]
        components: dict[str, Any] = {}
        for component in _COMPONENT_ALIASES:
            values = [
                float(item.objective_components[component])
                for item in records
                if component in item.objective_components
            ]
            components[component] = _median_summary(
                values,
                resamples=resamples,
                seed=_derived_seed(random_seed, f"{algorithm}:{component}"),
            )
        summaries[algorithm] = {
            "independently_feasible_trials": len(records),
            "raw_weighted_soft_penalty": _median_summary(
                penalties,
                resamples=resamples,
                seed=_derived_seed(random_seed, f"{algorithm}:raw"),
            ),
            "penalty_per_meeting": _median_summary(
                per_meeting,
                resamples=resamples,
                seed=_derived_seed(random_seed, f"{algorithm}:per-meeting"),
            ),
            "objective_components": components,
        }
    cp_values = [
        item.raw_penalty for item in feasible[CP_SAT] if item.raw_penalty is not None
    ]
    ga_values = [
        item.raw_penalty
        for item in feasible[GENETIC_ALGORITHM]
        if item.raw_penalty is not None
    ]
    if cp_values and ga_values:
        test = unpaired_permutation_test(
            cp_values,
            ga_values,
            statistic="median",
            resamples=resamples,
            seed=random_seed,
        )
        comparison: dict[str, Any] = {
            "available": True,
            "method": "two-sided deterministic label-permutation test of medians",
            **test,
            "vargha_delaney_a12_cp_sat_lower_penalty": vargha_delaney_a12(
                cp_values, ga_values
            ),
            "median_difference_cp_sat_minus_ga_bootstrap_95": list(
                _bootstrap_two_sample_difference(
                    cp_values,
                    ga_values,
                    statistic="median",
                    resamples=resamples,
                    seed=_derived_seed(random_seed, "difference"),
                )
            ),
        }
    else:
        comparison = {
            "available": False,
            "reason": "Both algorithms require at least one independently feasible penalty.",
            "p_value_two_sided": 1.0,
            "cp_sat_n": len(cp_values),
            "ga_n": len(ga_values),
            "vargha_delaney_a12_cp_sat_lower_penalty": None,
            "median_difference_cp_sat_minus_ga_bootstrap_95": None,
        }
    return {"by_algorithm": summaries, "comparison": comparison}


def _time_outcome(
    by_algorithm: Mapping[str, Sequence[TrialObservation]],
    *,
    deadline: float,
    resamples: int,
    random_seed: int,
) -> dict[str, Any]:
    observations: dict[str, tuple[list[float], list[bool]]] = {}
    summaries: dict[str, Any] = {}
    for algorithm in ALGORITHMS:
        times: list[float] = []
        events: list[bool] = []
        for item in by_algorithm[algorithm]:
            observed = bool(
                item.independently_feasible
                and item.first_feasible_seconds is not None
                and 0 <= item.first_feasible_seconds <= deadline
            )
            times.append(
                min(float(item.first_feasible_seconds), deadline)
                if observed and item.first_feasible_seconds is not None
                else deadline
            )
            events.append(observed)
        observations[algorithm] = (times, events)
        rmst = (
            restricted_mean_time_to_feasibility(times, events, deadline)
            if times
            else None
        )
        summaries[algorithm] = {
            "eligible_trials": len(times),
            "observed_feasibility_events": sum(events),
            "right_censored_trials": len(times) - sum(events),
            "rmst_seconds": rmst,
            "rmst_bootstrap_95": list(
                _bootstrap_rmst_interval(
                    times,
                    events,
                    deadline=deadline,
                    resamples=resamples,
                    seed=_derived_seed(random_seed, f"{algorithm}:rmst"),
                )
            )
            if times
            else None,
            "kaplan_meier": kaplan_meier_coordinates(times, events, deadline=deadline),
        }
    pairs = _paired_records(by_algorithm)
    paired_observations = []
    for cp, ga in pairs:
        paired_observations.append(
            (
                _time_observation(cp, deadline),
                _time_observation(ga, deadline),
            )
        )
    if paired_observations:
        cp_times = [pair[0][0] for pair in paired_observations]
        cp_events = [pair[0][1] for pair in paired_observations]
        ga_times = [pair[1][0] for pair in paired_observations]
        ga_events = [pair[1][1] for pair in paired_observations]
        observed_difference = restricted_mean_time_to_feasibility(
            cp_times, cp_events, deadline
        ) - restricted_mean_time_to_feasibility(ga_times, ga_events, deadline)
        permutation = _paired_rmst_permutation_test(
            paired_observations,
            deadline=deadline,
            resamples=resamples,
            seed=random_seed,
        )
        comparison: dict[str, Any] = {
            "available": True,
            "method": "two-sided deterministic within-seed swap permutation of RMST difference",
            "paired_seed_blocks": len(paired_observations),
            "rmst_difference_cp_sat_minus_ga_seconds": observed_difference,
            "rmst_difference_bootstrap_95": list(
                _bootstrap_paired_rmst_difference(
                    paired_observations,
                    deadline=deadline,
                    resamples=resamples,
                    seed=_derived_seed(random_seed, "rmst-difference"),
                )
            ),
            **permutation,
        }
    else:
        comparison = {
            "available": False,
            "reason": "At least one complete CP-SAT/GA seed block is required.",
            "paired_seed_blocks": 0,
            "rmst_difference_cp_sat_minus_ga_seconds": None,
            "rmst_difference_bootstrap_95": None,
            "p_value_two_sided": 1.0,
        }
    return {
        "deadline_seconds": deadline,
        "censoring_rule": "non-feasible trials are right-censored at the solver deadline",
        "by_algorithm": summaries,
        "comparison": comparison,
    }


def _winner_decision(
    full_scale: Mapping[str, Any] | None,
    *,
    formal_mode: bool,
    study_complete: bool,
    protocol_valid: bool,
) -> dict[str, Any]:
    thresholds = {
        "feasibility_percentage_points": 5.0,
        "median_penalty_relative_reduction": 0.05,
        "rmst_relative_reduction": 0.10,
        "holm_adjusted_alpha": 0.05,
    }
    if not formal_mode or not study_complete or not protocol_valid or full_scale is None:
        return {
            "winner": None,
            "claimable": False,
            "deciding_outcome": None,
            "conclusion": NO_FORMAL_CONCLUSION,
            "thresholds": thresholds,
        }

    outcomes = full_scale["primary_outcomes"]
    feasibility = outcomes["feasibility"]
    feasibility_comparison = feasibility["comparison"]
    difference_pp = feasibility_comparison.get(
        "percentage_point_difference_cp_sat_minus_ga"
    )
    if (
        feasibility_comparison.get("available")
        and difference_pp is not None
        and abs(float(difference_pp)) >= thresholds["feasibility_percentage_points"]
        and float(feasibility_comparison["p_value_holm_adjusted"])
        <= thresholds["holm_adjusted_alpha"]
    ):
        winner = CP_SAT if float(difference_pp) > 0 else GENETIC_ALGORITHM
        return _winner_payload(
            winner,
            "feasibility",
            (
                f"{_algorithm_label(winner)} demonstrated the preferred feasibility rate "
                "on the complete protocol-valid 100% instance."
            ),
            thresholds,
        )

    quality = outcomes["schedule_quality"]
    quality_comparison = quality["comparison"]
    cp_penalty = quality["by_algorithm"][CP_SAT]["raw_weighted_soft_penalty"]["median"]
    ga_penalty = quality["by_algorithm"][GENETIC_ALGORITHM][
        "raw_weighted_soft_penalty"
    ]["median"]
    penalty_reduction, penalty_winner = _relative_reduction(
        cp_penalty, ga_penalty, CP_SAT, GENETIC_ALGORITHM
    )
    if (
        quality_comparison.get("available")
        and penalty_winner
        and penalty_reduction is not None
        and penalty_reduction >= thresholds["median_penalty_relative_reduction"]
        and float(quality_comparison["p_value_holm_adjusted"])
        <= thresholds["holm_adjusted_alpha"]
    ):
        return _winner_payload(
            penalty_winner,
            "schedule_quality",
            (
                f"{_algorithm_label(penalty_winner)} demonstrated the preferred median "
                "feasible raw penalty on the complete protocol-valid 100% instance."
            ),
            thresholds,
        )

    time_result = outcomes["time_to_feasibility"]
    time_comparison = time_result["comparison"]
    cp_rmst = time_result["by_algorithm"][CP_SAT]["rmst_seconds"]
    ga_rmst = time_result["by_algorithm"][GENETIC_ALGORITHM]["rmst_seconds"]
    rmst_reduction, rmst_winner = _relative_reduction(
        cp_rmst, ga_rmst, CP_SAT, GENETIC_ALGORITHM
    )
    interval = time_comparison.get("rmst_difference_bootstrap_95")
    supported_direction = bool(
        isinstance(interval, list)
        and len(interval) == 2
        and (
            (rmst_winner == CP_SAT and float(interval[1]) < 0)
            or (rmst_winner == GENETIC_ALGORITHM and float(interval[0]) > 0)
        )
    )
    if (
        time_comparison.get("available")
        and rmst_winner
        and rmst_reduction is not None
        and rmst_reduction >= thresholds["rmst_relative_reduction"]
        and float(time_comparison["p_value_holm_adjusted"])
        <= thresholds["holm_adjusted_alpha"]
        and supported_direction
    ):
        return _winner_payload(
            rmst_winner,
            "time_to_feasibility",
            (
                f"{_algorithm_label(rmst_winner)} demonstrated the preferred RMST time "
                "to feasibility on the complete protocol-valid 100% instance."
            ),
            thresholds,
        )

    return {
        "winner": None,
        "claimable": True,
        "deciding_outcome": None,
        "conclusion": NO_MEANINGFUL_ADVANTAGE,
        "thresholds": thresholds,
    }


def _winner_payload(
    winner: str,
    deciding_outcome: str,
    conclusion: str,
    thresholds: Mapping[str, float],
) -> dict[str, Any]:
    return {
        "winner": winner,
        "winner_label": _algorithm_label(winner),
        "claimable": True,
        "deciding_outcome": deciding_outcome,
        "conclusion": conclusion,
        "thresholds": dict(thresholds),
    }


def _matrix_integrity(
    eligible: Sequence[TrialObservation],
    *,
    scales: Sequence[int],
    seeds: Sequence[int],
) -> dict[str, Any]:
    lookup = {
        (item.scale_percentage, item.seed, item.algorithm): item for item in eligible
    }
    by_scale: dict[str, Any] = {}
    complete = True
    for scale in scales:
        missing: list[dict[str, int | str]] = []
        signature_mismatches: list[int] = []
        for seed in seeds:
            records = [lookup.get((scale, seed, algorithm)) for algorithm in ALGORITHMS]
            for algorithm, record in zip(ALGORITHMS, records, strict=True):
                if record is None:
                    missing.append({"seed": seed, "algorithm": algorithm})
            present = [record for record in records if record is not None]
            signatures = {record.comparison_signature for record in present}
            if len(signatures) > 1 or (present and "" in signatures):
                signature_mismatches.append(seed)
        scale_complete = not missing and not signature_mismatches
        complete = complete and scale_complete
        by_scale[str(scale)] = {
            "complete": scale_complete,
            "expected_trials": len(seeds) * len(ALGORITHMS),
            "eligible_trials": sum(item.scale_percentage == scale for item in eligible),
            "missing_cells": missing,
            "comparison_signature_mismatch": signature_mismatches,
        }
    return {
        "complete": complete,
        "expected_scales": list(scales),
        "expected_seeds": list(seeds),
        "expected_measured_trials": len(scales) * len(seeds) * len(ALGORITHMS),
        "eligible_measured_trials": len(eligible),
        "by_scale": by_scale,
    }


def _assert_unique_eligible_cells(trials: Sequence[TrialObservation]) -> None:
    seen: set[tuple[int, int, str]] = set()
    duplicates: list[tuple[int, int, str]] = []
    for item in trials:
        key = (item.scale_percentage, item.seed, item.algorithm)
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    if duplicates:
        rendered = ", ".join(f"{scale}%/{seed}/{algorithm}" for scale, seed, algorithm in duplicates)
        raise ValueError(f"eligible analysis cells must be unique; duplicates: {rendered}")


def _paired_records(
    by_algorithm: Mapping[str, Sequence[TrialObservation]],
) -> list[tuple[TrialObservation, TrialObservation]]:
    cp = {item.seed: item for item in by_algorithm[CP_SAT]}
    ga = {item.seed: item for item in by_algorithm[GENETIC_ALGORITHM]}
    return [(cp[seed], ga[seed]) for seed in sorted(cp.keys() & ga.keys())]


def _paired_binary_exact_test(first: Sequence[int], second: Sequence[int]) -> dict[str, Any]:
    if len(first) != len(second) or not first:
        return {
            "available": False,
            "reason": "At least one complete paired seed block is required.",
            "method": "two-sided exact paired sign test",
            "discordant_pairs": 0,
            "p_value_two_sided": 1.0,
        }
    first_only = sum(left == 1 and right == 0 for left, right in zip(first, second, strict=True))
    second_only = sum(left == 0 and right == 1 for left, right in zip(first, second, strict=True))
    discordant = first_only + second_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(
            math.comb(discordant, index) for index in range(min(first_only, second_only) + 1)
        ) / (2**discordant)
        p_value = min(1.0, 2 * tail)
    return {
        "available": True,
        "method": "two-sided exact paired sign test on discordant seed blocks",
        "discordant_pairs": discordant,
        "cp_sat_only_feasible": first_only,
        "ga_only_feasible": second_only,
        "p_value_two_sided": p_value,
    }


def _paired_rmst_permutation_test(
    pairs: Sequence[tuple[tuple[float, bool], tuple[float, bool]]],
    *,
    deadline: float,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    observed = _rmst_pair_difference(pairs, deadline)
    pair_count = len(pairs)
    if pair_count <= 12:
        masks: Iterable[int] = range(2**pair_count)
        evaluated = 2**pair_count
        exact = True
    else:
        randomizer = Random(seed)
        masks = (randomizer.getrandbits(pair_count) for _ in range(resamples))
        evaluated = resamples
        exact = False
    extreme = 0
    for mask in masks:
        permuted = [
            (right, left) if mask & (1 << index) else (left, right)
            for index, (left, right) in enumerate(pairs)
        ]
        difference = _rmst_pair_difference(permuted, deadline)
        if abs(difference) >= abs(observed) - 1e-15:
            extreme += 1
    p_value = extreme / evaluated if exact else (extreme + 1) / (evaluated + 1)
    return {
        "observed_difference_first_minus_second": observed,
        "p_value_two_sided": p_value,
        "permutations": evaluated,
        "exact": exact,
        "seed": seed,
    }


def _rmst_pair_difference(
    pairs: Sequence[tuple[tuple[float, bool], tuple[float, bool]]], deadline: float
) -> float:
    first_times = [left[0] for left, _ in pairs]
    first_events = [left[1] for left, _ in pairs]
    second_times = [right[0] for _, right in pairs]
    second_events = [right[1] for _, right in pairs]
    return restricted_mean_time_to_feasibility(
        first_times, first_events, deadline
    ) - restricted_mean_time_to_feasibility(second_times, second_events, deadline)


def _bootstrap_paired_rmst_difference(
    pairs: Sequence[tuple[tuple[float, bool], tuple[float, bool]]],
    *,
    deadline: float,
    resamples: int,
    seed: int,
) -> tuple[float, float]:
    randomizer = Random(seed)
    estimates = sorted(
        _rmst_pair_difference(randomizer.choices(pairs, k=len(pairs)), deadline)
        for _ in range(resamples)
    )
    return _percentile_interval(estimates)


def _bootstrap_rmst_interval(
    times: Sequence[float],
    events: Sequence[bool],
    *,
    deadline: float,
    resamples: int,
    seed: int,
) -> tuple[float, float]:
    observations = list(zip(times, events, strict=True))
    randomizer = Random(seed)
    estimates: list[float] = []
    for _ in range(resamples):
        sample = randomizer.choices(observations, k=len(observations))
        sample_times = [item[0] for item in sample]
        sample_events = [item[1] for item in sample]
        estimates.append(
            restricted_mean_time_to_feasibility(sample_times, sample_events, deadline)
        )
    return _percentile_interval(sorted(estimates))


def _bootstrap_two_sample_difference(
    first: Sequence[float],
    second: Sequence[float],
    *,
    statistic: str,
    resamples: int,
    seed: int,
) -> tuple[float, float]:
    estimator = median if statistic == "median" else None
    if estimator is None:
        raise ValueError("unsupported bootstrap statistic")
    randomizer = Random(seed)
    estimates = sorted(
        float(estimator(randomizer.choices(first, k=len(first))))
        - float(estimator(randomizer.choices(second, k=len(second))))
        for _ in range(resamples)
    )
    return _percentile_interval(estimates)


def _percentile_interval(sorted_values: Sequence[float]) -> tuple[float, float]:
    return _quantile(sorted_values, 0.025), _quantile(sorted_values, 0.975)


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("quantile requires observations")
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return float(
        sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction
    )


def _median_summary(values: Sequence[float], *, resamples: int, seed: int) -> dict[str, Any]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        return {"count": 0, "median": None, "median_bootstrap_95": None}
    return {
        "count": len(finite),
        "median": float(median(finite)),
        "median_bootstrap_95": list(
            bootstrap_median_interval(finite, resamples=resamples, seed=seed)
        ),
        "values": finite,
    }


def _time_observation(item: TrialObservation, deadline: float) -> tuple[float, bool]:
    event = bool(
        item.independently_feasible
        and item.first_feasible_seconds is not None
        and 0 <= item.first_feasible_seconds <= deadline
    )
    return (
        min(float(item.first_feasible_seconds), deadline)
        if event and item.first_feasible_seconds is not None
        else deadline,
        event,
    )


def _relative_reduction(
    first: float | None,
    second: float | None,
    first_label: str,
    second_label: str,
) -> tuple[float | None, str | None]:
    if first is None or second is None or first == second:
        return None, None
    lower, higher, winner = (
        (first, second, first_label) if first < second else (second, first, second_label)
    )
    if higher <= 0:
        return None, None
    return (higher - lower) / higher, winner


def _coerce_trial(item: TrialObservation | Mapping[str, Any]) -> TrialObservation:
    if isinstance(item, TrialObservation):
        return item
    if not isinstance(item, Mapping):
        raise TypeError("trial records must be TrialObservation or mapping values")
    scale = int(item.get("scale_percentage", item.get("scale", 100)))
    if not 1 <= scale <= 100:
        raise ValueError("scale_percentage must be between 1 and 100")
    algorithm = _normalize_algorithm(str(item.get("algorithm", "")))
    raw_components = item.get("objective_components", {})
    raw_violations = item.get("hard_violation_categories", {})
    return TrialObservation(
        scale_percentage=scale,
        seed=int(item["seed"]),
        algorithm=algorithm,
        eligible=bool(item.get("eligible", True)),
        independently_feasible=bool(
            item.get("independently_feasible", item.get("feasible", False))
        ),
        first_feasible_seconds=_optional_finite(item.get("first_feasible_seconds")),
        raw_penalty=_optional_finite(item.get("raw_penalty", item.get("objective_value"))),
        meeting_count=max(0, int(item.get("meeting_count", 0))),
        objective_components=_normalized_components(raw_components),
        hard_violation_categories=_violation_counts(raw_violations),
        validator_present=bool(item.get("validator_present", True)),
        purpose=str(item.get("purpose", "MEASURED")).upper(),
        pair_attempt=max(1, int(item.get("pair_attempt", 1))),
        status=str(item.get("status", "")),
        failure_category=str(item.get("failure_category", "") or "").upper(),
        exclusion_reason=str(item.get("exclusion_reason", "") or ""),
        comparison_signature=str(item.get("comparison_signature", "") or ""),
        metadata={
            str(key): value
            for key, value in item.items()
            if key
            not in {
                "scale_percentage",
                "scale",
                "seed",
                "algorithm",
                "eligible",
                "independently_feasible",
                "feasible",
                "first_feasible_seconds",
                "raw_penalty",
                "objective_value",
                "meeting_count",
                "objective_components",
                "hard_violation_categories",
                "validator_present",
                "purpose",
                "pair_attempt",
                "status",
                "failure_category",
                "exclusion_reason",
                "comparison_signature",
            }
        },
    )


def _normalize_algorithm(value: str) -> str:
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "CP_SAT": CP_SAT,
        "CPSAT": CP_SAT,
        "GA": GENETIC_ALGORITHM,
        "GENETIC_ALGORITHM": GENETIC_ALGORITHM,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported algorithm: {value!r}") from exc


def _normalized_components(payload: Any) -> dict[str, float]:
    if not isinstance(payload, Mapping):
        return {}
    normalized: dict[str, float] = {}
    for canonical, aliases in _COMPONENT_ALIASES.items():
        for alias in aliases:
            value = _optional_finite(payload.get(alias))
            if value is not None:
                normalized[canonical] = value
                break
    return normalized


def _violation_counts(payload: Any) -> dict[str, int]:
    if not isinstance(payload, Mapping):
        return {}
    counts = payload.get("counts", payload)
    if not isinstance(counts, Mapping):
        return {}
    return {
        str(code): int(value)
        for code, value in counts.items()
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    }


def _optional_finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _derived_seed(base: int, label: str) -> int:
    digest = hashlib.sha256(f"{base}:{label}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _comparison_signature(**values: Any) -> str:
    material = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()


def _algorithm_label(algorithm: str) -> str:
    return "CP-SAT" if algorithm == CP_SAT else "Genetic Algorithm"
