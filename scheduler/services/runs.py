"""Create, execute, validate, and persist optimization runs."""

from __future__ import annotations

import math
from dataclasses import replace
from decimal import Decimal
from time import perf_counter
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from scheduler import models
from scheduler.domain import SolverAlgorithm as DomainAlgorithm
from scheduler.domain import (
    SolverConfig,
    SolverResult,
    SolverStatus,
    score_schedule,
    validate_schedule,
)
from scheduler.services.problem_builder import load_problem
from scheduler.solvers import CpSatSolver, GeneticAlgorithmSolver


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
    }
    if values["mutation_rate"] is not None:
        values["mutation_rate"] = float(values["mutation_rate"])
    if values["max_generations"] is not None:
        values["max_generations"] = int(values["max_generations"])
    return SolverConfig(**values)


@transaction.atomic
def create_run(
    *,
    snapshot: models.ProblemSnapshot,
    algorithm: str,
    requested_by: models.User,
    seed: int = 0,
    configuration: dict[str, Any] | None = None,
    experiment_batch: models.ExperimentBatch | None = None,
) -> models.ScheduleRun:
    if algorithm not in models.SolverAlgorithm.values:
        raise ValueError(f"Unsupported algorithm: {algorithm}")
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
        configuration=config,
        requested_by=requested_by,
    )
    parsed_config = build_solver_config(run)
    if (
        parsed_config.algorithm == DomainAlgorithm.GENETIC_ALGORITHM
        and parsed_config.worker_count != 1
    ):
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
            "experiment_batch_id": experiment_batch.pk if experiment_batch else None,
        },
    )
    return run


def queue_run(run: models.ScheduleRun) -> models.ScheduleRun:
    from scheduler.tasks import execute_schedule_run

    if run.status != models.RunStatus.QUEUED:
        raise ValueError("Only a queued run can be submitted to the worker.")
    result = execute_schedule_run.delay(run.pk)
    models.ScheduleRun.objects.filter(pk=run.pk).update(task_id=result.id or "")
    run.refresh_from_db()
    return run


def _solver_for(algorithm: DomainAlgorithm):
    if algorithm == DomainAlgorithm.CP_SAT:
        return CpSatSolver()
    return GeneticAlgorithmSolver()


@transaction.atomic
def _mark_started(run_id: int) -> models.ScheduleRun:
    run = models.ScheduleRun.objects.select_for_update().select_related("snapshot").get(pk=run_id)
    if run.status == models.RunStatus.CANCELLED:
        return run
    if run.status != models.RunStatus.QUEUED:
        raise ValueError(f"Run {run_id} is not queued (status={run.status}).")
    run.status = models.RunStatus.RUNNING
    run.started_at = timezone.now()
    run.error_message = ""
    run.save(update_fields=["status", "started_at", "error_message", "updated_at"])
    return run


def execute_run(run_id: int) -> models.ScheduleRun:
    task_started = perf_counter()
    run = _mark_started(run_id)
    if run.status == models.RunStatus.CANCELLED:
        return run
    try:
        problem = load_problem(run.snapshot)
        config = build_solver_config(run)
        result = _solver_for(config.algorithm).solve(problem, config)
        validation_started = perf_counter()
        independent_report = validate_schedule(problem, result.assignments)
        independent_objective = (
            score_schedule(problem, result.assignments) if independent_report.feasible else None
        )
        validation_seconds = perf_counter() - validation_started
        claimed_success = result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
        stopping_reason = result.stopping_reason
        status = result.status
        if claimed_success != independent_report.feasible:
            status = SolverStatus.ERROR
            stopping_reason = (
                "Solver status and the independent service-layer validator disagree."
            )
        metrics = tuple(
            (name, value)
            for name, value in result.metrics
            if name not in {"independent_validation_seconds", "shared_preprocessing_seconds"}
        ) + (
            ("independent_validation_seconds", validation_seconds),
            ("shared_preprocessing_seconds", run.snapshot.preprocessing_seconds),
        )
        verified_result = replace(
            result,
            status=status,
            validation=independent_report,
            objective=independent_objective,
            stopping_reason=stopping_reason,
            metrics=metrics,
        )
        persisted = persist_result(run_id, verified_result)
        models.RunMetric.objects.update_or_create(
            run=persisted,
            name="end_to_end_processing_seconds",
            defaults={
                "value": Decimal(str(perf_counter() - task_started)),
                "unit": "seconds",
            },
        )
        return persisted
    except Exception as exc:
        # A user may cancel while an in-process solver is unwinding. Preserve
        # that terminal decision instead of replacing it with FAILED.
        models.ScheduleRun.objects.filter(pk=run_id, status=models.RunStatus.RUNNING).update(
            status=models.RunStatus.FAILED,
            finished_at=timezone.now(),
            stopping_reason="Unhandled solver error",
            error_message=f"{type(exc).__name__}: {exc}",
        )
        raise


@transaction.atomic
def persist_result(run_id: int, result: SolverResult) -> models.ScheduleRun:
    run = (
        models.ScheduleRun.objects.select_for_update()
        .select_related("snapshot__revision__term", "requested_by")
        .get(pk=run_id)
    )
    if run.status == models.RunStatus.CANCELLED:
        return run
    if run.status != models.RunStatus.RUNNING:
        raise ValueError(f"Run {run_id} cannot accept a result while status={run.status}.")
    if result.problem_hash != load_problem(run.snapshot).canonical_hash:
        raise ValueError("Solver result problem hash does not match the stored snapshot.")
    run.status = model_status(result)
    run.finished_at = timezone.now()
    run.execution_seconds = result.runtime_seconds
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
    run.hard_violation_count = (
        result.validation.hard_violation_count if validation_evaluated else 0
    )
    run.stopping_reason = result.stopping_reason[:255]
    run.diagnostics = {
        "problem_hash": result.problem_hash,
        "config_hash": result.config_hash,
        "metrics": metrics,
    }
    run.result_data = {**result.to_dict(), "validation": validation_payload}
    run.error_message = ""
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
        result.validation.feasible
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
    slot_ids = {
        int(atom.atom_id.split(":", 1)[1]): atom
        for atom in problem.time_atoms
    }
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
