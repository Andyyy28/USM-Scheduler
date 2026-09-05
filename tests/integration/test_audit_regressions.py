import pytest
from django.core.exceptions import ValidationError
from django.test import Client, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from scheduler import models, views
from scheduler.services import workflow
from scheduler.services.exports import schedule_export_rows
from tests.integration.test_workflow_extensions import _seed, _snapshot

pytestmark = pytest.mark.django_db


@pytest.fixture
def schedule_setup():
    ids = _seed()
    central = models.User.objects.get(pk=ids["central_user_id"])
    reviewer = models.User.objects.get(pk=ids["reviewer_user_id"])
    college = models.College.objects.get(pk=ids["review_college_id"])
    client = APIClient()
    client.force_authenticate(central)
    snapshot = _snapshot(client, ids)
    with override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True):
        response = client.post(reverse("api:runs"), {
            "snapshot_id": snapshot.pk, "algorithm": "CP_SAT", "seed": 11,
            "configuration": {"time_limit_seconds": 1, "worker_count": 1},
        }, format="json")
    assert response.status_code == 202, response.content
    schedule = models.ScheduleVersion.objects.get(run_id=response.json()[0]["id"])
    return schedule, central, reviewer, college


def test_college_can_endorse_again_after_requesting_changes(schedule_setup):
    schedule, central, reviewer, college = schedule_setup
    schedule = workflow.submit_for_review(schedule, central)
    for status in [models.ReviewStatus.ENDORSED, models.ReviewStatus.CHANGES_REQUESTED, models.ReviewStatus.ENDORSED]:
        workflow.review_schedule(schedule=schedule, college=college, reviewer=reviewer, status=status, comment="Reviewed")
    assert schedule.reviews.count() == 3
    workflow.approve_schedule(schedule, central)
    schedule.refresh_from_db()
    assert schedule.status == models.ScheduleStatus.APPROVED


def test_stale_review_object_cannot_append_decision_after_approval(schedule_setup):
    schedule, central, reviewer, college = schedule_setup
    stale = workflow.submit_for_review(schedule, central)
    workflow.review_schedule(schedule=stale, college=college, reviewer=reviewer,
                             status=models.ReviewStatus.ENDORSED, comment="Reviewed")
    workflow.approve_schedule(schedule, central)
    with pytest.raises(ValidationError, match="under review"):
        workflow.review_schedule(schedule=stale, college=college, reviewer=reviewer,
                                 status=models.ReviewStatus.CHANGES_REQUESTED, comment="Late")
    assert schedule.reviews.count() == 1


def test_export_and_display_ignore_stale_allocations(schedule_setup):
    schedule, _, _, _ = schedule_setup
    from scheduler.services.assignment_display import prepare_assignments
    from scheduler.services.problem_builder import load_problem

    problem = load_problem(schedule.snapshot)
    row = next(row for row in schedule.assignments.select_related("meeting_requirement")
               if any(candidate.start_atom_id != f"slot:{row.start_time_slot_id}"
                      for candidate in problem.event_map[str(row.meeting_requirement.stable_key)].candidates))
    event = problem.event_map[str(row.meeting_requirement.stable_key)]
    candidate = next(candidate for candidate in event.candidates if candidate.start_atom_id != f"slot:{row.start_time_slot_id}")
    row.room_id = int(candidate.room_id)
    row.start_time_slot_id = int(candidate.start_atom_id.removeprefix("slot:"))
    row.save()
    last = max(candidate.occupied_atom_ids, key=lambda atom: problem.atom_map[atom].order)
    expected = models.TimeSlot.objects.get(pk=int(last.removeprefix("slot:"))).ends_at
    exported = next(item for item in schedule_export_rows(schedule) if item["meeting_id"] == event.event_id)
    assert exported["ends_at"] == expected.strftime("%H:%M")
    prepare_assignments(schedule, [row])
    assert views._assignment_view(row).ends_at == expected


def test_failed_history_filter_and_archived_actions(schedule_setup):
    schedule, central, _, _ = schedule_setup
    models.ScheduleRun.objects.filter(pk=schedule.run_id).update(status=models.RunStatus.FAILED)
    client = Client()
    client.force_login(central)
    response = client.get(reverse("scheduler:runs"), {"status": "error"})
    assert [row.id for row in response.context["runs"]] == [str(schedule.run_id)]
    detail = client.get(reverse("scheduler:run-detail", args=[schedule.run_id]))
    assert b"No timetable was created" in detail.content
    schedule.status = models.ScheduleStatus.ARCHIVED
    displayed = views._schedule_view(schedule)
    assert not displayed.approved and not displayed.can_lock


def test_reviews_cannot_be_rewritten_through_admin(schedule_setup):
    from django.contrib import admin
    from django.test import RequestFactory

    _, central, _, _ = schedule_setup
    request = RequestFactory().get("/admin/")
    request.user = central
    review_admin = admin.site._registry[models.ScheduleReview]
    assert not review_admin.has_add_permission(request)
    assert not review_admin.has_change_permission(request)
    assert not review_admin.has_delete_permission(request)


def test_history_filters_before_limit_and_rejects_comparing_run_with_itself(schedule_setup):
    schedule, central, _, _ = schedule_setup
    models.ScheduleRun.objects.filter(pk=schedule.run_id).update(status=models.RunStatus.FAILED)
    models.ScheduleRun.objects.bulk_create([
        models.ScheduleRun(snapshot=schedule.snapshot, requested_by=central,
                           algorithm=models.SolverAlgorithm.GENETIC_ALGORITHM, seed=index)
        for index in range(251)
    ])
    client = Client()
    client.force_login(central)
    response = client.get(reverse("scheduler:runs"), {"status": "error"})
    assert [row.id for row in response.context["runs"]] == [str(schedule.run_id)]
    response = client.get(reverse("scheduler:run-comparison"), {"left": schedule.run_id, "right": schedule.run_id})
    assert not response.context["comparable"]
    assert b"Choose two different runs" in response.content


def test_runtime_smoke_refuses_existing_data(schedule_setup):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    count = models.AcademicTerm.objects.count()
    with override_settings(CELERY_TASK_ALWAYS_EAGER=False), pytest.raises(CommandError, match="not empty"):
        call_command("check_runtime", confirm_empty_database=True)
    assert models.AcademicTerm.objects.count() == count


def test_cached_draft_assignment_cannot_change_after_review_begins(schedule_setup):
    schedule, central, _, _ = schedule_setup
    stale = schedule.assignments.select_related("schedule").first()
    assert stale.schedule.status == models.ScheduleStatus.DRAFT
    workflow.submit_for_review(schedule, central)
    stale.objective_contribution = {"tampered": True}
    with pytest.raises(ValidationError, match="DRAFT"):
        stale.save()
    with pytest.raises(ValidationError, match="DRAFT"):
        stale.delete()
    assert models.ScheduleAssignment.objects.filter(pk=stale.pk).exists()
    stale.refresh_from_db()
    assert "tampered" not in stale.objective_contribution
