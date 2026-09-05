from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from scheduler import models
from scheduler.services.problem_builder import build_and_store_snapshot

pytestmark = pytest.mark.django_db


def _seed() -> dict[str, int]:
    output = StringIO()
    call_command("seed_demo", stdout=output)
    return json.loads(output.getvalue().strip().splitlines()[-1])


def _draft_formal_study() -> tuple[models.ExperimentStudy, models.User, models.User]:
    identifiers = _seed()
    central = models.User.objects.get(pk=identifiers["central_user_id"])
    reviewer = models.User.objects.get(pk=identifiers["reviewer_user_id"])
    revision = models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"])
    objective = models.ObjectiveProfile.objects.get(pk=identifiers["objective_profile_id"])
    snapshot, _ = build_and_store_snapshot(revision, objective, central)
    study = models.ExperimentStudy.objects.create(
        name="Formal CP-SAT vs GA thesis study",
        mode=models.ExperimentMode.FORMAL,
        protocol_version="formal-v2",
        source_snapshot=snapshot,
        scale_percentages=list(models.ExperimentStudy.FORMAL_SCALES),
        seeds=list(models.ExperimentStudy.FORMAL_SEEDS),
        order_seed=models.ExperimentStudy.FORMAL_ORDER_SEED,
        deadline_seconds=models.ExperimentStudy.FORMAL_DEADLINE_SECONDS,
        cpu_limit=models.ExperimentStudy.FORMAL_CPU_LIMIT,
        memory_limit_mb=models.ExperimentStudy.FORMAL_MEMORY_LIMIT_MB,
        warmups_per_algorithm_scale=1,
        protocol_manifest={},
        created_by=central,
    )
    return study, central, reviewer


def test_research_tools_separates_formal_and_exploratory_workflows() -> None:
    identifiers = _seed()
    central = models.User.objects.get(pk=identifiers["central_user_id"])
    client = Client()
    client.force_login(central)

    formal = client.get(reverse("scheduler:research"))
    exploratory = client.get(reverse("scheduler:research") + "?workflow=exploratory")

    assert formal.status_code == 200
    assert b"Formal Study" in formal.content
    assert b"240" in formal.content
    assert b"One study, four nested demand levels" in formal.content
    assert exploratory.status_code == 200
    assert b"Exploratory results cannot produce a formal winner" in exploratory.content
    assert b"Create research batch" in exploratory.content


def test_only_central_users_see_formal_study_creation_controls() -> None:
    identifiers = _seed()
    central = models.User.objects.get(pk=identifiers["central_user_id"])
    reviewer = models.User.objects.get(pk=identifiers["reviewer_user_id"])
    create_url = reverse("api:formal-studies")
    client = Client()

    client.force_login(central)
    central_response = client.get(reverse("scheduler:research") + "?workflow=formal")

    assert central_response.status_code == 200
    central_html = central_response.content.decode()
    assert f'action="{create_url}"' in central_html
    assert 'method="post"' in central_html
    assert 'name="source_snapshot_id"' in central_html
    assert 'name="name"' in central_html
    assert 'name="scaling_seed"' in central_html
    assert 'name="solver_profiles"' in central_html

    client.force_login(reviewer)
    reviewer_response = client.get(reverse("scheduler:research") + "?workflow=formal")

    assert reviewer_response.status_code == 200
    reviewer_html = reviewer_response.content.decode()
    assert f'action="{create_url}"' not in reviewer_html
    assert 'name="solver_profiles"' not in reviewer_html


def test_formal_study_controls_follow_status_and_central_role() -> None:
    study, central, reviewer = _draft_formal_study()
    client = Client()
    detail_url = reverse("scheduler:formal-study-detail", args=[study.pk])
    action_urls = {
        "validate": reverse("api:formal-study-validate", args=[study.pk]),
        "queue": reverse("api:formal-study-queue", args=[study.pk]),
        "cancel": reverse("api:formal-study-cancel", args=[study.pk]),
    }

    expected_by_status = {
        models.StudyStatus.DRAFT: {"validate"},
        models.StudyStatus.INVALID: {"validate"},
        models.StudyStatus.READY: {"queue"},
        models.StudyStatus.QUEUED: {"cancel"},
        models.StudyStatus.RUNNING: {"cancel"},
        models.StudyStatus.COMPLETED: set(),
        models.StudyStatus.CANCELLED: set(),
        models.StudyStatus.FAILED: set(),
    }

    client.force_login(central)
    for status, visible_actions in expected_by_status.items():
        models.ExperimentStudy.objects.filter(pk=study.pk).update(status=status)
        response = client.get(detail_url)

        assert response.status_code == 200
        html = response.content.decode()
        for action, action_url in action_urls.items():
            expected_form = f'action="{action_url}"'
            if action in visible_actions:
                assert expected_form in html, f"{action} control missing for {status}"
            else:
                assert expected_form not in html, f"{action} control shown for {status}"

    models.ExperimentStudy.objects.filter(pk=study.pk).update(
        status=models.StudyStatus.QUEUED
    )
    client.force_login(reviewer)
    reviewer_response = client.get(detail_url)

    assert reviewer_response.status_code == 200
    reviewer_html = reviewer_response.content.decode()
    for action_url in action_urls.values():
        assert f'action="{action_url}"' not in reviewer_html


def test_formal_dashboard_is_gated_and_bundle_is_role_restricted() -> None:
    study, central, reviewer = _draft_formal_study()
    client = Client()
    client.force_login(central)

    response = client.get(reverse("scheduler:formal-study-detail", args=[study.pk]))

    assert response.status_code == 200
    html = response.content.decode()
    assert "No formal conclusion available." in html
    assert "Independent feasibility across demand" in html
    assert "Kaplan–Meier" in html
    assert "Feasible raw penalty" in html
    assert "Frozen instance characteristics" in html
    assert "CP-SAT proof and search" in html
    assert "GA search behavior" in html
    assert "fixed limit is 50 students" in html
    assert "seat utilization" not in html.casefold()

    bundle = client.get(reverse("scheduler:formal-study-evidence", args=[study.pk]))
    assert bundle.status_code == 200
    assert bundle["Content-Type"] == "application/zip"
    assert "attachment" in bundle["Content-Disposition"]

    client.force_login(reviewer)
    denied = client.get(reverse("scheduler:formal-study-evidence", args=[study.pk]))
    assert denied.status_code == 403


def test_non_formal_study_does_not_render_as_formal_evidence() -> None:
    identifiers = _seed()
    central = models.User.objects.get(pk=identifiers["central_user_id"])
    revision = models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"])
    objective = models.ObjectiveProfile.objects.get(pk=identifiers["objective_profile_id"])
    snapshot, _ = build_and_store_snapshot(revision, objective, central)
    study = models.ExperimentStudy.objects.create(
        name="Exploratory study",
        mode=models.ExperimentMode.EXPLORATORY,
        source_snapshot=snapshot,
        scale_percentages=[100],
        seeds=[1],
        order_seed=1,
        created_by=central,
    )
    client = Client()
    client.force_login(central)

    response = client.get(reverse("scheduler:formal-study-detail", args=[study.pk]))

    assert response.status_code == 404
