"""Plan, launch, or select the equal-budget CP-SAT/GA pilot-tuning grid."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from scheduler import models
from scheduler.domain import SolverConfig
from scheduler.services.runs import create_run, execute_run, queue_run
from scheduler.services.tuning import (
    SOLVER_TUNING_TIME_LIMIT_SECONDS,
    build_solver_tuning_plan,
    select_solver_tuning_profiles,
)

_RESEARCH_PHASE = "SYNTHETIC_EQUAL_BUDGET_TUNING"
_CP_SAT_TUNING_FIELDS = {"cp_model_presolve", "linearization_level"}
_BENCHMARK_QUEUE = "benchmark"
_TUNING_EXCLUSION_REASON = (
    "Excluded synthetic equal-budget tuning pilot; never part of final inference."
)


class Command(BaseCommand):
    help = (
        "Plan or explicitly run the excluded synthetic equal-budget pilot: "
        "six configurations by five seeds for each solver."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("snapshot_id", type=int)
        parser.add_argument(
            "--mode",
            choices=("plan", "direct", "queue", "select"),
            default="plan",
            help="Defaults to plan; direct/queue explicitly launch all 60 pilot runs.",
        )
        parser.add_argument("--user-id", type=int)
        parser.add_argument(
            "--confirm-synthetic",
            action="store_true",
            help="Attest that the pilot snapshot contains synthetic development data only.",
        )
        parser.add_argument("--output", type=Path)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            snapshot = models.ProblemSnapshot.objects.get(pk=options["snapshot_id"])
        except models.ProblemSnapshot.DoesNotExist as exc:
            raise CommandError(
                f"Problem snapshot {options['snapshot_id']} does not exist"
            ) from exc

        plan = build_solver_tuning_plan(snapshot)
        mode = options["mode"]
        if mode == "select":
            payload = self._selection_payload(snapshot, plan)
        else:
            payload = {"dry_run": mode == "plan", **plan}
            if mode in {"direct", "queue"}:
                self._assert_cp_sat_grid_supported()
                payload["created_run_ids"] = self._launch(plan, snapshot, options)

        rendered = json.dumps(payload, indent=2, sort_keys=True)
        if options["output"]:
            options["output"].write_text(rendered + "\n", encoding="utf-8")
        self.stdout.write(rendered)

    def _assert_cp_sat_grid_supported(self) -> None:
        available_fields = set(SolverConfig.__dataclass_fields__)
        missing = sorted(_CP_SAT_TUNING_FIELDS - available_fields)
        if missing:
            raise CommandError(
                "The current solver contract cannot apply the CP-SAT tuning grid; "
                f"missing fields: {', '.join(missing)}. Planning remains available."
            )

    def _launch(
        self,
        plan: dict[str, Any],
        snapshot: models.ProblemSnapshot,
        options: dict[str, Any],
    ) -> list[int]:
        if not options["user_id"]:
            raise CommandError("--user-id is required for direct or queue mode")
        if not options["confirm_synthetic"]:
            raise CommandError("--confirm-synthetic is required; authorized-term data cannot be used for pilot tuning")
        try:
            user = models.User.objects.get(pk=options["user_id"], is_active=True)
        except models.User.DoesNotExist as exc:
            raise CommandError("The active tuning user does not exist") from exc
        if not (
            user.is_superuser
            or user.role
            in {models.UserRole.SYSTEM_ADMIN, models.UserRole.CENTRAL_SCHEDULER}
        ):
            raise CommandError("An active central scheduler is required")

        run_ids = self._persist_plan(plan, snapshot, user)
        for run in models.ScheduleRun.objects.filter(pk__in=run_ids).order_by("pk"):
            if options["mode"] == "direct":
                execute_run(run.pk)
            else:
                queue_run(run)
        return run_ids

    @transaction.atomic
    def _persist_plan(
        self,
        plan: dict[str, Any],
        snapshot: models.ProblemSnapshot,
        user: models.User,
    ) -> list[int]:
        models.ProblemSnapshot.objects.select_for_update().get(pk=snapshot.pk)
        if models.ScheduleRun.objects.filter(
            purpose=models.RunPurpose.TUNING,
            configuration__solver_tuning_plan_hash=plan["plan_hash"],
        ).exists():
            raise CommandError("This pilot plan already has persisted runs; inspect or select it instead of duplicating the budget")
        run_ids: list[int] = []
        for entry in plan["runs"]:
            resolved = entry["resolved_configuration"]
            configuration = {
                key: value
                for key, value in resolved.items()
                if key not in {"algorithm", "seed"}
            }
            configuration.update(
                {
                    "research_phase": _RESEARCH_PHASE,
                    "excluded_from_final_inference": True,
                    "synthetic_data_confirmed": True,
                    "solver_tuning_protocol": plan["protocol_version"],
                    "solver_tuning_plan_hash": plan["plan_hash"],
                    "solver_tuning_configuration_id": entry["configuration_id"],
                    "solver_tuning_resolved_configuration_hash": entry[
                        "resolved_configuration_hash"
                    ],
                    "solver_tuning_order_seed": plan["order_seed"],
                    "solver_tuning_order_position": entry["position"],
                    "environment_manifest_hash": plan["environment_manifest_hash"],
                    "build_hash": plan["build_hash"],
                    "benchmark_queue": _BENCHMARK_QUEUE,
                    "resolved_configuration": resolved,
                    "persist_schedule": False,
                }
            )
            run = create_run(
                snapshot=snapshot,
                algorithm=entry["algorithm"],
                requested_by=user,
                seed=entry["seed"],
                configuration=configuration,
                purpose=models.RunPurpose.TUNING,
                included_in_analysis=False,
                exclusion_reason=_TUNING_EXCLUSION_REASON,
            )
            run_ids.append(run.pk)
        models.AuditLog.objects.create(
            actor=user,
            action="solver_tuning.plan_created",
            entity_type="ProblemSnapshot",
            entity_id=str(snapshot.pk),
            details={"plan": plan, "run_ids": run_ids, "synthetic_data_confirmed": True},
        )
        return run_ids

    def _selection_payload(
        self,
        snapshot: models.ProblemSnapshot,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        expected_runs = {
            (row["algorithm"], row["configuration_id"], row["seed"]): row
            for row in plan["runs"]
        }
        rows: list[dict[str, Any]] = []
        runs = snapshot.runs.filter(
            algorithm__in=(
                models.SolverAlgorithm.CP_SAT,
                models.SolverAlgorithm.GENETIC_ALGORITHM,
            ),
            purpose=models.RunPurpose.TUNING,
            included_in_analysis=False,
            configuration__research_phase=_RESEARCH_PHASE,
            configuration__solver_tuning_protocol=plan["protocol_version"],
            configuration__solver_tuning_plan_hash=plan["plan_hash"],
        )
        for run in runs:
            if not run.is_terminal:
                continue
            configuration_id = run.configuration.get(
                "solver_tuning_configuration_id",
                "",
            )
            expected = expected_runs.get((run.algorithm, configuration_id, run.seed))
            if expected is None:
                raise CommandError(
                    "A persisted tuning run does not belong to the frozen plan matrix."
                )
            diagnostics = run.diagnostics if isinstance(run.diagnostics, dict) else {}
            diagnostic_metrics = diagnostics.get("metrics") or {}
            implementation_version = (
                diagnostic_metrics.get("implementation_version")
                if isinstance(diagnostic_metrics, dict)
                else None
            )
            actual_resolved_configuration = {
                "algorithm": run.algorithm,
                "seed": run.seed,
                "time_limit_seconds": float(
                    run.configuration.get(
                        "time_limit_seconds",
                        SOLVER_TUNING_TIME_LIMIT_SECONDS,
                    )
                ),
                **{
                    key: (
                        implementation_version
                        if key == "implementation_version"
                        else run.configuration.get(key)
                    )
                    for key in expected["solver_configuration"]
                },
            }
            rows.append(
                {
                    "algorithm": run.algorithm,
                    "configuration_id": configuration_id,
                    "seed": run.seed,
                    "terminal": True,
                    "protocol_version": run.configuration.get(
                        "solver_tuning_protocol"
                    ),
                    "implementation_version": implementation_version,
                    "plan_hash": run.configuration.get("solver_tuning_plan_hash"),
                    "resolved_configuration_hash": models.canonical_sha256(
                        actual_resolved_configuration
                    ),
                    "time_limit_seconds": run.configuration.get(
                        "time_limit_seconds"
                    ),
                    "feasible": run.status
                    in {models.RunStatus.FEASIBLE, models.RunStatus.OPTIMAL},
                    "raw_soft_penalty": run.objective_value,
                    "first_feasible_seconds": run.first_feasible_seconds,
                    "execution_seconds": run.execution_seconds,
                }
            )
        try:
            return select_solver_tuning_profiles(rows, plan)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
