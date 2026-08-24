"""Create and optionally execute a controlled CP-SAT/GA experiment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from scheduler import models
from scheduler.services.experiments import (
    DEFAULT_EXPERIMENT_SEEDS,
    DEFAULT_MEMORY_LIMIT_MB,
    create_experiment_batch,
    deterministic_execution_order,
    execute_experiment_batch,
    export_experiment_csv,
    export_experiment_json,
    queue_experiment_batch,
    summarize_experiment,
)
from scheduler.services.runs import create_run, execute_run, queue_run


class Command(BaseCommand):
    help = "Plan, execute, or queue a reproducible CP-SAT versus GA comparison batch."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("snapshot_id", type=int)
        parser.add_argument(
            "--mode",
            choices=("plan", "direct", "queue"),
            default="plan",
            help="Defaults to plan-only so a 60-run experiment is never started accidentally.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Alias for --mode plan.")
        parser.add_argument("--user-id", type=int, help="Required for direct or queued execution.")
        parser.add_argument(
            "--seeds",
            default=f"{DEFAULT_EXPERIMENT_SEEDS[0]}-{DEFAULT_EXPERIMENT_SEEDS[-1]}",
            help="Comma-separated seeds and inclusive ranges, for example 1001-1030 or 1,4,9.",
        )
        parser.add_argument("--time-limit", type=int, default=300)
        parser.add_argument("--order-seed", type=int, default=0)
        parser.add_argument("--memory-limit-mb", type=int, default=DEFAULT_MEMORY_LIMIT_MB)
        parser.add_argument("--name")
        parser.add_argument(
            "--config-json",
            default="{}",
            help="Additional SolverConfig fields as a JSON object; protocol limits override conflicts.",
        )
        parser.add_argument("--export-json", type=Path)
        parser.add_argument("--export-csv", type=Path)

    def handle(self, *args: Any, **options: Any) -> None:
        mode = "plan" if options["dry_run"] else options["mode"]
        try:
            seeds = _parse_seeds(options["seeds"])
            run_configuration = json.loads(options["config_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CommandError(str(exc)) from exc
        if not isinstance(run_configuration, dict):
            raise CommandError("--config-json must decode to a JSON object")
        try:
            snapshot = models.ProblemSnapshot.objects.select_related("revision", "created_by").get(
                pk=options["snapshot_id"]
            )
        except models.ProblemSnapshot.DoesNotExist as exc:
            raise CommandError(f"Problem snapshot {options['snapshot_id']} does not exist") from exc

        plan = deterministic_execution_order(seeds, options["order_seed"])
        warm_up_plan = [
            {"algorithm": algorithm, "seed": 1000, "measured": False}
            for algorithm in models.SolverAlgorithm.values
        ]
        if mode == "plan":
            self.stdout.write(
                json.dumps(
                    {
                        "dry_run": True,
                        "snapshot_id": snapshot.pk,
                        "snapshot_hash": snapshot.snapshot_hash,
                        "time_limit_seconds": options["time_limit"],
                        "order_seed": options["order_seed"],
                        "warm_up_runs": warm_up_plan,
                        "runs": list(plan),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return

        if not options["user_id"]:
            raise CommandError("--user-id is required for direct or queue mode")
        try:
            user = models.User.objects.get(pk=options["user_id"])
        except models.User.DoesNotExist as exc:
            raise CommandError(f"User {options['user_id']} does not exist") from exc
        if not user.is_active or not (
            user.is_superuser
            or user.role in {models.UserRole.SYSTEM_ADMIN, models.UserRole.CENTRAL_SCHEDULER}
        ):
            raise CommandError("An active central scheduler is required")
        try:
            warm_up_runs = _execute_warm_ups(
                snapshot=snapshot,
                user=user,
                mode=mode,
                time_limit=options["time_limit"],
                run_configuration=run_configuration,
            )
            batch = create_experiment_batch(
                snapshot,
                user,
                seeds,
                options["time_limit"],
                options["order_seed"],
                name=options["name"],
                memory_limit_mb=options["memory_limit_mb"],
                run_configuration=run_configuration,
            )
            batch = (
                execute_experiment_batch(batch)
                if mode == "direct"
                else queue_experiment_batch(batch)
            )
        except (ValueError, TypeError) as exc:
            raise CommandError(str(exc)) from exc

        if options["export_json"]:
            options["export_json"].write_bytes(export_experiment_json(batch))
        if options["export_csv"]:
            options["export_csv"].write_bytes(export_experiment_csv(batch))
        summary = summarize_experiment(batch)
        summary["unmeasured_warm_up_runs"] = warm_up_runs
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
        self.stdout.write(self.style.SUCCESS(f"Experiment batch {batch.pk}: {batch.status}"))


def _parse_seeds(value: str) -> tuple[int, ...]:
    seeds: list[int] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            left, separator, right = part.partition("-")
            if not separator or not left.isdigit() or not right.isdigit():
                raise ValueError(f"invalid seed range {part!r}")
            start, end = int(left), int(right)
            if end < start:
                raise ValueError(f"seed range {part!r} is descending")
            seeds.extend(range(start, end + 1))
        elif part.isdigit():
            seeds.append(int(part))
        else:
            raise ValueError(f"invalid seed {part!r}")
    if not seeds:
        raise ValueError("at least one seed is required")
    if len(seeds) != len(set(seeds)):
        raise ValueError("seeds must be unique")
    return tuple(seeds)


def _execute_warm_ups(
    *,
    snapshot: models.ProblemSnapshot,
    user: models.User,
    mode: str,
    time_limit: int,
    run_configuration: dict[str, Any],
) -> list[dict[str, Any]]:
    """Submit one explicitly excluded warm-up per engine before measured runs."""

    rows: list[dict[str, Any]] = []
    for algorithm in models.SolverAlgorithm.values:
        run = create_run(
            snapshot=snapshot,
            algorithm=algorithm,
            requested_by=user,
            seed=1000,
            configuration={
                **run_configuration,
                "time_limit_seconds": time_limit,
                "worker_count": 1,
                "research_phase": "BENCHMARK_WARM_UP",
                "excluded_from_experiment": True,
                "persist_schedule": False,
                "warm_up_for_snapshot_hash": snapshot.snapshot_hash,
            },
        )
        run = execute_run(run.pk) if mode == "direct" else queue_run(run)
        rows.append(
            {
                "run_id": run.pk,
                "algorithm": algorithm,
                "seed": run.seed,
                "status": run.status,
                "measured": False,
            }
        )
    return rows
