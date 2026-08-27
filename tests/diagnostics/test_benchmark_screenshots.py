from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from playwright.sync_api import expect, sync_playwright

from scheduler import models
from scheduler.services.experiments import create_experiment_batch, execute_experiment_batch
from scheduler.services.problem_builder import build_and_store_snapshot

pytestmark = [pytest.mark.diagnostic, pytest.mark.django_db]


def test_complete_benchmark_report_writes_desktop_and_mobile_screenshots(live_server) -> None:  # type: ignore[no-untyped-def]
    output = StringIO()
    call_command("seed_demo", stdout=output)
    identifiers = json.loads(output.getvalue().strip().splitlines()[-1])
    user = models.User.objects.get(pk=identifiers["central_user_id"])
    user.set_password("diagnostic-browser-password")
    user.save(update_fields=["password"])
    revision = models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"])
    objective = models.ObjectiveProfile.objects.get(pk=identifiers["objective_profile_id"])
    snapshot, _ = build_and_store_snapshot(revision, objective, user)
    batch = create_experiment_batch(
        snapshot,
        user,
        seeds=(6101, 6102, 6103, 6104, 6105),
        time_limit=2,
        order_seed=20260824,
        name="Synthetic benchmark screenshot diagnostic",
        run_configuration={
            "population_size": 30,
            "tournament_size": 3,
            "max_generations": 20,
            "repair_attempts": 10,
            "persist_schedule": False,
        },
    )
    batch = execute_experiment_batch(batch)
    assert batch.status == models.ExperimentStatus.COMPLETED

    artifact_directory = Path("experiment-results").resolve()
    artifact_directory.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(live_server.url, wait_until="networkidle")
        page.get_by_label("Username").fill(user.username)
        page.get_by_label("Password").fill("diagnostic-browser-password")
        page.get_by_role("button", name="Sign in").click()
        page.goto(
            f"{live_server.url}/experiments/{batch.pk}/",
            wait_until="networkidle",
        )

        expect(page.get_by_role("heading", name="CP-SAT and Genetic Algorithm")).to_be_visible()
        expect(page.locator("[data-benchmark-chart]")).to_have_count(3)
        expect(page.locator("[data-benchmark-state='complete']")).to_be_visible()
        page.screenshot(
            path=str(artifact_directory / "benchmark-diagnostic-desktop.png"),
            full_page=True,
        )

        page.set_viewport_size({"width": 320, "height": 900})
        charts = page.locator("[data-benchmark-chart]")
        first_box = charts.nth(0).bounding_box()
        second_box = charts.nth(1).bounding_box()
        assert first_box is not None and second_box is not None
        assert abs(first_box["x"] - second_box["x"]) < 1
        assert second_box["y"] > first_box["y"] + first_box["height"]
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        page.screenshot(
            path=str(artifact_directory / "benchmark-diagnostic-mobile.png"),
            full_page=True,
        )
        browser.close()

    assert (artifact_directory / "benchmark-diagnostic-desktop.png").is_file()
    assert (artifact_directory / "benchmark-diagnostic-mobile.png").is_file()
