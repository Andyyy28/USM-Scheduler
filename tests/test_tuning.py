from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from scheduler import models
from scheduler.management.commands import ga_tuning_grid as tuning_command
from scheduler.services.tuning import (
    GA_TUNING_CROSSOVER_RATES,
    GA_TUNING_MUTATION_MULTIPLIERS,
    GA_TUNING_POPULATIONS,
    GA_TUNING_TOURNAMENT_SIZES,
    build_ga_tuning_plan,
    ga_tuning_configurations,
    select_ga_tuning_configuration,
)


def test_ga_tuning_grid_is_complete_deterministic_and_mutable_event_scaled() -> None:
    first = ga_tuning_configurations(80)
    second = ga_tuning_configurations(80)

    assert first == second
    assert len(first) == (
        len(GA_TUNING_POPULATIONS)
        * len(GA_TUNING_TOURNAMENT_SIZES)
        * len(GA_TUNING_CROSSOVER_RATES)
        * len(GA_TUNING_MUTATION_MULTIPLIERS)
    ) == 24
    assert len({row["configuration_id"] for row in first}) == 24
    assert {row["solver_configuration"]["mutation_rate"] for row in first} == {
        1 / 80,
        2 / 80,
    }
    assert {row["parameters"]["mutation_formula"] for row in first} == {
        "1/N_mutable",
        "2/N_mutable",
    }
    assert {
        row["solver_configuration"]["mutation_rate"]
        for row in ga_tuning_configurations(1)
    } == {1.0}


def test_ga_tuning_plan_randomizes_each_seed_block_and_hashes_full_provenance() -> None:
    plan = _plan()

    assert plan["protocol_version"] == "2.0"
    assert plan["implementation_version"] == "ga-v2"
    assert plan["mutable_event_count"] == 3
    assert plan["configuration_count"] == 24
    assert plan["run_count"] == 48
    assert len(plan["plan_hash"]) == 64
    first_block = [row["configuration_id"] for row in plan["runs"][:24]]
    second_block = [row["configuration_id"] for row in plan["runs"][24:]]
    canonical = [row["configuration_id"] for row in plan["configurations"]]
    assert set(first_block) == set(canonical)
    assert set(second_block) == set(canonical)
    assert first_block != canonical
    assert second_block != canonical
    assert all(row["resolved_configuration_hash"] for row in plan["runs"])


def test_ga_tuning_selection_is_feasibility_then_penalty_then_rmst() -> None:
    plan = _plan()
    first_id, second_id = (
        plan["configurations"][0]["configuration_id"],
        plan["configurations"][1]["configuration_id"],
    )
    observations = []
    for run in plan["runs"]:
        configuration_id = run["configuration_id"]
        penalty = 40 if configuration_id in {first_id, second_id} else 50
        first_feasible = 8.0 if configuration_id == first_id else 1.0
        observations.append(
            _observation(
                plan,
                run,
                feasible=True,
                penalty=penalty,
                first_feasible=first_feasible,
            )
        )

    selected = select_ga_tuning_configuration(observations, plan)

    assert selected["selected_configuration_id"] == second_id
    assert selected["ranking"][0]["feasibility_rate"] == 1.0
    assert selected["ranking"][0]["rmst_time_to_feasibility_seconds"] == 1.0
    assert selected["selected_profile"]["frozen"] is True
    assert selected["selected_profile"]["implementation_version"] == "ga-v2"
    assert selected["selected_profile"]["selection_hash"] == selected["selection_hash"]


def test_ga_tuning_selection_rejects_incomplete_duplicate_legacy_and_nonfinite_cells() -> None:
    plan = _plan()
    observations = [
        _observation(plan, run, feasible=False, penalty=None, first_feasible=None)
        for run in plan["runs"]
    ]

    with pytest.raises(ValueError, match="incomplete"):
        select_ga_tuning_configuration(observations[:-1], plan)

    duplicate = observations + [dict(observations[0])]
    with pytest.raises(ValueError, match="duplicate"):
        select_ga_tuning_configuration(duplicate, plan)

    legacy = deepcopy(observations)
    legacy[0]["protocol_version"] = "1.0"
    with pytest.raises(ValueError, match="legacy"):
        select_ga_tuning_configuration(legacy, plan)

    nonfinite = deepcopy(observations)
    nonfinite[0]["execution_seconds"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        select_ga_tuning_configuration(nonfinite, plan)


def test_tuning_command_scopes_plan_and_recomputes_actual_configuration_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filters: dict[str, object] = {}
    captured_rows: list[dict[str, object]] = []
    plan = _plan()
    entry = plan["runs"][0]
    resolved = entry["resolved_configuration"]
    run = SimpleNamespace(
        algorithm=models.SolverAlgorithm.GENETIC_ALGORITHM,
        seed=entry["seed"],
        status=models.RunStatus.FEASIBLE,
        is_terminal=True,
        configuration={
            "research_phase": "GA_SYNTHETIC_TUNING",
            "ga_tuning_protocol": "2.0",
            "ga_tuning_plan_hash": plan["plan_hash"],
            "ga_tuning_configuration_id": entry["configuration_id"],
            "ga_tuning_resolved_configuration_hash": "untrusted-claim",
            "time_limit_seconds": resolved["time_limit_seconds"],
            "worker_count": resolved["worker_count"],
            "population_size": resolved["population_size"],
            "tournament_size": resolved["tournament_size"],
            "crossover_rate": resolved["crossover_rate"],
            "mutation_rate": resolved["mutation_rate"],
            "elite_fraction": resolved["elite_fraction"],
            "repair_attempts": resolved["repair_attempts"],
            "max_generations": resolved["max_generations"],
            "implementation_version": resolved["implementation_version"],
        },
        diagnostics={"metrics": {"implementation_version": "ga-v2"}},
        objective_value=12,
        first_feasible_seconds=1.5,
        execution_seconds=9.0,
    )

    class _Runs:
        def filter(self, **kwargs: object) -> list[SimpleNamespace]:
            filters.update(kwargs)
            return [run]

    def fake_select(rows, plan):  # type: ignore[no-untyped-def]
        captured_rows.extend(rows)
        return {"selected": True, "plan_hash": plan["plan_hash"]}

    monkeypatch.setattr(tuning_command, "select_ga_tuning_configuration", fake_select)
    result = tuning_command.Command()._selection_payload(
        SimpleNamespace(runs=_Runs()),
        plan,
    )

    assert result["selected"] is True
    assert filters["configuration__ga_tuning_plan_hash"] == plan["plan_hash"]
    assert filters["configuration__ga_tuning_protocol"] == "2.0"
    assert captured_rows[0]["resolved_configuration_hash"] == entry[
        "resolved_configuration_hash"
    ]
    assert captured_rows[0]["resolved_configuration_hash"] != "untrusted-claim"


def _plan() -> dict[str, object]:
    events = [{"event_id": f"E{index}"} for index in range(4)]
    snapshot = SimpleNamespace(
        pk=7,
        snapshot_hash="a" * 64,
        event_count=4,
        input_data={
            "events": events,
            "locked_assignments": [{"event_id": "E0", "candidate_id": "C0"}],
        },
    )
    return build_ga_tuning_plan(
        snapshot,
        seeds=(2001, 2002),
        time_limit_seconds=10,
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
    return {
        "configuration_id": run["configuration_id"],
        "seed": run["seed"],
        "terminal": True,
        "protocol_version": plan["protocol_version"],
        "implementation_version": plan["implementation_version"],
        "plan_hash": plan["plan_hash"],
        "resolved_configuration_hash": run["resolved_configuration_hash"],
        "time_limit_seconds": plan["time_limit_seconds"],
        "feasible": feasible,
        "raw_soft_penalty": penalty,
        "first_feasible_seconds": first_feasible,
        "execution_seconds": 9.0,
    }
