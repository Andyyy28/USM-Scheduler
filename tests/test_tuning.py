from __future__ import annotations

from scheduler.services.tuning import (
    GA_TUNING_CROSSOVER_RATES,
    GA_TUNING_MUTATION_MULTIPLIERS,
    GA_TUNING_POPULATIONS,
    GA_TUNING_TOURNAMENT_SIZES,
    ga_tuning_configurations,
    select_ga_tuning_configuration,
)


def test_ga_tuning_grid_is_complete_deterministic_and_event_scaled() -> None:
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
    assert {row["parameters"]["mutation_formula"] for row in first} == {"1/N", "2/N"}


def test_ga_tuning_selection_is_feasibility_then_penalty_then_time() -> None:
    observations = [
        {
            "configuration_id": "high-feasibility",
            "feasible": True,
            "raw_soft_penalty": 50,
            "execution_seconds": 10,
        },
        {
            "configuration_id": "high-feasibility",
            "feasible": True,
            "raw_soft_penalty": 40,
            "execution_seconds": 12,
        },
        {
            "configuration_id": "low-penalty-but-fails",
            "feasible": True,
            "raw_soft_penalty": 1,
            "execution_seconds": 1,
        },
        {
            "configuration_id": "low-penalty-but-fails",
            "feasible": False,
            "raw_soft_penalty": None,
            "execution_seconds": 1,
        },
    ]

    selected = select_ga_tuning_configuration(observations)

    assert selected["selected_configuration_id"] == "high-feasibility"
    assert selected["ranking"][0]["feasibility_rate"] == 1.0
    assert len(selected["selection_hash"]) == 64
