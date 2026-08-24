"""Plan, execute, queue, or select the preregistered synthetic GA tuning grid."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from scheduler import models
from scheduler.services.runs import create_run, execute_run, queue_run
from scheduler.services.tuning import (
    GA_TUNING_SEEDS,
    build_ga_tuning_plan,
    select_ga_tuning_configuration,
)


class Command(BaseCommand):
    help = "Plan or explicitly run the fixed 24-by-10 GA pilot-tuning grid."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("snapshot_id", type=int)
        parser.add_argument(
            "--mode",
            choices=("plan", "direct", "queue", "select"),
            default="plan",
            help="Defaults to plan; direct/queue explicitly launch all pilot runs.",
        )
        parser.add_argument("--user-id", type=int)
        parser.add_argument("--time-limit", type=int, default=300)
        parser.add_argument("--output", type=Path)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            snapshot = models.ProblemSnapshot.objects.get(pk=options["snapshot_id"])
        except models.ProblemSnapshot.DoesNotExist as exc:
            raise CommandError(f"Problem snapshot {options['snapshot_id']} does not exist") from exc

        mode = options["mode"]
        if mode == "select":
            payload = self._selection_payload(snapshot)
        else:
            plan = build_ga_tuning_plan(snapshot, GA_TUNING_SEEDS)
            payload = {"dry_run": mode == "plan", **plan}
            if mode in {"direct", "queue"}:
                payload["created_run_ids"] = self._launch(plan, snapshot, options)

        rendered = json.dumps(payload, indent=2, sort_keys=True)
        if options["output"]:
            options["output"].write_text(rendered + "\n", encoding="utf-8")
        self.stdout.write(rendered)

    def _launch(
        self,
        plan: dict[str, Any],
        snapshot: models.ProblemSnapshot,
        options: dict[str, Any],
    ) -> list[int]:
        if not options["user_id"]:
            raise CommandError("--user-id is required for direct or queue mode")
        if options["time_limit"] <= 0:
            raise CommandError("--time-limit must be positive")
        try:
            user = models.User.objects.get(pk=options["user_id"], is_active=True)
        except models.User.DoesNotExist as exc:
            raise CommandError("The active tuning user does not exist") from exc
        if not (
            user.is_superuser
            or user.role in {models.UserRole.SYSTEM_ADMIN, models.UserRole.CENTRAL_SCHEDULER}
        ):
            raise CommandError("An active central scheduler is required")

        run_ids: list[int] = []
        for entry in plan["runs"]:
            configuration = {
                **entry["solver_configuration"],
                "time_limit_seconds": options["time_limit"],
                "worker_count": 1,
                "research_phase": "GA_SYNTHETIC_TUNING",
                "ga_tuning_protocol": plan["protocol_version"],
                "ga_tuning_plan_hash": plan["plan_hash"],
                "ga_tuning_configuration_id": entry["configuration_id"],
                "persist_schedule": False,
            }
            run = create_run(
                snapshot=snapshot,
                algorithm=models.SolverAlgorithm.GENETIC_ALGORITHM,
                requested_by=user,
                seed=entry["seed"],
                configuration=configuration,
            )
            run_ids.append(run.pk)
            if options["mode"] == "direct":
                execute_run(run.pk)
            else:
                queue_run(run)
        return run_ids

    def _selection_payload(self, snapshot: models.ProblemSnapshot) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        runs = snapshot.runs.filter(
            algorithm=models.SolverAlgorithm.GENETIC_ALGORITHM,
            configuration__research_phase="GA_SYNTHETIC_TUNING",
        )
        for run in runs:
            if not run.is_terminal:
                continue
            rows.append(
                {
                    "configuration_id": run.configuration.get(
                        "ga_tuning_configuration_id", ""
                    ),
                    "feasible": run.status in {
                        models.RunStatus.FEASIBLE,
                        models.RunStatus.OPTIMAL,
                    },
                    "raw_soft_penalty": run.objective_value,
                    "execution_seconds": run.execution_seconds,
                }
            )
        try:
            return select_ga_tuning_configuration(rows)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
