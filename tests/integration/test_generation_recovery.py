from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from django.urls import reverse
from rest_framework.test import APIClient

from scheduler import models
from scheduler.domain import SolverResult, score_schedule, validate_schedule
from scheduler.services.imports import commit_import, preview_workbook
from scheduler.services.problem_builder import build_and_store_snapshot, load_problem
from scheduler.services.runs import _finite_decimal, create_run
from scheduler.services.trial_data import build_trial_workbook_bytes
from tests.integration.test_api_workflow import seed_demo
from tests.integration.test_trial_data import _approved_trial_policies, _term

pytestmark = pytest.mark.django_db


@pytest.fixture
def generation(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    identifiers = seed_demo()
    user = models.User.objects.get(pk=identifiers["central_user_id"])
    snapshot, _ = build_and_store_snapshot(
        models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"]),
        models.ObjectiveProfile.objects.get(pk=identifiers["objective_profile_id"]), user,
    )
    client = APIClient()
    client.force_authenticate(user)
    return user, snapshot, client


@pytest.mark.parametrize("algorithm,quick", [("CP_SAT", True), ("GA", True), ("GA", False)])
def test_routine_form_persists_timetable_and_keeps_quick_run_out_of_research(settings, algorithm, quick):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    user = models.User.objects.create_user(username="practice-scheduler", role=models.UserRole.CENTRAL_SCHEDULER)
    term = _term()
    workbook = build_trial_workbook_bytes(**_approved_trial_policies(term, user))
    revision = commit_import(preview_workbook(workbook, term, user), user)
    objective = models.ObjectiveProfile.objects.create(name="Practice quality", term=term, is_approved=True,
                                                     approved_by=user)
    snapshot, _ = build_and_store_snapshot(revision, objective, user)
    client = APIClient()
    client.force_authenticate(user)
    response = client.post(reverse("api:runs"), {
        "snapshot_id": snapshot.pk, "algorithm": algorithm, "seed": "42",
        "time_limit_seconds": "30", "first_feasible_only": "true" if quick else "false",
        "max_generations": "1",
    })
    assert response.status_code == 202
    result = response.json()[0]
    run = models.ScheduleRun.objects.get(pk=result["id"])
    assert run.status == models.RunStatus.FEASIBLE
    assert run.schedule_version.assignments.count() == 14
    assert run.validation_result.is_feasible
    assert run.included_in_analysis is (not quick)
    assert run.configuration["first_feasible_only"] is quick
    problem = load_problem(snapshot)
    result = SolverResult.from_dict(run.result_data)
    assert result.validation == validate_schedule(problem, result.assignments)
    assert result.objective == score_schedule(problem, result.assignments)
    if quick:
        assert "validating a complete timetable" in run.stopping_reason
    if algorithm == "CP_SAT":
        assert run.best_bound is None
        assert run.relative_gap is None
    else:
        if quick:
            assert run.diagnostics["metrics"]["initial_population_size"] == 1
        metric = run.metrics.get(name="search_space_size")
        assert metric.value is None
        assert int(metric.metadata["exact_value"]) == run.diagnostics["metrics"]["search_space_size"]
    assert client.get(reverse("api:runs")).status_code == 200
    assert client.get(reverse("api:run-detail", args=[run.pk])).status_code == 200


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_eager_solver_crash_returns_saved_failure_not_html(generation, monkeypatch, error_type):
    user, snapshot, client = generation

    def fail(*args, **kwargs):
        raise error_type("Synthetic solver failure: internal diagnostic")

    monkeypatch.setattr("scheduler.solvers.GeneticAlgorithmSolver.solve", fail)
    response = client.post(reverse("api:runs"), {"snapshot_id": snapshot.pk, "algorithm": "GA"})
    assert response.status_code == 202
    assert response["Content-Type"].startswith("application/json")
    run = models.ScheduleRun.objects.get(pk=response.json()[0]["id"])
    assert run.status == models.RunStatus.FAILED
    assert run.execution_seconds is not None
    assert not models.ScheduleVersion.objects.filter(run=run).exists()
    client.force_login(user)
    page = client.get(reverse("scheduler:run-detail", args=[run.pk]))
    assert page.status_code == 200
    assert b"system error" in page.content
    assert b"internal diagnostic" not in page.content
    assert b"Set up a new attempt" in page.content


def test_worker_outage_is_a_readable_503(generation, monkeypatch):
    _, snapshot, client = generation

    def fail(*args, **kwargs):
        raise ConnectionError("Sensitive broker connection details")

    monkeypatch.setattr("scheduler.tasks.execute_schedule_run.apply_async", fail)
    response = client.post(reverse("api:runs"), {"snapshot_id": snapshot.pk, "algorithm": "GA"})
    assert response.status_code == 503
    assert "worker could not be reached" in response.json()["detail"]
    assert "Sensitive" not in response.content.decode()


def test_timeout_retry_preserves_data_and_seed_and_increases_time(generation):
    user, snapshot, client = generation
    run = create_run(snapshot=snapshot, requested_by=user, algorithm="CP_SAT", seed=19,
                     configuration={"time_limit_seconds": 300})
    models.ScheduleRun.objects.filter(pk=run.pk).update(status=models.RunStatus.TIMEOUT)
    client.force_login(user)
    page = client.get(reverse("scheduler:run-detail", args=[run.pk]))
    assert b"does not mean the semester is impossible" in page.content
    assert b"Try again with more time" in page.content
    retry = client.get(reverse("scheduler:runs"), {"retry": run.pk})
    assert retry.status_code == 200
    assert retry.context["retry_run"].snapshot_id == str(snapshot.pk)
    assert b'value="600"' in retry.content
    assert b'value="19"' in retry.content


def test_quick_mode_cannot_enter_a_research_batch(generation):
    user, snapshot, _ = generation
    with pytest.raises(ValueError, match="full optimization budget"):
        create_run(snapshot=snapshot, requested_by=user, algorithm="GA",
                   configuration={"first_feasible_only": True}, experiment_batch=object())


def test_metric_conversion_handles_values_larger_than_float():
    assert _finite_decimal(10**400) is None
    assert _finite_decimal(10**18) is None
    assert _finite_decimal(999_999_999_999_999_999) is None
    assert _finite_decimal(123456789) == Decimal("123456789.000000")
    assert _finite_decimal(1.23456789) == Decimal("1.234568")


def test_migration_recovers_existing_oversized_sqlite_metrics(generation):
    if connection.vendor != "sqlite":
        pytest.skip("PostgreSQL rejects an oversized row at insertion rather than on read.")
    user, snapshot, _ = generation
    run = create_run(snapshot=snapshot, requested_by=user, algorithm="GA")
    exact = 123456789012345678901234567890
    models.ScheduleRun.objects.filter(pk=run.pk).update(diagnostics={"metrics": {"search_space_size": exact}})
    models.RunMetric.objects.create(run=run, name="search_space_size", value=Decimal(exact))
    migration = import_module("scheduler.migrations.0008_preserve_large_run_metrics")
    migration.preserve_large_metrics(apps, SimpleNamespace(connection=connection))
    metric = run.metrics.get(name="search_space_size")
    assert metric.value is None
    assert metric.metadata["exact_value"] == str(exact)
    assert metric.metadata["recovered_from"] == "run_json"
