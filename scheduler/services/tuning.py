"""Preregistered Genetic Algorithm pilot-tuning plan and selection policy."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import product
from statistics import median
from typing import Any

from scheduler import models

GA_TUNING_PROTOCOL_VERSION = "1.0"
GA_TUNING_SEEDS = tuple(range(2001, 2011))
GA_TUNING_POPULATIONS = (100, 200, 400)
GA_TUNING_TOURNAMENT_SIZES = (3, 5)
GA_TUNING_CROSSOVER_RATES = (0.8, 0.9)
GA_TUNING_MUTATION_MULTIPLIERS = (1, 2)


def ga_tuning_configurations(event_count: int) -> tuple[dict[str, Any], ...]:
    """Return the complete fixed 24-configuration GA grid."""

    if type(event_count) is not int or event_count <= 0:
        raise ValueError("event_count must be a positive integer")
    rows: list[dict[str, Any]] = []
    for population, tournament, crossover, mutation_multiplier in product(
        GA_TUNING_POPULATIONS,
        GA_TUNING_TOURNAMENT_SIZES,
        GA_TUNING_CROSSOVER_RATES,
        GA_TUNING_MUTATION_MULTIPLIERS,
    ):
        parameters = {
            "population_size": population,
            "tournament_size": tournament,
            "crossover_rate": crossover,
            "mutation_formula": f"{mutation_multiplier}/N",
            "mutation_multiplier": mutation_multiplier,
            "elite_fraction": 0.05,
        }
        rows.append(
            {
                "configuration_id": models.canonical_sha256(parameters),
                "parameters": parameters,
                "solver_configuration": {
                    "population_size": population,
                    "tournament_size": tournament,
                    "crossover_rate": crossover,
                    "mutation_rate": mutation_multiplier / event_count,
                    "elite_fraction": 0.05,
                },
            }
        )
    return tuple(rows)


def build_ga_tuning_plan(
    snapshot: models.ProblemSnapshot,
    seeds: Iterable[int] = GA_TUNING_SEEDS,
) -> dict[str, Any]:
    """Build a deterministic, hash-addressed pilot plan without executing it."""

    normalized_seeds = _validated_seeds(seeds)
    configurations = ga_tuning_configurations(snapshot.event_count)
    runs = [
        {
            "configuration_id": configuration["configuration_id"],
            "seed": seed,
            "solver_configuration": configuration["solver_configuration"],
        }
        for configuration in configurations
        for seed in normalized_seeds
    ]
    manifest = {
        "protocol_version": GA_TUNING_PROTOCOL_VERSION,
        "snapshot_id": snapshot.pk,
        "snapshot_hash": snapshot.snapshot_hash,
        "event_count": snapshot.event_count,
        "seeds": list(normalized_seeds),
        "selection_order": [
            "highest_feasibility_rate",
            "lowest_median_feasible_raw_soft_penalty",
            "lowest_median_execution_seconds",
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
) -> dict[str, Any]:
    """Rank completed pilot configurations using the frozen lexicographic rule."""

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in observations:
        configuration_id = str(row.get("configuration_id", "")).strip()
        if not configuration_id:
            raise ValueError("every observation requires a configuration_id")
        if type(row.get("feasible")) is not bool:
            raise ValueError("every observation requires a Boolean feasible value")
        grouped[configuration_id].append(row)
    if not grouped:
        raise ValueError("at least one tuning observation is required")

    ranking: list[dict[str, Any]] = []
    for configuration_id, rows in grouped.items():
        feasible_rows = [row for row in rows if row["feasible"]]
        penalties = _numeric_values(feasible_rows, "raw_soft_penalty")
        times = _numeric_values(rows, "execution_seconds")
        ranking.append(
            {
                "configuration_id": configuration_id,
                "runs": len(rows),
                "feasible_runs": len(feasible_rows),
                "feasibility_rate": len(feasible_rows) / len(rows),
                "median_feasible_raw_soft_penalty": (
                    float(median(penalties)) if penalties else None
                ),
                "median_execution_seconds": float(median(times)) if times else None,
            }
        )

    ranking.sort(
        key=lambda row: (
            -row["feasibility_rate"],
            _none_as_infinity(row["median_feasible_raw_soft_penalty"]),
            _none_as_infinity(row["median_execution_seconds"]),
            row["configuration_id"],
        )
    )
    return {
        "protocol_version": GA_TUNING_PROTOCOL_VERSION,
        "selection_order": [
            "highest_feasibility_rate",
            "lowest_median_feasible_raw_soft_penalty",
            "lowest_median_execution_seconds",
            "configuration_id_tiebreak",
        ],
        "selected_configuration_id": ranking[0]["configuration_id"],
        "ranking": ranking,
        "selection_hash": models.canonical_sha256(ranking),
    }


def _validated_seeds(seeds: Iterable[int]) -> tuple[int, ...]:
    normalized = tuple(seeds)
    if not normalized or any(type(seed) is not int or seed < 0 for seed in normalized):
        raise ValueError("seeds must be non-empty, non-negative integers")
    if len(normalized) != len(set(normalized)):
        raise ValueError("seeds must be unique")
    return normalized


def _numeric_values(rows: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def _none_as_infinity(value: float | None) -> float:
    return float("inf") if value is None else value
