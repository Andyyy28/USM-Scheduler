"""Create, execute, validate, and persist optimization runs."""

from __future__ import annotations

import math
import os
import platform
import socket
import sys
import uuid
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from importlib import metadata as package_metadata
from time import perf_counter, process_time
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from scheduler import models
from scheduler.domain import (
    ProblemInstance,
    SolverConfig,
    SolverResult,
    SolverStatus,
    score_schedule,
    validate_schedule,
)
from scheduler.domain import SolverAlgorithm as DomainAlgorithm
from scheduler.services.problem_builder import load_problem
from scheduler.solvers import CpSatSolver, GeneticAlgorithmSolver

DEFAULT_INFRASTRUCTURE_GRACE_SECONDS = 60
DEFAULT_LEASE_RECONCILIATION_LIMIT = 500
_RUNTIME_DISTRIBUTIONS = (
    "Django",
    "djangorestframework",
    "celery",
    "redis",
    "ortools",
    "openpyxl",
    "pandas",
    "scipy",
    "psycopg",
)


class RunClaimBusy(RuntimeError):
    """Raised when another live worker owns a schedule-run lease."""

    def __init__(self, run_id: int, lease_expires_at: Any) -> None:
        self.run_id = run_id
        self.lease_expires_at = lease_expires_at
        remaining = 1.0
        if lease_expires_at is not None:
            remaining = max(
                1.0,
                (lease_expires_at - timezone.now()).total_seconds(),
            )
        self.retry_after_seconds = min(60, max(1, math.ceil(remaining)))
        super().__init__(
            f"Run {run_id} is already claimed until "
            f"{lease_expires_at.isoformat() if lease_expires_at else 'its active lease ends'}."
        )


def domain_algorithm(value: str) -> DomainAlgorithm:
    if value == models.SolverAlgorithm.CP_SAT:
        return DomainAlgorithm.CP_SAT
    if value == models.SolverAlgorithm.GENETIC_ALGORITHM:
        return DomainAlgorithm.GENETIC_ALGORITHM
    raise ValueError(f"Unsupported algorithm: {value}")


def model_status(result: SolverResult) -> str:
    mapping = {
        SolverStatus.OPTIMAL: models.RunStatus.OPTIMAL,
        SolverStatus.FEASIBLE: models.RunStatus.FEASIBLE,
        SolverStatus.INFEASIBLE: models.RunStatus.INFEASIBLE,
        SolverStatus.CANCELLED: models.RunStatus.CANCELLED,
        SolverStatus.ERROR: models.RunStatus.FAILED,
    }
    if result.status in mapping:
        return mapping[result.status]
    if result.status in {SolverStatus.NO_SOLUTION, SolverStatus.UNKNOWN}:
        return (
            models.RunStatus.TIMEOUT
            if "time" in result.stopping_reason.lower()
            else models.RunStatus.NO_SOLUTION
        )
    return models.RunStatus.FAILED


def build_solver_config(run: models.ScheduleRun) -> SolverConfig:
    values = {
        "algorithm": domain_algorithm(run.algorithm),
        "seed": run.seed,
        "time_limit_seconds": float(
            run.configuration.get(
                "time_limit_seconds",
                getattr(settings, "SOLVER_DEFAULT_TIME_LIMIT_SECONDS", 300),
            )
        ),
        "worker_count": int(run.configuration.get("worker_count", 1)),
        "population_size": int(run.configuration.get("population_size", 200)),
        "tournament_size": int(run.configuration.get("tournament_size", 3)),
        "crossover_rate": float(run.configuration.get("crossover_rate", 0.9)),
        "mutation_rate": run.configuration.get("mutation_rate"),
        "elite_fraction": float(run.configuration.get("elite_fraction", 0.05)),
        "repair_attempts": int(run.configuration.get("repair_attempts", 20)),
        "max_generations": run.configuration.get("max_generations"),
        "cp_model_presolve": run.configuration.get("cp_model_presolve", True),
        "linearization_level": int(run.configuration.get("linearization_level", 2)),
        "diagnostic_trace": run.configuration.get("diagnostic_trace", False),
    }
    if values["mutation_rate"] is not None:
        values["mutation_rate"] = float(values["mutation_rate"])
    if values["max_generations"] is not None:
        values["max_generations"] = int(values["max_generations"])
    return SolverConfig(**values)


def run_configuration_hash(*, algorithm: str, seed: int, configuration: dict[str, Any]) -> str:
    """Hash the complete immutable run configuration, including its seed."""

    return models.canonical_sha256({"algorithm": algorithm, "seed": seed, **dict(configuration)})


def _dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for distribution in _RUNTIME_DISTRIBUTIONS:
        try:
            versions[distribution] = package_metadata.version(distribution)
        except package_metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions


def _worker_provenance(task_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Capture the process that actually executed a run, not the web dispatcher."""

    host = socket.gethostname()
    process_id = os.getpid()
    dependencies = _dependency_versions()
    manifest = {
        "schema_version": "1.0",
        "build": {
            "app_build_id": os.getenv("APP_BUILD_ID") or None,
            "source_commit": os.getenv("SOURCE_COMMIT") or None,
            "container_image_id": os.getenv("CONTAINER_IMAGE_ID") or None,
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "logical_cpu_count": os.cpu_count(),
        "host_identity": host,
        "process_identity": str(process_id),
        "dependencies": dependencies,
        "task": dict(task_context or {}),
    }
    manifest["manifest_hash"] = models.canonical_sha256(manifest)
    return manifest


def _peak_resident_memory_mb() -> float | None:
    """Return process peak RSS on Linux/macOS and Windows without a new dependency."""

    try:
        import resource

        raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # Linux reports KiB; macOS and the BSD Python build report bytes.
        divisor = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
        return max(0.0, raw / divisor)
    except (ImportError, OSError, ValueError):
        pass

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        if ok:
            return max(0.0, counters.PeakWorkingSetSize / (1024.0 * 1024.0))
    except (AttributeError, OSError, ValueError):
        return None
    return None


def _infrastructure_grace_seconds(run: models.ScheduleRun) -> int:
    value = run.configuration.get(
        "infrastructure_grace_seconds",
        getattr(
            settings,
            "SCHEDULE_RUN_INFRASTRUCTURE_GRACE_SECONDS",
            DEFAULT_INFRASTRUCTURE_GRACE_SECONDS,
        ),
    )
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return DEFAULT_INFRASTRUCTURE_GRACE_SECONDS


def task_time_limit_seconds(run: models.ScheduleRun) -> int:
    """Infrastructure ceiling: solver deadline plus its frozen grace window."""

    deadline = math.ceil(build_solver_config(run).time_limit_seconds)
    return deadline + _infrastructure_grace_seconds(run)


def _legacy_lease_cutoff(now: Any) -> Any:
    """Bound old RUNNING rows created before leases were introduced.

    The configured Celery ceiling is the longest task the worker will permit.
    A RUNNING row without a lease and older than that ceiling cannot represent
    a live supported execution, so reconciliation may safely terminalize it.
    """

    try:
        ceiling_seconds = max(1, int(getattr(settings, "CELERY_TASK_TIME_LIMIT", 1860)))
    except (TypeError, ValueError):
        ceiling_seconds = 1860
    return now - timedelta(seconds=ceiling_seconds)


def _run_lease_is_stale(run: models.ScheduleRun, *, now: Any) -> bool:
    if run.lease_expires_at is not None:
        return run.lease_expires_at <= now
    reference = run.heartbeat_at or run.started_at or run.queued_at
    return reference <= _legacy_lease_cutoff(now)


@transaction.atomic
def create_run(
    *,
    snapshot: models.ProblemSnapshot,
    algorithm: str,
    requested_by: models.User,
    seed: int = 0,
    configuration: dict[str, Any] | None = None,
    experiment_batch: models.ExperimentBatch | None = None,
    purpose: str = models.RunPurpose.ROUTINE,
    included_in_analysis: bool | None = None,
    exclusion_reason: str = "",
) -> models.ScheduleRun:
    if algorithm not in models.SolverAlgorithm.values:
        raise ValueError(f"Unsupported algorithm: {algorithm}")
    if purpose not in models.RunPurpose.values:
        raise ValueError(f"Unsupported run purpose: {purpose}")
    if included_in_analysis is None:
        included_in_analysis = purpose == models.RunPurpose.ROUTINE
    exclusion_reason = str(exclusion_reason).strip()
    if not included_in_analysis and not exclusion_reason:
        raise ValueError("Excluded runs require an explicit exclusion reason.")
    config = dict(configuration or {})
    parent_schedule_id = config.get("parent_schedule_id")
    if parent_schedule_id not in (None, ""):
        parent = models.ScheduleVersion.objects.filter(pk=parent_schedule_id).first()
        if parent is None:
            raise ValueError("The requested parent schedule does not exist.")
        if parent.revision_id != snapshot.revision_id:
            raise ValueError("A regeneration parent must use the snapshot's dataset revision.")
        config["parent_schedule_id"] = parent.pk
    run = models.ScheduleRun(
        snapshot=snapshot,
        experiment_batch=experiment_batch,
        algorithm=algorithm,
        seed=seed,
        purpose=purpose,
        included_in_analysis=included_in_analysis,
        exclusion_reason=exclusion_reason[:255],
        configuration=config,
        configuration_hash=run_configuration_hash(
            algorithm=algorithm,
            seed=seed,
            configuration=config,
        ),
        requested_by=requested_by,
    )
    parsed_config = build_solver_config(run)
    if parsed_config.algorithm == DomainAlgorithm.GENETIC_ALGORITHM and parsed_config.worker_count != 1:
        raise ValueError("The Genetic Algorithm is single-threaded and requires worker_count=1.")
    run.full_clean()
    run.save()
    models.AuditLog.objects.create(
        actor=requested_by,
        action="run.created",
        entity_type="ScheduleRun",
        entity_id=str(run.pk),
        details={
            "snapshot_id": snapshot.pk,
            "snapshot_hash": snapshot.snapshot_hash,
            "algorithm": algorithm,
            "seed": seed,
            "purpose": purpose,
            "included_in_analysis": included_in_analysis,
            "exclusion_reason": exclusion_reason[:255],
            "experiment_batch_id": experiment_batch.pk if experiment_batch else None,
        },
    )
    return run


def queue_run(run: models.ScheduleRun) -> models.ScheduleRun:
    from scheduler.tasks import execute_schedule_run

    run.refresh_from_db()
    if run.status != models.RunStatus.QUEUED:
        raise ValueError("Only a queued run can be submitted to the worker.")
    if run.task_id:
        return run
    task_id = str(run.dispatch_key)
    claimed = models.ScheduleRun.objects.filter(
        pk=run.pk,
        status=models.RunStatus.QUEUED,
        task_id="",
    ).update(task_id=task_id)
    if not claimed:
        run.refresh_from_db()
        return run
    options: dict[str, Any] = {
        "task_id": task_id,
        "time_limit": task_time_limit_seconds(run),
    }
    queue_name = str(run.configuration.get("benchmark_queue", "")).strip()
    if queue_name:
        options["queue"] = queue_name
    try:
        execute_schedule_run.apply_async(args=[run.pk], **options)
    except Exception:
        models.ScheduleRun.objects.filter(
            pk=run.pk,
            status=models.RunStatus.QUEUED,
            task_id=task_id,
        ).update(task_id="")
        raise
    run.refresh_from_db()
    return run


def _solver_for(algorithm: DomainAlgorithm):
    if algorithm == DomainAlgorithm.CP_SAT:
        return CpSatSolver()
    return GeneticAlgorithmSolver()


def _verify_solver_result(
    problem: ProblemInstance,
    config: SolverConfig,
    result: SolverResult,
) -> SolverResult:
    """Reconstruct solver claims at the service boundary.

    A rejected result remains persistable as failed diagnostic evidence, but
    its untrusted validation/objective values can never promote a schedule.
    """

    independent_report = validate_schedule(problem, result.assignments)
    reconstructed_objective = None
    try:
        if result.assignments:
            reconstructed_objective = score_schedule(problem, result.assignments)
    except ValueError:
        # Empty, partial, duplicate, or invalid assignments have no complete
        # objective reconstruction.  The validation report above explains why.
        reconstructed_objective = None

    mismatches: list[str] = []
    if result.problem_hash != problem.canonical_hash:
        mismatches.append("problem hash")
    if result.config_hash != config.canonical_hash:
        mismatches.append("resolved configuration hash")
    if result.algorithm != config.algorithm:
        mismatches.append("algorithm")
    if result.seed != config.seed:
        mismatches.append("seed")
    if result.validation.feasible != independent_report.feasible:
        mismatches.append("feasibility")
    claimed_success = result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    if claimed_success != independent_report.feasible:
        mismatches.append("solver status feasibility claim")
    if result.objective != reconstructed_objective:
        mismatches.append("full objective breakdown")

    reserved_prefixes = ("service_verification_", "reported_")
    metrics = tuple((name, value) for name, value in result.metrics if not name.startswith(reserved_prefixes))
    metrics += (
        ("service_verification_performed", 1),
        ("service_verification_passed", int(not mismatches)),
        ("service_verification_mismatch_count", len(mismatches)),
    )
    status = result.status
    stopping_reason = result.stopping_reason
    if mismatches:
        reported_objective = result.objective
        metrics += (
            ("reported_status", result.status.value),
            ("reported_problem_hash", result.problem_hash),
            ("reported_config_hash", result.config_hash),
            ("reported_validation_feasible", result.validation.feasible),
        )
        if reported_objective is not None:
            metrics += tuple(
                (f"reported_objective_{name}", value) for name, value in reported_objective.to_dict().items()
            )
        status = SolverStatus.ERROR
        stopping_reason = "Service verification rejected solver result: " + ", ".join(mismatches) + "."

    # Objective values are decision evidence only for independently feasible
    # schedules.  Infeasible complete chromosomes may still have been scored
    # above solely to verify the solver's reported breakdown.
    trusted_objective = reconstructed_objective if independent_report.feasible else None
    return replace(
        result,
        status=status,
        validation=independent_report,
        objective=trusted_objective,
        stopping_reason=stopping_reason,
        metrics=metrics,
    )


def _expire_locked_run(run: models.ScheduleRun, *, now: Any) -> models.ScheduleRun:
    message = (
        "Worker lease expired before the run reached a terminal state; "
        "audited failure classification is required."
    )
    models.ScheduleRun.objects.filter(
        pk=run.pk,
        status=models.RunStatus.RUNNING,
    ).update(
        status=models.RunStatus.FAILED,
        finished_at=now,
        heartbeat_at=now,
        lease_expires_at=None,
        stopping_reason="Worker lease expired",
        error_message=message,
        failure_category=models.FailureCategory.UNCLASSIFIED,
        updated_at=now,
    )
    models.AuditLog.objects.create(
        actor=None,
        action="run.lease_expired",
        entity_type="ScheduleRun",
        entity_id=str(run.pk),
        details={
            "previous_claim_token": str(run.claim_token) if run.claim_token else None,
            "previous_lease_expires_at": (run.lease_expires_at.isoformat() if run.lease_expires_at else None),
            "classification": models.FailureCategory.UNCLASSIFIED,
        },
    )
    run.refresh_from_db()
    run._claim_acquired = False
    return run


@transaction.atomic
def _mark_started(
    run_id: int,
    *,
    task_context: dict[str, Any] | None = None,
) -> models.ScheduleRun:
    """Atomically acquire a time-bounded lease for one queued run."""

    run = (
        models.ScheduleRun.objects.select_for_update(of=("self",))
        .select_related("snapshot", "experiment_batch")
        .get(pk=run_id)
    )
    now = timezone.now()
    if run.is_terminal:
        run._claim_acquired = False
        return run
    if run.status == models.RunStatus.RUNNING:
        if run.lease_expires_at and run.lease_expires_at <= now:
            return _expire_locked_run(run, now=now)
        raise RunClaimBusy(run_id, run.lease_expires_at)
    if run.status != models.RunStatus.QUEUED:
        raise ValueError(f"Run {run_id} is not queued (status={run.status}).")

    task_id = str((task_context or {}).get("task_id") or "")
    if task_id and run.task_id and task_id != run.task_id:
        raise ValueError(f"Run {run_id} was dispatched under a different task identity.")

    actual_order = run.actual_order
    if run.experiment_batch_id and actual_order is None:
        # The parent-row lock serializes order assignment even if deployment is
        # accidentally configured with more than one consumer.
        batch = (
            models.ExperimentBatch.objects.select_for_update(of=("self",))
            .select_related("study")
            .get(pk=run.experiment_batch_id)
        )
        if batch.status in {
            models.ExperimentStatus.DRAFT,
            models.ExperimentStatus.QUEUED,
        }:
            models.ExperimentBatch.objects.filter(pk=batch.pk).update(
                status=models.ExperimentStatus.RUNNING,
                updated_at=now,
            )
        if (
            batch.study_id
            and batch.study.is_formal
            and batch.study.status == models.StudyStatus.QUEUED
        ):
            models.ExperimentStudy.objects.filter(pk=batch.study_id).update(
                status=models.StudyStatus.RUNNING,
                updated_at=now,
            )
        last_order = (
            models.ScheduleRun.objects.filter(experiment_batch_id=run.experiment_batch_id).aggregate(
                value=Max("actual_order")
            )["value"]
            or 0
        )
        actual_order = last_order + 1

    provenance = _worker_provenance(task_context)
    claim_token = uuid.uuid4()
    configuration_hash = run.configuration_hash or run_configuration_hash(
        algorithm=run.algorithm,
        seed=run.seed,
        configuration=run.configuration,
    )
    run.status = models.RunStatus.RUNNING
    run.claim_token = claim_token
    run.lease_expires_at = now + timedelta(seconds=task_time_limit_seconds(run))
    run.heartbeat_at = now
    run.actual_order = actual_order
    run.started_at = now
    run.finished_at = None
    run.error_message = ""
    run.configuration_hash = configuration_hash
    run.source_commit = str(provenance["build"].get("source_commit") or "")[:64]
    run.container_image = str(provenance["build"].get("container_image_id") or "")[:255]
    run.dependency_versions = provenance["dependencies"]
    run.host_identity = str(provenance["host_identity"])[:255]
    run.process_identity = str(provenance["process_identity"])[:255]
    run.worker_manifest = provenance
    run.save(
        update_fields=[
            "status",
            "claim_token",
            "lease_expires_at",
            "heartbeat_at",
            "actual_order",
            "started_at",
            "finished_at",
            "error_message",
            "configuration_hash",
            "source_commit",
            "container_image",
            "dependency_versions",
            "host_identity",
            "process_identity",
            "worker_manifest",
            "updated_at",
        ]
    )
    run._claim_acquired = True
    return run


def execute_run(
    run_id: int,
    *,
    task_context: dict[str, Any] | None = None,
) -> models.ScheduleRun:
    task_started = perf_counter()
    process_started = process_time()
    run = _mark_started(run_id, task_context=task_context)
    if not getattr(run, "_claim_acquired", False):
        return run
    claim_token = run.claim_token
    try:
        problem = load_problem(run.snapshot)
        config = build_solver_config(run)
        result = _solver_for(config.algorithm).solve(problem, config)
        process_cpu_seconds = max(0.0, process_time() - process_started)
        peak_rss_mb = _peak_resident_memory_mb()
        models.ScheduleRun.objects.filter(
            pk=run_id,
            status=models.RunStatus.RUNNING,
            claim_token=claim_token,
        ).update(heartbeat_at=timezone.now())
        persisted = persist_result(
            run_id,
            result,
            claim_token=claim_token,
            process_cpu_seconds=process_cpu_seconds,
            peak_rss_mb=peak_rss_mb,
            task_started_at=task_started,
        )
        return persisted
    except Exception as exc:
        process_cpu_seconds = max(0.0, process_time() - process_started)
        peak_rss_mb = _peak_resident_memory_mb()
        failed_at = timezone.now()
        # A user may cancel while an in-process solver is unwinding. Preserve
        # that terminal decision instead of replacing it with FAILED.
        failed = models.ScheduleRun.objects.filter(
            pk=run_id,
            status=models.RunStatus.RUNNING,
            claim_token=claim_token,
        ).update(
            status=models.RunStatus.FAILED,
            finished_at=failed_at,
            heartbeat_at=failed_at,
            lease_expires_at=None,
            process_cpu_seconds=process_cpu_seconds,
            peak_rss_mb=peak_rss_mb,
            stopping_reason="Unhandled solver error",
            error_message=f"{type(exc).__name__}: {exc}",
            failure_category=models.FailureCategory.UNCLASSIFIED,
            updated_at=failed_at,
        )
        if failed:
            models.AuditLog.objects.create(
                actor=run.requested_by,
                action="run.execution_failed",
                entity_type="ScheduleRun",
                entity_id=str(run_id),
                details={
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                    "claim_token": str(claim_token) if claim_token else None,
                    "failure_category": models.FailureCategory.UNCLASSIFIED,
                },
            )
        raise


def stale_run_ids(*, at: Any | None = None, limit: int = DEFAULT_LEASE_RECONCILIATION_LIMIT) -> list[int]:
    """Return bounded stale lease candidates for monitoring or a dry run.

    Rows without a lease are included only after the global Celery execution
    ceiling. This covers legacy/inconsistent RUNNING rows without treating a
    recently-started migration-era task as lost.
    """

    if limit <= 0:
        raise ValueError("limit must be positive")
    now = at or timezone.now()
    legacy_cutoff = _legacy_lease_cutoff(now)
    return list(
        models.ScheduleRun.objects.filter(
            status=models.RunStatus.RUNNING,
        )
        .filter(
            Q(lease_expires_at__lte=now)
            | Q(
                lease_expires_at__isnull=True,
                heartbeat_at__lte=legacy_cutoff,
            )
            | Q(
                lease_expires_at__isnull=True,
                heartbeat_at__isnull=True,
                started_at__lte=legacy_cutoff,
            )
            | Q(
                lease_expires_at__isnull=True,
                heartbeat_at__isnull=True,
                started_at__isnull=True,
                queued_at__lte=legacy_cutoff,
            )
        )
        .order_by("lease_expires_at", "started_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )


def refresh_run_containers(run_id: int) -> None:
    """Refresh batch and formal-study lifecycle after every worker outcome."""

    run = models.ScheduleRun.objects.select_related("experiment_batch__study").filter(pk=run_id).first()
    if run is None or not run.experiment_batch_id:
        return

    from scheduler.services.experiments import refresh_experiment_status

    batch = refresh_experiment_status(run.experiment_batch)
    study = batch.study
    if study is None or not study.is_formal:
        return

    terminal_statuses = set(models.RunStatus.values) - {
        models.RunStatus.QUEUED,
        models.RunStatus.RUNNING,
    }
    now = timezone.now()
    with transaction.atomic():
        locked_study = models.ExperimentStudy.objects.select_for_update().get(pk=study.pk)
        if locked_study.status in {
            models.StudyStatus.INVALID,
            models.StudyStatus.CANCELLED,
        }:
            return

        for formal_batch in locked_study.batches.select_for_update().all():
            rows = list(
                formal_batch.runs.values(
                    "status",
                    "purpose",
                    "included_in_analysis",
                    "failure_category",
                )
            )
            statuses = [row["status"] for row in rows]
            if rows and all(status in terminal_statuses for status in statuses):
                blocking_failure = any(
                    row["purpose"] == models.RunPurpose.MEASURED
                    and row["included_in_analysis"]
                    and row["status"] in {models.RunStatus.FAILED, models.RunStatus.CANCELLED}
                    and row["failure_category"]
                    in {
                        "",
                        models.FailureCategory.UNCLASSIFIED,
                        models.FailureCategory.INFRASTRUCTURE,
                        models.FailureCategory.USER_CANCELLATION,
                    }
                    for row in rows
                )
                new_batch_status = (
                    models.ExperimentStatus.FAILED if blocking_failure else models.ExperimentStatus.COMPLETED
                )
            elif any(status == models.RunStatus.RUNNING for status in statuses) or any(
                status in terminal_statuses for status in statuses
            ):
                new_batch_status = models.ExperimentStatus.RUNNING
            else:
                new_batch_status = models.ExperimentStatus.QUEUED
            if formal_batch.status != new_batch_status:
                models.ExperimentBatch.objects.filter(pk=formal_batch.pk).update(
                    status=new_batch_status,
                    updated_at=now,
                )

        all_rows = list(
            models.ScheduleRun.objects.filter(experiment_batch__study=locked_study).values(
                "status",
                "purpose",
                "included_in_analysis",
                "failure_category",
            )
        )
        statuses = [row["status"] for row in all_rows]
        if all_rows and all(status in terminal_statuses for status in statuses):
            blocking_failure = any(
                row["purpose"] == models.RunPurpose.MEASURED
                and row["included_in_analysis"]
                and row["status"] in {models.RunStatus.FAILED, models.RunStatus.CANCELLED}
                and row["failure_category"]
                in {
                    "",
                    models.FailureCategory.UNCLASSIFIED,
                    models.FailureCategory.INFRASTRUCTURE,
                    models.FailureCategory.USER_CANCELLATION,
                }
                for row in all_rows
            )
            new_study_status = models.StudyStatus.FAILED if blocking_failure else models.StudyStatus.COMPLETED
        elif any(status == models.RunStatus.RUNNING for status in statuses) or any(
            status in terminal_statuses for status in statuses
        ):
            new_study_status = models.StudyStatus.RUNNING
        else:
            new_study_status = models.StudyStatus.QUEUED

        # A dispatch failure remains visible until a task actually progresses.
        if locked_study.status == models.StudyStatus.FAILED and not any(
            status != models.RunStatus.QUEUED for status in statuses
        ):
            return
        if locked_study.status != new_study_status:
            models.ExperimentStudy.objects.filter(pk=locked_study.pk).update(
                status=new_study_status,
                updated_at=now,
            )


def reconcile_stale_runs(
    *,
    at: Any | None = None,
    limit: int = DEFAULT_LEASE_RECONCILIATION_LIMIT,
) -> list[int]:
    """Terminalize expired worker claims so no run remains stranded as RUNNING.

    Reconciliation deliberately records ``UNCLASSIFIED`` instead of guessing
    that a timeout was an infrastructure failure. Formal evidence can only be
    excluded after the central scheduler performs the existing audited
    classification and paired-replacement workflow.
    """

    now = at or timezone.now()
    candidates = stale_run_ids(at=now, limit=limit)
    reconciled: list[int] = []
    for run_id in candidates:
        with transaction.atomic():
            run = models.ScheduleRun.objects.select_for_update().get(pk=run_id)
            if run.status != models.RunStatus.RUNNING or not _run_lease_is_stale(run, now=now):
                continue
            _expire_locked_run(run, now=now)
            reconciled.append(run_id)
    for run_id in reconciled:
        refresh_run_containers(run_id)
    return reconciled


@transaction.atomic
def persist_result(
    run_id: int,
    result: SolverResult,
    *,
    claim_token: uuid.UUID | None = None,
    process_cpu_seconds: float | None = None,
    peak_rss_mb: float | None = None,
    task_started_at: float | None = None,
) -> models.ScheduleRun:
    run = (
        models.ScheduleRun.objects.select_for_update(of=("self",))
        .select_related("snapshot__revision__term", "requested_by")
        .get(pk=run_id)
    )
    if run.status == models.RunStatus.CANCELLED:
        return run
    if run.status != models.RunStatus.RUNNING:
        raise ValueError(f"Run {run_id} cannot accept a result while status={run.status}.")
    if claim_token is not None and run.claim_token != claim_token:
        raise ValueError(f"Run {run_id} is owned by a different worker claim.")
    problem = load_problem(run.snapshot)
    config = build_solver_config(run)
    validation_started = perf_counter()
    result = _verify_solver_result(problem, config, result)
    validation_seconds = perf_counter() - validation_started
    metrics = tuple(
        (name, value)
        for name, value in result.metrics
        if name not in {"independent_validation_seconds", "shared_preprocessing_seconds"}
    ) + (
        ("independent_validation_seconds", validation_seconds),
        ("shared_preprocessing_seconds", run.snapshot.preprocessing_seconds),
    )
    result = replace(result, metrics=metrics)
    run.status = model_status(result)
    run.finished_at = timezone.now()
    run.heartbeat_at = run.finished_at
    run.lease_expires_at = None
    run.execution_seconds = result.runtime_seconds
    run.process_cpu_seconds = process_cpu_seconds
    run.peak_rss_mb = peak_rss_mb
    run.first_feasible_seconds = result.first_feasible_seconds
    run.objective_value = result.objective.weighted_total if result.objective else None
    metrics = dict(result.metrics)
    run.best_bound = _finite_float(metrics.get("best_objective_bound"))
    run.relative_gap = _finite_float(metrics.get("relative_gap"))
    validation_evaluated = bool(result.assignments)
    validation_payload = (
        result.validation.to_dict()
        if validation_evaluated
        else {
            "feasible": False,
            "evaluated": False,
            "reason": "No candidate schedule was returned for hard-constraint validation.",
            "violations": [],
            "counts": {},
        }
    )
    run.hard_violation_count = result.validation.hard_violation_count if validation_evaluated else 0
    run.stopping_reason = result.stopping_reason[:255]
    run.diagnostics = {
        "problem_hash": result.problem_hash,
        "config_hash": result.config_hash,
        "metrics": metrics,
    }
    run.result_data = {**result.to_dict(), "validation": validation_payload}
    run.error_message = ""
    if not run.configuration_hash:
        run.configuration_hash = run_configuration_hash(
            algorithm=run.algorithm,
            seed=run.seed,
            configuration=run.configuration,
        )
    run.full_clean()
    run.save()

    validation_defaults = {
        "is_feasible": result.validation.feasible if validation_evaluated else False,
        "hard_violation_count": run.hard_violation_count,
        "violations": validation_payload,
        "raw_soft_penalty": result.objective.weighted_total if result.objective else 0,
        "objective_breakdown": result.objective.to_dict() if result.objective else {},
        "normalized_quality_score": result.objective.quality_score if result.objective else None,
        "validator_version": "1.1",
    }
    models.ValidationResult.objects.update_or_create(run=run, defaults=validation_defaults)
    models.RunMetric.objects.filter(run=run).delete()
    metric_rows = []
    for name, value in result.metrics:
        numeric = _finite_decimal(value)
        if numeric is not None:
            metric_rows.append(models.RunMetric(run=run, name=name, value=numeric))
    if result.first_feasible_seconds is not None:
        metric_rows.append(
            models.RunMetric(
                run=run,
                name="first_feasible_seconds",
                value=Decimal(str(result.first_feasible_seconds)),
                unit="seconds",
            )
        )
    if process_cpu_seconds is not None:
        metric_rows.append(
            models.RunMetric(
                run=run,
                name="process_cpu_seconds",
                value=Decimal(str(process_cpu_seconds)),
                unit="seconds",
            )
        )
    if peak_rss_mb is not None:
        metric_rows.append(
            models.RunMetric(
                run=run,
                name="peak_rss_mb",
                value=Decimal(str(peak_rss_mb)),
                unit="MiB",
            )
        )
    if result.objective:
        metric_rows.extend(
            [
                models.RunMetric(
                    run=run,
                    name="soft_penalty",
                    value=Decimal(result.objective.weighted_total),
                    unit="penalty",
                ),
                models.RunMetric(
                    run=run,
                    name="quality_score",
                    value=Decimal(str(result.objective.quality_score)),
                    unit="score",
                ),
            ]
        )
    # Metric names emitted by solvers are unique; guard against our derived aliases.
    unique_rows = {row.name: row for row in metric_rows}
    models.RunMetric.objects.bulk_create(unique_rows.values())

    if (
        result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
        and result.validation.feasible
        and result.assignments
        and run.configuration.get("persist_schedule", True)
    ):
        _create_schedule_version(run, result, validation_defaults)
    models.AuditLog.objects.create(
        actor=run.requested_by,
        action="run.completed",
        entity_type="ScheduleRun",
        entity_id=str(run.pk),
        details={
            "status": run.status,
            "hard_violation_count": run.hard_violation_count,
            "objective_value": run.objective_value,
            "problem_hash": result.problem_hash,
            "config_hash": result.config_hash,
        },
    )
    if task_started_at is not None:
        models.RunMetric.objects.update_or_create(
            run=run,
            name="end_to_end_processing_seconds",
            defaults={
                "value": Decimal(str(max(0.0, perf_counter() - task_started_at))),
                "unit": "seconds",
            },
        )
    return run


def _create_schedule_version(
    run: models.ScheduleRun,
    result: SolverResult,
    validation_defaults: dict[str, Any],
) -> models.ScheduleVersion:
    revision = run.snapshot.revision
    next_version = (
        models.ScheduleVersion.objects.filter(term=revision.term).aggregate(Max("version_number"))[
            "version_number__max"
        ]
        or 0
    ) + 1
    source = (
        models.ScheduleSource.CP_SAT
        if run.algorithm == models.SolverAlgorithm.CP_SAT
        else models.ScheduleSource.GENETIC_ALGORITHM
    )
    parent = None
    parent_id = run.configuration.get("parent_schedule_id")
    if parent_id:
        parent = models.ScheduleVersion.objects.filter(
            pk=parent_id,
            term=revision.term,
            revision=revision,
        ).first()
        if parent is None:
            raise ValueError("The regeneration parent no longer matches this dataset revision.")
    schedule = models.ScheduleVersion.objects.create(
        term=revision.term,
        revision=revision,
        snapshot=run.snapshot,
        run=run,
        parent=parent,
        version_number=next_version,
        name=f"{run.get_algorithm_display()} seed {run.seed}",
        source=source,
        status=models.ScheduleStatus.DRAFT,
        objective_value=result.objective.weighted_total if result.objective else None,
        objective_breakdown=result.objective.to_dict() if result.objective else {},
        created_by=run.requested_by,
    )
    problem = load_problem(run.snapshot)
    event_map = problem.event_map
    meeting_by_key = {
        str(meeting.stable_key): meeting
        for meeting in models.MeetingRequirement.objects.filter(
            offering__revision=revision,
            stable_key__in=[assignment.event_id for assignment in result.assignments],
        ).prefetch_related("offering__section_links", "offering__instructor_links")
    }
    slot_ids = {int(atom.atom_id.split(":", 1)[1]): atom for atom in problem.time_atoms}
    slots = models.TimeSlot.objects.in_bulk(slot_ids)

    for assignment in result.assignments:
        meeting = meeting_by_key[assignment.event_id]
        candidate = event_map[assignment.event_id].candidate_map[assignment.candidate_id]
        start_slot_id = int(candidate.start_atom_id.split(":", 1)[1])
        schedule_assignment = models.ScheduleAssignment.objects.create(
            schedule=schedule,
            meeting_requirement=meeting,
            room_id=int(candidate.room_id),
            start_time_slot=slots[start_slot_id],
            placement_data=candidate.to_dict(),
            objective_contribution={"preference_penalty": candidate.preference_penalty},
        )
        occupied_ids = [int(atom.split(":", 1)[1]) for atom in candidate.occupied_atom_ids]
        models.ScheduleRoomAllocation.objects.bulk_create(
            [
                models.ScheduleRoomAllocation(
                    schedule=schedule,
                    assignment=schedule_assignment,
                    room_id=int(candidate.room_id),
                    time_slot=slots[slot_id],
                )
                for slot_id in occupied_ids
            ]
        )
        section_ids = meeting.offering.section_links.values_list("section_id", flat=True)
        models.ScheduleSectionAllocation.objects.bulk_create(
            [
                models.ScheduleSectionAllocation(
                    schedule=schedule,
                    assignment=schedule_assignment,
                    section_id=section_id,
                    time_slot=slots[slot_id],
                )
                for section_id in section_ids
                for slot_id in occupied_ids
            ]
        )
        instructor_ids = meeting.offering.instructor_links.values_list("instructor_id", flat=True)
        models.ScheduleInstructorAllocation.objects.bulk_create(
            [
                models.ScheduleInstructorAllocation(
                    schedule=schedule,
                    assignment=schedule_assignment,
                    instructor_id=instructor_id,
                    time_slot=slots[slot_id],
                )
                for instructor_id in instructor_ids
                for slot_id in occupied_ids
            ]
        )
    models.ValidationResult.objects.create(schedule_version=schedule, **validation_defaults)
    return schedule


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _finite_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return Decimal(str(converted)) if math.isfinite(converted) else None
