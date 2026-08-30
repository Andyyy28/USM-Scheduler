"""Simulated persisted pilot evidence for integrity tests, never research data."""

from __future__ import annotations

from scheduler import models
from scheduler.management.commands.solver_tuning_grid import Command
from scheduler.services.problem_builder import load_problem
from scheduler.services.runs import build_solver_config
from scheduler.services.tuning import build_solver_tuning_plan, select_solver_tuning_profiles


def persisted_tuning_profiles(
    snapshot: models.ProblemSnapshot,
    actor: models.User,
) -> dict:
    plan = build_solver_tuning_plan(snapshot, environment={
        "build": {"source_commit": "synthetic-test-build", "container_image_id": "synthetic-test-image"},
        "evidence_class": "test_fixture_only",
    })
    run_ids = Command()._persist_plan(plan, snapshot, actor)
    runs = list(models.ScheduleRun.objects.filter(pk__in=run_ids).order_by("pk"))
    problem_hash = load_problem(snapshot).canonical_hash
    observations = []
    for run, entry in zip(runs, plan["runs"], strict=True):
        implementation = entry["solver_configuration"]["implementation_version"]
        models.ScheduleRun.objects.filter(pk=run.pk).update(
            status=models.RunStatus.NO_SOLUTION,
            execution_seconds=60.0,
            diagnostics={
                "problem_hash": problem_hash,
                "config_hash": build_solver_config(run).canonical_hash,
                "metrics": {"implementation_version": implementation},
            },
        )
        models.ValidationResult.objects.create(
            run=run,
            is_feasible=False,
            hard_violation_count=0,
            violations={"evaluated": False, "reason": "Simulated no-solution test observation"},
            validator_version="1.1",
        )
        observations.append({
            "algorithm": run.algorithm,
            "configuration_id": entry["configuration_id"],
            "seed": run.seed,
            "terminal": True,
            "protocol_version": plan["protocol_version"],
            "implementation_version": implementation,
            "plan_hash": plan["plan_hash"],
            "resolved_configuration_hash": entry["resolved_configuration_hash"],
            "time_limit_seconds": 60.0,
            "feasible": False,
            "raw_soft_penalty": None,
            "first_feasible_seconds": None,
            "execution_seconds": 60.0,
        })
    return select_solver_tuning_profiles(observations, plan)["selected_profiles"]
