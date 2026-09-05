from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from scheduler import models
from scheduler.services.tuning import (
    CP_SAT_TUNING_LINEARIZATION_LEVELS,
    CP_SAT_TUNING_PRESOLVE_VALUES,
    SOLVER_TUNING_GA_MUTATION_MULTIPLIERS,
    SOLVER_TUNING_GA_POPULATIONS,
    SOLVER_TUNING_SEEDS,
    SOLVER_TUNING_TIME_LIMIT_SECONDS,
    build_solver_tuning_plan,
    select_solver_tuning_profiles,
    solver_tuning_configurations,
)


def test_equal_budget_grid_has_six_deterministic_configurations_per_solver() -> None:
    first = solver_tuning_configurations(80)
    second = solver_tuning_configurations(80)

    assert first == second
    assert len(first) == 12
    assert len({row["configuration_id"] for row in first}) == 12

    cp_sat = [
        row for row in first if row["algorithm"] == models.SolverAlgorithm.CP_SAT
    ]
    ga = [
        row
        for row in first
        if row["algorithm"] == models.SolverAlgorithm.GENETIC_ALGORITHM
    ]
    assert len(cp_sat) == (
        len(CP_SAT_TUNING_PRESOLVE_VALUES)
        * len(CP_SAT_TUNING_LINEARIZATION_LEVELS)
    ) == 6
    assert {
        (
            row["solver_configuration"]["cp_model_presolve"],
            row["solver_configuration"]["linearization_level"],
        )
        for row in cp_sat
    } == {
        (presolve, linearization)
        for presolve in (False, True)
        for linearization in (0, 1, 2)
    }

    assert len(ga) == (
        len(SOLVER_TUNING_GA_POPULATIONS)
        * len(SOLVER_TUNING_GA_MUTATION_MULTIPLIERS)
    ) == 6
    assert {row["solver_configuration"]["population_size"] for row in ga} == {
        100,
        200,
        400,
    }
    assert {row["solver_configuration"]["mutation_rate"] for row in ga} == {
        1 / 80,
        2 / 80,
    }
    assert {row["parameters"]["mutation_formula"] for row in ga} == {
        "1/N_mutable",
        "2/N_mutable",
    }
    assert all(row["solver_configuration"]["tournament_size"] == 3 for row in ga)
    assert all(row["solver_configuration"]["crossover_rate"] == 0.9 for row in ga)
    assert all(row["solver_configuration"]["elite_fraction"] == 0.05 for row in ga)
    assert all(row["solver_configuration"]["repair_attempts"] == 20 for row in ga)


def test_equal_budget_plan_freezes_sixty_runs_and_thirty_minutes_per_solver() -> None:
    first = _plan()
    second = _plan()

    assert first == second
    assert first["protocol_defaults_applied"] is True
    assert first["seeds"] == list(SOLVER_TUNING_SEEDS) == [2001, 2002, 2003, 2004, 2005]
    assert first["time_limit_seconds"] == float(SOLVER_TUNING_TIME_LIMIT_SECONDS) == 60.0
    assert first["configuration_count"] == 12
    assert first["run_count"] == 60
    assert first["configuration_count_by_algorithm"] == {
        models.SolverAlgorithm.CP_SAT: 6,
        models.SolverAlgorithm.GENETIC_ALGORITHM: 6,
    }
    assert first["allocated_seconds_by_algorithm"] == {
        models.SolverAlgorithm.CP_SAT: 1800.0,
        models.SolverAlgorithm.GENETIC_ALGORITHM: 1800.0,
    }
    assert first["per_algorithm_budget_seconds"] == 1800.0
    assert len(first["plan_hash"]) == 64

    all_ids = {row["configuration_id"] for row in first["configurations"]}
    for index in range(len(SOLVER_TUNING_SEEDS)):
        block = first["runs"][index * 12 : (index + 1) * 12]
        assert {row["configuration_id"] for row in block} == all_ids
        assert len({row["seed"] for row in block}) == 1
        assert sum(
            row["algorithm"] == models.SolverAlgorithm.CP_SAT for row in block
        ) == 6
        assert sum(
            row["algorithm"] == models.SolverAlgorithm.GENETIC_ALGORITHM
            for row in block
        ) == 6


def test_equal_budget_selection_uses_same_lexicographic_policy_per_solver() -> None:
    plan = _plan()
    by_algorithm = {
        algorithm: [
            row for row in plan["configurations"] if row["algorithm"] == algorithm
        ]
        for algorithm in (
            models.SolverAlgorithm.CP_SAT,
            models.SolverAlgorithm.GENETIC_ALGORITHM,
        )
    }
    cp_first, cp_second = by_algorithm[models.SolverAlgorithm.CP_SAT][:2]
    ga_first, ga_second = by_algorithm[models.SolverAlgorithm.GENETIC_ALGORITHM][:2]

    observations = []
    for run in plan["runs"]:
        feasible = True
        penalty = 50
        first_feasible = 20.0
        if run["configuration_id"] in {
            cp_first["configuration_id"],
            cp_second["configuration_id"],
        }:
            penalty = 10
            first_feasible = (
                10.0 if run["configuration_id"] == cp_first["configuration_id"] else 5.0
            )
        if run["configuration_id"] == ga_first["configuration_id"] and run["seed"] == 2001:
            feasible = False
            penalty = None
            first_feasible = None
        elif run["configuration_id"] == ga_second["configuration_id"]:
            penalty = 12
            first_feasible = 8.0
        observations.append(
            _observation(
                plan,
                run,
                feasible=feasible,
                penalty=penalty,
                first_feasible=first_feasible,
            )
        )

    selection = select_solver_tuning_profiles(observations, plan)

    assert selection["selected_configuration_ids"][models.SolverAlgorithm.CP_SAT] == (
        cp_second["configuration_id"]
    )
    assert selection["selected_configuration_ids"][
        models.SolverAlgorithm.GENETIC_ALGORITHM
    ] == ga_second["configuration_id"]
    assert selection["selected_profiles"][models.SolverAlgorithm.CP_SAT]["frozen"] is True
    assert selection["selected_profiles"][models.SolverAlgorithm.GENETIC_ALGORITHM][
        "frozen"
    ] is True
    assert selection["selection_order"] == [
        "highest_feasibility_rate",
        "lowest_median_feasible_raw_soft_penalty",
        "lowest_rmst_time_to_feasibility",
        "configuration_id_tiebreak",
    ]
    assert len(selection["selection_hash"]) == 64


def test_equal_budget_selection_rejects_incomplete_duplicate_and_tampered_evidence() -> None:
    plan = _plan()
    observations = [
        _observation(plan, run, feasible=False, penalty=None, first_feasible=None)
        for run in plan["runs"]
    ]

    with pytest.raises(ValueError, match="incomplete"):
        select_solver_tuning_profiles(observations[:-1], plan)

    with pytest.raises(ValueError, match="duplicate"):
        select_solver_tuning_profiles(observations + [dict(observations[0])], plan)

    tampered_observations = deepcopy(observations)
    tampered_observations[0]["resolved_configuration_hash"] = "0" * 64
    with pytest.raises(ValueError, match="resolved configuration mismatch"):
        select_solver_tuning_profiles(tampered_observations, plan)

    tampered_plan = deepcopy(plan)
    tampered_plan["time_limit_seconds"] = 61.0
    with pytest.raises(ValueError, match="plan hash"):
        select_solver_tuning_profiles(observations, tampered_plan)


def _plan() -> dict[str, object]:
    snapshot = SimpleNamespace(
        pk=11,
        snapshot_hash="b" * 64,
        event_count=4,
        input_data={
            "events": [{"event_id": f"E{index}"} for index in range(4)],
            "locked_assignments": [{"event_id": "E0", "candidate_id": "C0"}],
        },
    )
    return build_solver_tuning_plan(
        snapshot,
        environment={"build": {"source_commit": "test-commit"}},
    )


def _observation(
    plan: dict[str, object],
    run: dict[str, object],
    *,
    feasible: bool,
    penalty: int | None,
    first_feasible: float | None,
) -> dict[str, object]:
    configuration = next(
        row
        for row in plan["configurations"]
        if row["configuration_id"] == run["configuration_id"]
    )
    return {
        "algorithm": run["algorithm"],
        "configuration_id": run["configuration_id"],
        "seed": run["seed"],
        "terminal": True,
        "protocol_version": plan["protocol_version"],
        "implementation_version": configuration["parameters"]["implementation_version"],
        "plan_hash": plan["plan_hash"],
        "resolved_configuration_hash": run["resolved_configuration_hash"],
        "time_limit_seconds": plan["time_limit_seconds"],
        "feasible": feasible,
        "raw_soft_penalty": penalty,
        "first_feasible_seconds": first_feasible,
        "execution_seconds": 59.0,
    }

