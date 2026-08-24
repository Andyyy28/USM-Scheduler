from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from scheduler import models
from scheduler.services import workflow

pytestmark = pytest.mark.django_db


def _seed() -> dict[str, int]:
    output = StringIO()
    call_command("seed_demo", stdout=output)
    return json.loads(output.getvalue().strip().splitlines()[-1])


def _snapshot(client: APIClient, identifiers: dict[str, int]) -> models.ProblemSnapshot:
    response = client.post(
        reverse("api:snapshots"),
        {
            "revision_id": identifiers["revision_id"],
            "objective_profile_id": identifiers["objective_profile_id"],
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    return models.ProblemSnapshot.objects.get(pk=response.json()["id"])


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
def test_locked_regeneration_creates_a_valid_child_schedule() -> None:
    identifiers = _seed()
    central = models.User.objects.get(pk=identifiers["central_user_id"])
    reviewer = models.User.objects.get(pk=identifiers["reviewer_user_id"])
    college = models.College.objects.get(pk=identifiers["review_college_id"])
    client = APIClient()
    client.force_authenticate(central)
    snapshot = _snapshot(client, identifiers)
    run_response = client.post(
        reverse("api:runs"),
        {
            "snapshot_id": snapshot.pk,
            "algorithm": "CP_SAT",
            "seed": 11,
            "configuration": {"time_limit_seconds": 1, "worker_count": 1},
        },
        format="json",
    )
    assert run_response.status_code == 202, run_response.content
    parent = models.ScheduleVersion.objects.get(run_id=run_response.json()[0]["id"])
    assert client.post(reverse("api:schedule-submit-review", args=[parent.pk])).status_code == 200
    client.force_authenticate(reviewer)
    assert client.post(
        reverse("api:schedule-review", args=[parent.pk]),
        {
            "college_id": college.pk,
            "status": models.ReviewStatus.ENDORSED,
            "comment": "College boundary and teaching-room rules verified.",
        },
        format="json",
    ).status_code == 201
    client.force_authenticate(central)
    assert client.post(reverse("api:schedule-approve", args=[parent.pk])).status_code == 200
    locked_assignment = parent.assignments.order_by("pk").first()
    assert client.post(
        reverse("api:schedule-lock", args=[parent.pk]),
        {"assignment_ids": [locked_assignment.pk], "reason": "Keep accepted placement"},
        format="json",
    ).status_code == 201

    regeneration = client.post(
        reverse("api:schedule-regenerate", args=[parent.pk]),
        {
            "algorithm": "CP_SAT",
            "seed": 12,
            "time_limit_seconds": 1,
        },
        format="json",
    )
    assert regeneration.status_code == 202, regeneration.content
    child = models.ScheduleVersion.objects.get(run_id=regeneration.json()[0]["id"])
    assert child.parent == parent
    assert child.snapshot.snapshot_hash != parent.snapshot.snapshot_hash
    child_locked = child.assignments.get(
        meeting_requirement=locked_assignment.meeting_requirement
    )
    assert child_locked.room_id == locked_assignment.room_id
    assert child_locked.start_time_slot_id == locked_assignment.start_time_slot_id
    assert child.validation_result.is_feasible


def test_experiment_clone_finalize_and_report_apis() -> None:
    identifiers = _seed()
    central = models.User.objects.get(pk=identifiers["central_user_id"])
    client = APIClient()
    client.force_authenticate(central)
    snapshot = _snapshot(client, identifiers)

    experiment_response = client.post(
        reverse("api:experiments"),
        {
            "snapshot_id": snapshot.pk,
            "name": "Two-seed API plan",
            "seeds": [1001, 1002],
            "time_limit_seconds": 2,
            "order_seed": 44,
            "configuration": {"population_size": 20, "tournament_size": 2},
        },
        format="json",
    )
    assert experiment_response.status_code == 201, experiment_response.content
    experiment_id = experiment_response.json()["id"]
    batch = models.ExperimentBatch.objects.get(pk=experiment_id)
    assert batch.runs.count() == 4
    assert batch.memory_limit_mb == 2048
    assert all(run.configuration["worker_count"] == 1 for run in batch.runs.all())
    report = client.get(reverse("api:experiment-detail", args=[experiment_id]))
    assert report.status_code == 200
    assert report.json()["batch"]["environment_manifest"]["manifest_hash"]
    page_client = Client()
    page_client.force_login(central)
    report_page = page_client.get(reverse("scheduler:experiment-detail", args=[experiment_id]))
    assert report_page.status_code == 200
    assert b"Controlled algorithm comparison" in report_page.content
    assert client.get(reverse("api:experiment-export", args=[experiment_id, "json"])).status_code == 200
    assert client.get(reverse("api:experiment-export", args=[experiment_id, "csv"])).status_code == 200

    clone_response = client.post(
        reverse("api:term-clone", args=[identifiers["revision_id"]]),
        {
            "academic_year": "2027-2028",
            "semester": models.Semester.FIRST,
            "starts_on": "2027-08-01",
            "ends_on": "2027-12-20",
            "label": "API semester clone",
        },
        format="json",
    )
    assert clone_response.status_code == 201, clone_response.content
    clone = models.TermDatasetRevision.objects.get(pk=clone_response.json()["id"])
    cloned_objective = clone.term.objective_profiles.get()
    cloned_objective.is_approved = True
    cloned_objective.approved_by = central
    cloned_objective.save()
    finalize = client.post(
        reverse("api:revision-finalize", args=[clone.pk]),
        {"objective_profile_id": cloned_objective.pk},
        format="json",
    )
    assert finalize.status_code == 200, finalize.content
    clone.refresh_from_db()
    assert clone.status == models.RevisionStatus.COMMITTED


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
def test_approval_revalidates_persisted_placement_and_latest_college_decision() -> None:
    identifiers = _seed()
    central = models.User.objects.get(pk=identifiers["central_user_id"])
    reviewer = models.User.objects.get(pk=identifiers["reviewer_user_id"])
    college = models.College.objects.get(pk=identifiers["review_college_id"])
    client = APIClient()
    client.force_authenticate(central)
    snapshot = _snapshot(client, identifiers)
    run_response = client.post(
        reverse("api:runs"),
        {
            "snapshot_id": snapshot.pk,
            "algorithm": "CP_SAT",
            "seed": 91,
            "configuration": {"time_limit_seconds": 1, "worker_count": 1},
        },
        format="json",
    )
    assert run_response.status_code == 202, run_response.content
    schedule = models.ScheduleVersion.objects.get(run_id=run_response.json()[0]["id"])
    assert client.post(reverse("api:schedule-submit-review", args=[schedule.pk])).status_code == 200
    client.force_authenticate(reviewer)
    assert client.post(
        reverse("api:schedule-review", args=[schedule.pk]),
        {
            "college_id": college.pk,
            "status": models.ReviewStatus.ENDORSED,
            "comment": "Reviewed and endorsed.",
        },
        format="json",
    ).status_code == 201

    assignment = schedule.assignments.select_related("meeting_requirement").first()
    original_start_id = assignment.start_time_slot_id
    event = next(
        item
        for item in snapshot.input_data["events"]
        if item["event_id"] == str(assignment.meeting_requirement.stable_key)
    )
    legal_start_ids = {
        int(candidate["start_atom_id"].split(":", 1)[1])
        for candidate in event["candidates"]
        if candidate["room_id"] == str(assignment.room_id)
    }
    invalid_start = schedule.revision.time_slots.exclude(pk__in=legal_start_ids).first()
    assert invalid_start is not None
    models.ScheduleAssignment.objects.filter(pk=assignment.pk).update(
        start_time_slot=invalid_start
    )
    client.force_authenticate(central)
    invalid_approval = client.post(reverse("api:schedule-approve", args=[schedule.pk]))
    assert invalid_approval.status_code == 400
    schedule.refresh_from_db()
    assert schedule.status == models.ScheduleStatus.UNDER_REVIEW

    models.ScheduleAssignment.objects.filter(pk=assignment.pk).update(
        start_time_slot_id=original_start_id
    )
    client.force_authenticate(reviewer)
    assert client.post(
        reverse("api:schedule-review", args=[schedule.pk]),
        {
            "college_id": college.pk,
            "status": models.ReviewStatus.CHANGES_REQUESTED,
            "comment": "A later review decision requests a change.",
        },
        format="json",
    ).status_code == 201
    client.force_authenticate(central)
    superseded_endorsement = client.post(
        reverse("api:schedule-approve", args=[schedule.pk])
    )
    assert superseded_endorsement.status_code == 400
    assert b"Missing college endorsement" in superseded_endorsement.content


def test_authenticated_operator_pages_render_current_contracts() -> None:
    identifiers = _seed()
    central = models.User.objects.get(pk=identifiers["central_user_id"])
    client = Client()
    client.force_login(central)
    for route in (
        "scheduler:dashboard",
        "scheduler:terms",
        "scheduler:runs",
        "scheduler:schedules",
        "scheduler:imports",
        "scheduler:reviews",
        "scheduler:run-comparison",
    ):
        response = client.get(reverse(route))
        assert response.status_code == 200, route
    assert reverse("api:import-preview") in client.get(reverse("scheduler:imports")).content.decode()
    terms_html = client.get(reverse("scheduler:terms")).content.decode()
    assert "Clone a semester planning base" in terms_html
    runs_html = client.get(reverse("scheduler:runs")).content.decode()
    assert "Preflight and freeze a problem snapshot" in runs_html
    assert "controlled 30-seed comparison" in runs_html


def test_running_solver_cancellation_terminates_the_dedicated_worker_task(monkeypatch) -> None:
    identifiers = _seed()
    central = models.User.objects.get(pk=identifiers["central_user_id"])
    client = APIClient()
    client.force_authenticate(central)
    snapshot = _snapshot(client, identifiers)
    run = models.ScheduleRun.objects.create(
        snapshot=snapshot,
        algorithm=models.SolverAlgorithm.CP_SAT,
        seed=77,
        status=models.RunStatus.RUNNING,
        task_id="solver-task-77",
        requested_by=central,
    )
    calls: list[tuple[str, bool, str | None]] = []

    def fake_revoke(task_id: str, *, terminate: bool, signal: str | None = None) -> None:
        calls.append((task_id, terminate, signal))

    monkeypatch.setattr(workflow.current_app.control, "revoke", fake_revoke)

    cancelled = workflow.cancel_run(run, central)

    assert calls == [("solver-task-77", True, "SIGTERM")]
    assert cancelled.status == models.RunStatus.CANCELLED
