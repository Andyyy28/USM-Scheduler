from __future__ import annotations

import hashlib
import json
from io import BytesIO, StringIO
from unittest.mock import patch
from zipfile import ZipFile

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from scheduler import models
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
        "configuration_id": f"{algorithm}-api-test",
        "configuration": configuration,
        "selection_metrics": {
            "feasibility_rate": 1.0,
            "median_feasible_raw_soft_penalty": 0,
            "rmst_time_to_feasibility_seconds": 1.0,
        },
    }
    return {**payload, "profile_hash": models.canonical_sha256(payload)}


def _formal_api_fixture() -> tuple[models.User, models.User, models.ProblemSnapshot, dict]:
    output = StringIO()
    call_command("seed_demo", stdout=output)
    identifiers = json.loads(output.getvalue().strip().splitlines()[-1])
    central = models.User.objects.get(pk=identifiers["central_user_id"])
    reviewer = models.User.objects.get(pk=identifiers["reviewer_user_id"])
    revision = models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"])
    objective = models.ObjectiveProfile(
        name="Approved formal API objective",
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
        approved_by=central,
    )
    objective.save()
    snapshot, _ = build_and_store_snapshot(revision, objective, central)
    profiles = persisted_tuning_profiles(snapshot, central)
    return central, reviewer, snapshot, profiles


def test_formal_study_api_end_to_end_permissions_and_failure_replacement() -> None:
    central, reviewer, snapshot, profiles = _formal_api_fixture()
    client = APIClient()

    assert client.get(reverse("api:formal-studies")).status_code in {401, 403}
    client.force_authenticate(reviewer)
    denied_create = client.post(
        reverse("api:formal-studies"),
        {"source_snapshot_id": snapshot.pk, "solver_profiles": profiles},
        format="json",
    )
    assert denied_create.status_code == 403

    client.force_authenticate(central)
    invalid = client.post(
        reverse("api:formal-studies"),
        {"source_snapshot_id": snapshot.pk, "solver_profiles": {}},
        format="json",
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "FORMAL_STUDY_ERROR"
    assert invalid.json()["issues"][0]["code"] == "INCOMPLETE_TUNING_PROFILES"

    created = client.post(
        reverse("api:formal-studies"),
        {
            "source_snapshot_id": snapshot.pk,
            "name": "API protocol acceptance study",
            "solver_profiles": profiles,
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    payload = created.json()
    study_id = payload["id"]
    assert payload["mode"] == models.ExperimentMode.FORMAL
    assert payload["batch_count"] == 4
    assert payload["run_count"] == 260

    listed = client.get(
        reverse("api:formal-studies"),
        {"source_snapshot_id": snapshot.pk, "status": models.StudyStatus.DRAFT},
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [study_id]

    detail_url = reverse("api:formal-study-detail", args=[study_id])
    client.force_authenticate(reviewer)
    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert detail.json()["counts"]["all_runs"] == 260
    assert detail.json()["formal_conclusion"]["status"] == (
        "NO_FORMAL_CONCLUSION_AVAILABLE"
    )
    analysis = client.get(reverse("api:formal-study-analysis", args=[study_id]))
    assert analysis.status_code == 200
    assert analysis.json()["formal_conclusion"] == "No formal conclusion available."
    assert client.get(reverse("api:formal-study-evidence", args=[study_id])).status_code == 403
    assert client.post(reverse("api:formal-study-validate", args=[study_id])).status_code == 403

    client.force_authenticate(central)
    validated = client.post(reverse("api:formal-study-validate", args=[study_id]))
    assert validated.status_code == 200
    assert validated.json()["valid"] is True
    assert validated.json()["integrity"]["formal_eligible"] is True

    evidence = client.get(reverse("api:formal-study-evidence", args=[study_id]))
    assert evidence.status_code == 200
    assert evidence["Content-Type"] == "application/zip"
    assert evidence["X-Evidence-SHA256"] == hashlib.sha256(evidence.content).hexdigest()
    with ZipFile(BytesIO(evidence.content)) as archive:
        assert "study-manifest.json" in archive.namelist()
        assert "trials.csv" in archive.namelist()
        assert "checksums.sha256" in archive.namelist()

    with patch(
        "scheduler.services.formal_studies._dispatch_run_to_benchmark",
        return_value=False,
    ):
        queued = client.post(reverse("api:formal-study-queue", args=[study_id]))
    assert queued.status_code == 202, queued.content
    assert queued.json()["status"] == models.StudyStatus.QUEUED

    batch = models.ExperimentBatch.objects.get(
        study_id=study_id,
        planned_scale_percentage=25,
    )
    failed = batch.runs.get(
        seed=1001,
        algorithm=models.SolverAlgorithm.CP_SAT,
        purpose=models.RunPurpose.MEASURED,
        pair_attempt=1,
    )
    counterpart = batch.runs.get(
        seed=1001,
        algorithm=models.SolverAlgorithm.GENETIC_ALGORITHM,
        purpose=models.RunPurpose.MEASURED,
        pair_attempt=1,
    )
    finished_at = timezone.now()
    models.ScheduleRun.objects.filter(pk=failed.pk).update(
        status=models.RunStatus.FAILED,
        failure_category=models.FailureCategory.UNCLASSIFIED,
        finished_at=finished_at,
    )
    models.ScheduleRun.objects.filter(pk=counterpart.pk).update(
        status=models.RunStatus.TIMEOUT,
        finished_at=finished_at,
    )

    classify_url = reverse("api:formal-run-classify-failure", args=[failed.pk])
    client.force_authenticate(reviewer)
    assert client.post(
        classify_url,
        {"category": "INFRASTRUCTURE", "reason": "Worker process was lost."},
        format="json",
    ).status_code == 403

    client.force_authenticate(central)
    classified = client.post(
        classify_url,
        {"category": "infrastructure", "reason": "Worker process was lost."},
        format="json",
    )
    assert classified.status_code == 200, classified.content
    assert classified.json()["failure_category"] == models.FailureCategory.INFRASTRUCTURE
    assert classified.json()["included_in_analysis"] is False
    duplicate_classification = client.post(
        classify_url,
        {"category": "INFRASTRUCTURE", "reason": "Duplicate audit."},
        format="json",
    )
    assert duplicate_classification.status_code == 400

    replacement_url = reverse("api:formal-run-replace-pair", args=[failed.pk])
    client.force_authenticate(reviewer)
    assert client.post(replacement_url).status_code == 403
    client.force_authenticate(central)
    replacement = client.post(replacement_url)
    assert replacement.status_code == 201, replacement.content
    replacement_payload = replacement.json()
    assert replacement_payload["pair_attempt"] == 2
    assert len(replacement_payload["replacement_runs"]) == 2
    assert {
        item["algorithm"] for item in replacement_payload["replacement_runs"]
    } == set(models.SolverAlgorithm.values)
    assert all(
        item["pair_attempt"] == 2 and item["included_in_analysis"]
        for item in replacement_payload["replacement_runs"]
    )
    assert client.post(replacement_url).status_code == 400

    cancelled = client.post(reverse("api:formal-study-cancel", args=[study_id]))
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == models.StudyStatus.CANCELLED
    assert cancelled.json()["formal_conclusion"]["status"] == (
        "NO_FORMAL_CONCLUSION_AVAILABLE"
    )
