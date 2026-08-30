"""Versioned equal-budget solver tuning and legacy GA tuning policies."""

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
from scheduler.solvers.cp_sat import CP_SAT_IMPLEMENTATION_VERSION
from scheduler.solvers.genetic import GA_IMPLEMENTATION_VERSION

SOLVER_TUNING_PROTOCOL_VERSION = "3.0"
SOLVER_TUNING_ARTIFACT_SCHEMA_VERSION = "1.0"
SOLVER_TUNING_ORDER_SEED = 20260824
SOLVER_TUNING_SEEDS = tuple(range(2001, 2006))
SOLVER_TUNING_TIME_LIMIT_SECONDS = 60
CP_SAT_TUNING_PRESOLVE_VALUES = (False, True)
CP_SAT_TUNING_LINEARIZATION_LEVELS = (0, 1, 2)
SOLVER_TUNING_GA_POPULATIONS = (100, 200, 400)
SOLVER_TUNING_GA_MUTATION_MULTIPLIERS = (1, 2)

_SELECTION_ORDER = (
    "highest_feasibility_rate",
    "lowest_median_feasible_raw_soft_penalty",
    "lowest_rmst_time_to_feasibility",
    "configuration_id_tiebreak",
)


def solver_tuning_configurations(
    mutable_event_count: int,
) -> tuple[dict[str, Any], ...]:
    """Return the equal-budget six-configuration grid for each solver.

    Configuration identifiers include the algorithm and every active or fixed
    tuning parameter. This prevents a coincidental cross-algorithm hash match
    and makes the selected profiles independently reproducible.
    """

    if type(mutable_event_count) is not int or mutable_event_count < 0:
        raise ValueError("mutable_event_count must be a non-negative integer")

    rows: list[dict[str, Any]] = []
    for presolve, linearization_level in product(
        CP_SAT_TUNING_PRESOLVE_VALUES,
        CP_SAT_TUNING_LINEARIZATION_LEVELS,
    ):
        parameters = {
            "cp_model_presolve": presolve,
            "linearization_level": linearization_level,
            "worker_count": 1,
            "implementation_version": CP_SAT_IMPLEMENTATION_VERSION,
        }
        identity = {
            "algorithm": models.SolverAlgorithm.CP_SAT,
            "parameters": parameters,
        }
        rows.append(
            {
                "algorithm": models.SolverAlgorithm.CP_SAT,
                "configuration_id": models.canonical_sha256(identity),
                "parameters": parameters,
                "solver_configuration": dict(parameters),
            }
        )

    for population, mutation_multiplier in product(
        SOLVER_TUNING_GA_POPULATIONS,
        SOLVER_TUNING_GA_MUTATION_MULTIPLIERS,
    ):
        mutation_rate = (
            min(1.0, mutation_multiplier / mutable_event_count)
            if mutable_event_count
            else 0.0
        )
        parameters = {
            "population_size": population,
            "tournament_size": 3,
            "crossover_rate": 0.9,
            "mutation_formula": f"{mutation_multiplier}/N_mutable",
            "mutation_multiplier": mutation_multiplier,
            "mutable_event_count": mutable_event_count,
            "elite_fraction": 0.05,
            "repair_attempts": 20,
            "max_generations": None,
            "worker_count": 1,
            "implementation_version": GA_IMPLEMENTATION_VERSION,
        }
        solver_configuration = {
            key: value
            for key, value in parameters.items()
            if key not in {"mutation_formula", "mutation_multiplier", "mutable_event_count"}
        }
        solver_configuration["mutation_rate"] = mutation_rate
        identity = {
            "algorithm": models.SolverAlgorithm.GENETIC_ALGORITHM,
            "parameters": parameters,
        }
        rows.append(
            {
                "algorithm": models.SolverAlgorithm.GENETIC_ALGORITHM,
                "configuration_id": models.canonical_sha256(identity),
                "parameters": parameters,
                "solver_configuration": solver_configuration,
            }
        )
    return tuple(rows)


def build_solver_tuning_plan(
    snapshot: models.ProblemSnapshot,
    seeds: Iterable[int] = SOLVER_TUNING_SEEDS,
    *,
    time_limit_seconds: int | float = SOLVER_TUNING_TIME_LIMIT_SECONDS,
    order_seed: int = SOLVER_TUNING_ORDER_SEED,
    environment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, equal-budget CP-SAT/GA pilot-tuning plan."""

    normalized_seeds = _validated_seeds(seeds)
    deadline = _positive_finite(time_limit_seconds, "time_limit_seconds")
    if type(order_seed) is not int or order_seed < 0:
        raise ValueError("order_seed must be a non-negative integer")

    mutable_event_count = _mutable_event_count(snapshot)
    configurations = solver_tuning_configurations(mutable_event_count)
    configuration_by_id = {row["configuration_id"]: row for row in configurations}
    execution_order = _randomized_configuration_order(
        configurations,
        normalized_seeds,
        order_seed,
    )
    runs: list[dict[str, Any]] = []
    for entry in execution_order:
        configuration = configuration_by_id[entry["configuration_id"]]
        resolved_configuration = {
            "algorithm": configuration["algorithm"],
            "seed": entry["seed"],
            "time_limit_seconds": deadline,
            **configuration["solver_configuration"],
        }
        runs.append(
            {
                **entry,
                "algorithm": configuration["algorithm"],
                "solver_configuration": configuration["solver_configuration"],
                "resolved_configuration": resolved_configuration,
                "resolved_configuration_hash": models.canonical_sha256(
                    resolved_configuration
                ),
            }
        )

    configuration_count_by_algorithm = {
        algorithm: sum(row["algorithm"] == algorithm for row in configurations)
        for algorithm in (
            models.SolverAlgorithm.CP_SAT,
            models.SolverAlgorithm.GENETIC_ALGORITHM,
        )
    }
    allocated_seconds_by_algorithm = {
        algorithm: count * len(normalized_seeds) * deadline
        for algorithm, count in configuration_count_by_algorithm.items()
    }
    if len(set(allocated_seconds_by_algorithm.values())) != 1:
        raise ValueError("solver tuning grids must receive identical allocated time")

    environment_manifest = _environment_manifest(environment)
    build_hash = models.canonical_sha256(environment_manifest.get("build", {}))
    manifest = {
        "artifact_schema_version": SOLVER_TUNING_ARTIFACT_SCHEMA_VERSION,
        "protocol_version": SOLVER_TUNING_PROTOCOL_VERSION,
        "evidence_class": "synthetic_pilot_tuning_excluded_from_final_inference",
        "protocol_defaults_applied": (
            normalized_seeds == SOLVER_TUNING_SEEDS
            and deadline == float(SOLVER_TUNING_TIME_LIMIT_SECONDS)
            and order_seed == SOLVER_TUNING_ORDER_SEED
        ),
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
        "selection_order": list(_SELECTION_ORDER),
        "configuration_count_by_algorithm": configuration_count_by_algorithm,
        "allocated_seconds_by_algorithm": allocated_seconds_by_algorithm,
        "configurations": list(configurations),
        "runs": runs,
    }
    return {
        **manifest,
        "configuration_count": len(configurations),
        "run_count": len(runs),
        "per_algorithm_budget_seconds": next(
            iter(allocated_seconds_by_algorithm.values())
        ),
        "plan_hash": models.canonical_sha256(manifest),
    }


def select_solver_tuning_profiles(
    observations: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Select one frozen profile per solver under one shared ranking policy."""

    if plan is None:
        raise ValueError("a complete equal-budget solver tuning plan is required")
    _validate_solver_tuning_plan(plan)
    plan_hash = str(plan["plan_hash"])
    deadline = _positive_finite(plan["time_limit_seconds"], "plan time_limit_seconds")
    expected_runs = {
        (str(row["algorithm"]), str(row["configuration_id"]), int(row["seed"])): row
        for row in plan["runs"]
    }
    configuration_by_id = {
        (str(row["algorithm"]), str(row["configuration_id"])): row
        for row in plan["configurations"]
    }
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    seen_cells: set[tuple[str, str, int]] = set()

    for row in observations:
        algorithm = str(row.get("algorithm", "")).strip()
        configuration_id = str(row.get("configuration_id", "")).strip()
        seed = row.get("seed")
        if type(seed) is not int or seed < 0:
            raise ValueError("every observation requires a non-negative integer seed")
        cell = (algorithm, configuration_id, seed)
        expected = expected_runs.get(cell)
        if expected is None:
            raise ValueError(
                "observation "
                f"({algorithm!r}, {configuration_id!r}, {seed}) is not in the frozen plan"
            )
        if cell in seen_cells:
            raise ValueError(
                "duplicate observation for "
                f"{algorithm}/{configuration_id!r}, seed {seed}"
            )
        expected_configuration = configuration_by_id[(algorithm, configuration_id)]
        _validate_solver_tuning_observation(
            row,
            plan_hash=plan_hash,
            deadline=deadline,
            implementation_version=str(
                expected_configuration["parameters"]["implementation_version"]
            ),
        )
        if row.get("resolved_configuration_hash") != expected["resolved_configuration_hash"]:
            raise ValueError(
                f"resolved configuration mismatch for {algorithm}/{configuration_id!r}, "
                f"seed {seed}"
            )
        seen_cells.add(cell)
        grouped[(algorithm, configuration_id)].append(row)

    missing = sorted(set(expected_runs) - seen_cells)
    if missing:
        preview = ", ".join(
            f"{algorithm}/{configuration_id}/{seed}"
            for algorithm, configuration_id, seed in missing[:5]
        )
        suffix = "" if len(missing) <= 5 else f" and {len(missing) - 5} more"
        raise ValueError(f"tuning matrix is incomplete; missing {preview}{suffix}")
    if len(observations) != len(expected_runs):
        raise ValueError("tuning matrix must contain exactly one observation per planned cell")

    rankings: dict[str, list[dict[str, Any]]] = {}
    selected_configuration_ids: dict[str, str] = {}
    selected_profiles: dict[str, dict[str, Any]] = {}
    for algorithm in (
        models.SolverAlgorithm.CP_SAT,
        models.SolverAlgorithm.GENETIC_ALGORITHM,
    ):
        algorithm_configurations = [
            row for row in plan["configurations"] if row["algorithm"] == algorithm
        ]
        ranking = [
            _tuning_ranking_row(
                str(configuration["configuration_id"]),
                grouped[(algorithm, str(configuration["configuration_id"]))],
                deadline,
            )
            for configuration in algorithm_configurations
        ]
        ranking.sort(
            key=lambda row: (
                -row["feasibility_rate"],
                _none_as_infinity(row["median_feasible_raw_soft_penalty"]),
                row["rmst_time_to_feasibility_seconds"],
                row["configuration_id"],
            )
        )
        selected_id = ranking[0]["configuration_id"]
        selected = next(
            row
            for row in algorithm_configurations
            if row["configuration_id"] == selected_id
        )
        frozen_configuration = {
            "algorithm": algorithm,
            "time_limit_seconds": deadline,
            **dict(selected["solver_configuration"]),
        }
        profile_payload = {
            "artifact_schema_version": SOLVER_TUNING_ARTIFACT_SCHEMA_VERSION,
            "frozen": True,
            "protocol_version": SOLVER_TUNING_PROTOCOL_VERSION,
            "algorithm": algorithm,
            "implementation_version": selected["parameters"][
                "implementation_version"
            ],
            "plan_hash": plan_hash,
            "configuration_id": selected_id,
            "tuning_parameters": dict(selected["parameters"]),
            "configuration": frozen_configuration,
            "selection_metrics": ranking[0],
        }
        profile_hash = models.canonical_sha256(profile_payload)
        rankings[algorithm] = ranking
        selected_configuration_ids[algorithm] = selected_id
        selected_profiles[algorithm] = {
            **profile_payload,
            "profile_hash": profile_hash,
        }

    selection_payload = {
        "artifact_schema_version": SOLVER_TUNING_ARTIFACT_SCHEMA_VERSION,
        "protocol_version": SOLVER_TUNING_PROTOCOL_VERSION,
        "evidence_class": "synthetic_pilot_tuning_excluded_from_final_inference",
        "plan_hash": plan_hash,
        "selection_order": list(_SELECTION_ORDER),
        "selected_configuration_ids": selected_configuration_ids,
        "selected_profiles": selected_profiles,
        "rankings": rankings,
    }
    return {
        **selection_payload,
        "selection_hash": models.canonical_sha256(selection_payload),
    }


# Descriptive aliases for callers that name the artifact rather than the command.
build_equal_budget_tuning_plan = build_solver_tuning_plan
select_solver_tuning_configurations = select_solver_tuning_profiles

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


def _validate_solver_tuning_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("artifact_schema_version") != SOLVER_TUNING_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("unsupported solver tuning artifact schema")
    if plan.get("protocol_version") != SOLVER_TUNING_PROTOCOL_VERSION:
        raise ValueError("legacy or unsupported equal-budget solver tuning protocol")
    manifest = {
        key: value
        for key, value in plan.items()
        if key
        not in {
            "configuration_count",
            "run_count",
            "per_algorithm_budget_seconds",
            "plan_hash",
            "dry_run",
        }
    }
    if models.canonical_sha256(manifest) != plan.get("plan_hash"):
        raise ValueError("solver tuning plan hash does not match its frozen manifest")

    configurations = plan.get("configurations")
    runs = plan.get("runs")
    seeds = plan.get("seeds")
    if not isinstance(configurations, list) or len(configurations) != 12:
        raise ValueError("solver tuning plan must contain exactly 12 configurations")
    if not isinstance(seeds, list) or not seeds:
        raise ValueError("solver tuning plan requires at least one seed")
    expected_algorithms = {
        models.SolverAlgorithm.CP_SAT,
        models.SolverAlgorithm.GENETIC_ALGORITHM,
    }
    counts = {
        algorithm: sum(row.get("algorithm") == algorithm for row in configurations)
        for algorithm in expected_algorithms
    }
    if set(counts.values()) != {6}:
        raise ValueError("each solver tuning grid must contain exactly six configurations")
    if plan.get("configuration_count_by_algorithm") != counts:
        raise ValueError("solver tuning configuration counts do not match the manifest")

    expected_run_count = len(configurations) * len(seeds)
    if not isinstance(runs, list) or len(runs) != expected_run_count:
        raise ValueError("solver tuning plan run matrix is incomplete")
    deadline = _positive_finite(plan.get("time_limit_seconds"), "plan time_limit_seconds")
    expected_allocations = {
        algorithm: count * len(seeds) * deadline
        for algorithm, count in counts.items()
    }
    if plan.get("allocated_seconds_by_algorithm") != expected_allocations:
        raise ValueError("solver tuning allocated-time manifest is inconsistent")
    if len(set(expected_allocations.values())) != 1:
        raise ValueError("solver tuning algorithms do not have equal allocated time")
    if plan.get("per_algorithm_budget_seconds") != next(
        iter(expected_allocations.values())
    ):
        raise ValueError("solver tuning per-algorithm budget is inconsistent")


def _validate_solver_tuning_observation(
    row: Mapping[str, Any],
    *,
    plan_hash: str,
    deadline: float,
    implementation_version: str,
) -> None:
    if row.get("terminal") is not True:
        raise ValueError("every tuning observation must be terminal")
    if row.get("protocol_version") != SOLVER_TUNING_PROTOCOL_VERSION:
        raise ValueError("legacy tuning observations cannot be mixed with this protocol")
    if row.get("implementation_version") != implementation_version:
        raise ValueError("tuning observation implementation version does not match its grid")
    if row.get("plan_hash") != plan_hash:
        raise ValueError("tuning observation belongs to a different plan")
    observed_deadline = _positive_finite(
        row.get("time_limit_seconds"),
        "observation time_limit_seconds",
    )
    if observed_deadline != deadline:
        raise ValueError("mixed tuning deadlines are not comparable")
    if type(row.get("feasible")) is not bool:
        raise ValueError("every observation requires a Boolean feasible value")
    execution_seconds = _nonnegative_finite(
        row.get("execution_seconds"),
        "execution_seconds",
    )
    if execution_seconds is None:
        raise ValueError("every terminal tuning observation requires execution_seconds")
    first_feasible = _nonnegative_finite(
        row.get("first_feasible_seconds"),
        "first_feasible_seconds",
    )
    penalty = _nonnegative_finite(row.get("raw_soft_penalty"), "raw_soft_penalty")
    if row["feasible"] and (first_feasible is None or penalty is None):
        raise ValueError(
            "feasible observations require finite first-feasible time and raw penalty"
        )
    if not row["feasible"] and first_feasible is not None:
        raise ValueError("non-feasible observations cannot report first-feasible time")


def _tuning_ranking_row(
    configuration_id: str,
    rows: Sequence[Mapping[str, Any]],
    deadline: float,
) -> dict[str, Any]:
    feasible_rows = [row for row in rows if row["feasible"]]
    penalties = [float(row["raw_soft_penalty"]) for row in feasible_rows]
    time_observations = [
        min(float(row["first_feasible_seconds"]), deadline)
        if row["feasible"]
        else deadline
        for row in rows
    ]
    time_successes = [
        bool(row["feasible"] and float(row["first_feasible_seconds"]) < deadline)
        for row in rows
    ]
    execution_times = [float(row["execution_seconds"]) for row in rows]
    return {
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


def _validate_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("protocol_version") != GA_TUNING_PROTOCOL_VERSION:
        raise ValueError("legacy or unsupported GA tuning protocol; expected 2.0")
    if plan.get("implementation_version") != GA_IMPLEMENTATION_VERSION:
        raise ValueError(f"GA tuning plan implementation version is not {GA_IMPLEMENTATION_VERSION}")
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
        raise ValueError(f"legacy solver observations cannot be mixed with {GA_IMPLEMENTATION_VERSION}")
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
