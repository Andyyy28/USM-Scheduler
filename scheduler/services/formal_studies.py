"""Protocol-locked CP-SAT/GA thesis-study orchestration.

The existing :mod:`scheduler.services.experiments` module remains the flexible
exploratory interface.  This module deliberately exposes a much smaller
surface: one preregistered protocol, four nested instances, one frozen solver
profile per algorithm, and an audited single paired replacement for
infrastructure failures.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Any

from celery import current_app
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from scheduler import models
from scheduler.services.runs import build_solver_config, task_time_limit_seconds
from scheduler.services.scaling import (
    DEFAULT_SCALING_SEED,
    create_scaling_snapshots,
    plan_scaling_snapshots,
)
from scheduler.services.statistics import restricted_mean_time_to_feasibility
from scheduler.services.tuning import (
    SOLVER_TUNING_ARTIFACT_SCHEMA_VERSION,
    SOLVER_TUNING_ORDER_SEED,
    SOLVER_TUNING_PROTOCOL_VERSION,
    SOLVER_TUNING_SEEDS,
    SOLVER_TUNING_TIME_LIMIT_SECONDS,
    build_solver_tuning_plan,
    solver_tuning_configurations,
)
from scheduler.solvers.cp_sat import CP_SAT_IMPLEMENTATION_VERSION
from scheduler.solvers.genetic import GA_IMPLEMENTATION_VERSION

FORMAL_PROTOCOL_VERSION = "formal-v2"
FORMAL_SCALES = (25, 50, 75, 100)
FORMAL_SEEDS = tuple(range(1001, 1031))
FORMAL_ORDER_SEED = 20260824
FORMAL_DEADLINE_SECONDS = 300
FORMAL_CPU_LIMIT = 1
FORMAL_MEMORY_LIMIT_MB = 2048
FORMAL_INFRASTRUCTURE_GRACE_SECONDS = 60
FORMAL_WARMUP_SEED = 9000
FORMAL_TRACE_SEED = 9001
FORMAL_FEASIBILITY_DIAGNOSTIC_SEED = 9002
FORMAL_FEASIBILITY_DIAGNOSTIC_SECONDS = 1800
FORMAL_MEASURED_RUN_COUNT = 240
FORMAL_WARMUP_RUN_COUNT = 8
FORMAL_FEASIBILITY_DIAGNOSTIC_COUNT = 4
FORMAL_TRACE_RUN_COUNT = 8
FORMAL_TOTAL_RUN_COUNT = 260
FORMAL_BENCHMARK_QUEUE = "benchmark"
TUNING_RESEARCH_PHASE = "SYNTHETIC_EQUAL_BUDGET_TUNING"
TUNING_EVIDENCE_CLASS = "synthetic_pilot_tuning_excluded_from_final_inference"

_ALGORITHMS = (
    models.SolverAlgorithm.CP_SAT,
    models.SolverAlgorithm.GENETIC_ALGORITHM,
)
_SUCCESS_STATUSES = {models.RunStatus.FEASIBLE, models.RunStatus.OPTIMAL}
_TERMINAL_STATUSES = set(models.RunStatus.values) - {
    models.RunStatus.QUEUED,
    models.RunStatus.RUNNING,
}


@dataclass(frozen=True, slots=True)
class ProtocolIssue:
    code: str
    message: str
    field: str | None = None
    entity_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field:
            result["field"] = self.field
        if self.entity_id:
            result["entity_id"] = self.entity_id
        return result


class FormalStudyError(ValueError):
    """A user-correctable protocol error with machine-readable issues."""

    def __init__(self, message: str, issues: Sequence[ProtocolIssue] = ()) -> None:
        super().__init__(message)
        self.issues = tuple(issues)


def _issue(
    code: str,
    message: str,
    field: str | None = None,
    entity_id: object | None = None,
) -> ProtocolIssue:
    return ProtocolIssue(
        code=code,
        message=message,
        field=field,
        entity_id=str(entity_id) if entity_id is not None else None,
    )


def _require_central_actor(actor: models.User) -> None:
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise FormalStudyError("An active authenticated user is required.")
    if not actor.is_superuser and actor.role not in {
        models.UserRole.SYSTEM_ADMIN,
        models.UserRole.CENTRAL_SCHEDULER,
    }:
        raise FormalStudyError(
            "Only central schedulers and system administrators may control formal studies."
        )


def _canonical_profile_payload(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in profile.items() if key != "profile_hash"}


def _normalize_solver_profiles(
    value: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise FormalStudyError(
            "Frozen profiles selected by the equal-budget tuning pilot are required.",
            (
                _issue(
                    "MISSING_TUNING_PROFILES",
                    "Provide one frozen tuning profile for CP-SAT and one for GA.",
                    "solver_profiles",
                ),
            ),
        )
    aliases = {
        "CP-SAT": models.SolverAlgorithm.CP_SAT,
        "CP_SAT": models.SolverAlgorithm.CP_SAT,
        "GA": models.SolverAlgorithm.GENETIC_ALGORITHM,
        "GENETIC_ALGORITHM": models.SolverAlgorithm.GENETIC_ALGORITHM,
    }
    normalized: dict[str, dict[str, Any]] = {}
    for raw_algorithm, raw_profile in value.items():
        algorithm = aliases.get(str(raw_algorithm).upper(), str(raw_algorithm))
        if algorithm not in _ALGORITHMS:
            raise FormalStudyError(
                "The tuning selection contains an unsupported algorithm.",
                (_issue("UNKNOWN_ALGORITHM", str(raw_algorithm), "solver_profiles"),),
            )
        if not isinstance(raw_profile, Mapping):
            raise FormalStudyError(
                "Every frozen solver profile must be a JSON object.",
                (_issue("INVALID_TUNING_PROFILE", algorithm, "solver_profiles"),),
            )
        normalized[algorithm] = dict(raw_profile)
    if set(normalized) != set(_ALGORITHMS):
        raise FormalStudyError(
            "Exactly one CP-SAT and one GA tuning profile are required.",
            (_issue("INCOMPLETE_TUNING_PROFILES", "Both algorithms are required."),),
        )

    plan_hashes: set[str] = set()
    issues: list[ProtocolIssue] = []
    for algorithm, profile in normalized.items():
        configuration = profile.get("configuration")
        expected_version = (
            CP_SAT_IMPLEMENTATION_VERSION
            if algorithm == models.SolverAlgorithm.CP_SAT
            else GA_IMPLEMENTATION_VERSION
        )
        if profile.get("frozen") is not True:
            issues.append(
                _issue("PROFILE_NOT_FROZEN", f"{algorithm} profile is not frozen.", algorithm)
            )
        if profile.get("protocol_version") != SOLVER_TUNING_PROTOCOL_VERSION:
            issues.append(
                _issue(
                    "TUNING_PROTOCOL_MISMATCH",
                    f"{algorithm} was not selected by tuning protocol {SOLVER_TUNING_PROTOCOL_VERSION}.",
                    algorithm,
                )
            )
        if profile.get("algorithm") != algorithm:
            issues.append(
                _issue("PROFILE_ALGORITHM_MISMATCH", f"{algorithm} profile is mislabeled.", algorithm)
            )
        if profile.get("implementation_version") != expected_version:
            issues.append(
                _issue(
                    "IMPLEMENTATION_VERSION_MISMATCH",
                    f"{algorithm} profile does not match implementation {expected_version}.",
                    algorithm,
                )
            )
        plan_hash = profile.get("plan_hash")
        if not isinstance(plan_hash, str) or len(plan_hash) != 64:
            issues.append(
                _issue("MISSING_TUNING_PLAN_HASH", f"{algorithm} needs a tuning-plan hash.", algorithm)
            )
        else:
            plan_hashes.add(plan_hash)
        profile_hash = profile.get("profile_hash")
        expected_profile_hash = models.canonical_sha256(_canonical_profile_payload(profile))
        if profile_hash != expected_profile_hash:
            issues.append(
                _issue("PROFILE_HASH_MISMATCH", f"{algorithm} profile hash does not verify.", algorithm)
            )
        if not isinstance(configuration, Mapping):
            issues.append(
                _issue("MISSING_SOLVER_CONFIGURATION", f"{algorithm} configuration is missing.", algorithm)
            )
            continue
        config = dict(configuration)
        if config.get("algorithm") != algorithm or float(config.get("time_limit_seconds", 0)) != 60.0:
            issues.append(
                _issue(
                    "TUNING_BUDGET_MISMATCH",
                    f"{algorithm} profile must come from a 60-second tuning cell.",
                    algorithm,
                )
            )
        if int(config.get("worker_count", 1)) != 1:
            issues.append(
                _issue("TUNING_WORKER_MISMATCH", f"{algorithm} must use one solver worker.", algorithm)
            )
        if algorithm == models.SolverAlgorithm.CP_SAT:
            if type(config.get("cp_model_presolve", True)) is not bool:
                issues.append(_issue("INVALID_CP_PRESOLVE", "CP-SAT presolve must be Boolean."))
            if config.get("linearization_level", 2) not in {0, 1, 2}:
                issues.append(
                    _issue("INVALID_CP_LINEARIZATION", "CP-SAT linearization must be 0, 1, or 2.")
                )
        else:
            if config.get("population_size") not in {100, 200, 400}:
                issues.append(
                    _issue("INVALID_GA_POPULATION", "GA population must be 100, 200, or 400.")
                )
            fixed = {
                "tournament_size": 3,
                "crossover_rate": 0.9,
                "elite_fraction": 0.05,
                "repair_attempts": 20,
            }
            for key, expected in fixed.items():
                if config.get(key) != expected:
                    issues.append(
                        _issue(
                            "INVALID_GA_FIXED_PARAMETER",
                            f"GA {key} must be {expected!r}.",
                            key,
                        )
                    )
            mutation_rate = config.get("mutation_rate")
            tuning_parameters = profile.get("tuning_parameters", {})
            multiplier = tuning_parameters.get("mutation_multiplier") if isinstance(tuning_parameters, Mapping) else None
            mutable_count = tuning_parameters.get("mutable_event_count") if isinstance(tuning_parameters, Mapping) else None
            if isinstance(mutation_rate, bool) or not isinstance(mutation_rate, (int, float)):
                issues.append(_issue("INVALID_GA_MUTATION", "GA mutation rate must be numeric."))
            elif (
                type(multiplier) is not int
                or multiplier not in {1, 2}
                or type(mutable_count) is not int
                or mutable_count <= 0
                or mutation_rate != min(1.0, multiplier / mutable_count)
                or tuning_parameters.get("mutation_formula") != f"{multiplier}/N_mutable"
            ):
                issues.append(_issue("INVALID_GA_MUTATION", "GA mutation must be the authenticated 1/N or 2/N pilot formula."))
    if len(plan_hashes) > 1:
        issues.append(
            _issue(
                "UNEQUAL_TUNING_PILOT",
                "CP-SAT and GA profiles must come from the same equal-budget tuning plan.",
            )
        )
    if issues:
        raise FormalStudyError("Frozen solver profiles failed verification.", issues)
    return normalized


def _tuning_mutable_event_count(snapshot: models.ProblemSnapshot) -> int:
    input_data = snapshot.input_data if isinstance(snapshot.input_data, dict) else {}
    events = input_data.get("events", [])
    locks = input_data.get("locked_assignments", [])
    event_ids = {
        str(row.get("event_id", row.get("id", "")))
        for row in events
        if isinstance(row, dict) and row.get("event_id", row.get("id")) not in (None, "")
    }
    locked_ids = {
        str(row.get("event_id", ""))
        for row in locks
        if isinstance(row, dict) and row.get("event_id") not in (None, "")
    }
    event_count = len(event_ids) if event_ids else int(snapshot.event_count)
    return max(0, event_count - len(event_ids & locked_ids))


def _finite_nonnegative(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if isfinite(result) and result >= 0 else None


def _persisted_tuning_ranking_row(
    configuration_id: str,
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    feasible = [row for row in observations if row["feasible"]]
    penalties = [float(row["raw_soft_penalty"]) for row in feasible]
    deadline = float(SOLVER_TUNING_TIME_LIMIT_SECONDS)
    time_observations = [
        min(float(row["first_feasible_seconds"]), deadline)
        if row["feasible"]
        else deadline
        for row in observations
    ]
    time_successes = [
        bool(row["feasible"] and float(row["first_feasible_seconds"]) < deadline)
        for row in observations
    ]
    execution_times = [float(row["execution_seconds"]) for row in observations]
    return {
        "configuration_id": configuration_id,
        "runs": len(observations),
        "feasible_runs": len(feasible),
        "censored_runs": sum(not success for success in time_successes),
        "feasibility_rate": len(feasible) / len(observations),
        "median_feasible_raw_soft_penalty": (
            float(median(penalties)) if penalties else None
        ),
        "rmst_time_to_feasibility_seconds": restricted_mean_time_to_feasibility(
            time_observations,
            time_successes,
            deadline,
        ),
        "median_execution_seconds": float(median(execution_times)),
    }


def _authenticate_persisted_tuning_profiles(
    profiles: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind submitted profiles to the complete excluded pilot stored in the database.

    A profile hash only proves that a JSON object is internally consistent.  It
    does not prove that the advertised configuration won the preregistered
    pilot.  This verifier rebuilds the fixed grid from the persisted tuning
    snapshot, validates every one of the 60 terminal cells, repeats the frozen
    selection rule, and returns an evidence digest suitable for the immutable
    formal-study manifest.
    """

    plan_hashes = {str(profile.get("plan_hash", "")) for profile in profiles.values()}
    if len(plan_hashes) != 1:
        raise FormalStudyError(
            "Frozen tuning profiles do not share one persisted pilot.",
            (_issue("UNEQUAL_TUNING_PILOT", "Both profiles must share one plan hash."),),
        )
    plan_hash = next(iter(plan_hashes))
    runs = list(
        models.ScheduleRun.objects.filter(
            purpose=models.RunPurpose.TUNING,
            included_in_analysis=False,
            configuration__research_phase=TUNING_RESEARCH_PHASE,
            configuration__solver_tuning_protocol=SOLVER_TUNING_PROTOCOL_VERSION,
            configuration__solver_tuning_plan_hash=plan_hash,
        )
        .select_related("snapshot", "validation_result")
        .order_by("algorithm", "seed", "pk")
    )
    issues: list[ProtocolIssue] = []
    expected_count = len(SOLVER_TUNING_SEEDS) * 12
    if len(runs) != expected_count:
        issues.append(
            _issue(
                "INCOMPLETE_PERSISTED_TUNING_PILOT",
                f"Plan {plan_hash} has {len(runs)} persisted excluded cells; {expected_count} are required.",
                "solver_profiles",
            )
        )
    snapshot_ids = {run.snapshot_id for run in runs}
    if len(snapshot_ids) != 1:
        issues.append(
            _issue(
                "MIXED_TUNING_SNAPSHOTS",
                "All persisted pilot cells must use one frozen synthetic snapshot.",
                "solver_profiles",
            )
        )
    if issues:
        raise FormalStudyError("Persisted tuning evidence failed verification.", issues)

    tuning_snapshot = runs[0].snapshot
    audits = list(models.AuditLog.objects.filter(
        action="solver_tuning.plan_created",
        entity_type="ProblemSnapshot",
        entity_id=str(tuning_snapshot.pk),
        details__plan__plan_hash=plan_hash,
    ))
    if len(audits) != 1 or audits[0].details.get("synthetic_data_confirmed") is not True:
        raise FormalStudyError(
            "The pilot lacks its audited synthetic-data plan.",
            (_issue("MISSING_TUNING_PLAN_AUDIT", "Exactly one audited synthetic pilot plan is required."),),
        )
    audited_plan = audits[0].details.get("plan", {})
    try:
        reconstructed_plan = build_solver_tuning_plan(
            tuning_snapshot,
            environment=audited_plan.get("environment_manifest"),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise FormalStudyError("The audited tuning plan is malformed.") from exc
    if audited_plan != reconstructed_plan or audited_plan.get("plan_hash") != plan_hash:
        raise FormalStudyError(
            "The audited pilot plan does not match the fixed protocol.",
            (_issue("TUNING_PLAN_HASH_MISMATCH", "The full pilot manifest must reproduce exactly."),),
        )
    if set(audits[0].details.get("run_ids", ())) != {run.pk for run in runs}:
        raise FormalStudyError(
            "The pilot runs do not match their creation audit.",
            (_issue("TUNING_RUN_AUDIT_MISMATCH", "Pilot run identities must match the frozen plan audit."),),
        )
    planned_positions = {
        (row["algorithm"], row["configuration_id"], row["seed"]): row["position"]
        for row in audited_plan["runs"]
    }
    from scheduler.services.problem_builder import load_problem

    tuning_problem_hash = load_problem(tuning_snapshot).canonical_hash
    mutable_event_count = _tuning_mutable_event_count(tuning_snapshot)
    if mutable_event_count <= 0:
        issues.append(
            _issue(
                "EMPTY_TUNING_PROBLEM",
                "The tuning snapshot must contain at least one mutable meeting.",
                "solver_profiles",
            )
        )
    grid = solver_tuning_configurations(mutable_event_count)
    configuration_by_id = {
        str(row["configuration_id"]): row for row in grid
    }
    expected_cells = {
        (str(row["algorithm"]), str(row["configuration_id"]), seed)
        for row in grid
        for seed in SOLVER_TUNING_SEEDS
    }
    seen_cells: set[tuple[str, str, int]] = set()
    observations_by_configuration: dict[tuple[str, str], list[dict[str, Any]]] = {
        (str(row["algorithm"]), str(row["configuration_id"])): [] for row in grid
    }
    evidence_rows: list[dict[str, Any]] = []
    environment_hashes: set[str] = set()
    build_hashes: set[str] = set()
    order_positions: set[int] = set()

    for run in runs:
        configuration = run.configuration if isinstance(run.configuration, dict) else {}
        configuration_id = str(configuration.get("solver_tuning_configuration_id", ""))
        cell = (str(run.algorithm), configuration_id, int(run.seed))
        expected = configuration_by_id.get(configuration_id)
        if cell in seen_cells:
            issues.append(
                _issue(
                    "DUPLICATE_TUNING_CELL",
                    f"Duplicate persisted pilot cell {cell!r}.",
                    entity_id=run.pk,
                )
            )
        seen_cells.add(cell)
        if cell not in expected_cells or expected is None or expected["algorithm"] != run.algorithm:
            issues.append(
                _issue(
                    "UNPLANNED_TUNING_CELL",
                    f"Run {run.pk} is not in the fixed six-by-five grid for {run.algorithm}.",
                    entity_id=run.pk,
                )
            )
            continue
        if (
            run.experiment_batch_id is not None
            or run.pair_attempt != 1
            or run.included_in_analysis
            or not run.exclusion_reason
            or configuration.get("excluded_from_final_inference") is not True
        ):
            issues.append(
                _issue(
                    "TUNING_EXCLUSION_MISMATCH",
                    f"Run {run.pk} is not immutable excluded pilot evidence.",
                    entity_id=run.pk,
                )
            )
        if not run.is_terminal:
            issues.append(
                _issue(
                    "NONTERMINAL_TUNING_CELL",
                    f"Run {run.pk} has not reached a terminal outcome.",
                    entity_id=run.pk,
                )
            )
        if run.status in {models.RunStatus.FAILED, models.RunStatus.CANCELLED} and (
            run.failure_category != models.FailureCategory.ALGORITHM
        ):
            issues.append(
                _issue(
                    "INVALID_TUNING_FAILURE",
                    f"Run {run.pk} has a non-algorithm failure and cannot select a solver profile.",
                    entity_id=run.pk,
                )
            )
        expected_hash = models.canonical_sha256(
            {"algorithm": run.algorithm, "seed": run.seed, **configuration}
        )
        if not run.configuration_hash or run.configuration_hash != expected_hash:
            issues.append(
                _issue(
                    "TUNING_CONFIGURATION_HASH_MISMATCH",
                    f"Run {run.pk} configuration evidence does not verify.",
                    entity_id=run.pk,
                )
            )
        expected_solver_configuration = dict(expected["solver_configuration"])
        expected_fields = {
            "time_limit_seconds": float(SOLVER_TUNING_TIME_LIMIT_SECONDS),
            **expected_solver_configuration,
        }
        mismatched_fields = [
            key for key, value in expected_fields.items() if configuration.get(key) != value
        ]
        resolved_configuration = {
            "algorithm": run.algorithm,
            "seed": run.seed,
            **expected_fields,
        }
        if configuration.get("solver_tuning_resolved_configuration_hash") != models.canonical_sha256(
            resolved_configuration
        ):
            mismatched_fields.append("solver_tuning_resolved_configuration_hash")
        if configuration.get("solver_tuning_order_seed") != SOLVER_TUNING_ORDER_SEED:
            mismatched_fields.append("solver_tuning_order_seed")
        position = configuration.get("solver_tuning_order_position")
        if type(position) is not int or not 0 <= position < expected_count:
            mismatched_fields.append("solver_tuning_order_position")
        else:
            order_positions.add(position)
            if position != planned_positions.get(cell):
                mismatched_fields.append("solver_tuning_order_position")
        if mismatched_fields:
            issues.append(
                _issue(
                    "TUNING_GRID_MISMATCH",
                    f"Run {run.pk} differs from the frozen grid: {', '.join(sorted(set(mismatched_fields)))}.",
                    entity_id=run.pk,
                )
            )

        environment_hash = str(configuration.get("environment_manifest_hash", ""))
        build_hash = str(configuration.get("build_hash", ""))
        if len(environment_hash) != 64 or len(build_hash) != 64:
            issues.append(
                _issue(
                    "MISSING_TUNING_ENVIRONMENT_HASH",
                    f"Run {run.pk} lacks frozen environment/build hashes.",
                    entity_id=run.pk,
                )
            )
        environment_hashes.add(environment_hash)
        build_hashes.add(build_hash)

        diagnostics = run.diagnostics if isinstance(run.diagnostics, dict) else {}
        metrics = diagnostics.get("metrics") if isinstance(diagnostics.get("metrics"), dict) else {}
        if metrics.get("implementation_version") != expected["parameters"]["implementation_version"]:
            issues.append(
                _issue(
                    "TUNING_IMPLEMENTATION_MISMATCH",
                    f"Run {run.pk} does not attest the configured solver implementation.",
                    entity_id=run.pk,
                )
            )
        if run.status not in {models.RunStatus.FAILED, models.RunStatus.CANCELLED} and (
            diagnostics.get("problem_hash") != tuning_problem_hash
            or diagnostics.get("config_hash") != build_solver_config(run).canonical_hash
        ):
            issues.append(_issue(
                "TUNING_SOLVER_SIGNATURE_MISMATCH",
                f"Run {run.pk} does not attest the frozen solver contract.",
                entity_id=run.pk,
            ))
        execution_seconds = _finite_nonnegative(run.execution_seconds)
        if execution_seconds is None:
            issues.append(
                _issue(
                    "MISSING_TUNING_RUNTIME",
                    f"Run {run.pk} lacks a finite execution time.",
                    entity_id=run.pk,
                )
            )
            execution_seconds = 0.0

        try:
            validation = run.validation_result
        except models.ValidationResult.DoesNotExist:
            validation = None
        if validation is None and run.status not in {models.RunStatus.FAILED, models.RunStatus.CANCELLED}:
            issues.append(_issue(
                "MISSING_TUNING_VALIDATOR_EVIDENCE",
                f"Run {run.pk} lacks the independent validator record.",
                entity_id=run.pk,
            ))
        feasible_status = run.status in _SUCCESS_STATUSES
        feasible = bool(
            validation
            and validation.is_feasible
            and validation.hard_violation_count == 0
            and feasible_status
        )
        if feasible_status != feasible:
            issues.append(
                _issue(
                    "TUNING_VALIDATION_MISMATCH",
                    f"Run {run.pk} status is not supported by independent validation evidence.",
                    entity_id=run.pk,
                )
            )
        first_feasible = _finite_nonnegative(run.first_feasible_seconds)
        penalty = (
            _finite_nonnegative(validation.raw_soft_penalty)
            if validation is not None and feasible
            else None
        )
        if feasible and (
            first_feasible is None
            or first_feasible > float(SOLVER_TUNING_TIME_LIMIT_SECONDS)
            or penalty is None
            or run.objective_value != validation.raw_soft_penalty
        ):
            issues.append(
                _issue(
                    "INVALID_TUNING_FEASIBLE_EVIDENCE",
                    f"Run {run.pk} has inconsistent time or objective evidence.",
                    entity_id=run.pk,
                )
            )
        if not feasible and run.first_feasible_seconds is not None:
            issues.append(
                _issue(
                    "INVALID_TUNING_CENSORING",
                    f"Non-feasible run {run.pk} reports a first-feasible time.",
                    entity_id=run.pk,
                )
            )

        observation = {
            "feasible": feasible,
            "raw_soft_penalty": penalty,
            "first_feasible_seconds": first_feasible,
            "execution_seconds": execution_seconds,
        }
        observations_by_configuration[(run.algorithm, configuration_id)].append(observation)
        evidence_rows.append(
            {
                "run_id": run.pk,
                "algorithm": run.algorithm,
                "configuration_id": configuration_id,
                "seed": run.seed,
                "configuration_hash": run.configuration_hash,
                "status": run.status,
                "feasible": feasible,
                "raw_soft_penalty": penalty,
                "first_feasible_seconds": first_feasible,
                "execution_seconds": execution_seconds,
                "validator_version": validation.validator_version if validation else None,
            }
        )

    missing_cells = expected_cells - seen_cells
    if missing_cells:
        issues.append(
            _issue(
                "INCOMPLETE_TUNING_MATRIX",
                f"The persisted pilot is missing {len(missing_cells)} fixed grid cells.",
                "solver_profiles",
            )
        )
    if order_positions != set(range(expected_count)):
        issues.append(
            _issue(
                "INVALID_TUNING_ORDER",
                "The persisted pilot order positions are incomplete or duplicated.",
                "solver_profiles",
            )
        )
    if len(environment_hashes) != 1 or len(build_hashes) != 1:
        issues.append(
            _issue(
                "MIXED_TUNING_ENVIRONMENT",
                "All tuning cells must share one environment manifest and build hash.",
                "solver_profiles",
            )
        )
    if issues:
        raise FormalStudyError("Persisted tuning evidence failed verification.", issues)

    rankings: dict[str, list[dict[str, Any]]] = {}
    selected_configuration_ids: dict[str, str] = {}
    expected_profiles: dict[str, dict[str, Any]] = {}
    for algorithm in _ALGORITHMS:
        algorithm_grid = [row for row in grid if row["algorithm"] == algorithm]
        ranking = [
            _persisted_tuning_ranking_row(
                str(row["configuration_id"]),
                observations_by_configuration[(algorithm, str(row["configuration_id"]))],
            )
            for row in algorithm_grid
        ]
        ranking.sort(
            key=lambda row: (
                -row["feasibility_rate"],
                float("inf")
                if row["median_feasible_raw_soft_penalty"] is None
                else row["median_feasible_raw_soft_penalty"],
                row["rmst_time_to_feasibility_seconds"],
                row["configuration_id"],
            )
        )
        selected_id = str(ranking[0]["configuration_id"])
        selected = configuration_by_id[selected_id]
        profile_payload = {
            "artifact_schema_version": SOLVER_TUNING_ARTIFACT_SCHEMA_VERSION,
            "frozen": True,
            "protocol_version": SOLVER_TUNING_PROTOCOL_VERSION,
            "algorithm": algorithm,
            "implementation_version": selected["parameters"]["implementation_version"],
            "plan_hash": plan_hash,
            "configuration_id": selected_id,
            "tuning_parameters": dict(selected["parameters"]),
            "configuration": {
                "algorithm": algorithm,
                "time_limit_seconds": float(SOLVER_TUNING_TIME_LIMIT_SECONDS),
                **dict(selected["solver_configuration"]),
            },
            "selection_metrics": ranking[0],
        }
        expected_profile = {
            **profile_payload,
            "profile_hash": models.canonical_sha256(profile_payload),
        }
        if dict(profiles[algorithm]) != expected_profile:
            issues.append(
                _issue(
                    "TUNING_SELECTION_MISMATCH",
                    f"The submitted {algorithm} profile was not selected from persisted pilot outcomes.",
                    algorithm,
                )
            )
        rankings[algorithm] = ranking
        selected_configuration_ids[algorithm] = selected_id
        expected_profiles[algorithm] = expected_profile
    if issues:
        raise FormalStudyError("Frozen solver profiles failed persisted selection verification.", issues)

    evidence_rows.sort(
        key=lambda row: (row["algorithm"], row["configuration_id"], row["seed"], row["run_id"])
    )
    evidence_hash = models.canonical_sha256(evidence_rows)
    selection_payload = {
        "artifact_schema_version": SOLVER_TUNING_ARTIFACT_SCHEMA_VERSION,
        "protocol_version": SOLVER_TUNING_PROTOCOL_VERSION,
        "evidence_class": TUNING_EVIDENCE_CLASS,
        "plan_hash": plan_hash,
        "tuning_snapshot_id": tuning_snapshot.pk,
        "tuning_snapshot_hash": tuning_snapshot.snapshot_hash,
        "mutable_event_count": mutable_event_count,
        "run_ids": [row["run_id"] for row in evidence_rows],
        "evidence_hash": evidence_hash,
        "selected_configuration_ids": selected_configuration_ids,
        "selected_profiles": expected_profiles,
        "rankings": rankings,
    }
    return {
        "plan_hash": plan_hash,
        "tuning_snapshot_id": tuning_snapshot.pk,
        "tuning_snapshot_hash": tuning_snapshot.snapshot_hash,
        "run_count": len(evidence_rows),
        "run_ids": selection_payload["run_ids"],
        "environment_manifest_hash": next(iter(environment_hashes)),
        "build_hash": next(iter(build_hashes)),
        "evidence_hash": evidence_hash,
        "selection_hash": models.canonical_sha256(selection_payload),
    }


def _run_configuration(
    profile: Mapping[str, Any],
    *,
    algorithm: str,
    deadline_seconds: int,
    study: models.ExperimentStudy,
    snapshot: models.ProblemSnapshot,
    run_kind: str,
    diagnostic_trace: bool = False,
) -> dict[str, Any]:
    frozen = dict(profile["configuration"])
    frozen.pop("algorithm", None)
    frozen.pop("time_limit_seconds", None)
    if algorithm == models.SolverAlgorithm.GENETIC_ALGORITHM:
        multiplier = int(profile["tuning_parameters"]["mutation_multiplier"])
        mutable_count = _tuning_mutable_event_count(snapshot)
        frozen.update({
            "mutation_rate": min(1.0, multiplier / mutable_count) if mutable_count else 0.0,
            "mutation_formula": f"{multiplier}/N_mutable",
            "mutation_multiplier": multiplier,
            "mutable_event_count": mutable_count,
        })
    return {
        **frozen,
        "time_limit_seconds": deadline_seconds,
        "worker_count": 1,
        "memory_limit_mb": FORMAL_MEMORY_LIMIT_MB,
        "persist_schedule": False,
        "formal_protocol": FORMAL_PROTOCOL_VERSION,
        "formal_run_kind": run_kind,
        "diagnostic_trace": diagnostic_trace,
        "solver_profile_hash": profile["profile_hash"],
        "tuning_plan_hash": profile["plan_hash"],
        "study_manifest_hash": study.manifest_hash,
        "snapshot_hash": snapshot.snapshot_hash,
        "constraint_manifest_hash": snapshot.constraint_manifest_hash,
        "objective_profile_hash": snapshot.objective_profile.profile_hash,
        "implementation_version": profile["implementation_version"],
        "infrastructure_grace_seconds": FORMAL_INFRASTRUCTURE_GRACE_SECONDS,
        "benchmark_queue": FORMAL_BENCHMARK_QUEUE,
        "algorithm_signature": algorithm,
    }


def _create_matrix_run(
    *,
    batch: models.ExperimentBatch,
    actor: models.User,
    algorithm: str,
    seed: int,
    purpose: str,
    included_in_analysis: bool,
    exclusion_reason: str,
    planned_order: int,
    configuration: dict[str, Any],
) -> models.ScheduleRun:
    run = models.ScheduleRun(
        experiment_batch=batch,
        snapshot=batch.snapshot,
        algorithm=algorithm,
        seed=seed,
        purpose=purpose,
        pair_attempt=1,
        planned_order=planned_order,
        included_in_analysis=included_in_analysis,
        exclusion_reason=exclusion_reason,
        configuration=configuration,
        configuration_hash=models.canonical_sha256(
            {"algorithm": algorithm, "seed": seed, **configuration}
        ),
        requested_by=actor,
    )
    # Resolve through the same public adapter used by execution before any
    # immutable protocol row is written.
    build_solver_config(run)
    run.full_clean()
    run.save()
    return run


def _randomized_algorithms(seed: int, order_seed: int) -> tuple[str, str]:
    # Importing Random locally keeps the manifest-independent helper simple.
    from random import Random

    algorithms = list(_ALGORITHMS)
    Random(f"{order_seed}:{seed}").shuffle(algorithms)
    return (algorithms[0], algorithms[1])


@transaction.atomic
def create_formal_study(
    *,
    source_snapshot: models.ProblemSnapshot,
    actor: models.User,
    solver_profiles: Mapping[str, Any],
    name: str | None = None,
    scaling_seed: int = DEFAULT_SCALING_SEED,
) -> models.ExperimentStudy:
    """Create the immutable four-scale, 260-run formal protocol matrix."""

    _require_central_actor(actor)
    profiles = _normalize_solver_profiles(solver_profiles)
    tuning_evidence = _authenticate_persisted_tuning_profiles(profiles)
    if type(scaling_seed) is not int or scaling_seed < 0:
        raise FormalStudyError("scaling_seed must be a non-negative integer.")

    source_issues = validate_source_snapshot(source_snapshot)
    if source_issues:
        raise FormalStudyError(
            "The source snapshot is not eligible for a formal study.", source_issues
        )

    scaling_plan = plan_scaling_snapshots(source_snapshot, seed=scaling_seed)
    snapshots = create_scaling_snapshots(source_snapshot, actor, seed=scaling_seed)
    levels = {level.percentage: level for level in scaling_plan.levels}
    instance_manifest = [
        {
            "planned_percentage": percentage,
            "actual_event_percentage": levels[percentage].actual_event_percentage,
            "actual_offering_percentage": levels[percentage].actual_offering_percentage,
            "snapshot_id": snapshots[percentage].pk,
            "snapshot_hash": snapshots[percentage].snapshot_hash,
            "selection_hash": levels[percentage].selection_hash,
            "selected_offering_ids": list(levels[percentage].selected_offering_ids),
            "locked_offering_count": levels[percentage].locked_offering_count,
            "retained_locked_offering_count": levels[percentage].retained_locked_offering_count,
        }
        for percentage in FORMAL_SCALES
    ]
    protocol_manifest = {
        "study_question": (
            "How do CP-SAT and a tuned Genetic Algorithm differ in feasibility, "
            "schedule quality, and time to feasibility as scheduling demand increases?"
        ),
        "evidence_class": "formal_thesis_experiment",
        "source_snapshot_hash": source_snapshot.snapshot_hash,
        "constraint_manifest_hash": source_snapshot.constraint_manifest_hash,
        "objective_profile_hash": source_snapshot.objective_profile.profile_hash,
        "fixed_student_limit": 50,
        "scaling_seed": scaling_seed,
        "scaling_plan_hash": models.canonical_sha256(scaling_plan.to_dict()),
        "instances": instance_manifest,
        "solver_profiles": profiles,
        "tuning_evidence": tuning_evidence,
        "expected_counts": {
            "measured": FORMAL_MEASURED_RUN_COUNT,
            "warmup": FORMAL_WARMUP_RUN_COUNT,
            "cp_sat_feasibility_diagnostics": FORMAL_FEASIBILITY_DIAGNOSTIC_COUNT,
            "trace_diagnostics": FORMAL_TRACE_RUN_COUNT,
            "all_planned": FORMAL_TOTAL_RUN_COUNT,
        },
        "execution": {
            "queue": FORMAL_BENCHMARK_QUEUE,
            "sequential": True,
            "concurrency": 1,
            "prefetch": 1,
            "cpu_limit": FORMAL_CPU_LIMIT,
            "memory_limit_mb": FORMAL_MEMORY_LIMIT_MB,
            "task_per_child": 1,
            "infrastructure_grace_seconds": FORMAL_INFRASTRUCTURE_GRACE_SECONDS,
        },
        "analysis": {
            "primary_outcomes": [
                "independent_feasibility",
                "feasible_raw_soft_penalty",
                "right_censored_time_to_feasibility_rmst",
            ],
            "holm_family_size_per_scale": 3,
            "success_only_time_average_is_primary": False,
        },
    }
    study = models.ExperimentStudy(
        name=(name or f"Formal CP-SAT vs GA / {source_snapshot.snapshot_hash[:12]}")[:200],
        mode=models.ExperimentMode.FORMAL,
        protocol_version=FORMAL_PROTOCOL_VERSION,
        status=models.StudyStatus.DRAFT,
        source_snapshot=source_snapshot,
        scale_percentages=list(FORMAL_SCALES),
        seeds=list(FORMAL_SEEDS),
        order_seed=FORMAL_ORDER_SEED,
        deadline_seconds=FORMAL_DEADLINE_SECONDS,
        cpu_limit=FORMAL_CPU_LIMIT,
        memory_limit_mb=FORMAL_MEMORY_LIMIT_MB,
        warmups_per_algorithm_scale=1,
        protocol_manifest=protocol_manifest,
        protocol_integrity={
            "formal_eligible": False,
            "validated_at": None,
            "issues": [
                _issue(
                    "VALIDATION_REQUIRED",
                    "Run formal preflight before queueing this study.",
                ).to_dict()
            ],
        },
        created_by=actor,
    )
    study.manifest_hash = models.canonical_sha256(study.manifest_payload())
    study.full_clean()
    study.save()

    global_position = 0
    for percentage in FORMAL_SCALES:
        level = levels[percentage]
        snapshot = snapshots[percentage]
        batch = models.ExperimentBatch(
            name=f"{study.name} / {percentage}% demand",
            study=study,
            snapshot=snapshot,
            planned_scale_percentage=percentage,
            actual_scale_percentage=level.actual_event_percentage,
            status=models.ExperimentStatus.DRAFT,
            seeds=list(FORMAL_SEEDS),
            order_seed=FORMAL_ORDER_SEED,
            time_limit_seconds=FORMAL_DEADLINE_SECONDS,
            cpu_limit=FORMAL_CPU_LIMIT,
            memory_limit_mb=FORMAL_MEMORY_LIMIT_MB,
            configuration={
                "protocol_version": FORMAL_PROTOCOL_VERSION,
                "formal": True,
                "sequential_execution": True,
                "planned_scale_percentage": percentage,
                "actual_event_percentage": level.actual_event_percentage,
                "actual_offering_percentage": level.actual_offering_percentage,
                "selection_hash": level.selection_hash,
                "source_snapshot_hash": source_snapshot.snapshot_hash,
                "snapshot_hash": snapshot.snapshot_hash,
                "solver_profile_hashes": {
                    algorithm: profiles[algorithm]["profile_hash"]
                    for algorithm in _ALGORITHMS
                },
                "execution_order": [],
            },
            created_by=actor,
        )
        batch.full_clean()
        batch.save()
        persisted_plan: list[dict[str, Any]] = []
        local_position = 0

        for algorithm in _randomized_algorithms(FORMAL_WARMUP_SEED, FORMAL_ORDER_SEED + percentage):
            local_position += 1
            global_position += 1
            run = _create_matrix_run(
                batch=batch,
                actor=actor,
                algorithm=algorithm,
                seed=FORMAL_WARMUP_SEED,
                purpose=models.RunPurpose.WARMUP,
                included_in_analysis=False,
                exclusion_reason="Protocol warm-up; excluded before data collection.",
                planned_order=local_position,
                configuration=_run_configuration(
                    profiles[algorithm],
                    algorithm=algorithm,
                    deadline_seconds=FORMAL_DEADLINE_SECONDS,
                    study=study,
                    snapshot=snapshot,
                    run_kind="warmup",
                ),
            )
            persisted_plan.append(
                {"run_id": run.pk, "position": local_position, "study_position": global_position}
            )

        for seed in FORMAL_SEEDS:
            for algorithm in _randomized_algorithms(seed, FORMAL_ORDER_SEED):
                local_position += 1
                global_position += 1
                run = _create_matrix_run(
                    batch=batch,
                    actor=actor,
                    algorithm=algorithm,
                    seed=seed,
                    purpose=models.RunPurpose.MEASURED,
                    included_in_analysis=True,
                    exclusion_reason="",
                    planned_order=local_position,
                    configuration=_run_configuration(
                        profiles[algorithm],
                        algorithm=algorithm,
                        deadline_seconds=FORMAL_DEADLINE_SECONDS,
                        study=study,
                        snapshot=snapshot,
                        run_kind="measured",
                    ),
                )
                persisted_plan.append(
                    {"run_id": run.pk, "position": local_position, "study_position": global_position}
                )

        local_position += 1
        global_position += 1
        diagnostic = _create_matrix_run(
            batch=batch,
            actor=actor,
            algorithm=models.SolverAlgorithm.CP_SAT,
            seed=FORMAL_FEASIBILITY_DIAGNOSTIC_SEED,
            purpose=models.RunPurpose.DIAGNOSTIC,
            included_in_analysis=False,
            exclusion_reason="Extended CP-SAT feasibility diagnostic; excluded from inference.",
            planned_order=local_position,
            configuration=_run_configuration(
                profiles[models.SolverAlgorithm.CP_SAT],
                algorithm=models.SolverAlgorithm.CP_SAT,
                deadline_seconds=FORMAL_FEASIBILITY_DIAGNOSTIC_SECONDS,
                study=study,
                snapshot=snapshot,
                run_kind="feasibility_diagnostic",
            ),
        )
        persisted_plan.append(
            {"run_id": diagnostic.pk, "position": local_position, "study_position": global_position}
        )

        for algorithm in _randomized_algorithms(FORMAL_TRACE_SEED, FORMAL_ORDER_SEED + percentage):
            local_position += 1
            global_position += 1
            trace = _create_matrix_run(
                batch=batch,
                actor=actor,
                algorithm=algorithm,
                seed=FORMAL_TRACE_SEED,
                purpose=models.RunPurpose.DIAGNOSTIC,
                included_in_analysis=False,
                exclusion_reason="Diagnostic convergence trace; excluded from inference.",
                planned_order=local_position,
                configuration=_run_configuration(
                    profiles[algorithm],
                    algorithm=algorithm,
                    deadline_seconds=FORMAL_DEADLINE_SECONDS,
                    study=study,
                    snapshot=snapshot,
                    run_kind="trace",
                    diagnostic_trace=True,
                ),
            )
            persisted_plan.append(
                {"run_id": trace.pk, "position": local_position, "study_position": global_position}
            )

        batch.configuration = {**batch.configuration, "execution_order": persisted_plan}
        batch._allow_protocol_update = True
        batch.save(update_fields=["configuration", "updated_at"])

    models.AuditLog.objects.create(
        actor=actor,
        action="formal_study.created",
        entity_type="ExperimentStudy",
        entity_id=str(study.pk),
        details={
            "manifest_hash": study.manifest_hash,
            "source_snapshot_hash": source_snapshot.snapshot_hash,
            "scale_snapshots": {
                str(scale): snapshots[scale].snapshot_hash for scale in FORMAL_SCALES
            },
            "measured_run_count": FORMAL_MEASURED_RUN_COUNT,
            "excluded_run_count": FORMAL_TOTAL_RUN_COUNT - FORMAL_MEASURED_RUN_COUNT,
        },
    )
    return study


def _manifest_policy_issues(snapshot: models.ProblemSnapshot) -> list[ProtocolIssue]:
    manifest = snapshot.rule_manifest
    issues: list[ProtocolIssue] = []
    if not isinstance(manifest, dict) or manifest.get("schema") != "constraint-manifest-v2":
        return [
            _issue(
                "INVALID_CONSTRAINT_MANIFEST",
                "The snapshot must contain a constraint-manifest-v2 object.",
                "rule_manifest",
            )
        ]
    if snapshot.constraint_manifest_hash != models.canonical_sha256(manifest):
        issues.append(
            _issue(
                "CONSTRAINT_MANIFEST_HASH_MISMATCH",
                "The frozen constraint-manifest hash does not verify.",
                "constraint_manifest_hash",
            )
        )
    fixed_rule = manifest.get("fixed_student_rule")
    if not isinstance(fixed_rule, dict):
        issues.append(
            _issue("MISSING_FIXED_STUDENT_POLICY", "The fixed 50-student policy is missing.")
        )
    elif (
        fixed_rule.get("rule_code") != "FIXED_STUDENT_LIMIT_50"
        or fixed_rule.get("limit") != 50
        or fixed_rule.get("approved") is not True
        or not fixed_rule.get("policy_hash")
    ):
        issues.append(
            _issue(
                "UNAPPROVED_FIXED_STUDENT_POLICY",
                "The fixed 50-student rule must be approved and hash-addressed.",
            )
        )
    policies = manifest.get("policies")
    policy_hashes = manifest.get("policy_hashes")
    if not isinstance(policies, list) or not policies:
        issues.append(_issue("MISSING_APPROVED_POLICIES", "No approved rule policies were frozen."))
        policies = []
    if not isinstance(policy_hashes, list) or not policy_hashes:
        issues.append(_issue("MISSING_POLICY_HASHES", "The policy hash list is empty."))
        policy_hashes = []
    expected_term_id = snapshot.revision.term_id
    reflected_hashes: list[str] = []
    for row in policies:
        if not isinstance(row, dict):
            issues.append(_issue("INVALID_POLICY_ROW", "Every policy entry must be an object."))
            continue
        policy_hash = row.get("policy_hash")
        if isinstance(policy_hash, str):
            reflected_hashes.append(policy_hash)
        if (
            row.get("is_approved") is not True
            or not row.get("approved_by_id")
            or not row.get("approved_at")
        ):
            issues.append(
                _issue(
                    "UNAPPROVED_POLICY",
                    f"Policy {row.get('rule_code') or policy_hash or '?'} is not approved.",
                )
            )
        if row.get("effective_term_id") != expected_term_id:
            issues.append(
                _issue(
                    "POLICY_TERM_MISMATCH",
                    f"Policy {row.get('rule_code') or policy_hash or '?'} belongs to another term.",
                )
            )
        database_policy = models.ConstraintPolicyVersion.objects.filter(
            policy_hash=policy_hash,
            effective_term_id=expected_term_id,
            is_approved=True,
            approved_by__isnull=False,
            approved_at__isnull=False,
        ).first()
        if database_policy is None:
            issues.append(
                _issue(
                    "POLICY_PROVENANCE_NOT_FOUND",
                    f"Policy hash {policy_hash or '?'} is not an approved term policy.",
                )
            )
        elif any(
            row.get(key) != expected
            for key, expected in {
                "rule_code": database_policy.rule_code,
                "version": database_policy.version,
                "classification": database_policy.classification,
                "owner_office": database_policy.owner_office,
                "source": database_policy.source,
                "parameters": database_policy.parameters,
            }.items()
        ):
            issues.append(
                _issue(
                    "POLICY_PROVENANCE_MISMATCH",
                    f"Frozen fields for policy {database_policy.rule_code} do not match its approved row.",
                )
            )
    if sorted(set(reflected_hashes)) != sorted(set(policy_hashes)):
        issues.append(
            _issue(
                "POLICY_HASH_SET_MISMATCH",
                "The policy hash list does not exactly match the frozen policy rows.",
            )
        )
    if isinstance(fixed_rule, dict) and fixed_rule.get("policy_hash") not in set(policy_hashes):
        issues.append(
            _issue(
                "FIXED_POLICY_HASH_NOT_REFLECTED",
                "The fixed-rule policy hash is absent from policy_hashes.",
            )
        )
    return issues


def validate_source_snapshot(snapshot: models.ProblemSnapshot) -> tuple[ProtocolIssue, ...]:
    """Return every reason a snapshot cannot anchor formal inference."""

    issues: list[ProtocolIssue] = []
    if not models.schema_version_at_least(snapshot.schema_version, 1, 2):
        issues.append(_issue("SNAPSHOT_SCHEMA_TOO_OLD", "Formal studies require schema 1.2."))
    if snapshot.fixed_student_limit != 50:
        issues.append(_issue("INVALID_STUDENT_LIMIT", "The frozen student limit must be 50."))
    if snapshot.snapshot_hash != models.canonical_sha256(snapshot.hash_payload()):
        issues.append(_issue("SNAPSHOT_HASH_MISMATCH", "The source snapshot hash does not verify."))

    from scheduler.services.problem_builder import _domain_objective_profile, load_problem
    from scheduler.services.snapshot_integrity import snapshot_consistency_issues

    try:
        problem = load_problem(snapshot)
    except (TypeError, ValueError, KeyError) as exc:
        issues.append(_issue("INVALID_FROZEN_CONTRACT", f"The solver contract is malformed: {exc}."))
        return tuple(issues)
    issues.extend(ProtocolIssue(**row) for row in snapshot_consistency_issues(snapshot))

    objective = snapshot.objective_profile
    if problem.objective_profile.to_dict() != _domain_objective_profile(objective).to_dict():
        issues.append(
            _issue("FROZEN_OBJECTIVE_MISMATCH", "The solver objective differs from the approved objective profile.")
        )
    if (
        not objective.is_approved
        or not objective.approved_by_id
        or not objective.approved_at
        or objective.profile_hash != models.canonical_sha256(objective.hash_payload())
    ):
        issues.append(
            _issue(
                "OBJECTIVE_NOT_APPROVED",
                "The objective profile must be approved, immutable, and hash-verifiable.",
            )
        )
    normalizers = objective.normalization_denominators
    if (
        not isinstance(normalizers, dict)
        or set(normalizers) != set(models.default_objective_weights())
        or any(type(value) is not int or value <= 0 for value in normalizers.values())
        or normalizers == models.default_objective_normalizers()
    ):
        issues.append(
            _issue(
                "PLACEHOLDER_OBJECTIVE_NORMALIZERS",
                "Institution-approved, non-placeholder objective normalizers are required.",
            )
        )

    issues.extend(_manifest_policy_issues(snapshot))
    events = snapshot.input_data.get("events") if isinstance(snapshot.input_data, dict) else None
    if not isinstance(events, list) or not events:
        issues.append(_issue("EMPTY_PROBLEM", "The snapshot contains no meetings."))
        events = []
    candidate_map = snapshot.candidate_map
    if not isinstance(candidate_map, dict):
        issues.append(_issue("INVALID_CANDIDATE_MAP", "Candidate domains must be a JSON object."))
        candidate_map = {}

    section_headcounts = snapshot.section_headcounts
    if not isinstance(section_headcounts, dict) or not section_headcounts:
        issues.append(_issue("MISSING_ENROLLMENT", "Section enrollment evidence is missing."))
        section_headcounts = {}
    for section_id, count in section_headcounts.items():
        if type(count) is not int or not 1 <= count <= 50:
            issues.append(
                _issue(
                    "INVALID_SECTION_ENROLLMENT",
                    f"Section {section_id} must contain 1 to 50 students.",
                    "section_headcounts",
                    section_id,
                )
            )

    meeting_headcounts = snapshot.meeting_headcounts
    if not isinstance(meeting_headcounts, dict) or not meeting_headcounts:
        issues.append(_issue("MISSING_MEETING_HEADCOUNTS", "Meeting headcounts are missing."))
        meeting_headcounts = {}
    event_ids: set[str] = set()
    referenced_sections: set[str] = set()
    referenced_instructors: set[str] = set()
    referenced_rooms: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            issues.append(_issue("INVALID_EVENT", "Every meeting must be a JSON object."))
            continue
        event_id = str(event.get("event_id", event.get("id", "")))
        if not event_id:
            issues.append(_issue("MISSING_EVENT_ID", "Every meeting needs a stable event ID."))
            continue
        event_ids.add(event_id)
        sections = {str(value) for value in event.get("section_ids", ())}
        referenced_sections.update(sections)
        referenced_instructors.update(str(value) for value in event.get("instructor_ids", ()))
        candidates = candidate_map.get(event_id)
        if not isinstance(candidates, list) or not candidates:
            issues.append(
                _issue(
                    "EMPTY_CANDIDATE_DOMAIN",
                    f"Meeting {event_id} has no legal room-time candidates.",
                    "candidate_map",
                    event_id,
                )
            )
        else:
            referenced_rooms.update(
                str(candidate.get("room_id"))
                for candidate in candidates
                if isinstance(candidate, dict) and candidate.get("room_id") is not None
            )
        headcount = meeting_headcounts.get(event_id)
        expected = sum(section_headcounts.get(section_id, 0) for section_id in sections)
        if type(headcount) is not int or headcount != expected or not 1 <= headcount <= 50:
            issues.append(
                _issue(
                    "INVALID_MEETING_HEADCOUNT",
                    (
                        f"Meeting {event_id} freezes {headcount!r} students; the sum of its "
                        f"unique sections is {expected} and must not exceed 50."
                    ),
                    "meeting_headcounts",
                    event_id,
                )
            )
        if event.get("fixed_student_limit") != 50:
            issues.append(
                _issue(
                    "EVENT_STUDENT_LIMIT_NOT_FROZEN",
                    f"Meeting {event_id} does not freeze the fixed limit of 50.",
                    entity_id=event_id,
                )
            )
    missing_sections = sorted(referenced_sections - set(section_headcounts))
    if missing_sections:
        issues.append(
            _issue(
                "INCOMPLETE_ENROLLMENT",
                "Missing enrollment for sections: " + ", ".join(missing_sections[:10]),
            )
        )
    missing_meetings = sorted(event_ids - set(meeting_headcounts))
    if missing_meetings:
        issues.append(
            _issue(
                "INCOMPLETE_MEETING_HEADCOUNTS",
                "Missing meeting headcount for: " + ", ".join(missing_meetings[:10]),
            )
        )

    room_evidence = snapshot.input_data.get("room_evidence", [])
    instructor_evidence = snapshot.input_data.get("instructor_evidence", [])
    if not isinstance(room_evidence, list) or not isinstance(instructor_evidence, list):
        issues.append(_issue("INVALID_AVAILABILITY_EVIDENCE", "Availability evidence is malformed."))
        room_evidence = []
        instructor_evidence = []
    room_by_id = {
        str(row.get("room_id")): row for row in room_evidence if isinstance(row, dict)
    }
    instructor_by_id = {
        str(row.get("instructor_id")): row
        for row in instructor_evidence
        if isinstance(row, dict)
    }
    for room_id in sorted(referenced_rooms):
        row = room_by_id.get(room_id)
        if row is None or not isinstance(row.get("available_atom_ids"), list):
            issues.append(
                _issue(
                    "INCOMPLETE_ROOM_AVAILABILITY",
                    f"Room {room_id} lacks frozen availability evidence.",
                    entity_id=room_id,
                )
            )
    for instructor_id in sorted(referenced_instructors):
        row = instructor_by_id.get(instructor_id)
        if row is None or not isinstance(row.get("available_atom_ids"), list):
            issues.append(
                _issue(
                    "INCOMPLETE_INSTRUCTOR_AVAILABILITY",
                    f"Instructor {instructor_id} lacks frozen availability evidence.",
                    entity_id=instructor_id,
                )
            )
            continue
        daily_limit = row.get("max_daily_teaching_atoms")
        acknowledged_no_limit = row.get("acknowledge_no_daily_limit") is True
        has_positive_limit = type(daily_limit) is int and daily_limit > 0
        if has_positive_limit == acknowledged_no_limit:
            issues.append(
                _issue(
                    "INCOMPLETE_DAILY_LOAD_POLICY",
                    (
                        f"Instructor {instructor_id} needs either one positive daily-atom "
                        "limit or an explicit no-limit acknowledgement."
                    ),
                    entity_id=instructor_id,
                )
            )
        if not row.get("daily_load_policy_hash"):
            issues.append(
                _issue(
                    "MISSING_DAILY_LOAD_POLICY_HASH",
                    f"Instructor {instructor_id} lacks approved daily-load provenance.",
                    entity_id=instructor_id,
                )
            )
    if not isinstance(snapshot.reserved_block_evidence, list):
        issues.append(_issue("INVALID_RESERVED_BLOCK_EVIDENCE", "Reserved-block evidence is malformed."))
    if not isinstance(snapshot.instructor_daily_load_evidence, list):
        issues.append(_issue("INVALID_DAILY_LOAD_EVIDENCE", "Daily-load evidence is malformed."))
    return tuple(issues)


def _study_protocol_issues(
    study: models.ExperimentStudy,
    *,
    terminal: bool = False,
) -> list[ProtocolIssue]:
    issues: list[ProtocolIssue] = list(validate_source_snapshot(study.source_snapshot))
    expected_fields = {
        "mode": models.ExperimentMode.FORMAL,
        "protocol_version": FORMAL_PROTOCOL_VERSION,
        "scale_percentages": list(FORMAL_SCALES),
        "seeds": list(FORMAL_SEEDS),
        "order_seed": FORMAL_ORDER_SEED,
        "deadline_seconds": FORMAL_DEADLINE_SECONDS,
        "cpu_limit": FORMAL_CPU_LIMIT,
        "memory_limit_mb": FORMAL_MEMORY_LIMIT_MB,
        "warmups_per_algorithm_scale": 1,
    }
    for field, expected in expected_fields.items():
        if getattr(study, field) != expected:
            issues.append(
                _issue(
                    "FORMAL_CONSTANT_MISMATCH",
                    f"{field} must equal {expected!r}.",
                    field,
                )
            )
    if study.manifest_hash != models.canonical_sha256(study.manifest_payload()):
        issues.append(_issue("STUDY_MANIFEST_HASH_MISMATCH", "The study manifest hash does not verify."))
    try:
        profiles = _normalize_solver_profiles(study.protocol_manifest.get("solver_profiles"))
        tuning_evidence = _authenticate_persisted_tuning_profiles(profiles)
        if study.protocol_manifest.get("tuning_evidence") != tuning_evidence:
            issues.append(
                _issue(
                    "TUNING_EVIDENCE_CHANGED",
                    "The persisted pilot evidence no longer matches the frozen study selection.",
                    "tuning_evidence",
                )
            )
    except FormalStudyError as exc:
        issues.extend(exc.issues or (_issue("INVALID_TUNING_PROFILES", str(exc)),))

    batches = list(study.batches.select_related("snapshot__objective_profile").prefetch_related("runs"))
    by_scale = {batch.planned_scale_percentage: batch for batch in batches}
    if len(batches) != 4 or set(by_scale) != set(FORMAL_SCALES):
        issues.append(
            _issue("INVALID_SCALE_MATRIX", "A formal study requires exactly 25%, 50%, 75%, and 100% batches.")
        )
        return issues
    manifest_instances = {
        row.get("planned_percentage"): row
        for row in study.protocol_manifest.get("instances", [])
        if isinstance(row, dict)
    }
    for scale in FORMAL_SCALES:
        batch = by_scale[scale]
        if (
            batch.seeds != list(FORMAL_SEEDS)
            or batch.order_seed != FORMAL_ORDER_SEED
            or batch.time_limit_seconds != FORMAL_DEADLINE_SECONDS
            or batch.cpu_limit != FORMAL_CPU_LIMIT
            or batch.memory_limit_mb != FORMAL_MEMORY_LIMIT_MB
        ):
            issues.append(
                _issue(
                    "BATCH_CONSTANT_MISMATCH",
                    f"The {scale}% batch does not match formal limits.",
                    entity_id=batch.pk,
                )
            )
        instance = manifest_instances.get(scale)
        if (
            not instance
            or instance.get("snapshot_hash") != batch.snapshot.snapshot_hash
            or batch.configuration.get("selection_hash") != instance.get("selection_hash")
        ):
            issues.append(
                _issue(
                    "SCALE_PROVENANCE_MISMATCH",
                    f"The {scale}% instance does not match the frozen scaling manifest.",
                    entity_id=batch.pk,
                )
            )
        issues.extend(validate_source_snapshot(batch.snapshot))

        runs = list(batch.runs.all())
        matrix_runs = [run for run in runs if run.pair_attempt == 1] if terminal else runs
        counts = Counter(run.purpose for run in matrix_runs)
        if len(matrix_runs) != 65 or counts != Counter(
            {
                models.RunPurpose.MEASURED: 60,
                models.RunPurpose.WARMUP: 2,
                models.RunPurpose.DIAGNOSTIC: 3,
            }
        ):
            issues.append(
                _issue(
                    "INVALID_RUN_MATRIX",
                    f"The {scale}% batch must contain 60 measured, 2 warm-up, and 3 diagnostic runs.",
                    entity_id=batch.pk,
                )
            )
            continue
        measured_cells = Counter(
            (run.seed, run.algorithm)
            for run in runs
            if run.purpose == models.RunPurpose.MEASURED and run.pair_attempt == 1
        )
        expected_cells = Counter((seed, algorithm) for seed in FORMAL_SEEDS for algorithm in _ALGORITHMS)
        if measured_cells != expected_cells:
            issues.append(
                _issue(
                    "UNPAIRED_MEASURED_MATRIX",
                    f"The {scale}% measured matrix is not one CP-SAT/GA pair per seed.",
                    entity_id=batch.pk,
                )
            )
        planned_orders = [run.planned_order for run in runs]
        if (
            any(type(order) is not int for order in planned_orders)
            or sorted(planned_orders) != list(range(1, len(runs) + 1))
        ):
            issues.append(
                _issue(
                    "INVALID_PLANNED_ORDER",
                    f"The {scale}% planned execution order is incomplete or duplicated.",
                    entity_id=batch.pk,
                )
            )
        for run in runs:
            expected_hash = models.canonical_sha256(
                {"algorithm": run.algorithm, "seed": run.seed, **run.configuration}
            )
            if run.configuration_hash != expected_hash:
                issues.append(
                    _issue(
                        "RUN_CONFIGURATION_HASH_MISMATCH",
                        f"Run {run.pk} configuration hash does not verify.",
                        entity_id=run.pk,
                    )
                )
            if run.snapshot_id != batch.snapshot_id:
                issues.append(
                    _issue("RUN_SNAPSHOT_MISMATCH", f"Run {run.pk} uses another snapshot.", entity_id=run.pk)
                )
            if run.purpose == models.RunPurpose.MEASURED:
                if not terminal and (not run.included_in_analysis or run.exclusion_reason):
                    issues.append(
                        _issue("MEASURED_RUN_EXCLUDED", f"Measured run {run.pk} is pre-excluded.")
                    )
                if run.seed not in FORMAL_SEEDS or run.configuration.get("time_limit_seconds") != 300:
                    issues.append(_issue("MEASURED_RUN_CONSTANT_MISMATCH", f"Run {run.pk} is invalid."))
            elif run.included_in_analysis or not run.exclusion_reason:
                issues.append(_issue("EXCLUDED_RUN_INCLUDED", f"Run {run.pk} must be excluded."))
        if terminal:
            issues.extend(_terminal_pair_attempt_issues(batch, runs))
        diagnostics = [run for run in runs if run.purpose == models.RunPurpose.DIAGNOSTIC]
        extended = [
            run
            for run in diagnostics
            if run.configuration.get("formal_run_kind") == "feasibility_diagnostic"
        ]
        traces = [
            run for run in diagnostics if run.configuration.get("formal_run_kind") == "trace"
        ]
        if (
            len(extended) != 1
            or extended[0].algorithm != models.SolverAlgorithm.CP_SAT
            or extended[0].seed != FORMAL_FEASIBILITY_DIAGNOSTIC_SEED
            or extended[0].configuration.get("time_limit_seconds")
            != FORMAL_FEASIBILITY_DIAGNOSTIC_SECONDS
            or len(traces) != 2
            or {run.algorithm for run in traces} != set(_ALGORITHMS)
            or any(run.seed != FORMAL_TRACE_SEED for run in traces)
        ):
            issues.append(
                _issue(
                    "INVALID_DIAGNOSTIC_MATRIX",
                    f"The {scale}% diagnostic matrix does not match the preregistration.",
                )
            )
    return issues


@transaction.atomic
def validate_formal_study(
    study: models.ExperimentStudy,
    *,
    actor: models.User,
) -> dict[str, Any]:
    _require_central_actor(actor)
    locked = models.ExperimentStudy.objects.select_for_update().get(pk=study.pk)
    if not locked.is_formal:
        raise FormalStudyError("This endpoint accepts formal studies only.")
    if locked.status not in {models.StudyStatus.DRAFT, models.StudyStatus.READY, models.StudyStatus.INVALID}:
        raise FormalStudyError("Preflight cannot rewrite a queued or completed study.")
    issues = _study_protocol_issues(locked)
    checked_at = timezone.now()
    integrity = {
        "formal_eligible": not issues,
        "validated_at": checked_at.isoformat(),
        "validator_version": FORMAL_PROTOCOL_VERSION,
        "issues": [issue.to_dict() for issue in issues],
        "expected_measured_runs": FORMAL_MEASURED_RUN_COUNT,
        "expected_all_runs": FORMAL_TOTAL_RUN_COUNT,
    }
    status_value = models.StudyStatus.READY if not issues else models.StudyStatus.INVALID
    invalid_reason = "" if not issues else "; ".join(issue.code for issue in issues[:20])
    models.ExperimentStudy.objects.filter(pk=locked.pk).update(
        status=status_value,
        protocol_integrity=integrity,
        invalid_reason=invalid_reason,
        updated_at=checked_at,
    )
    models.AuditLog.objects.create(
        actor=actor,
        action="formal_study.validated",
        entity_type="ExperimentStudy",
        entity_id=str(locked.pk),
        details={
            "formal_eligible": not issues,
            "issue_codes": [issue.code for issue in issues],
            "manifest_hash": locked.manifest_hash,
        },
    )
    return {"valid": not issues, "status": status_value, "integrity": integrity}


def formal_ordered_runs(study: models.ExperimentStudy) -> list[models.ScheduleRun]:
    result: list[models.ScheduleRun] = []
    for batch in study.batches.order_by("planned_scale_percentage", "pk"):
        result.extend(
            batch.runs.order_by("planned_order", "pair_attempt", "pk")
        )
    return result


def _dispatch_run_to_benchmark(run: models.ScheduleRun) -> bool:
    """Claim and dispatch once using the immutable dispatch key as task ID."""

    from scheduler.tasks import execute_schedule_run

    task_id = str(run.dispatch_key)
    claimed = models.ScheduleRun.objects.filter(
        pk=run.pk,
        status=models.RunStatus.QUEUED,
        task_id="",
    ).update(task_id=task_id)
    if not claimed:
        return False
    try:
        execute_schedule_run.apply_async(
            args=[run.pk],
            task_id=task_id,
            queue=FORMAL_BENCHMARK_QUEUE,
            time_limit=task_time_limit_seconds(run),
        )
    except Exception:
        models.ScheduleRun.objects.filter(pk=run.pk, task_id=task_id).update(task_id="")
        raise
    return True


def _best_effort_revoke_runs(
    runs: Sequence[models.ScheduleRun],
) -> list[dict[str, str]]:
    """Revoke published tasks without letting a broker outage block DB safety."""

    failures: list[dict[str, str]] = []
    for run in runs:
        if not run.task_id:
            continue
        try:
            if run.status == models.RunStatus.RUNNING:
                current_app.control.revoke(run.task_id, terminate=True, signal="SIGTERM")
            else:
                current_app.control.revoke(run.task_id, terminate=False)
        except Exception as exc:  # pragma: no cover - transport-specific
            failures.append(
                {
                    "run_id": str(run.pk),
                    "task_id": run.task_id,
                    "error_type": type(exc).__name__,
                }
            )
    return failures


def _dispatch_replacement_pair_after_commit(
    run_ids: tuple[int, int],
    *,
    study_id: int,
    actor_id: int,
) -> None:
    """Dispatch a replacement pair only after its database transaction commits.

    A broker failure at this point is a second infrastructure failure for the
    affected seed block.  Preserve the replacement rows, audit the observation,
    and invalidate the formal study instead of leaving apparently runnable rows
    stranded without a task identifier.
    """

    dispatched = 0
    try:
        for run in models.ScheduleRun.objects.filter(pk__in=run_ids).order_by(
            "planned_order", "pk"
        ):
            dispatched += int(_dispatch_run_to_benchmark(run))
    except Exception as exc:  # pragma: no cover - broker-specific failure path
        now = timezone.now()
        reason = f"Replacement dispatch infrastructure failure: {type(exc).__name__}"
        active = list(
            models.ScheduleRun.objects.filter(
                pk__in=run_ids,
                status__in=[models.RunStatus.QUEUED, models.RunStatus.RUNNING],
            )
        )
        revoke_failures = _best_effort_revoke_runs(active)
        models.ScheduleRun.objects.filter(
            pk__in=[run.pk for run in active],
        ).update(
            status=models.RunStatus.FAILED,
            finished_at=now,
            heartbeat_at=now,
            lease_expires_at=None,
            stopping_reason=reason[:255],
            error_message=str(exc)[:2000],
            failure_category=models.FailureCategory.INFRASTRUCTURE,
            failure_classified_by_id=actor_id,
            failure_classified_at=now,
            included_in_analysis=False,
            exclusion_reason=(
                "Second infrastructure failure during paired replacement dispatch."
            ),
            updated_at=now,
        )
        models.ExperimentBatch.objects.filter(runs__pk__in=run_ids).update(
            status=models.ExperimentStatus.FAILED,
            updated_at=now,
        )
        models.ExperimentStudy.objects.filter(pk=study_id).update(
            status=models.StudyStatus.INVALID,
            invalid_reason=(
                "A paired replacement encountered a second infrastructure failure; "
                "no formal conclusion is available."
            ),
            updated_at=now,
        )
        models.AuditLog.objects.create(
            actor_id=actor_id,
            action="formal_run.replacement_dispatch_failed",
            entity_type="ExperimentStudy",
            entity_id=str(study_id),
            details={
                "replacement_run_ids": list(run_ids),
                "dispatched_before_failure": dispatched,
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
                "revoke_failures": revoke_failures,
            },
        )
        return
    models.AuditLog.objects.create(
        actor_id=actor_id,
        action="formal_run.replacement_pair_dispatched",
        entity_type="ExperimentStudy",
        entity_id=str(study_id),
        details={
            "replacement_run_ids": list(run_ids),
            "new_dispatch_claims": dispatched,
            "queue": FORMAL_BENCHMARK_QUEUE,
        },
    )


def queue_formal_study(
    study: models.ExperimentStudy,
    *,
    actor: models.User,
) -> models.ExperimentStudy:
    _require_central_actor(actor)
    study.refresh_from_db()
    if study.status != models.StudyStatus.READY:
        raise FormalStudyError("Only a successfully validated formal study can be queued.")
    # Recheck immutable protocol evidence immediately before external dispatch.
    issues = _study_protocol_issues(study)
    if issues:
        now = timezone.now()
        models.ExperimentStudy.objects.filter(pk=study.pk).update(
            status=models.StudyStatus.INVALID,
            invalid_reason="; ".join(issue.code for issue in issues[:20]),
            protocol_integrity={
                "formal_eligible": False,
                "validated_at": now.isoformat(),
                "issues": [issue.to_dict() for issue in issues],
            },
            updated_at=now,
        )
        raise FormalStudyError("Formal preflight changed before queueing.", issues)

    now = timezone.now()
    models.ExperimentStudy.objects.filter(pk=study.pk).update(
        status=models.StudyStatus.QUEUED,
        updated_at=now,
    )
    study.batches.filter(status=models.ExperimentStatus.DRAFT).update(
        status=models.ExperimentStatus.QUEUED,
        updated_at=now,
    )
    ordered_runs = formal_ordered_runs(study)
    dispatched = 0
    try:
        for run in ordered_runs:
            dispatched += int(_dispatch_run_to_benchmark(run))
    except Exception as exc:
        # Publishing hundreds of tasks cannot be made atomic with the database.
        # Fail closed if the broker accepts only part of the matrix: revoke every
        # published task, terminalize all remaining observations, and preserve an
        # audited infrastructure failure.  INVALID is intentionally guarded by
        # refresh_run_containers, so late worker deliveries cannot resurrect or
        # strand this study as RUNNING.
        now = timezone.now()
        active = list(
            models.ScheduleRun.objects.filter(
                experiment_batch__study=study,
                status__in=[models.RunStatus.QUEUED, models.RunStatus.RUNNING],
            ).only("pk", "status", "task_id")
        )
        revoke_failures = _best_effort_revoke_runs(active)
        failure_reason = "Formal matrix dispatch failed before all tasks were published."
        models.ScheduleRun.objects.filter(pk__in=[run.pk for run in active]).update(
            status=models.RunStatus.FAILED,
            finished_at=now,
            heartbeat_at=now,
            lease_expires_at=None,
            stopping_reason="Benchmark dispatch infrastructure failure",
            error_message=f"{type(exc).__name__}: {exc}"[:2000],
            failure_category=models.FailureCategory.INFRASTRUCTURE,
            failure_classified_by=actor,
            failure_classified_at=now,
            included_in_analysis=False,
            exclusion_reason=failure_reason,
            updated_at=now,
        )
        study.batches.update(status=models.ExperimentStatus.FAILED, updated_at=now)
        models.ExperimentStudy.objects.filter(pk=study.pk).update(
            status=models.StudyStatus.INVALID,
            invalid_reason=(
                "Formal matrix dispatch was incomplete; all published tasks were revoked "
                "and no formal conclusion is available."
            ),
            updated_at=now,
        )
        models.AuditLog.objects.create(
            actor=actor,
            action="formal_study.dispatch_failed",
            entity_type="ExperimentStudy",
            entity_id=str(study.pk),
            details={
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
                "published_before_failure": dispatched,
                "terminalized_run_count": len(active),
                "revoke_failures": revoke_failures,
            },
        )
        raise
    models.AuditLog.objects.create(
        actor=actor,
        action="formal_study.queued",
        entity_type="ExperimentStudy",
        entity_id=str(study.pk),
        details={"queue": FORMAL_BENCHMARK_QUEUE, "dispatched_run_count": dispatched},
    )
    study.refresh_from_db()
    return study


@transaction.atomic
def cancel_formal_study(
    study: models.ExperimentStudy,
    *,
    actor: models.User,
) -> models.ExperimentStudy:
    _require_central_actor(actor)
    locked = models.ExperimentStudy.objects.select_for_update().get(pk=study.pk)
    if not locked.is_formal:
        raise FormalStudyError("This endpoint accepts formal studies only.")
    if locked.status in {models.StudyStatus.COMPLETED, models.StudyStatus.CANCELLED}:
        return locked
    active = list(
        models.ScheduleRun.objects.select_for_update().filter(
            experiment_batch__study=locked,
            status__in=[models.RunStatus.QUEUED, models.RunStatus.RUNNING],
        )
    )
    now = timezone.now()
    revoke_failures = _best_effort_revoke_runs(active)
    models.ScheduleRun.objects.filter(pk__in=[run.pk for run in active]).update(
        status=models.RunStatus.CANCELLED,
        finished_at=now,
        heartbeat_at=now,
        lease_expires_at=None,
        stopping_reason="Formal study cancelled by authorized user",
        failure_category=models.FailureCategory.USER_CANCELLATION,
        failure_classified_by=actor,
        failure_classified_at=now,
        included_in_analysis=False,
        exclusion_reason="User cancellation invalidates formal inference.",
        updated_at=now,
    )
    locked.batches.exclude(status=models.ExperimentStatus.COMPLETED).update(
        status=models.ExperimentStatus.CANCELLED,
        updated_at=now,
    )
    models.ExperimentStudy.objects.filter(pk=locked.pk).update(
        status=models.StudyStatus.CANCELLED,
        cancelled_by=actor,
        cancelled_at=now,
        invalid_reason="Study cancelled; no formal conclusion available.",
        updated_at=now,
    )
    models.AuditLog.objects.create(
        actor=actor,
        action="formal_study.cancelled",
        entity_type="ExperimentStudy",
        entity_id=str(locked.pk),
        details={
            "cancelled_active_runs": len(active),
            "revoke_failures": revoke_failures,
        },
    )
    locked.refresh_from_db()
    return locked


@transaction.atomic
def classify_run_failure(
    run: models.ScheduleRun,
    *,
    actor: models.User,
    category: str,
    reason: str,
) -> models.ScheduleRun:
    """Append an audited classification to a failed/cancelled formal trial."""

    _require_central_actor(actor)
    locked = (
        models.ScheduleRun.objects.select_for_update(of=("self",))
        .select_related("experiment_batch__study")
        .get(pk=run.pk)
    )
    study = locked.experiment_batch.study if locked.experiment_batch_id else None
    if not study or not study.is_formal:
        raise FormalStudyError("Failure classification here is limited to formal-study runs.")
    if locked.status not in {models.RunStatus.FAILED, models.RunStatus.CANCELLED}:
        raise FormalStudyError("Only failed or cancelled runs require failure classification.")
    if category not in models.FailureCategory.values:
        raise FormalStudyError("Unknown failure category.")
    reason = str(reason).strip()
    if not reason:
        raise FormalStudyError("An audited classification reason is required.")
    if locked.failure_classified_by_id or (
        locked.failure_category and locked.failure_category != models.FailureCategory.UNCLASSIFIED
    ):
        raise FormalStudyError("This failure already has an immutable audited classification.")
    if locked.status == models.RunStatus.CANCELLED and category != models.FailureCategory.USER_CANCELLATION:
        raise FormalStudyError("Cancelled runs must be classified as user cancellation.")
    if locked.status == models.RunStatus.FAILED and category == models.FailureCategory.USER_CANCELLATION:
        raise FormalStudyError("A failed run cannot be classified as user cancellation.")

    now = timezone.now()
    included = category == models.FailureCategory.ALGORITHM
    exclusion_reason = "" if included else reason[:255]
    models.ScheduleRun.objects.filter(pk=locked.pk).update(
        failure_category=category,
        failure_classified_by=actor,
        failure_classified_at=now,
        included_in_analysis=included,
        exclusion_reason=exclusion_reason,
        updated_at=now,
    )
    invalidated = False
    if category == models.FailureCategory.INFRASTRUCTURE and locked.pair_attempt >= 2:
        invalidated = True
        models.ExperimentStudy.objects.filter(pk=study.pk).update(
            status=models.StudyStatus.INVALID,
            invalid_reason=(
                f"Second infrastructure failure for scale "
                f"{locked.experiment_batch.planned_scale_percentage}, seed {locked.seed}; "
                "no formal conclusion available."
            ),
            updated_at=now,
        )
    elif category in {
        models.FailureCategory.USER_CANCELLATION,
        models.FailureCategory.UNCLASSIFIED,
    }:
        models.ExperimentStudy.objects.filter(pk=study.pk).update(
            status=models.StudyStatus.INVALID,
            invalid_reason=f"{category} trial prevents formal inference.",
            updated_at=now,
        )
        invalidated = True
    models.AuditLog.objects.create(
        actor=actor,
        action="formal_run.failure_classified",
        entity_type="ScheduleRun",
        entity_id=str(locked.pk),
        details={
            "category": category,
            "reason": reason,
            "pair_attempt": locked.pair_attempt,
            "study_id": study.pk,
            "study_invalidated": invalidated,
        },
    )
    if not invalidated:
        from scheduler.services.runs import refresh_run_containers

        transaction.on_commit(lambda: refresh_run_containers(locked.pk))
    locked.refresh_from_db()
    return locked


@transaction.atomic
def create_paired_infrastructure_replacement(
    failed_run: models.ScheduleRun,
    *,
    actor: models.User,
) -> tuple[models.ScheduleRun, models.ScheduleRun]:
    """Replace a complete seed pair once while retaining both original rows."""

    _require_central_actor(actor)
    failed = (
        models.ScheduleRun.objects.select_for_update(of=("self",))
        .select_related("experiment_batch__study", "snapshot")
        .get(pk=failed_run.pk)
    )
    batch = failed.experiment_batch
    study = batch.study if batch else None
    if not study or not study.is_formal or failed.purpose != models.RunPurpose.MEASURED:
        raise FormalStudyError("Only measured formal-trial pairs can be replaced.")
    if failed.failure_category != models.FailureCategory.INFRASTRUCTURE or not failed.failure_classified_by_id:
        raise FormalStudyError("The triggering failure needs an audited infrastructure classification.")
    if failed.pair_attempt != 1:
        models.ExperimentStudy.objects.filter(pk=study.pk).update(
            status=models.StudyStatus.INVALID,
            invalid_reason="A second infrastructure replacement was requested.",
            updated_at=timezone.now(),
        )
        raise FormalStudyError("Only one paired replacement is allowed.")
    originals = list(
        models.ScheduleRun.objects.select_for_update()
        .filter(
            experiment_batch=batch,
            seed=failed.seed,
            purpose=models.RunPurpose.MEASURED,
            pair_attempt=1,
        )
        .order_by("planned_order", "pk")
    )
    if len(originals) != 2 or {run.algorithm for run in originals} != set(_ALGORITHMS):
        raise FormalStudyError("The original CP-SAT/GA seed pair is incomplete.")
    if any(run.status not in _TERMINAL_STATUSES for run in originals):
        raise FormalStudyError("Both original pair members must be terminal before replacement.")
    if models.ScheduleRun.objects.filter(
        experiment_batch=batch,
        seed=failed.seed,
        purpose=models.RunPurpose.MEASURED,
        pair_attempt__gte=2,
    ).exists():
        raise FormalStudyError("This seed pair already has its single replacement attempt.")

    now = timezone.now()
    reason = (
        f"Superseded by audited paired attempt 2 after infrastructure failure in run {failed.pk}."
    )
    models.ScheduleRun.objects.filter(pk__in=[run.pk for run in originals]).update(
        included_in_analysis=False,
        exclusion_reason=reason[:255],
        updated_at=now,
    )
    max_order = batch.runs.aggregate(value=Max("planned_order"))["value"] or 0
    replacements: list[models.ScheduleRun] = []
    for offset, original in enumerate(originals, start=1):
        configuration = {
            **original.configuration,
            "formal_run_kind": "measured_replacement",
            "replacement_attempt": 2,
            "replacement_for_run_id": original.pk,
        }
        replacement = models.ScheduleRun(
            experiment_batch=batch,
            snapshot=original.snapshot,
            algorithm=original.algorithm,
            seed=original.seed,
            purpose=models.RunPurpose.MEASURED,
            pair_attempt=2,
            planned_order=max_order + offset,
            replacement_for=original,
            included_in_analysis=True,
            configuration=configuration,
            configuration_hash=models.canonical_sha256(
                {"algorithm": original.algorithm, "seed": original.seed, **configuration}
            ),
            requested_by=actor,
        )
        build_solver_config(replacement)
        replacement.full_clean()
        replacement.save()
        replacements.append(replacement)
    models.ExperimentBatch.objects.filter(pk=batch.pk).update(
        status=models.ExperimentStatus.QUEUED,
        updated_at=now,
    )
    models.ExperimentStudy.objects.filter(pk=study.pk).update(
        status=models.StudyStatus.QUEUED,
        invalid_reason="",
        updated_at=now,
    )
    models.AuditLog.objects.create(
        actor=actor,
        action="formal_run.pair_replaced",
        entity_type="ExperimentStudy",
        entity_id=str(study.pk),
        details={
            "batch_id": batch.pk,
            "scale": batch.planned_scale_percentage,
            "seed": failed.seed,
            "original_run_ids": [run.pk for run in originals],
            "replacement_run_ids": [run.pk for run in replacements],
            "pair_attempt": 2,
        },
    )
    replacement_ids = (replacements[0].pk, replacements[1].pk)
    transaction.on_commit(
        lambda: _dispatch_replacement_pair_after_commit(
            replacement_ids,
            study_id=study.pk,
            actor_id=actor.pk,
        )
    )
    return replacements[0], replacements[1]


def _terminal_pair_attempt_issues(
    batch: models.ExperimentBatch,
    runs: Sequence[models.ScheduleRun],
) -> list[ProtocolIssue]:
    issues: list[ProtocolIssue] = []
    if any(run.status not in _TERMINAL_STATUSES for run in runs):
        issues.append(
            _issue("FORMAL_RUNS_PENDING", "All planned runs must be terminal.", entity_id=batch.pk)
        )
    measured = [run for run in runs if run.purpose == models.RunPurpose.MEASURED]
    for seed in FORMAL_SEEDS:
        pair_rows = [run for run in measured if run.seed == seed]
        originals = [run for run in pair_rows if run.pair_attempt == 1]
        replacements = [run for run in pair_rows if run.pair_attempt == 2]
        eligible = [run for run in pair_rows if run.included_in_analysis]
        if (
            len(eligible) != 2
            or {run.algorithm for run in eligible} != set(_ALGORITHMS)
            or len({run.pair_attempt for run in eligible}) != 1
            or any(run.exclusion_reason for run in eligible)
            or any(run.pair_attempt not in {1, 2} for run in pair_rows)
        ):
            issues.append(
                _issue(
                    "INVALID_ELIGIBLE_SEED_PAIR",
                    f"Scale {batch.planned_scale_percentage}% seed {seed} lacks one complete eligible attempt.",
                    entity_id=batch.pk,
                )
            )
        if not replacements:
            if any(not run.included_in_analysis or run.exclusion_reason for run in originals):
                issues.append(
                    _issue(
                        "UNREPLACED_EXCLUDED_PAIR",
                        f"Scale {batch.planned_scale_percentage}% seed {seed} excludes original trials without a replacement pair.",
                        entity_id=batch.pk,
                    )
                )
            continue
        original_by_algorithm = {run.algorithm: run for run in originals}
        valid_replacement = (
            len(originals) == 2
            and len(replacements) == 2
            and {run.algorithm for run in replacements} == set(_ALGORITHMS)
            and all(not run.included_in_analysis and run.exclusion_reason for run in originals)
            and all(run.included_in_analysis and not run.exclusion_reason for run in replacements)
            and any(
                run.failure_category == models.FailureCategory.INFRASTRUCTURE
                and run.failure_classified_by_id is not None
                and run.failure_classified_at is not None
                for run in originals
            )
        )
        if valid_replacement:
            valid_replacement = all(
                run.replacement_for_id == original_by_algorithm[run.algorithm].pk
                for run in replacements
            )
        if not valid_replacement:
            issues.append(
                _issue(
                    "INVALID_PAIRED_REPLACEMENT_EVIDENCE",
                    f"Scale {batch.planned_scale_percentage}% seed {seed} lacks an audited, complete replacement pair.",
                    entity_id=batch.pk,
                )
            )
    return issues


def _expected_terminal_configuration(
    study: models.ExperimentStudy,
    run: models.ScheduleRun,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    configuration = _run_configuration(
        profile,
        algorithm=run.algorithm,
        deadline_seconds=FORMAL_DEADLINE_SECONDS,
        study=study,
        snapshot=run.snapshot,
        run_kind="measured" if run.pair_attempt == 1 else "measured_replacement",
    )
    if run.pair_attempt == 2:
        configuration.update(
            {
                "replacement_attempt": 2,
                "replacement_for_run_id": run.replacement_for_id,
            }
        )
    return configuration


def _terminal_provenance_issues(
    study: models.ExperimentStudy,
    eligible: Sequence[models.ScheduleRun],
) -> list[ProtocolIssue]:
    """Recheck the actual worker evidence, not just the dispatcher's preflight."""

    from scheduler.services.problem_builder import load_problem

    issues: list[ProtocolIssue] = []
    profiles = study.protocol_manifest.get("solver_profiles", {})
    identities: dict[str, set[str]] = {
        "source_commit": set(),
        "container_image": set(),
        "dependency_versions": set(),
        "host_identity": set(),
    }
    process_identities: set[tuple[str, str]] = set()
    problem_hashes: dict[int, str] = {}
    timed_runs: list[models.ScheduleRun] = []
    for run in eligible:
        if run.algorithm not in profiles:
            issues.append(
                _issue("MISSING_FROZEN_SOLVER_PROFILE", f"Run {run.pk} has no frozen solver profile.", entity_id=run.pk)
            )
            continue
        expected_configuration = _expected_terminal_configuration(study, run, profiles[run.algorithm])
        if run.configuration != expected_configuration:
            issues.append(
                _issue(
                    "TERMINAL_CONFIGURATION_MISMATCH",
                    f"Run {run.pk} does not match its frozen solver profile and problem hashes.",
                    entity_id=run.pk,
                )
            )
        if run.configuration_hash != models.canonical_sha256(
            {"algorithm": run.algorithm, "seed": run.seed, **run.configuration}
        ):
            issues.append(
                _issue("TERMINAL_CONFIGURATION_HASH_MISMATCH", f"Run {run.pk} configuration hash does not verify.", entity_id=run.pk)
            )
        if run.snapshot_id != run.experiment_batch.snapshot_id:
            issues.append(
                _issue("TERMINAL_SNAPSHOT_MISMATCH", f"Run {run.pk} uses a different problem snapshot.", entity_id=run.pk)
            )
        if (
            type(run.actual_order) is not int
            or type(run.planned_order) is not int
            or run.actual_order != run.planned_order
        ):
            issues.append(
                _issue(
                    "TERMINAL_EXECUTION_ORDER_MISMATCH",
                    f"Run {run.pk} lacks the expected actual execution-order evidence.",
                    entity_id=run.pk,
                )
            )
        if (
            run.started_at is None
            or run.finished_at is None
            or run.finished_at < run.started_at
        ):
            issues.append(
                _issue("MISSING_TERMINAL_TIMESTAMPS", f"Run {run.pk} lacks ordered start/finish timestamps.", entity_id=run.pk)
            )
        else:
            timed_runs.append(run)

        for field in ("source_commit", "container_image", "host_identity", "process_identity"):
            value = getattr(run, field)
            if not isinstance(value, str) or not value.strip():
                issues.append(
                    _issue(
                        "MISSING_TERMINAL_PROVENANCE",
                        f"Run {run.pk} lacks {field} from its executing worker.",
                        field,
                        run.pk,
                    )
                )
            elif field in identities:
                identities[field].add(value)
        dependencies = run.dependency_versions
        if not isinstance(dependencies, dict) or not dependencies or not any(
            isinstance(value, str) and value.strip() for value in dependencies.values()
        ):
            issues.append(
                _issue(
                    "MISSING_TERMINAL_DEPENDENCIES",
                    f"Run {run.pk} lacks dependency-version evidence.",
                    "dependency_versions",
                    run.pk,
                )
            )
        else:
            identities["dependency_versions"].add(models.canonical_sha256(dependencies))
        process_key = (str(run.host_identity), str(run.process_identity))
        if process_key in process_identities:
            issues.append(
                _issue(
                    "REUSED_FORMAL_SOLVER_PROCESS",
                    f"Run {run.pk} reused a process already recorded for another eligible trial.",
                    "process_identity",
                    run.pk,
                )
            )
        process_identities.add(process_key)

        manifest = run.worker_manifest if isinstance(run.worker_manifest, dict) else {}
        manifest_payload = {key: value for key, value in manifest.items() if key != "manifest_hash"}
        build = manifest.get("build") if isinstance(manifest.get("build"), dict) else {}
        task = manifest.get("task") if isinstance(manifest.get("task"), dict) else {}
        if (
            not manifest
            or manifest.get("manifest_hash") != models.canonical_sha256(manifest_payload)
            or build.get("source_commit") != run.source_commit
            or build.get("container_image_id") != run.container_image
            or manifest.get("dependencies") != dependencies
            or manifest.get("host_identity") != run.host_identity
            or manifest.get("process_identity") != run.process_identity
            or not run.task_id
            or task.get("task_id") != run.task_id
            or task.get("routing_key") != FORMAL_BENCHMARK_QUEUE
        ):
            issues.append(
                _issue(
                    "TERMINAL_WORKER_MANIFEST_MISMATCH",
                    f"Run {run.pk} worker manifest does not authenticate its recorded provenance.",
                    "worker_manifest",
                    run.pk,
                )
            )

        # An audited algorithm error is an eligible non-feasible observation,
        # but it may not have returned a solver result. Successful result paths
        # must attest the domain/configuration actually consumed by the engine.
        if run.status not in {models.RunStatus.FAILED, models.RunStatus.CANCELLED}:
            try:
                if run.snapshot_id not in problem_hashes:
                    problem_hashes[run.snapshot_id] = load_problem(run.snapshot).canonical_hash
                config_hash = build_solver_config(run).canonical_hash
            except (TypeError, ValueError, KeyError) as exc:
                issues.append(
                    _issue("INVALID_TERMINAL_SOLVER_CONTRACT", f"Run {run.pk} contract cannot be reconstructed: {exc}.", entity_id=run.pk)
                )
                continue
            diagnostics = run.diagnostics if isinstance(run.diagnostics, dict) else {}
            metrics = diagnostics.get("metrics") if isinstance(diagnostics.get("metrics"), dict) else {}
            if (
                diagnostics.get("problem_hash") != problem_hashes[run.snapshot_id]
                or diagnostics.get("config_hash") != config_hash
                or metrics.get("implementation_version") != profiles[run.algorithm]["implementation_version"]
            ):
                issues.append(
                    _issue(
                        "TERMINAL_SOLVER_SIGNATURE_MISMATCH",
                        f"Run {run.pk} result signature does not match the frozen solver contract.",
                        "diagnostics",
                        run.pk,
                    )
                )

    for field, values in identities.items():
        if len(values) > 1:
            issues.append(
                _issue(
                    "MIXED_TERMINAL_PROVENANCE",
                    f"Eligible formal trials do not share one {field}.",
                    field,
                )
            )
    timed_runs.sort(key=lambda run: (run.started_at, run.pk))
    if any(
        previous.finished_at > current.started_at
        for previous, current in zip(timed_runs, timed_runs[1:], strict=False)
    ):
        issues.append(
            _issue("OVERLAPPING_FORMAL_TRIALS", "Eligible formal trials were not executed sequentially.")
        )
    return issues


def terminal_formal_integrity(study: models.ExperimentStudy) -> dict[str, Any]:
    """Return a fresh, non-mutating terminal audit for every conclusion surface."""

    eligible = list(
        models.ScheduleRun.objects.filter(
            experiment_batch__study=study,
            purpose=models.RunPurpose.MEASURED,
            included_in_analysis=True,
        ).select_related("snapshot__objective_profile", "experiment_batch", "validation_result")
    )
    issues = _study_protocol_issues(study, terminal=True)
    issues.extend(_terminal_provenance_issues(study, eligible))
    return {
        "valid": not issues,
        "validator_version": f"{FORMAL_PROTOCOL_VERSION}-terminal-1",
        "eligible_trials": len(eligible),
        "issues": [issue.to_dict() for issue in issues],
    }


def _formal_conclusion_gate(study: models.ExperimentStudy) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if study.status != models.StudyStatus.COMPLETED:
        reasons.append("STUDY_NOT_COMPLETE")
    if not study.protocol_integrity.get("formal_eligible"):
        reasons.append("PROTOCOL_NOT_VALID")
    if study.invalid_reason:
        reasons.append("STUDY_INVALIDATED")
    measured = list(
        models.ScheduleRun.objects.filter(
            experiment_batch__study=study,
            purpose=models.RunPurpose.MEASURED,
        ).select_related("validation_result")
    )
    eligible = [run for run in measured if run.included_in_analysis]
    if len(eligible) != FORMAL_MEASURED_RUN_COUNT:
        reasons.append("ELIGIBLE_DENOMINATOR_NOT_240")
    if any(run.status not in _TERMINAL_STATUSES for run in eligible):
        reasons.append("ELIGIBLE_TRIALS_PENDING")
    if any(
        run.failure_category in {
            "",
            models.FailureCategory.UNCLASSIFIED,
            models.FailureCategory.INFRASTRUCTURE,
            models.FailureCategory.USER_CANCELLATION,
        }
        for run in eligible
        if run.status in {models.RunStatus.FAILED, models.RunStatus.CANCELLED}
    ):
        reasons.append("UNCLASSIFIED_OR_INELIGIBLE_FAILURE")
    full_batch = study.batches.filter(planned_scale_percentage=100).first()
    if full_batch is None:
        reasons.append("FULL_INSTANCE_MISSING")
    else:
        full_eligible = [run for run in eligible if run.experiment_batch_id == full_batch.pk]
        if len(full_eligible) != 60:
            reasons.append("FULL_INSTANCE_DENOMINATOR_NOT_60")
    if not reasons:
        terminal_integrity = terminal_formal_integrity(study)
        reasons.extend(issue["code"] for issue in terminal_integrity["issues"])
    return (not reasons, list(dict.fromkeys(reasons)))


def inspect_formal_study(study: models.ExperimentStudy) -> dict[str, Any]:
    if not study.is_formal:
        raise FormalStudyError("This endpoint accepts formal studies only.")
    runs = list(
        models.ScheduleRun.objects.filter(experiment_batch__study=study)
        .select_related("experiment_batch")
        .order_by("experiment_batch__planned_scale_percentage", "planned_order", "pair_attempt")
    )
    status_counts = Counter(run.status for run in runs)
    purpose_counts = Counter(run.purpose for run in runs)
    failure_counts = Counter(run.failure_category or "NOT_APPLICABLE" for run in runs)
    conclusion_available, conclusion_reasons = _formal_conclusion_gate(study)
    scales: list[dict[str, Any]] = []
    for batch in study.batches.order_by("planned_scale_percentage"):
        batch_runs = [run for run in runs if run.experiment_batch_id == batch.pk]
        eligible = [
            run
            for run in batch_runs
            if run.purpose == models.RunPurpose.MEASURED and run.included_in_analysis
        ]
        independently_feasible = [
            run
            for run in eligible
            if run.status in _SUCCESS_STATUSES
            and hasattr(run, "validation_result")
            and run.validation_result.is_feasible
        ]
        scales.append(
            {
                "batch_id": batch.pk,
                "planned_percentage": batch.planned_scale_percentage,
                "actual_percentage": batch.actual_scale_percentage,
                "snapshot_id": batch.snapshot_id,
                "status": batch.status,
                "planned_runs": len(batch_runs),
                "eligible_measured_runs": len(eligible),
                "independently_feasible_runs": len(independently_feasible),
                "pending_runs": sum(run.status not in _TERMINAL_STATUSES for run in batch_runs),
                "replacement_runs": sum(run.pair_attempt > 1 for run in batch_runs),
                "excluded_runs": sum(not run.included_in_analysis for run in batch_runs),
            }
        )
    return {
        "id": study.pk,
        "name": study.name,
        "mode": study.mode,
        "protocol_version": study.protocol_version,
        "status": study.status,
        "manifest_hash": study.manifest_hash,
        "source_snapshot_id": study.source_snapshot_id,
        "protocol_integrity": study.protocol_integrity,
        "invalid_reason": study.invalid_reason,
        "counts": {
            "all_runs": len(runs),
            "by_status": dict(sorted(status_counts.items())),
            "by_purpose": dict(sorted(purpose_counts.items())),
            "by_failure_category": dict(sorted(failure_counts.items())),
            "included_measured": sum(
                run.purpose == models.RunPurpose.MEASURED and run.included_in_analysis
                for run in runs
            ),
            "excluded": sum(not run.included_in_analysis for run in runs),
            "replacements": sum(run.pair_attempt > 1 for run in runs),
        },
        "scales": scales,
        "formal_conclusion": {
            "available": conclusion_available,
            "status": (
                "AVAILABLE_FOR_WINNER_ANALYSIS"
                if conclusion_available
                else "NO_FORMAL_CONCLUSION_AVAILABLE"
            ),
            "reasons": conclusion_reasons,
        },
        "created_at": study.created_at.isoformat(),
        "cancelled_at": study.cancelled_at.isoformat() if study.cancelled_at else None,
    }


def formal_evidence_bundle(study: models.ExperimentStudy) -> tuple[bytes, str]:
    """Call the evidence service without coupling orchestration to its implementation."""

    try:
        from scheduler.services.evidence_bundle import build_study_evidence_bundle
    except ImportError as exc:  # The hook remains explicit during incremental rollout.
        raise NotImplementedError("The deterministic evidence-bundle service is not installed.") from exc
    content = build_study_evidence_bundle(study)
    if not isinstance(content, bytes):
        raise TypeError("The evidence-bundle service must return bytes.")
    return content, f"formal-study-{study.pk}-{study.manifest_hash[:12]}-evidence.zip"
