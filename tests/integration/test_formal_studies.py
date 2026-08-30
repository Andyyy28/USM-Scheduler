from __future__ import annotations

import json
from io import BytesIO, StringIO
from zipfile import ZipFile

import pytest
from django.core.management import call_command

from scheduler import models
from scheduler.services import formal_studies
from scheduler.services.evidence_bundle import build_study_evidence_bundle
from scheduler.services.formal_studies import (
    FORMAL_MEASURED_RUN_COUNT,
    FORMAL_TOTAL_RUN_COUNT,
    create_formal_study,
    inspect_formal_study,
    validate_formal_study,
    validate_source_snapshot,
)
from scheduler.services.problem_builder import build_and_store_snapshot
from scheduler.services.tuning import (
    SOLVER_TUNING_ARTIFACT_SCHEMA_VERSION,
    SOLVER_TUNING_PROTOCOL_VERSION,
)
from scheduler.solvers.cp_sat import CP_SAT_IMPLEMENTATION_VERSION
from scheduler.solvers.genetic import GA_IMPLEMENTATION_VERSION
from tests.integration.formal_fixtures import persisted_tuning_profiles

pytestmark = pytest.mark.django_db


def _frozen_profile(algorithm: str, plan_hash: str) -> dict[str, object]:
    is_cp_sat = algorithm == models.SolverAlgorithm.CP_SAT
    configuration = {
        "algorithm": algorithm,
        "time_limit_seconds": 60.0,
        "worker_count": 1,
        **(
            {"cp_model_presolve": True, "linearization_level": 2}
            if is_cp_sat
            else {
                "population_size": 200,
                "mutation_rate": 0.01,
                "tournament_size": 3,
                "crossover_rate": 0.9,
                "elite_fraction": 0.05,
                "repair_attempts": 20,
            }
        ),
    }
    payload: dict[str, object] = {
        "artifact_schema_version": SOLVER_TUNING_ARTIFACT_SCHEMA_VERSION,
        "frozen": True,
        "protocol_version": SOLVER_TUNING_PROTOCOL_VERSION,
        "algorithm": algorithm,
        "implementation_version": (
            CP_SAT_IMPLEMENTATION_VERSION if is_cp_sat else GA_IMPLEMENTATION_VERSION
        ),
        "plan_hash": plan_hash,
        "configuration_id": f"{algorithm}-test",
        "configuration": configuration,
        "selection_metrics": {
            "feasibility_rate": 1.0,
            "median_feasible_raw_soft_penalty": 0,
            "rmst_time_to_feasibility_seconds": 1.0,
        },
    }
    return {**payload, "profile_hash": models.canonical_sha256(payload)}


def _ready_formal_study() -> tuple[models.ExperimentStudy, models.User]:
    output = StringIO()
    call_command("seed_demo", stdout=output)
    identifiers = json.loads(output.getvalue().strip().splitlines()[-1])
    actor = models.User.objects.get(pk=identifiers["central_user_id"])
    revision = models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"])
    objective = models.ObjectiveProfile.objects.create(
        name="Infrastructure recovery objective",
        version=7,
        term=revision.term,
        weights=models.default_objective_weights(),
        definitions=models.default_objective_definitions(),
        normalization_denominators={
            "instructor_preference": 10,
            "section_internal_gaps": 20,
            "instructor_internal_gaps": 20,
            "daily_load_imbalance": 40,
        },
        is_approved=True,
        approved_by=actor,
    )
    snapshot, _ = build_and_store_snapshot(revision, objective, actor)
    study = create_formal_study(
        source_snapshot=snapshot,
        actor=actor,
        solver_profiles=persisted_tuning_profiles(snapshot, actor),
    )
    validation = validate_formal_study(study, actor=actor)
    assert validation["valid"] is True
    study.refresh_from_db()
    return study, actor


def test_formal_study_freezes_exact_four_scale_260_run_matrix() -> None:
    output = StringIO()
    call_command("seed_demo", stdout=output)
    identifiers = json.loads(output.getvalue().strip().splitlines()[-1])
    actor = models.User.objects.get(pk=identifiers["central_user_id"])
    revision = models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"])
    objective = models.ObjectiveProfile(
        name="Institutional formal objective",
        version=1,
        term=revision.term,
        weights=models.default_objective_weights(),
        definitions=models.default_objective_definitions(),
        normalization_denominators={
            "instructor_preference": 10,
            "section_internal_gaps": 20,
            "instructor_internal_gaps": 20,
            "daily_load_imbalance": 40,
        },
        is_approved=True,
        approved_by=actor,
    )
    objective.save()
    snapshot, _ = build_and_store_snapshot(revision, objective, actor)
    source_issues = validate_source_snapshot(snapshot)
    assert not source_issues, [issue.to_dict() for issue in source_issues]
    study = create_formal_study(
        source_snapshot=snapshot,
        actor=actor,
        solver_profiles=persisted_tuning_profiles(snapshot, actor),
    )

    assert list(
        study.batches.order_by("planned_scale_percentage").values_list(
            "planned_scale_percentage", flat=True
        )
    ) == [25, 50, 75, 100]
    assert models.ScheduleRun.objects.filter(experiment_batch__study=study).count() == (
        FORMAL_TOTAL_RUN_COUNT
    )
    assert models.ScheduleRun.objects.filter(
        experiment_batch__study=study,
        purpose=models.RunPurpose.MEASURED,
        included_in_analysis=True,
    ).count() == FORMAL_MEASURED_RUN_COUNT
    validation = validate_formal_study(study, actor=actor)
    assert validation["valid"] is True, validation
    study.refresh_from_db()
    inspection = inspect_formal_study(study)
    assert study.status == models.StudyStatus.READY
    assert inspection["counts"]["all_runs"] == FORMAL_TOTAL_RUN_COUNT
    assert inspection["formal_conclusion"]["available"] is False

    first_bundle = build_study_evidence_bundle(study)
    second_bundle = build_study_evidence_bundle(study)
    assert first_bundle == second_bundle
    with ZipFile(BytesIO(first_bundle)) as archive:
        assert {
            "study-manifest.json",
            "instances.csv",
            "trials.csv",
            "summary.json",
            "report.html",
            "checksums.sha256",
            "README.md",
            "figures/feasibility.svg",
            "figures/time-to-feasibility.svg",
            "figures/feasible-penalty.svg",
        } <= set(archive.namelist())
        report = archive.read("report.html").decode("utf-8")
        assert "No formal conclusion available." in report
        assert "seat utilization" not in report.casefold()


def test_partial_broker_publish_fails_closed_without_stranded_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    study, actor = _ready_formal_study()
    published: list[int] = []

    def partial_publish(run: models.ScheduleRun) -> bool:
        if not published:
            models.ScheduleRun.objects.filter(pk=run.pk).update(
                task_id=str(run.dispatch_key)
            )
            published.append(run.pk)
            return True
        raise ConnectionError("synthetic partial broker outage")

    def fail_revoke(*args, **kwargs):
        raise ConnectionError("synthetic revoke outage")

    monkeypatch.setattr(formal_studies, "_dispatch_run_to_benchmark", partial_publish)
    monkeypatch.setattr(formal_studies.current_app.control, "revoke", fail_revoke)

    with pytest.raises(ConnectionError, match="partial broker outage"):
        formal_studies.queue_formal_study(study, actor=actor)

    study.refresh_from_db()
    assert study.status == models.StudyStatus.INVALID
    active = models.ScheduleRun.objects.filter(
        experiment_batch__study=study,
        status__in=[models.RunStatus.QUEUED, models.RunStatus.RUNNING],
    )
    assert not active.exists()
    assert models.ScheduleRun.objects.filter(
        experiment_batch__study=study,
        failure_category=models.FailureCategory.INFRASTRUCTURE,
        included_in_analysis=False,
    ).count() == FORMAL_TOTAL_RUN_COUNT
    audit = models.AuditLog.objects.get(
        action="formal_study.dispatch_failed",
        entity_id=str(study.pk),
    )
    assert audit.details["published_before_failure"] == 1
    assert audit.details["terminalized_run_count"] == FORMAL_TOTAL_RUN_COUNT
    assert audit.details["revoke_failures"][0]["error_type"] == "ConnectionError"


def test_partial_replacement_publish_invalidates_both_replacement_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    study, actor = _ready_formal_study()
    batch = study.batches.order_by("planned_scale_percentage").first()
    replacements = list(batch.runs.order_by("planned_order")[:2])
    published: list[int] = []

    def partial_publish(run: models.ScheduleRun) -> bool:
        if not published:
            models.ScheduleRun.objects.filter(pk=run.pk).update(
                task_id=str(run.dispatch_key)
            )
            published.append(run.pk)
            return True
        raise ConnectionError("synthetic replacement broker outage")

    def fail_revoke(*args, **kwargs):
        raise ConnectionError("synthetic replacement revoke outage")

    monkeypatch.setattr(formal_studies, "_dispatch_run_to_benchmark", partial_publish)
    monkeypatch.setattr(formal_studies.current_app.control, "revoke", fail_revoke)

    formal_studies._dispatch_replacement_pair_after_commit(
        (replacements[0].pk, replacements[1].pk),
        study_id=study.pk,
        actor_id=actor.pk,
    )

    study.refresh_from_db()
    batch.refresh_from_db()
    assert study.status == models.StudyStatus.INVALID
    assert batch.status == models.ExperimentStatus.FAILED
    persisted = list(models.ScheduleRun.objects.filter(pk__in=[run.pk for run in replacements]))
    assert {run.status for run in persisted} == {models.RunStatus.FAILED}
    assert not any(run.included_in_analysis for run in persisted)
    assert not any(run.lease_expires_at for run in persisted)
    audit = models.AuditLog.objects.get(
        action="formal_run.replacement_dispatch_failed",
        entity_id=str(study.pk),
    )
    assert audit.details["dispatched_before_failure"] == 1
    assert audit.details["revoke_failures"][0]["error_type"] == "ConnectionError"


def test_algorithm_failure_classification_refreshes_parent_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks,
) -> None:
    study, actor = _ready_formal_study()
    failed = models.ScheduleRun.objects.filter(
        experiment_batch__study=study,
        purpose=models.RunPurpose.MEASURED,
    ).first()
    models.ScheduleRun.objects.filter(pk=failed.pk).update(
        status=models.RunStatus.FAILED,
        failure_category=models.FailureCategory.UNCLASSIFIED,
        finished_at=failed.created_at,
    )
    failed.refresh_from_db()
    refreshed: list[int] = []

    from scheduler.services import runs as run_services

    monkeypatch.setattr(
        run_services,
        "refresh_run_containers",
        lambda run_id: refreshed.append(run_id),
    )

    with django_capture_on_commit_callbacks(execute=True):
        classified = formal_studies.classify_run_failure(
            failed,
            actor=actor,
            category=models.FailureCategory.ALGORITHM,
            reason="Solver returned an audited algorithm-level error observation.",
        )

    assert classified.failure_category == models.FailureCategory.ALGORITHM
    assert classified.included_in_analysis is True
    assert refreshed == [failed.pk]
