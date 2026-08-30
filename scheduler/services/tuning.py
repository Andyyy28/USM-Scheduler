"""Versioned Genetic Algorithm tuning plan and frozen selection policy."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import product
from random import Random
from statistics import median
from typing import Any

from scheduler import models
from scheduler.services.statistics import restricted_mean_time_to_feasibility
from scheduler.solvers.genetic import GA_IMPLEMENTATION_VERSION

GA_TUNING_PROTOCOL_VERSION = "2.0"
GA_TUNING_ORDER_SEED = 20260824
GA_TUNING_SEEDS = tuple(range(2001, 2011))
GA_TUNING_POPULATIONS = (100, 200, 400)
GA_TUNING_TOURNAMENT_SIZES = (3, 5)
GA_TUNING_CROSSOVER_RATES = (0.8, 0.9)
GA_TUNING_MUTATION_MULTIPLIERS = (1, 2)


def ga_tuning_configurations(mutable_event_count: int) -> tuple[dict[str, Any], ...]:
    """Return the complete fixed 24-configuration GA-v2 grid."""

    if type(mutable_event_count) is not int or mutable_event_count < 0:
        raise ValueError("mutable_event_count must be a non-negative integer")
    rows: list[dict[str, Any]] = []
    for population, tournament, crossover, mutation_multiplier in product(
        GA_TUNING_POPULATIONS,
        GA_TUNING_TOURNAMENT_SIZES,
        GA_TUNING_CROSSOVER_RATES,
        GA_TUNING_MUTATION_MULTIPLIERS,
    ):
        mutation_rate = (
            min(1.0, mutation_multiplier / mutable_event_count)
            if mutable_event_count
            else 0.0
        )
        parameters = {
            "population_size": population,
            "tournament_size": tournament,
            "crossover_rate": crossover,
            "mutation_formula": f"{mutation_multiplier}/N_mutable",
            "mutation_multiplier": mutation_multiplier,
            "mutable_event_count": mutable_event_count,
            "elite_fraction": 0.05,
            "implementation_version": GA_IMPLEMENTATION_VERSION,
        }
        solver_configuration = {
            "population_size": population,
            "tournament_size": tournament,
            "crossover_rate": crossover,
            "mutation_rate": mutation_rate,
            "elite_fraction": 0.05,
            "implementation_version": GA_IMPLEMENTATION_VERSION,
        }
        rows.append(
            {
                "configuration_id": models.canonical_sha256(parameters),
                "parameters": parameters,
                "solver_configuration": solver_configuration,
            }
        )
    return tuple(rows)


def build_ga_tuning_plan(
    snapshot: models.ProblemSnapshot,
    seeds: Iterable[int] = GA_TUNING_SEEDS,
    *,
    time_limit_seconds: int | float = 300,
    order_seed: int = GA_TUNING_ORDER_SEED,
    environment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, hash-addressed GA-v2 plan without executing it."""

    normalized_seeds = _validated_seeds(seeds)
    deadline = _positive_finite(time_limit_seconds, "time_limit_seconds")
    if type(order_seed) is not int or order_seed < 0:
        raise ValueError("order_seed must be a non-negative integer")
    mutable_event_count = _mutable_event_count(snapshot)
    configurations = ga_tuning_configurations(mutable_event_count)
    configuration_by_id = {
        row["configuration_id"]: row for row in configurations
    }
    execution_order = _randomized_configuration_order(
        configurations, normalized_seeds, order_seed
    )
    common_configuration = {
        "algorithm": models.SolverAlgorithm.GENETIC_ALGORITHM,
        "time_limit_seconds": deadline,
        "worker_count": 1,
        "repair_attempts": 20,
        "max_generations": None,
        "implementation_version": GA_IMPLEMENTATION_VERSION,
    }
    runs = []
    for entry in execution_order:
        configuration = configuration_by_id[entry["configuration_id"]]
        resolved_configuration = {
            **common_configuration,
            **configuration["solver_configuration"],
            "seed": entry["seed"],
        }
        runs.append(
            {
                **entry,
                "solver_configuration": configuration["solver_configuration"],
                "resolved_configuration": resolved_configuration,
                "resolved_configuration_hash": models.canonical_sha256(
                    resolved_configuration
                ),
            }
        )

    environment_manifest = _environment_manifest(environment)
    build_hash = models.canonical_sha256(environment_manifest.get("build", {}))
    manifest = {
        "protocol_version": GA_TUNING_PROTOCOL_VERSION,
        "implementation_version": GA_IMPLEMENTATION_VERSION,
        "snapshot_id": snapshot.pk,
        "snapshot_hash": snapshot.snapshot_hash,
        "event_count": snapshot.event_count,
        "mutable_event_count": mutable_event_count,
        "time_limit_seconds": deadline,
        "seeds": list(normalized_seeds),
        "order_seed": order_seed,
        "environment_manifest": environment_manifest,
        "environment_manifest_hash": environment_manifest["manifest_hash"],
        "build_hash": build_hash,
        "common_resolved_configuration": common_configuration,
        "selection_order": [
            "highest_feasibility_rate",
            "lowest_median_feasible_raw_soft_penalty",
            "lowest_rmst_time_to_feasibility",
            "configuration_id_tiebreak",
        ],
        "configurations": list(configurations),
        "runs": runs,
    }
    return {
        **manifest,
        "configuration_count": len(configurations),
        "run_count": len(runs),
        "plan_hash": models.canonical_sha256(manifest),
    }


def select_ga_tuning_configuration(
    observations: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and rank a complete GA-v2 matrix, then freeze its selected profile."""

    if plan is None:
        raise ValueError("a complete GA tuning protocol 2.0 plan is required")
    _validate_plan(plan)
    plan_hash = str(plan["plan_hash"])
    deadline = _positive_finite(plan["time_limit_seconds"], "plan time_limit_seconds")
    expected_runs = {
        (str(row["configuration_id"]), int(row["seed"])): row
        for row in plan["runs"]
    }
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    seen_cells: set[tuple[str, int]] = set()
    for row in observations:
        _validate_observation_provenance(row, plan_hash, deadline)
        configuration_id = str(row.get("configuration_id", "")).strip()
        seed = row.get("seed")
        if type(seed) is not int or seed < 0:
            raise ValueError("every observation requires a non-negative integer seed")
        cell = (configuration_id, seed)
        expected = expected_runs.get(cell)
        if expected is None:
            raise ValueError(
                f"observation ({configuration_id!r}, {seed}) is not in the frozen plan"
            )
        if cell in seen_cells:
            raise ValueError(
                f"duplicate observation for configuration {configuration_id!r}, seed {seed}"
            )
        if row.get("resolved_configuration_hash") != expected["resolved_configuration_hash"]:
            raise ValueError(
                f"resolved configuration mismatch for {configuration_id!r}, seed {seed}"
            )
        seen_cells.add(cell)
        grouped[configuration_id].append(row)

    missing = sorted(set(expected_runs) - seen_cells)
    if missing:
        preview = ", ".join(
            f"{configuration_id}/{seed}" for configuration_id, seed in missing[:5]
        )
        suffix = "" if len(missing) <= 5 else f" and {len(missing) - 5} more"
        raise ValueError(f"tuning matrix is incomplete; missing {preview}{suffix}")
    if len(observations) != len(expected_runs):
        raise ValueError("tuning matrix must contain exactly one observation per planned cell")

    ranking: list[dict[str, Any]] = []
    for configuration in plan["configurations"]:
        configuration_id = str(configuration["configuration_id"])
        rows = grouped[configuration_id]
        feasible_rows = [row for row in rows if row["feasible"]]
        penalties = [float(row["raw_soft_penalty"]) for row in feasible_rows]
        time_observations = [
            float(row["first_feasible_seconds"])
            if row["feasible"]
            else deadline
            for row in rows
        ]
        time_successes = [
            bool(row["feasible"] and float(row["first_feasible_seconds"]) < deadline)
            for row in rows
        ]
        execution_times = [float(row["execution_seconds"]) for row in rows]
        ranking.append(
            {
                "configuration_id": configuration_id,
                "runs": len(rows),
                "feasible_runs": len(feasible_rows),
                "censored_runs": sum(not success for success in time_successes),
                "feasibility_rate": len(feasible_rows) / len(rows),
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
        )

    ranking.sort(
        key=lambda row: (
            -row["feasibility_rate"],
            _none_as_infinity(row["median_feasible_raw_soft_penalty"]),
            row["rmst_time_to_feasibility_seconds"],
            row["configuration_id"],
        )
    )
    selected_id = ranking[0]["configuration_id"]
    selected_configuration = next(
        row for row in plan["configurations"] if row["configuration_id"] == selected_id
    )
    frozen_configuration = {
        **dict(plan["common_resolved_configuration"]),
        **dict(selected_configuration["solver_configuration"]),
    }
    selection_payload = {
        "protocol_version": GA_TUNING_PROTOCOL_VERSION,
        "implementation_version": GA_IMPLEMENTATION_VERSION,
        "plan_hash": plan_hash,
        "selected_configuration_id": selected_id,
        "configuration": frozen_configuration,
        "selection_metrics": ranking[0],
        "ranking": ranking,
    }
    selection_hash = models.canonical_sha256(selection_payload)
    selected_profile = {
        "artifact_schema_version": "1.0",
        "frozen": True,
        "protocol_version": GA_TUNING_PROTOCOL_VERSION,
        "implementation_version": GA_IMPLEMENTATION_VERSION,
        "plan_hash": plan_hash,
        "selection_hash": selection_hash,
        "configuration_id": selected_id,
        "configuration": frozen_configuration,
        "selection_metrics": ranking[0],
    }
    return {
        **selection_payload,
        "selection_order": list(plan["selection_order"]),
        "selection_hash": selection_hash,
        "selected_profile": selected_profile,
    }


def _randomized_configuration_order(
    configurations: Sequence[Mapping[str, Any]],
    seeds: tuple[int, ...],
    order_seed: int,
) -> tuple[dict[str, Any], ...]:
    randomizer = Random(order_seed)
    entries: list[dict[str, Any]] = []
    position = 0
    configuration_ids = [str(row["configuration_id"]) for row in configurations]
    for seed in seeds:
        block = list(configuration_ids)
        randomizer.shuffle(block)
        for within_seed_position, configuration_id in enumerate(block):
            entries.append(
                {
                    "position": position,
                    "within_seed_position": within_seed_position,
                    "configuration_id": configuration_id,
                    "seed": seed,
                }
            )
            position += 1
    return tuple(entries)


def _mutable_event_count(snapshot: models.ProblemSnapshot) -> int:
    events = (
        snapshot.input_data.get("events", [])
        if isinstance(snapshot.input_data, dict)
        else []
    )
    locks = (
        snapshot.input_data.get("locked_assignments", [])
        if isinstance(snapshot.input_data, dict)
        else []
    )
    event_ids = {
        str(row.get("event_id", row.get("id", "")))
        for row in events
        if isinstance(row, dict)
    }
    locked_ids = {
        str(row.get("event_id", ""))
        for row in locks
        if isinstance(row, dict)
    }
    event_count = len(event_ids) if event_ids else int(snapshot.event_count)
    return max(0, event_count - len(event_ids & locked_ids))


def _environment_manifest(environment: Mapping[str, Any] | None) -> dict[str, Any]:
    if environment is None:
        # Local import avoids coupling module import to the experiment service.
        from scheduler.services.experiments import environment_manifest

        return environment_manifest()
    manifest = dict(environment)
    supplied_hash = manifest.pop("manifest_hash", None)
    computed_hash = models.canonical_sha256(manifest)
    if supplied_hash is not None and supplied_hash != computed_hash:
        raise ValueError("environment manifest hash does not match its content")
    return {**manifest, "manifest_hash": computed_hash}


def _validate_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("protocol_version") != GA_TUNING_PROTOCOL_VERSION:
        raise ValueError("legacy or unsupported GA tuning protocol; expected 2.0")
    if plan.get("implementation_version") != GA_IMPLEMENTATION_VERSION:
        raise ValueError("GA tuning plan implementation version is not ga-v2")
    manifest = {
        key: value
        for key, value in plan.items()
        if key not in {"configuration_count", "run_count", "plan_hash", "dry_run"}
    }
    if models.canonical_sha256(manifest) != plan.get("plan_hash"):
        raise ValueError("GA tuning plan hash does not match its frozen manifest")
    configurations = plan.get("configurations")
    runs = plan.get("runs")
    if not isinstance(configurations, list) or len(configurations) != 24:
        raise ValueError("GA tuning plan must contain exactly 24 configurations")
    expected_run_count = len(configurations) * len(plan.get("seeds", []))
    if not isinstance(runs, list) or len(runs) != expected_run_count:
        raise ValueError("GA tuning plan run matrix is incomplete")


def _validate_observation_provenance(
    row: Mapping[str, Any], plan_hash: str, deadline: float
) -> None:
    if row.get("terminal") is not True:
        raise ValueError("every tuning observation must be terminal")
    if row.get("protocol_version") != GA_TUNING_PROTOCOL_VERSION:
        raise ValueError("legacy tuning observations cannot be mixed with protocol 2.0")
    if row.get("implementation_version") != GA_IMPLEMENTATION_VERSION:
        raise ValueError("legacy solver observations cannot be mixed with ga-v2")
    if row.get("plan_hash") != plan_hash:
        raise ValueError("tuning observation belongs to a different plan")
    observed_deadline = _positive_finite(
        row.get("time_limit_seconds"), "observation time_limit_seconds"
    )
    if observed_deadline != deadline:
        raise ValueError("mixed tuning deadlines are not comparable")
    if type(row.get("feasible")) is not bool:
        raise ValueError("every observation requires a Boolean feasible value")
    execution_seconds = _nonnegative_finite(
        row.get("execution_seconds"), "execution_seconds"
    )
    if execution_seconds is None:
        raise ValueError("every terminal tuning observation requires execution_seconds")
    first_feasible = _nonnegative_finite(
        row.get("first_feasible_seconds"), "first_feasible_seconds"
    )
    penalty = _nonnegative_finite(row.get("raw_soft_penalty"), "raw_soft_penalty")
    if row["feasible"] and (first_feasible is None or penalty is None):
        raise ValueError(
            "feasible observations require finite first-feasible time and raw penalty"
        )
    if not row["feasible"] and first_feasible is not None:
        raise ValueError("non-feasible observations cannot report first-feasible time")


def _validated_seeds(seeds: Iterable[int]) -> tuple[int, ...]:
    normalized = tuple(seeds)
    if not normalized or any(type(seed) is not int or seed < 0 for seed in normalized):
        raise ValueError("seeds must be non-empty, non-negative integers")
    if len(normalized) != len(set(normalized)):
        raise ValueError("seeds must be unique")
    return normalized


def _positive_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite and positive")
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return converted


def _nonnegative_finite(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite and non-negative when provided")
    converted = float(value)
    if not math.isfinite(converted) or converted < 0:
        raise ValueError(f"{name} must be finite and non-negative when provided")
    return converted


def _none_as_infinity(value: float | None) -> float:
    return float("inf") if value is None else value
