"""Controlled CP-SAT/GA experiment orchestration and reporting.

This module keeps benchmark runs honest: every seed receives one run from each
algorithm over the same immutable snapshot, order is deterministically
randomized within each seed block, and execution is submitted sequentially.
Failed and timed-out runs remain in every denominator.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import platform
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from importlib import metadata as package_metadata
from itertools import combinations
from random import Random
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from scheduler import models
from scheduler.services.runs import build_solver_config, create_run, execute_run, queue_run
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

DEFAULT_EXPERIMENT_SEEDS = tuple(range(1001, 1031))
RETRY_EPISODE_CAP = 5
PROTOCOL_VERSION = "1.0"
BENCHMARK_SCHEMA_VERSION = "1.0"
DEFAULT_MEMORY_LIMIT_MB = int(getattr(settings, "SOLVER_MEMORY_LIMIT_MB", 2048))
SENSITIVITY_MULTIPLIERS = (0.5, 1.0, 2.0)
SUCCESS_STATUSES = {models.RunStatus.FEASIBLE, models.RunStatus.OPTIMAL}
TERMINAL_STATUSES = {
    models.RunStatus.FEASIBLE,
    models.RunStatus.OPTIMAL,
    models.RunStatus.INFEASIBLE,
    models.RunStatus.NO_SOLUTION,
    models.RunStatus.TIMEOUT,
    models.RunStatus.CANCELLED,
    models.RunStatus.FAILED,
}

_RUNTIME_DISTRIBUTIONS = (
    "Django",
    "djangorestframework",
    "celery",
    "redis",
    "ortools",
    "openpyxl",
    "pandas",
    "scipy",
    "psycopg",
    "gunicorn",
    "whitenoise",
)


def environment_manifest() -> dict[str, Any]:
    """Capture the execution environment needed to interpret benchmark results."""

    packages: dict[str, str | None] = {}
    for distribution in _RUNTIME_DISTRIBUTIONS:
        try:
            packages[distribution] = package_metadata.version(distribution)
        except package_metadata.PackageNotFoundError:
            packages[distribution] = None
    manifest: dict[str, Any] = {
        "build": {
            "app_build_id": os.getenv("APP_BUILD_ID") or None,
            "source_commit": os.getenv("SOURCE_COMMIT") or None,
            "container_image_id": os.getenv("CONTAINER_IMAGE_ID") or None,
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "logical_cpu_count": os.cpu_count(),
        "packages": packages,
    }
    return {**manifest, "manifest_hash": models.canonical_sha256(manifest)}


def deterministic_execution_order(
    seeds: Iterable[int], order_seed: int
) -> tuple[dict[str, Any], ...]:
    """Return adjacent, deterministically randomized CP-SAT/GA seed blocks."""

    normalized = _validated_seeds(seeds)
    if type(order_seed) is not int or order_seed < 0:
        raise ValueError("order_seed must be a non-negative integer")
    randomizer = Random(order_seed)
    entries: list[dict[str, Any]] = []
    position = 0
    for seed in normalized:
        algorithms = [
            models.SolverAlgorithm.CP_SAT,
            models.SolverAlgorithm.GENETIC_ALGORITHM,
        ]
        randomizer.shuffle(algorithms)
        for within_seed_position, algorithm in enumerate(algorithms):
            entries.append(
                {
                    "position": position,
                    "seed": seed,
                    "within_seed_position": within_seed_position,
                    "algorithm": algorithm,
                }
            )
            position += 1
    return tuple(entries)


@transaction.atomic
def create_experiment_batch(
    snapshot: models.ProblemSnapshot,
    user: models.User,
    seeds: Iterable[int] = DEFAULT_EXPERIMENT_SEEDS,
    time_limit: int = 300,
    order_seed: int = 0,
    *,
    name: str | None = None,
    memory_limit_mb: int | None = DEFAULT_MEMORY_LIMIT_MB,
    run_configuration: Mapping[str, Any] | None = None,
) -> models.ExperimentBatch:
    """Create one draft batch and its complete immutable run matrix."""

    if not models.ObjectiveProfile.objects.filter(
        pk=snapshot.objective_profile_id,
        is_approved=True,
        approved_by__isnull=False,
    ).exists():
        raise ValueError("experiment batches require an approved objective profile")
    normalized_seeds = _validated_seeds(seeds)
    if type(time_limit) is not int or time_limit <= 0:
        raise ValueError("time_limit must be a positive integer number of seconds")
    if memory_limit_mb is None:
        memory_limit_mb = DEFAULT_MEMORY_LIMIT_MB
    if type(memory_limit_mb) is not int or memory_limit_mb <= 0:
        raise ValueError("memory_limit_mb must be positive when provided")
    if memory_limit_mb != DEFAULT_MEMORY_LIMIT_MB:
        raise ValueError(
            "memory_limit_mb must match the configured single-worker memory ceiling "
            f"({DEFAULT_MEMORY_LIMIT_MB} MB)"
        )
    custom_configuration = dict(run_configuration or {})
    _ensure_json_object(custom_configuration, "run_configuration")
    plan = deterministic_execution_order(normalized_seeds, order_seed)
    manifest = environment_manifest()
    study_name = name or f"CP-SAT vs GA / {snapshot.snapshot_hash[:12]}"
    study = models.ExperimentStudy(
        name=study_name,
        mode=models.ExperimentMode.EXPLORATORY,
        protocol_version="exploratory-v1",
        status=models.StudyStatus.DRAFT,
        source_snapshot=snapshot,
        scale_percentages=[100],
        seeds=list(normalized_seeds),
        order_seed=order_seed,
        deadline_seconds=time_limit,
        cpu_limit=1,
        memory_limit_mb=memory_limit_mb,
        warmups_per_algorithm_scale=0,
        protocol_manifest={
            "classification": "exploratory",
            "configurable": True,
            "formal_conclusion_allowed": False,
            "environment_manifest_hash": manifest["manifest_hash"],
        },
        protocol_integrity={
            "formal_eligible": False,
            "reason": (
                "Configurable exploratory protocol; this study cannot produce a formal winner."
            ),
        },
        created_by=user,
    )
    study.full_clean()
    study.save()
    batch = models.ExperimentBatch(
        name=study_name,
        study=study,
        snapshot=snapshot,
        seeds=list(normalized_seeds),
        order_seed=order_seed,
        time_limit_seconds=time_limit,
        cpu_limit=1,
        memory_limit_mb=memory_limit_mb,
        configuration={
            "protocol_version": PROTOCOL_VERSION,
            "sequential_execution": True,
            "retry_episode_cap": RETRY_EPISODE_CAP,
            "execution_order": list(plan),
            "environment_manifest": manifest,
            "run_configuration": custom_configuration,
        },
        created_by=user,
    )
    batch.full_clean()
    batch.save()

    persisted_plan: list[dict[str, Any]] = []
    for entry in plan:
        configuration = {
            **custom_configuration,
            "benchmark_protocol": PROTOCOL_VERSION,
            "experiment_order_index": entry["position"],
            "order_seed": order_seed,
            "time_limit_seconds": time_limit,
            "worker_count": 1,
        }
        run = create_run(
            snapshot=snapshot,
            algorithm=entry["algorithm"],
            requested_by=user,
            seed=entry["seed"],
            configuration=configuration,
            experiment_batch=batch,
        )
        persisted_plan.append({**entry, "run_id": run.pk})

    batch.configuration = {**batch.configuration, "execution_order": persisted_plan}
    batch._allow_protocol_update = True
    batch.full_clean()
    batch.save(update_fields=["configuration", "updated_at"])
    models.AuditLog.objects.create(
        actor=user,
        action="experiment.created",
        entity_type="ExperimentBatch",
        entity_id=str(batch.pk),
        details={
            "snapshot_id": snapshot.pk,
            "snapshot_hash": snapshot.snapshot_hash,
            "seeds": list(normalized_seeds),
            "time_limit_seconds": time_limit,
            "order_seed": order_seed,
            "memory_limit_mb": memory_limit_mb,
            "environment_manifest_hash": manifest["manifest_hash"],
        },
    )
    return batch


def ordered_experiment_runs(batch: models.ExperimentBatch) -> list[models.ScheduleRun]:
    """Load runs in the frozen protocol order, independent of database ordering."""

    runs = list(
        batch.runs.select_related("snapshot", "validation_result").prefetch_related("metrics")
    )
    by_id = {run.pk: run for run in runs}
    plan = batch.configuration.get("execution_order", [])
    ordered = [by_id[item["run_id"]] for item in plan if item.get("run_id") in by_id]
    seen = {run.pk for run in ordered}
    remainder = sorted(
        (run for run in runs if run.pk not in seen),
        key=lambda run: (
            _order_integer(run.configuration.get("experiment_order_index")),
            run.seed,
            run.algorithm,
            run.pk,
        ),
    )
    return ordered + remainder


def execute_experiment_batch(batch: models.ExperimentBatch) -> models.ExperimentBatch:
    """Execute all still-queued runs serially in the calling process.

    One failed run does not hide or prevent later observations. The run service
    persists its own failure, and the batch becomes ``FAILED`` after all planned
    runs have been attempted.
    """

    batch.refresh_from_db()
    if batch.status in {models.ExperimentStatus.COMPLETED, models.ExperimentStatus.CANCELLED}:
        return batch
    _set_batch_status(batch, models.ExperimentStatus.QUEUED)
    _set_batch_status(batch, models.ExperimentStatus.RUNNING)
    for run in ordered_experiment_runs(batch):
        run.refresh_from_db()
        if run.status in TERMINAL_STATUSES:
            continue
        if run.status != models.RunStatus.QUEUED:
            continue
        try:
            execute_run(run.pk)
        except Exception as exc:  # The remaining benchmark observations still run.
            models.ScheduleRun.objects.filter(
                pk=run.pk,
                status__in={models.RunStatus.QUEUED, models.RunStatus.RUNNING},
            ).update(
                status=models.RunStatus.FAILED,
                finished_at=timezone.now(),
                stopping_reason="Unhandled experiment execution error",
                error_message=f"{type(exc).__name__}: {exc}",
            )
    return refresh_experiment_status(batch)


def queue_experiment_batch(batch: models.ExperimentBatch) -> models.ExperimentBatch:
    """Submit runs to Celery in frozen order.

    Deployment fixes worker concurrency to one, so FIFO submission is also
    sequential execution. This function intentionally does not enqueue runs in
    parallel or conceal queue submission failures.
    """

    batch.refresh_from_db()
    if batch.status != models.ExperimentStatus.DRAFT:
        raise ValueError("only a draft experiment batch can be queued")
    _set_batch_status(batch, models.ExperimentStatus.QUEUED)
    try:
        for run in ordered_experiment_runs(batch):
            if run.status == models.RunStatus.QUEUED and not run.task_id:
                queue_run(run)
    except Exception:
        _set_batch_status(batch, models.ExperimentStatus.FAILED)
        raise
    batch.refresh_from_db()
    return batch


@transaction.atomic
def refresh_experiment_status(batch: models.ExperimentBatch) -> models.ExperimentBatch:
    """Derive and persist the batch lifecycle from all planned run states."""

    locked = models.ExperimentBatch.objects.select_for_update().get(pk=batch.pk)
    if locked.status == models.ExperimentStatus.CANCELLED:
        return locked
    statuses = list(locked.runs.values_list("status", flat=True))
    if not statuses:
        new_status = models.ExperimentStatus.DRAFT
    elif all(status in TERMINAL_STATUSES for status in statuses):
        if all(status == models.RunStatus.CANCELLED for status in statuses):
            new_status = models.ExperimentStatus.CANCELLED
        elif any(status == models.RunStatus.FAILED for status in statuses):
            new_status = models.ExperimentStatus.FAILED
        else:
            new_status = models.ExperimentStatus.COMPLETED
    elif locked.status == models.ExperimentStatus.DRAFT:
        new_status = models.ExperimentStatus.DRAFT
    elif locked.status == models.ExperimentStatus.FAILED:
        new_status = models.ExperimentStatus.FAILED
    elif any(status == models.RunStatus.RUNNING for status in statuses) or any(
        status in TERMINAL_STATUSES for status in statuses
    ):
        new_status = models.ExperimentStatus.RUNNING
    else:
        new_status = models.ExperimentStatus.QUEUED
    if locked.status != new_status:
        locked.status = new_status
        locked.save(update_fields=["status", "updated_at"])
    _sync_exploratory_study_status(locked, new_status)
    return locked


def summarize_experiment(batch: models.ExperimentBatch) -> dict[str, Any]:
    """Build the complete source-backed comparison report as a JSON-safe dict."""

    if batch.status not in {models.ExperimentStatus.DRAFT, models.ExperimentStatus.CANCELLED}:
        batch = refresh_experiment_status(batch)
    else:
        batch.refresh_from_db()
    runs = ordered_experiment_runs(batch)
    by_algorithm = {
        algorithm: [run for run in runs if run.algorithm == algorithm]
        for algorithm in models.SolverAlgorithm.values
    }
    algorithm_summaries = {
        algorithm: _summarize_algorithm(sample, batch.time_limit_seconds)
        for algorithm, sample in by_algorithm.items()
    }
    cp_runs = by_algorithm[models.SolverAlgorithm.CP_SAT]
    ga_runs = by_algorithm[models.SolverAlgorithm.GENETIC_ALGORITHM]
    observed_cp_runs = [run for run in cp_runs if run.status in TERMINAL_STATUSES]
    observed_ga_runs = [run for run in ga_runs if run.status in TERMINAL_STATUSES]
    cp_penalties = _feasible_values(observed_cp_runs, "objective_value")
    ga_penalties = _feasible_values(observed_ga_runs, "objective_value")
    cp_execution = _numeric_values(observed_cp_runs, "execution_seconds")
    ga_execution = _numeric_values(observed_ga_runs, "execution_seconds")
    cp_ttf = _censored_feasibility_times(observed_cp_runs, batch.time_limit_seconds)
    ga_ttf = _censored_feasibility_times(observed_ga_runs, batch.time_limit_seconds)
    comparative_tests = _comparative_tests(
        observed_cp_runs,
        observed_ga_runs,
        deadline=batch.time_limit_seconds,
    )
    primary_engine_decision = {
        **_primary_engine_decision(algorithm_summaries, comparative_tests),
        "evidence_class": "EXPLORATORY",
        "formal_claimable": False,
        "formal_conclusion": "No formal conclusion available.",
    }
    benchmark = _benchmark_summary(batch, runs, algorithm_summaries)

    return {
        "protocol_version": PROTOCOL_VERSION,
        "study_mode": models.ExperimentMode.EXPLORATORY,
        "formal_conclusion": "No formal conclusion available.",
        "batch": {
            "id": batch.pk,
            "name": batch.name,
            "status": batch.status,
            "snapshot_id": batch.snapshot_id,
            "snapshot_hash": batch.snapshot.snapshot_hash,
            "seeds": batch.seeds,
            "order_seed": batch.order_seed,
            "time_limit_seconds": batch.time_limit_seconds,
            "cpu_limit": batch.cpu_limit,
            "memory_limit_mb": batch.memory_limit_mb,
            "environment_manifest": batch.configuration.get("environment_manifest", {}),
            "requested_run_configuration": batch.configuration.get("run_configuration", {}),
            "objective_profile": {
                "id": batch.snapshot.objective_profile_id,
                "hash": batch.snapshot.objective_profile.profile_hash,
                "weights": batch.snapshot.objective_profile.weights,
                "definitions": batch.snapshot.objective_profile.definitions,
                "normalization_denominators": (
                    batch.snapshot.objective_profile.normalization_denominators
                ),
            },
            "created_at": batch.created_at.isoformat(),
        },
        "algorithms": algorithm_summaries,
        "benchmark": benchmark,
        "effect_sizes": {
            "cp_sat_probability_lower_feasible_penalty_a12": _a12_or_none(
                cp_penalties, ga_penalties
            ),
            "cp_sat_probability_faster_execution_a12": _a12_or_none(
                cp_execution, ga_execution
            ),
            "cp_sat_probability_faster_time_to_feasibility_a12": _a12_or_none(
                cp_ttf, ga_ttf
            ),
        },
        "comparative_tests": comparative_tests,
        "primary_engine_decision": primary_engine_decision,
        "placement_consistency": _placement_consistency(by_algorithm),
        "objective_weight_sensitivity": _objective_weight_sensitivity(batch, by_algorithm),
        "quality_metric_policy": {
            "primary": "feasible_soft_penalty",
            "primary_interpretation": "raw weighted soft penalty; lower is better",
            "secondary": [
                "feasible_penalty_per_meeting",
                "feasible_normalized_quality_score",
            ],
            "objective_component_weights": _objective_component_weights(batch.snapshot),
            "normalizer_review": _objective_normalizer_review(batch.snapshot),
        },
        "retry_episodes": _retry_episode_summary(runs),
        "room_utilization": _room_utilization_summary(batch, by_algorithm),
        "run_order": [
            {
                "position": index,
                "run_id": run.pk,
                "seed": run.seed,
                "algorithm": run.algorithm,
                "status": run.status,
            }
            for index, run in enumerate(runs)
        ],
    }


def _objective_normalizer_review(snapshot: models.ProblemSnapshot) -> dict[str, Any]:
    """Flag the known all-ones defaults without changing quality semantics."""

    expected = models.default_objective_normalizers()
    configured = snapshot.objective_profile.normalization_denominators
    values = configured if isinstance(configured, Mapping) else {}
    default_components = sorted(
        component
        for component, default_value in expected.items()
        if values.get(component) == default_value
    )
    all_defaults = len(default_components) == len(expected)
    return {
        "status": "placeholder_defaults" if all_defaults else "configured",
        "requires_stakeholder_review": all_defaults,
        "all_default_denominators_are_one": all_defaults,
        "default_components": default_components,
        "message": (
            "All objective normalizers use the placeholder denominator 1; interpret "
            "the normalized quality score as secondary until stakeholder review."
            if all_defaults
            else "Objective normalizers are not the complete all-ones default set."
        ),
    }


def _benchmark_summary(
    batch: models.ExperimentBatch,
    runs: Sequence[models.ScheduleRun],
    algorithm_summaries: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Project experiment evidence into a small, chart-safe benchmark contract.

    This deliberately contains no blended score. Each metric retains its own
    unit, direction, denominator, and uncertainty interval so a partial report
    cannot accidentally look like a completed head-to-head result.
    """

    issues = _experiment_protocol_issues(batch, runs)
    algorithm_ids = [
        models.SolverAlgorithm.CP_SAT,
        models.SolverAlgorithm.GENETIC_ALGORITHM,
    ]
    by_algorithm: dict[str, dict[str, Any]] = {}
    for algorithm in algorithm_ids:
        summary = algorithm_summaries[algorithm]
        planned = int(summary.get("planned_runs", 0))
        observed = int(summary.get("observed_runs", 0))
        pending = int(summary.get("pending_runs", 0))
        feasible = int(summary.get("feasible_runs", 0))
        rate = _optional_float(summary.get("success_rate"))
        interval = summary.get("success_rate_wilson_95")
        wilson = (
            list(interval)
            if isinstance(interval, (list, tuple)) and len(interval) == 2
            else [None, None]
        )
        penalty = _summary_median(summary.get("feasible_soft_penalty"))
        penalty_interval = summary.get("feasible_soft_penalty_median_bootstrap_95")
        penalty_bootstrap = (
            list(penalty_interval)
            if isinstance(penalty_interval, (list, tuple)) and len(penalty_interval) == 2
            else None
        )
        rmst = _optional_float(summary.get("rmst_time_to_feasibility_seconds"))
        by_algorithm[algorithm] = {
            "algorithm": algorithm,
            "label": dict(models.SolverAlgorithm.choices)[algorithm],
            "planned_runs": planned,
            "observed_runs": observed,
            "pending_runs": pending,
            "feasibility_rate": {
                "available": rate is not None,
                "value": rate,
                "wilson_95": wilson,
                "feasible_runs": feasible,
                "observed_runs": observed,
                "planned_runs": planned,
                "direction": "higher_is_better",
                "unavailable_reason": (
                    None if rate is not None else "No terminal runs have been observed."
                ),
            },
            "median_feasible_raw_penalty": {
                "available": penalty is not None,
                "value": penalty,
                "bootstrap_95": penalty_bootstrap,
                "feasible_runs": feasible,
                "direction": "lower_is_better",
                "unit": "raw_weighted_soft_penalty",
                "unavailable_reason": (
                    None
                    if penalty is not None
                    else "No feasible runs with a raw penalty have been observed."
                ),
            },
            "rmst_time_to_feasibility_seconds": {
                "available": rmst is not None,
                "value": rmst,
                "deadline_seconds": float(batch.time_limit_seconds),
                "observed_runs": observed,
                "censored_runs": int(summary.get("rmst_censored_runs", 0)),
                "direction": "lower_is_better",
                "unit": "seconds",
                "unavailable_reason": (
                    None if rmst is not None else "No terminal runs have been observed."
                ),
            },
        }

    observed_counts = [by_algorithm[item]["observed_runs"] for item in algorithm_ids]
    pending_total = sum(by_algorithm[item]["pending_runs"] for item in algorithm_ids)
    comparability_reasons: list[dict[str, str]] = []
    if issues:
        state = "invalid"
        comparable = False
        state_message = (
            "Benchmark invalid: controlled-experiment protocol integrity checks failed."
        )
        comparability_reasons.extend(
            {"code": str(issue["code"]), "message": str(issue["message"])}
            for issue in issues
        )
    elif not any(observed_counts):
        state = "unavailable"
        comparable = False
        state_message = "Benchmark unavailable: no terminal runs have been observed."
        comparability_reasons.append(
            {
                "code": "NO_TERMINAL_RUNS",
                "message": "At least one terminal run from each algorithm is required.",
            }
        )
    elif not all(observed_counts):
        state = "preliminary"
        comparable = False
        state_message = (
            "Preliminary benchmark: both algorithms need at least one terminal run "
            "before comparison."
        )
        comparability_reasons.append(
            {
                "code": "ONE_SIDED_EVIDENCE",
                "message": "Only one algorithm currently has terminal observations.",
            }
        )
    elif pending_total:
        state = "preliminary"
        comparable = True
        state_message = (
            "Preliminary benchmark: results may change while planned runs are pending."
        )
    else:
        state = "complete"
        comparable = True
        state_message = (
            "Benchmark complete: all planned CP-SAT and GA runs are terminal and "
            "protocol-compatible."
        )

    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "state": state,
        "state_message": state_message,
        "comparable": comparable,
        "comparability_reasons": comparability_reasons,
        "protocol_integrity": {
            "valid": not issues,
            "issues": issues,
        },
        "algorithm_ids": algorithm_ids,
        "by_algorithm": by_algorithm,
    }


def _experiment_protocol_issues(
    batch: models.ExperimentBatch, runs: Sequence[models.ScheduleRun]
) -> list[dict[str, Any]]:
    """Return deterministic protocol defects without discarding run evidence."""

    issues: list[dict[str, Any]] = []
    expected = {
        (int(seed), algorithm)
        for seed in batch.seeds
        for algorithm in (
            models.SolverAlgorithm.CP_SAT,
            models.SolverAlgorithm.GENETIC_ALGORITHM,
        )
    }
    observed_cells = [(run.seed, run.algorithm) for run in runs]
    observed = set(observed_cells)
    if len(observed_cells) != len(observed) or observed != expected:
        missing = sorted(expected - observed)
        unexpected = sorted(observed - expected)
        issues.append(
            {
                "code": "RUN_MATRIX_MISMATCH",
                "message": "The persisted run matrix does not contain exactly one run per seed and algorithm.",
                "missing_cells": [list(item) for item in missing],
                "unexpected_cells": [list(item) for item in unexpected],
            }
        )

    wrong_snapshot = [run.pk for run in runs if run.snapshot_id != batch.snapshot_id]
    if wrong_snapshot:
        issues.append(
            {
                "code": "SNAPSHOT_MISMATCH",
                "message": "One or more runs do not use the experiment snapshot.",
                "run_ids": wrong_snapshot,
            }
        )

    deadline_mismatch: list[int] = []
    worker_mismatch: list[int] = []
    protocol_mismatch: list[int] = []
    configuration_signatures: defaultdict[str, set[str]] = defaultdict(set)
    implementation_versions: defaultdict[str, set[str]] = defaultdict(set)
    configuration_errors: list[int] = []
    verification_failures: list[int] = []
    implementation_provenance_mismatches: list[int] = []
    expected_problem_hash: str | None = None
    has_problem_hash_evidence = any(
        run.status in TERMINAL_STATUSES
        and isinstance(run.diagnostics, Mapping)
        and bool(run.diagnostics.get("problem_hash"))
        for run in runs
    )
    if has_problem_hash_evidence:
        try:
            from scheduler.services.problem_builder import load_problem

            expected_problem_hash = load_problem(batch.snapshot).canonical_hash
        except (KeyError, TypeError, ValueError):
            # Some legacy/test snapshots predate the complete serialized-domain
            # contract. Explicit service-verification evidence remains authoritative.
            pass
    for run in runs:
        try:
            config = build_solver_config(run)
        except (TypeError, ValueError):
            configuration_errors.append(run.pk)
            continue
        if not math.isclose(
            config.time_limit_seconds,
            float(batch.time_limit_seconds),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            deadline_mismatch.append(run.pk)
        if config.worker_count != batch.cpu_limit:
            worker_mismatch.append(run.pk)
        if run.configuration.get("benchmark_protocol") != PROTOCOL_VERSION:
            protocol_mismatch.append(run.pk)
        configuration_signatures[run.algorithm].add(
            comparison_configuration_signature(run)
        )
        implementation_version = run_implementation_version(run)
        if run.status in TERMINAL_STATUSES or implementation_version != "legacy-unversioned":
            implementation_versions[run.algorithm].add(implementation_version)
        if _implementation_version_mismatch(run):
            implementation_provenance_mismatches.append(run.pk)
        diagnostics = run.diagnostics if isinstance(run.diagnostics, Mapping) else {}
        diagnostic_metrics = diagnostics.get("metrics", {})
        if run.status in TERMINAL_STATUSES and isinstance(diagnostic_metrics, Mapping):
            verification_passed = diagnostic_metrics.get("service_verification_passed")
            verification_failed = (
                verification_passed == 0
                or verification_passed == "0"
                or "reported_problem_hash" in diagnostic_metrics
                or "reported_config_hash" in diagnostic_metrics
            )
            diagnostic_config_hash = diagnostics.get("config_hash")
            diagnostic_problem_hash = diagnostics.get("problem_hash")
            verification_failed = verification_failed or bool(
                diagnostic_config_hash
                and diagnostic_config_hash != config.canonical_hash
            )
            verification_failed = verification_failed or bool(
                expected_problem_hash
                and diagnostic_problem_hash
                and diagnostic_problem_hash != expected_problem_hash
            )
            if verification_failed:
                verification_failures.append(run.pk)

    for code, message, run_ids in (
        (
            "INVALID_SOLVER_CONFIGURATION",
            "One or more run configurations cannot be resolved.",
            configuration_errors,
        ),
        (
            "DEADLINE_MISMATCH",
            "One or more runs do not use the experiment deadline.",
            deadline_mismatch,
        ),
        (
            "WORKER_COUNT_MISMATCH",
            "One or more runs do not use the experiment worker count.",
            worker_mismatch,
        ),
        (
            "PROTOCOL_VERSION_MISMATCH",
            "One or more runs do not carry the frozen benchmark protocol version.",
            protocol_mismatch,
        ),
        (
            "RESULT_VERIFICATION_FAILURE",
            "One or more terminal results failed independent service verification.",
            verification_failures,
        ),
        (
            "IMPLEMENTATION_PROVENANCE_MISMATCH",
            "One or more terminal results report a different implementation version than requested.",
            implementation_provenance_mismatches,
        ),
    ):
        if run_ids:
            issues.append({"code": code, "message": message, "run_ids": run_ids})

    for algorithm in (
        models.SolverAlgorithm.CP_SAT,
        models.SolverAlgorithm.GENETIC_ALGORITHM,
    ):
        if len(configuration_signatures[algorithm]) > 1:
            issues.append(
                {
                    "code": "ALGORITHM_CONFIGURATION_MISMATCH",
                    "message": f"{algorithm} runs contain multiple resolved configurations.",
                    "algorithm": algorithm,
                }
            )
        if len(implementation_versions[algorithm]) > 1:
            issues.append(
                {
                    "code": "IMPLEMENTATION_VERSION_MISMATCH",
                    "message": f"{algorithm} runs contain multiple implementation versions.",
                    "algorithm": algorithm,
                }
            )
    return issues


def comparison_configuration_signature(run: models.ScheduleRun) -> str:
    """Hash result-affecting solver parameters, excluding comparison strata."""

    resolved = build_solver_config(run).to_dict()
    for field in ("algorithm", "seed", "time_limit_seconds", "worker_count"):
        resolved.pop(field, None)
    return models.canonical_sha256(resolved)


def run_implementation_version(run: models.ScheduleRun) -> str:
    """Read immutable implementation provenance with an explicit legacy value."""

    configured = _configured_implementation_version(run)
    diagnostic_version = _diagnostic_implementation_version(run)
    if run.status in TERMINAL_STATUSES:
        return diagnostic_version or "legacy-unversioned"
    return configured or diagnostic_version or "legacy-unversioned"


def _configured_implementation_version(run: models.ScheduleRun) -> str | None:
    configured = run.configuration.get("implementation_version")
    if configured in (None, ""):
        configured = run.configuration.get("solver_implementation_version")
    return str(configured) if configured not in (None, "") else None


def _diagnostic_implementation_version(run: models.ScheduleRun) -> str | None:
    diagnostics = run.diagnostics if isinstance(run.diagnostics, Mapping) else {}
    metric_evidence = diagnostics.get("metrics", {})
    if isinstance(metric_evidence, Mapping):
        diagnostic_version = metric_evidence.get("implementation_version")
        if diagnostic_version not in (None, ""):
            return str(diagnostic_version)
    diagnostic_version = diagnostics.get("implementation_version")
    if diagnostic_version not in (None, ""):
        return str(diagnostic_version)
    return None


def _implementation_version_mismatch(run: models.ScheduleRun) -> bool:
    if run.status not in TERMINAL_STATUSES:
        return False
    configured = _configured_implementation_version(run)
    diagnostic = _diagnostic_implementation_version(run)
    return bool(configured and diagnostic and configured != diagnostic)


def snapshot_comparison_heterogeneity(
    runs: Sequence[models.ScheduleRun],
) -> dict[str, Any]:
    """Describe dimensions that make an ad-hoc snapshot comparison invalid."""

    deadlines: set[float] = set()
    worker_counts: set[int] = set()
    versions: defaultdict[str, set[str]] = defaultdict(set)
    configurations: defaultdict[str, set[str]] = defaultdict(set)
    invalid_configuration_runs: list[int] = []
    implementation_provenance_mismatch_runs: list[int] = []
    for run in runs:
        try:
            config = build_solver_config(run)
            deadlines.add(config.time_limit_seconds)
            worker_counts.add(config.worker_count)
            configurations[run.algorithm].add(comparison_configuration_signature(run))
        except (TypeError, ValueError):
            invalid_configuration_runs.append(run.pk)
        implementation_version = run_implementation_version(run)
        if run.status in TERMINAL_STATUSES or implementation_version != "legacy-unversioned":
            versions[run.algorithm].add(implementation_version)
        if _implementation_version_mismatch(run):
            implementation_provenance_mismatch_runs.append(run.pk)

    heterogeneous: dict[str, Any] = {}
    if len(deadlines) > 1:
        heterogeneous["time_limit_seconds"] = sorted(deadlines)
    if len(worker_counts) > 1:
        heterogeneous["worker_count"] = sorted(worker_counts)
    mixed_versions = {
        algorithm: sorted(values)
        for algorithm, values in versions.items()
        if len(values) > 1
    }
    if mixed_versions:
        heterogeneous["implementation_versions_by_algorithm"] = mixed_versions
    mixed_configurations = {
        algorithm: sorted(values)
        for algorithm, values in configurations.items()
        if len(values) > 1
    }
    if mixed_configurations:
        heterogeneous["configuration_hashes_by_algorithm"] = mixed_configurations
    if invalid_configuration_runs:
        heterogeneous["invalid_configuration_run_ids"] = invalid_configuration_runs
    if implementation_provenance_mismatch_runs:
        heterogeneous["implementation_provenance_mismatch_run_ids"] = (
            implementation_provenance_mismatch_runs
        )
    return heterogeneous


def export_experiment_json(batch: models.ExperimentBatch) -> bytes:
    """Export a complete summary and per-run evidence as deterministic UTF-8 JSON."""

    runs = ordered_experiment_runs(batch)
    payload = {
        "summary": summarize_experiment(batch),
        "runs": [_run_export_row(index, run) for index, run in enumerate(runs)],
    }
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True).encode(
        "utf-8"
    )


def export_experiment_csv(batch: models.ExperimentBatch) -> bytes:
    """Export one auditable row per attempted run as UTF-8 CSV."""

    output = io.StringIO(newline="")
    summary = summarize_experiment(batch)
    decision = summary["primary_engine_decision"]
    fieldnames = [
        "batch_id",
        "snapshot_hash",
        "primary_engine_winner",
        "primary_engine_deciding_tier",
        "primary_engine_decision_rationale",
        "order_index",
        "run_id",
        "seed",
        "algorithm",
        "meeting_count",
        "status",
        "feasible",
        "execution_seconds",
        "shared_preprocessing_seconds",
        "independent_validation_seconds",
        "end_to_end_processing_seconds",
        "first_feasible_seconds",
        "objective_value",
        "raw_soft_penalty",
        "penalty_per_meeting",
        "normalized_quality_score",
        "objective_breakdown",
        "hard_violation_count",
        "stopping_reason",
        "problem_hash",
        "config_hash",
        "solver_configuration",
        "hard_violation_vector",
        "assignments",
        "environment_manifest",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for index, run in enumerate(ordered_experiment_runs(batch)):
        row = _run_export_row(index, run)
        writer.writerow(
            {
                "batch_id": batch.pk,
                "snapshot_hash": batch.snapshot.snapshot_hash,
                "primary_engine_winner": decision["winner"],
                "primary_engine_deciding_tier": decision["deciding_tier"],
                "primary_engine_decision_rationale": decision["rationale"],
                "order_index": index,
                "run_id": run.pk,
                "seed": run.seed,
                "algorithm": run.algorithm,
                "meeting_count": run.snapshot.event_count,
                "status": run.status,
                "feasible": row["feasible"],
                "execution_seconds": run.execution_seconds,
                "shared_preprocessing_seconds": row["shared_preprocessing_seconds"],
                "independent_validation_seconds": row["independent_validation_seconds"],
                "end_to_end_processing_seconds": row["end_to_end_processing_seconds"],
                "first_feasible_seconds": run.first_feasible_seconds,
                "objective_value": run.objective_value,
                "raw_soft_penalty": row["raw_soft_penalty"],
                "penalty_per_meeting": row["penalty_per_meeting"],
                "normalized_quality_score": row["normalized_quality_score"],
                "objective_breakdown": json.dumps(
                    row["objective_breakdown"], sort_keys=True, separators=(",", ":")
                ),
                "hard_violation_count": run.hard_violation_count,
                "stopping_reason": run.stopping_reason,
                "problem_hash": run.diagnostics.get("problem_hash", ""),
                "config_hash": run.diagnostics.get("config_hash", ""),
                "solver_configuration": json.dumps(
                    row["solver_configuration"], sort_keys=True, separators=(",", ":")
                ),
                "hard_violation_vector": json.dumps(
                    row["hard_violation_vector"], sort_keys=True, separators=(",", ":")
                ),
                "assignments": json.dumps(
                    row["assignments"], sort_keys=True, separators=(",", ":")
                ),
                "environment_manifest": json.dumps(
                    batch.configuration.get("environment_manifest", {}),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
    return output.getvalue().encode("utf-8")


def _summarize_algorithm(
    runs: Sequence[models.ScheduleRun], deadline: float
) -> dict[str, Any]:
    observed_runs = [run for run in runs if run.status in TERMINAL_STATUSES]
    successes = [run for run in observed_runs if _is_feasible(run)]
    interval = (
        wilson_interval(len(successes), len(observed_runs))
        if observed_runs
        else (None, None)
    )
    hard_vectors = {str(run.pk): _hard_violation_vector(run) for run in observed_runs}
    aggregate_vector: Counter[str] = Counter()
    for vector in hard_vectors.values():
        aggregate_vector.update(vector)
    times, observed = _rmst_observations(observed_runs, deadline)
    execution_values = _numeric_values(observed_runs, "execution_seconds")
    preprocessing_values = _extracted_values(
        observed_runs, lambda run: _timing_value(run, "shared_preprocessing_seconds")
    )
    validation_values = _extracted_values(
        observed_runs, lambda run: _timing_value(run, "independent_validation_seconds")
    )
    end_to_end_values = _extracted_values(
        observed_runs, lambda run: _timing_value(run, "end_to_end_processing_seconds")
    )
    first_feasible_values = _feasible_values(observed_runs, "first_feasible_seconds")
    penalty_values = _feasible_values(observed_runs, "objective_value")
    penalty_per_meeting_values = _extracted_values(
        successes, _penalty_per_meeting
    )
    normalized_quality_values = _extracted_values(
        successes, _normalized_quality_score
    )
    component_values: dict[str, list[float]] = {
        component: [] for component in _objective_component_names()
    }
    for run in successes:
        components = _raw_objective_components(run)
        if components is None:
            continue
        for component, value in components.items():
            component_values[component].append(value)
    return {
        "runs": len(runs),
        "planned_runs": len(runs),
        "observed_runs": len(observed_runs),
        "pending_runs": len(runs) - len(observed_runs),
        "feasible_runs": len(successes),
        "success_rate": len(successes) / len(observed_runs) if observed_runs else None,
        "success_rate_wilson_95": list(interval),
        "status_counts": dict(sorted(Counter(run.status for run in runs).items())),
        "execution_seconds": describe(execution_values).to_dict(),
        "execution_seconds_median_bootstrap_95": _bootstrap_or_none(execution_values, 101),
        "shared_preprocessing_seconds": describe(preprocessing_values).to_dict(),
        "independent_validation_seconds": describe(validation_values).to_dict(),
        "end_to_end_processing_seconds": describe(end_to_end_values).to_dict(),
        "first_feasible_seconds": describe(first_feasible_values).to_dict(),
        "first_feasible_seconds_median_bootstrap_95": _bootstrap_or_none(
            first_feasible_values, 102
        ),
        "feasible_soft_penalty": describe(penalty_values).to_dict(),
        "feasible_soft_penalty_median_bootstrap_95": _bootstrap_or_none(
            penalty_values, 103
        ),
        "feasible_penalty_per_meeting": describe(penalty_per_meeting_values).to_dict(),
        "feasible_normalized_quality_score": describe(normalized_quality_values).to_dict(),
        "feasible_objective_components": {
            component: describe(values).to_dict()
            for component, values in component_values.items()
        },
        "solver_configuration_by_run": {
            str(run.pk): _solver_configuration_evidence(run) for run in runs
        },
        "hard_violation_count": describe(
            float(run.hard_violation_count) for run in observed_runs
        ).to_dict(),
        "hard_violation_vector": dict(sorted(aggregate_vector.items())),
        "hard_violation_vector_by_run": hard_vectors,
        "rmst_time_to_feasibility_seconds": (
            restricted_mean_time_to_feasibility(times, observed, deadline) if times else None
        ),
        "rmst_observed_feasible_events": sum(observed),
        "rmst_censored_runs": len(observed) - sum(observed),
    }


def _bootstrap_or_none(values: Sequence[float], seed_offset: int) -> list[float] | None:
    if not values:
        return None
    return list(
        bootstrap_median_interval(
            values,
            resamples=10_000,
            seed=20260824 + seed_offset,
        )
    )


def _comparative_tests(
    cp_runs: Sequence[models.ScheduleRun],
    ga_runs: Sequence[models.ScheduleRun],
    *,
    deadline: float,
) -> dict[str, Any]:
    """Preregistered independent-sample tests; common seed labels are not pairs."""

    outcomes = (
        (
            "feasible_generation",
            [1.0 if _is_feasible(run) else 0.0 for run in cp_runs],
            [1.0 if _is_feasible(run) else 0.0 for run in ga_runs],
            "mean",
        ),
        (
            "execution_seconds",
            _numeric_values(cp_runs, "execution_seconds"),
            _numeric_values(ga_runs, "execution_seconds"),
            "median",
        ),
        (
            "censored_time_to_feasibility_seconds",
            _censored_feasibility_times(cp_runs, deadline),
            _censored_feasibility_times(ga_runs, deadline),
            "median",
        ),
        (
            "feasible_soft_penalty",
            _feasible_values(cp_runs, "objective_value"),
            _feasible_values(ga_runs, "objective_value"),
            "median",
        ),
    )
    reports: dict[str, dict[str, Any]] = {}
    tested_names: list[str] = []
    raw_p_values: list[float] = []
    for index, (name, cp_values, ga_values, statistic) in enumerate(outcomes):
        if not cp_values or not ga_values:
            reports[name] = {
                "available": False,
                "reason": "Both algorithms require at least one observed value.",
                "cp_sat_n": len(cp_values),
                "ga_n": len(ga_values),
            }
            continue
        result = unpaired_permutation_test(
            cp_values,
            ga_values,
            statistic=statistic,
            resamples=10_000,
            seed=20260824 + index,
        )
        reports[name] = {
            "available": True,
            "method": "two-sided independent label-permutation test",
            "cp_sat_n": len(cp_values),
            "ga_n": len(ga_values),
            **result,
        }
        tested_names.append(name)
        raw_p_values.append(float(result["p_value_two_sided"]))
    if raw_p_values:
        for name, adjusted in zip(tested_names, holm_adjust(raw_p_values), strict=True):
            reports[name]["p_value_holm_adjusted"] = adjusted
    return {
        "pairing_assumption": "independent samples; numeric seed equality is not statistical pairing",
        "familywise_alpha": 0.05,
        "holm_family": tested_names,
        "outcomes": reports,
    }


def _primary_engine_decision(
    algorithm_summaries: Mapping[str, Mapping[str, Any]],
    comparative_tests: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the preregistered feasibility-quality-time decision hierarchy."""

    cp_algorithm = models.SolverAlgorithm.CP_SAT
    ga_algorithm = models.SolverAlgorithm.GENETIC_ALGORITHM
    cp = algorithm_summaries[cp_algorithm]
    ga = algorithm_summaries[ga_algorithm]
    outcomes = comparative_tests.get("outcomes", {})
    thresholds = {
        "feasibility_rate_absolute_difference": 0.05,
        "quality_median_relative_reduction": 0.05,
        "holm_adjusted_alpha": 0.05,
        "rmst_relative_reduction": 0.10,
    }

    cp_rate = _optional_float(cp.get("success_rate"))
    ga_rate = _optional_float(ga.get("success_rate"))
    feasibility_p = _adjusted_p_value(outcomes.get("feasible_generation"))
    rate_difference = (
        abs(cp_rate - ga_rate) if cp_rate is not None and ga_rate is not None else None
    )
    feasibility_practical = (
        rate_difference is not None
        and rate_difference >= thresholds["feasibility_rate_absolute_difference"]
    )
    feasibility_statistical = (
        feasibility_p is not None and feasibility_p <= thresholds["holm_adjusted_alpha"]
    )
    feasibility_winner = None
    if feasibility_practical and feasibility_statistical and cp_rate != ga_rate:
        feasibility_winner = cp_algorithm if cp_rate > ga_rate else ga_algorithm
    feasibility_available = cp_rate is not None and ga_rate is not None
    feasibility_unresolved = feasibility_practical and not feasibility_statistical
    quality_applicable = feasibility_available and not feasibility_practical

    cp_penalty = _summary_median(cp.get("feasible_soft_penalty"))
    ga_penalty = _summary_median(ga.get("feasible_soft_penalty"))
    quality_reduction, quality_candidate = _lower_is_better_reduction(
        cp_penalty, ga_penalty, cp_algorithm, ga_algorithm
    )
    quality_p = _adjusted_p_value(outcomes.get("feasible_soft_penalty"))
    quality_practical = (
        quality_reduction is not None
        and quality_reduction >= thresholds["quality_median_relative_reduction"]
    )
    quality_statistical = (
        quality_p is not None and quality_p <= thresholds["holm_adjusted_alpha"]
    )
    quality_winner = (
        quality_candidate
        if quality_applicable and quality_practical and quality_statistical
        else None
    )

    cp_rmst = _optional_float(cp.get("rmst_time_to_feasibility_seconds"))
    ga_rmst = _optional_float(ga.get("rmst_time_to_feasibility_seconds"))
    rmst_reduction, time_candidate = _lower_is_better_reduction(
        cp_rmst, ga_rmst, cp_algorithm, ga_algorithm
    )
    time_practical = (
        rmst_reduction is not None
        and rmst_reduction >= thresholds["rmst_relative_reduction"]
    )
    censored_time_report = outcomes.get("censored_time_to_feasibility_seconds")
    time_p = _adjusted_p_value(censored_time_report)
    censored_time_difference = _outcome_difference(censored_time_report)
    censored_time_candidate = None
    if censored_time_difference is not None and censored_time_difference != 0:
        censored_time_candidate = (
            cp_algorithm if censored_time_difference < 0 else ga_algorithm
        )
    time_direction_agrees = (
        time_candidate is not None and time_candidate == censored_time_candidate
    )
    time_applicable = quality_applicable and quality_winner is None
    time_winner = (
        time_candidate
        if time_applicable and time_practical and time_direction_agrees
        else None
    )

    if not feasibility_available:
        winner = None
        deciding_tier = "feasibility"
        decision_status = "insufficient_feasibility_evidence"
        rationale = (
            "Feasibility rates are unavailable for one or both algorithms; "
            "lower-priority tiers were not evaluated."
        )
    elif feasibility_winner:
        winner = feasibility_winner
        deciding_tier = "feasibility"
        decision_status = "winner"
        rationale = (
            f"{winner} exceeded the feasibility-rate threshold and the Holm-adjusted "
            "significance threshold; lower-priority tiers were not used."
        )
    elif feasibility_unresolved:
        winner = None
        deciding_tier = "feasibility"
        decision_status = "unresolved_feasibility"
        rationale = (
            "The feasibility-rate difference reached 5 percentage points but was not "
            "Holm-adjusted significant; feasibility is unresolved and lower-priority "
            "tiers were not evaluated."
        )
    elif quality_winner:
        winner = quality_winner
        deciding_tier = "feasible_schedule_quality"
        decision_status = "winner"
        rationale = (
            f"{winner} achieved at least a 5% reduction in median common raw penalty "
            "with Holm-adjusted p <= 0.05 after feasibility did not decide."
        )
    elif time_winner:
        winner = time_winner
        deciding_tier = "time_to_feasibility"
        decision_status = "winner"
        rationale = (
            f"{winner} achieved at least a 10% reduction in RMST time to feasibility "
            "and the censored-time analysis pointed in the same direction after "
            "feasibility and quality did not decide."
        )
    else:
        winner = None
        deciding_tier = None
        decision_status = "no_winner"
        rationale = (
            "No algorithm cleared the preregistered decision rule at the applicable "
            "tier; no winner is forced."
        )

    return {
        "winner": winner,
        "no_forced_winner": winner is None,
        "deciding_tier": deciding_tier,
        "decision_status": decision_status,
        "rationale": rationale,
        "lexicographic_order": [
            "feasibility",
            "feasible_schedule_quality",
            "time_to_feasibility",
        ],
        "thresholds": thresholds,
        "tiers": {
            "feasibility": {
                "cp_sat_success_rate": cp_rate,
                "ga_success_rate": ga_rate,
                "absolute_difference": rate_difference,
                "holm_adjusted_p": feasibility_p,
                "available": feasibility_available,
                "practical_threshold_met": feasibility_practical,
                "statistical_threshold_met": feasibility_statistical,
                "blocks_lower_tiers": feasibility_practical,
                "unresolved": feasibility_unresolved,
                "winner_if_decisive": feasibility_winner,
            },
            "feasible_schedule_quality": {
                "metric": "median common raw soft penalty (lower is better)",
                "cp_sat_median": cp_penalty,
                "ga_median": ga_penalty,
                "relative_reduction": quality_reduction,
                "holm_adjusted_p": quality_p,
                "applicable": quality_applicable,
                "practical_threshold_met": quality_practical,
                "statistical_threshold_met": quality_statistical,
                "winner_if_decisive": quality_winner,
            },
            "time_to_feasibility": {
                "metric": "restricted mean time to feasibility (lower is better)",
                "cp_sat_rmst_seconds": cp_rmst,
                "ga_rmst_seconds": ga_rmst,
                "relative_reduction": rmst_reduction,
                "holm_adjusted_p_descriptive": time_p,
                "censored_time_observed_difference_cp_sat_minus_ga": (
                    censored_time_difference
                ),
                "censored_time_direction_agrees": time_direction_agrees,
                "applicable": time_applicable,
                "practical_threshold_met": time_practical,
                "winner_if_decisive": time_winner,
            },
        },
    }


def _adjusted_p_value(report: Any) -> float | None:
    if not isinstance(report, Mapping) or not report.get("available"):
        return None
    return _optional_float(report.get("p_value_holm_adjusted"))


def _outcome_difference(report: Any) -> float | None:
    if not isinstance(report, Mapping) or not report.get("available"):
        return None
    return _optional_float(report.get("observed_difference_first_minus_second"))


def _summary_median(summary: Any) -> float | None:
    return _optional_float(summary.get("median")) if isinstance(summary, Mapping) else None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _lower_is_better_reduction(
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


def _rmst_observations(
    runs: Sequence[models.ScheduleRun], deadline: float
) -> tuple[list[float], list[bool]]:
    times: list[float] = []
    observed: list[bool] = []
    for run in runs:
        is_observed = _is_feasible(run) and run.first_feasible_seconds is not None
        times.append(float(run.first_feasible_seconds) if is_observed else float(deadline))
        observed.append(is_observed)
    return times, observed


def _censored_feasibility_times(
    runs: Sequence[models.ScheduleRun], deadline: float
) -> list[float]:
    times, _ = _rmst_observations(runs, deadline)
    return times


def _placement_consistency(
    by_algorithm: Mapping[str, Sequence[models.ScheduleRun]],
) -> dict[str, Any]:
    result: dict[str, Any] = {"by_algorithm": {}}
    maps_by_algorithm: dict[str, list[tuple[int, Mapping[str, str]]]] = {}
    for algorithm, runs in by_algorithm.items():
        maps = [
            (run.seed, assignments)
            for run in runs
            if _is_feasible(run) and (assignments := _assignment_map(run))
        ]
        maps_by_algorithm[algorithm] = maps
        distances = [
            normalized_hamming(left[1], right[1]) for left, right in combinations(maps, 2)
        ]
        result["by_algorithm"][algorithm] = {
            "feasible_schedules": len(maps),
            "pairwise_comparisons": len(distances),
            "normalized_hamming_distance": describe(distances).to_dict(),
        }

    cp_by_seed = dict(maps_by_algorithm.get(models.SolverAlgorithm.CP_SAT, ()))
    ga_by_seed = dict(maps_by_algorithm.get(models.SolverAlgorithm.GENETIC_ALGORITHM, ()))
    common_seeds = sorted(set(cp_by_seed) & set(ga_by_seed))
    paired = [normalized_hamming(cp_by_seed[seed], ga_by_seed[seed]) for seed in common_seeds]
    result["paired_cp_sat_vs_ga"] = {
        "seeds": common_seeds,
        "normalized_hamming_distance": describe(paired).to_dict(),
    }
    return result


def _objective_weight_sensitivity(
    batch: models.ExperimentBatch,
    by_algorithm: Mapping[str, Sequence[models.ScheduleRun]],
) -> dict[str, Any]:
    component_weights = _objective_component_weights(batch.snapshot)
    raw_by_algorithm: dict[str, list[tuple[int, int, dict[str, float]]]] = {}
    missing_run_ids: list[int] = []
    for algorithm, runs in by_algorithm.items():
        observations: list[tuple[int, int, dict[str, float]]] = []
        for run in runs:
            if not _is_feasible(run):
                continue
            raw = _raw_objective_components(run)
            if raw is None:
                missing_run_ids.append(run.pk)
            else:
                observations.append((run.pk, run.seed, raw))
        raw_by_algorithm[algorithm] = observations

    nominal_values = _sensitivity_values(raw_by_algorithm, component_weights, None, 1.0)
    nominal_medians = _algorithm_medians(nominal_values)
    nominal_winner = _lower_median_winner(nominal_medians)
    scenarios: list[dict[str, Any]] = []
    for component in component_weights:
        for multiplier in SENSITIVITY_MULTIPLIERS:
            values = _sensitivity_values(
                raw_by_algorithm,
                component_weights,
                component,
                multiplier,
            )
            medians = _algorithm_medians(values)
            winner = _lower_median_winner(medians)
            scenarios.append(
                {
                    "component": component,
                    "multiplier": multiplier,
                    "algorithm_medians": medians,
                    "algorithm_values": values,
                    "winner": winner,
                    "nominal_winner_changed": (
                        None
                        if nominal_winner is None or winner is None
                        else winner != nominal_winner
                    ),
                }
            )
    definite_changes = [
        scenario["nominal_winner_changed"]
        for scenario in scenarios
        if scenario["nominal_winner_changed"] is not None
    ]
    return {
        "available": all(raw_by_algorithm.get(algorithm) for algorithm in models.SolverAlgorithm.values),
        "method": "Analytical one-at-a-time rescore of stored raw objective components.",
        "base_weights": component_weights,
        "multipliers": list(SENSITIVITY_MULTIPLIERS),
        "nominal": {
            "algorithm_medians": nominal_medians,
            "algorithm_values": nominal_values,
            "winner": nominal_winner,
        },
        "scenarios": scenarios,
        "nominal_winner_changes": any(definite_changes),
        "missing_raw_component_run_ids": sorted(missing_run_ids),
    }


def _objective_component_weights(snapshot: models.ProblemSnapshot) -> dict[str, float]:
    domain_profile = snapshot.input_data.get("objective_profile", {})
    persisted_weights = snapshot.objective_profile.weights or {}
    return {
        "preference_penalty": float(
            domain_profile.get(
                "preference_weight", persisted_weights.get("instructor_preference", 0)
            )
        ),
        "section_gap_atoms": float(
            domain_profile.get(
                "section_gap_weight", persisted_weights.get("section_internal_gaps", 0)
            )
        ),
        "instructor_gap_atoms": float(
            domain_profile.get(
                "instructor_gap_weight", persisted_weights.get("instructor_internal_gaps", 0)
            )
        ),
        "load_imbalance": float(
            domain_profile.get(
                "load_imbalance_weight", persisted_weights.get("daily_load_imbalance", 0)
            )
        ),
    }


def _objective_component_names() -> tuple[str, ...]:
    return (
        "preference_penalty",
        "section_gap_atoms",
        "instructor_gap_atoms",
        "load_imbalance",
    )


def _objective_payload(run: models.ScheduleRun) -> dict[str, Any] | None:
    payload = run.result_data.get("objective") if run.result_data else None
    if not isinstance(payload, dict):
        try:
            payload = run.validation_result.objective_breakdown
        except models.ValidationResult.DoesNotExist:
            return None
    return payload if isinstance(payload, dict) else None


def _raw_objective_components(run: models.ScheduleRun) -> dict[str, float] | None:
    payload = _objective_payload(run)
    component_names = _objective_component_names()
    if not isinstance(payload, dict) or any(
        isinstance(payload.get(name), bool)
        or not isinstance(payload.get(name), (int, float))
        for name in component_names
    ):
        return None
    return {name: float(payload[name]) for name in component_names}


def _objective_breakdown(run: models.ScheduleRun) -> dict[str, int | float]:
    payload = _objective_payload(run) or {}
    return {
        str(name): value
        for name, value in payload.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _normalized_quality_score(run: models.ScheduleRun) -> float | None:
    payload = _objective_payload(run) or {}
    quality = payload.get("quality_score")
    if isinstance(quality, (int, float)) and not isinstance(quality, bool):
        return float(quality)
    try:
        quality = run.validation_result.normalized_quality_score
    except models.ValidationResult.DoesNotExist:
        quality = None
    if quality is not None:
        return float(quality)
    return _metric_value(run, "quality_score")


def _penalty_per_meeting(run: models.ScheduleRun) -> float | None:
    if run.objective_value is None or run.snapshot.event_count <= 0:
        return None
    return float(run.objective_value) / run.snapshot.event_count


def _metric_value(run: models.ScheduleRun, name: str) -> float | None:
    for metric in run.metrics.all():
        if metric.name == name:
            return float(metric.value)
    return None


def _timing_value(run: models.ScheduleRun, name: str) -> float | None:
    value = _metric_value(run, name)
    if value is None and name == "shared_preprocessing_seconds":
        return float(run.snapshot.preprocessing_seconds)
    return value


def _solver_configuration_evidence(run: models.ScheduleRun) -> dict[str, Any]:
    try:
        config = build_solver_config(run)
        resolved = config.to_dict()
        resolved_config_hash = config.canonical_hash
        configuration_error = None
    except (TypeError, ValueError) as exc:
        resolved = {}
        resolved_config_hash = None
        configuration_error = str(exc)
    effective: dict[str, float] = {}
    if run.algorithm == models.SolverAlgorithm.GENETIC_ALGORITHM:
        mutation_rate = _metric_value(run, "mutation_rate")
        if mutation_rate is None and resolved.get("mutation_rate") is None:
            mutation_rate = 1.0 / max(1, run.snapshot.event_count)
        if mutation_rate is not None:
            effective["mutation_rate"] = mutation_rate
    return {
        "persisted": run.configuration,
        "resolved": resolved,
        "effective_parameters": effective,
        "resolved_config_hash": resolved_config_hash,
        "config_hash": run.diagnostics.get("config_hash", ""),
        "configuration_error": configuration_error,
    }


def _sensitivity_values(
    raw_by_algorithm: Mapping[str, Sequence[tuple[int, int, dict[str, float]]]],
    component_weights: Mapping[str, float],
    varied_component: str | None,
    multiplier: float,
) -> dict[str, list[dict[str, int | float]]]:
    result: dict[str, list[dict[str, int | float]]] = {}
    for algorithm, observations in raw_by_algorithm.items():
        values = []
        for run_id, seed, raw in observations:
            score = sum(
                raw[component]
                * weight
                * (multiplier if component == varied_component else 1.0)
                for component, weight in component_weights.items()
            )
            values.append({"run_id": run_id, "seed": seed, "score": score})
        result[algorithm] = values
    return result


def _algorithm_medians(
    values: Mapping[str, Sequence[Mapping[str, int | float]]],
) -> dict[str, float | None]:
    return {
        algorithm: describe(float(item["score"]) for item in sample).median
        for algorithm, sample in values.items()
    }


def _lower_median_winner(medians: Mapping[str, float | None]) -> str | None:
    cp_value = medians.get(models.SolverAlgorithm.CP_SAT)
    ga_value = medians.get(models.SolverAlgorithm.GENETIC_ALGORITHM)
    if cp_value is None or ga_value is None:
        return None
    if cp_value == ga_value:
        return "TIE"
    return (
        models.SolverAlgorithm.CP_SAT
        if cp_value < ga_value
        else models.SolverAlgorithm.GENETIC_ALGORITHM
    )


def _retry_episode_summary(runs: Sequence[models.ScheduleRun]) -> dict[str, Any]:
    """Summarize only explicitly tagged operational retries, never benchmark seeds."""

    grouped: dict[tuple[str, str], list[models.ScheduleRun]] = defaultdict(list)
    for run in runs:
        episode_id = run.configuration.get("retry_episode_id")
        if episode_id not in (None, "") and run.status in TERMINAL_STATUSES:
            grouped[(run.algorithm, str(episode_id))].append(run)

    episodes: list[dict[str, Any]] = []
    for (algorithm, episode_id), attempts in sorted(grouped.items()):
        attempts.sort(
            key=lambda run: (
                _order_integer(run.configuration.get("retry_attempt")),
                _order_integer(run.configuration.get("experiment_order_index")),
                run.pk,
            )
        )
        considered = attempts[:RETRY_EPISODE_CAP]
        success_index = next(
            (index for index, run in enumerate(considered) if _is_feasible(run)), None
        )
        episodes.append(
            {
                "algorithm": algorithm,
                "episode_id": episode_id,
                "attempts_observed": len(attempts),
                "attempts_considered": len(considered),
                "excluded_beyond_cap": max(0, len(attempts) - RETRY_EPISODE_CAP),
                "successful": success_index is not None,
                "retries_required": (
                    success_index if success_index is not None else len(considered)
                ),
            }
        )
    return {
        "cap": RETRY_EPISODE_CAP,
        "available": bool(episodes),
        "observed_episodes": len(episodes),
        "benchmark_runs_inferred_as_retries": False,
        "episodes": episodes,
        "retries_required": describe(
            float(episode["retries_required"]) for episode in episodes
        ).to_dict(),
    }


def _room_utilization_summary(
    batch: models.ExperimentBatch,
    by_algorithm: Mapping[str, Sequence[models.ScheduleRun]],
) -> dict[str, Any]:
    eligible_room_ids = _eligible_room_ids(batch.snapshot)
    denominators = _room_available_atom_denominators(batch.snapshot, eligible_room_ids)
    observations: list[dict[str, Any]] = []
    for algorithm, runs in by_algorithm.items():
        for run in runs:
            schedule = _schedule_for_run(run)
            if schedule is None or not schedule.room_allocations.exists():
                continue
            occupied = {
                row["room_id"]: row["occupied_atoms"]
                for row in schedule.room_allocations.values("room_id").annotate(
                    occupied_atoms=Count("id")
                )
            }
            for room_id, available_atoms in sorted(denominators.items()):
                if available_atoms <= 0:
                    continue
                occupied_atoms = int(occupied.get(room_id, 0))
                observations.append(
                    {
                        "algorithm": algorithm,
                        "run_id": run.pk,
                        "schedule_id": schedule.pk,
                        "room_id": room_id,
                        "occupied_atoms": occupied_atoms,
                        "available_atoms": available_atoms,
                        "utilization": occupied_atoms / available_atoms,
                    }
                )
    return {
        "available": bool(observations),
        "definition": "occupied room-atoms / configured available room-atoms",
        "eligible_rooms": len(eligible_room_ids),
        "eligible_rooms_with_denominator": len(denominators),
        "by_algorithm": {
            algorithm: describe(
                observation["utilization"]
                for observation in observations
                if observation["algorithm"] == algorithm
            ).to_dict()
            for algorithm in models.SolverAlgorithm.values
        },
        "observations": observations,
    }


def _eligible_room_ids(snapshot: models.ProblemSnapshot) -> set[int]:
    room_ids: set[int] = set()
    candidate_groups = snapshot.candidate_map.values() if snapshot.candidate_map else ()
    for candidates in candidate_groups:
        for candidate in candidates:
            room_id = candidate.get("room_id", candidate.get("room"))
            try:
                room_ids.add(int(room_id))
            except (TypeError, ValueError):
                continue
    if not room_ids:
        for event in snapshot.input_data.get("events", ()):
            for candidate in event.get("candidates", ()):
                try:
                    room_ids.add(int(candidate.get("room_id")))
                except (TypeError, ValueError):
                    continue
    return room_ids


def _room_available_atom_denominators(
    snapshot: models.ProblemSnapshot, eligible_room_ids: set[int]
) -> dict[int, int]:
    if not eligible_room_ids:
        return {}
    schedulable_atom_count = snapshot.revision.time_slots.filter(
        is_active=True, is_break=False
    ).count()
    profiles = models.RoomAvailabilityProfile.objects.filter(
        revision=snapshot.revision,
        room_id__in=eligible_room_ids,
    )
    denominators: dict[int, int] = {}
    for profile in profiles:
        if profile.assume_fully_available:
            denominators[profile.room_id] = schedulable_atom_count
        else:
            denominators[profile.room_id] = (
                profile.availability_rows.filter(
                    is_available=True,
                    time_slot__is_active=True,
                    time_slot__is_break=False,
                )
                .values("time_slot_id")
                .distinct()
                .count()
            )
    return denominators


def _schedule_for_run(run: models.ScheduleRun) -> models.ScheduleVersion | None:
    try:
        return run.schedule_version
    except models.ScheduleVersion.DoesNotExist:
        return None


def _assignment_map(run: models.ScheduleRun) -> dict[str, str]:
    assignments = run.result_data.get("assignments", ()) if run.result_data else ()
    result = {
        str(item["event_id"]): str(item["candidate_id"])
        for item in assignments
        if isinstance(item, dict)
        and item.get("event_id") is not None
        and item.get("candidate_id") is not None
    }
    if result:
        return result
    schedule = _schedule_for_run(run)
    if schedule is None:
        return {}
    return {
        str(assignment.meeting_requirement.stable_key): str(
            assignment.placement_data.get("candidate_id", "")
        )
        for assignment in schedule.assignments.select_related("meeting_requirement")
        if assignment.placement_data.get("candidate_id")
    }


def _hard_violation_vector(run: models.ScheduleRun) -> dict[str, int]:
    try:
        payload = run.validation_result.violations
    except models.ValidationResult.DoesNotExist:
        payload = run.result_data.get("validation", {}) if run.result_data else {}
    counts = payload.get("counts", {}) if isinstance(payload, dict) else {}
    return {
        str(code): int(count)
        for code, count in counts.items()
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0
    }


def _run_export_row(index: int, run: models.ScheduleRun) -> dict[str, Any]:
    raw_soft_penalty = float(run.objective_value) if run.objective_value is not None else None
    return {
        "order_index": index,
        "run_id": run.pk,
        "seed": run.seed,
        "algorithm": run.algorithm,
        "meeting_count": run.snapshot.event_count,
        "status": run.status,
        "feasible": _is_feasible(run),
        "execution_seconds": run.execution_seconds,
        "shared_preprocessing_seconds": _timing_value(run, "shared_preprocessing_seconds"),
        "independent_validation_seconds": _timing_value(
            run, "independent_validation_seconds"
        ),
        "end_to_end_processing_seconds": _timing_value(
            run, "end_to_end_processing_seconds"
        ),
        "first_feasible_seconds": run.first_feasible_seconds,
        "objective_value": run.objective_value,
        "raw_soft_penalty": raw_soft_penalty,
        "penalty_per_meeting": _penalty_per_meeting(run),
        "normalized_quality_score": _normalized_quality_score(run),
        "objective_breakdown": _objective_breakdown(run),
        "hard_violation_count": run.hard_violation_count,
        "hard_violation_vector": _hard_violation_vector(run),
        "stopping_reason": run.stopping_reason,
        "problem_hash": run.diagnostics.get("problem_hash", ""),
        "config_hash": run.diagnostics.get("config_hash", ""),
        "solver_configuration": _solver_configuration_evidence(run),
        "assignments": _assignment_map(run),
    }


def _is_feasible(run: models.ScheduleRun) -> bool:
    return run.status in SUCCESS_STATUSES


def _numeric_values(runs: Sequence[models.ScheduleRun], field: str) -> list[float]:
    return [
        float(value)
        for run in runs
        if (value := getattr(run, field)) is not None
    ]


def _extracted_values(
    runs: Sequence[models.ScheduleRun], extractor: Any
) -> list[float]:
    return [float(value) for run in runs if (value := extractor(run)) is not None]


def _feasible_values(runs: Sequence[models.ScheduleRun], field: str) -> list[float]:
    return [
        float(value)
        for run in runs
        if _is_feasible(run) and (value := getattr(run, field)) is not None
    ]


def _a12_or_none(first: Sequence[float], second: Sequence[float]) -> float | None:
    return vargha_delaney_a12(first, second) if first and second else None


def _order_integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 10**9


def _set_batch_status(batch: models.ExperimentBatch, status: str) -> None:
    if batch.status == status:
        _sync_exploratory_study_status(batch, status)
        return
    batch.status = status
    batch.full_clean()
    batch.save(update_fields=["status", "updated_at"])
    _sync_exploratory_study_status(batch, status)


def _sync_exploratory_study_status(
    batch: models.ExperimentBatch,
    status: str,
) -> None:
    if not batch.study_id:
        return
    models.ExperimentStudy.objects.filter(
        pk=batch.study_id,
        mode=models.ExperimentMode.EXPLORATORY,
    ).update(status=status, updated_at=timezone.now())


def _validated_seeds(seeds: Iterable[int]) -> tuple[int, ...]:
    result = tuple(seeds)
    if not result:
        raise ValueError("at least one experiment seed is required")
    if any(type(seed) is not int or seed < 0 for seed in result):
        raise ValueError("seeds must be non-negative integers")
    if len(result) != len(set(result)):
        raise ValueError("experiment seeds must be unique")
    return result


def _ensure_json_object(value: Mapping[str, Any], name: str) -> None:
    try:
        serialized = json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain only finite JSON values") from exc
    if not serialized.startswith("{"):
        raise ValueError(f"{name} must be a JSON object")
