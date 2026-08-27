from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pytest

from scheduler import models
from scheduler.domain import SolverAlgorithm, SolverConfig, SolverStatus
from scheduler.services.imports import commit_import, preview_workbook
from scheduler.services.problem_builder import build_and_store_snapshot
from scheduler.services.trial_data import build_trial_workbook_bytes
from scheduler.services.tuning import (
    GA_TUNING_SEEDS,
    build_ga_tuning_plan,
    select_ga_tuning_configuration,
)
from scheduler.solvers import CpSatSolver, GeneticAlgorithmSolver, is_ortools_available

pytestmark = [
    pytest.mark.diagnostic,
    pytest.mark.django_db,
    pytest.mark.skipif(not is_ortools_available(), reason="OR-Tools is not installed"),
]

_SMOKE_SEEDS = (5001, 5002, 5003, 5004, 5005)
_TERMINAL_SOLVER_STATUSES = {
    SolverStatus.OPTIMAL,
    SolverStatus.FEASIBLE,
    SolverStatus.INFEASIBLE,
    SolverStatus.NO_SOLUTION,
    SolverStatus.UNKNOWN,
}


def test_full_v2_matrix_and_five_seed_smoke_write_diagnostic_artifact() -> None:
    user = models.User.objects.create_user(
        username="solver-v2-diagnostic",
        role=models.UserRole.CENTRAL_SCHEDULER,
    )
    term = models.AcademicTerm.objects.create(
        academic_year="2026-2027",
        semester=models.Semester.FIRST,
        campus="Kabacan",
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 12, 20),
    )
    batch = preview_workbook(build_trial_workbook_bytes(), term, user)
    revision = commit_import(batch, user)
    objective = models.ObjectiveProfile.objects.create(
        name="Synthetic diagnostic objective",
        term=term,
        is_approved=True,
        approved_by=user,
    )
    snapshot, built = build_and_store_snapshot(revision, objective, user)
    problem = built.problem

    plan = build_ga_tuning_plan(
        snapshot,
        GA_TUNING_SEEDS,
        time_limit_seconds=1,
    )
    tuning_rows = []
    observations = []
    for entry in plan["runs"]:
        values = entry["resolved_configuration"]
        config = SolverConfig(
            algorithm=SolverAlgorithm.GENETIC_ALGORITHM,
            seed=entry["seed"],
            time_limit_seconds=values["time_limit_seconds"],
            worker_count=values["worker_count"],
            population_size=values["population_size"],
            tournament_size=values["tournament_size"],
            crossover_rate=values["crossover_rate"],
            mutation_rate=values["mutation_rate"],
            elite_fraction=values["elite_fraction"],
            repair_attempts=values["repair_attempts"],
            max_generations=values["max_generations"],
        )
        result = GeneticAlgorithmSolver().solve(problem, config)
        feasible = result.validation.feasible
        row = {
            "position": entry["position"],
            "configuration_id": entry["configuration_id"],
            "seed": entry["seed"],
            "status": result.status.value,
            "feasible": feasible,
            "raw_soft_penalty": (
                result.objective.weighted_total
                if feasible and result.objective is not None
                else None
            ),
            "first_feasible_seconds": result.first_feasible_seconds,
            "execution_seconds": result.runtime_seconds,
            "metrics": dict(result.metrics),
        }
        tuning_rows.append(row)
        observations.append(
            {
                **row,
                "terminal": True,
                "protocol_version": plan["protocol_version"],
                "implementation_version": plan["implementation_version"],
                "plan_hash": plan["plan_hash"],
                "resolved_configuration_hash": entry["resolved_configuration_hash"],
                "time_limit_seconds": plan["time_limit_seconds"],
            }
        )

    selection = select_ga_tuning_configuration(observations, plan)
    selected_id = selection["selected_configuration_id"]
    selected = next(
        row for row in plan["configurations"] if row["configuration_id"] == selected_id
    )["solver_configuration"]
    smoke_rows = []
    for seed in _SMOKE_SEEDS:
        configs = (
            SolverConfig(
                algorithm=SolverAlgorithm.CP_SAT,
                seed=seed,
                time_limit_seconds=2,
                worker_count=1,
            ),
            SolverConfig(
                algorithm=SolverAlgorithm.GENETIC_ALGORITHM,
                seed=seed,
                time_limit_seconds=2,
                worker_count=1,
                population_size=selected["population_size"],
                tournament_size=selected["tournament_size"],
                crossover_rate=selected["crossover_rate"],
                mutation_rate=selected["mutation_rate"],
                elite_fraction=selected["elite_fraction"],
                repair_attempts=20,
            ),
        )
        for config in configs:
            solver = (
                CpSatSolver()
                if config.algorithm is SolverAlgorithm.CP_SAT
                else GeneticAlgorithmSolver()
            )
            result = solver.solve(problem, config)
            smoke_rows.append(
                {
                    "seed": seed,
                    "algorithm": config.algorithm.value,
                    "status": result.status.value,
                    "feasible": result.validation.feasible,
                    "raw_soft_penalty": (
                        result.objective.weighted_total
                        if result.validation.feasible and result.objective is not None
                        else None
                    ),
                    "first_feasible_seconds": result.first_feasible_seconds,
                    "execution_seconds": result.runtime_seconds,
                    "metrics": dict(result.metrics),
                }
            )

    artifact = {
        "artifact_schema_version": "1.0",
        "evidence_class": "diagnostic_not_formal_thesis_evidence",
        "warning": (
            "Synthetic disposable-database diagnostic with shortened deadlines; "
            "do not combine with the formal protocol."
        ),
        "dataset": {
            "name": "USM-Scheduler-Synthetic-Trial-v1",
            "meeting_count": len(problem.events),
            "mutable_event_count": plan["mutable_event_count"],
            "snapshot_hash": snapshot.snapshot_hash,
        },
        "tuning": {
            "plan_hash": plan["plan_hash"],
            "protocol_version": plan["protocol_version"],
            "implementation_version": plan["implementation_version"],
            "deadline_seconds": plan["time_limit_seconds"],
            "order_seed": plan["order_seed"],
            "configuration_count": plan["configuration_count"],
            "seed_count": len(plan["seeds"]),
            "observations": tuning_rows,
            "selected_profile": selection["selected_profile"],
        },
        "smoke_comparison": {
            "deadline_seconds": 2,
            "seeds": list(_SMOKE_SEEDS),
            "observations": smoke_rows,
        },
    }
    artifact_path = Path("experiment-results") / "solver-v2-synthetic-diagnostic.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(artifact, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    assert len(tuning_rows) == 24 * 10
    assert len({(row["configuration_id"], row["seed"]) for row in tuning_rows}) == 240
    assert len(smoke_rows) == 5 * 2
    assert all(math.isfinite(row["execution_seconds"]) for row in tuning_rows + smoke_rows)
    assert all(SolverStatus(row["status"]) in _TERMINAL_SOLVER_STATUSES for row in tuning_rows)
    assert artifact_path.is_file()
