from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect, sync_playwright

from scheduler import models
from scheduler import views as scheduler_views
from scheduler.services import experiments as experiment_services

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.e2e]


def _browser_benchmark_summary() -> dict[str, object]:
    algorithm_summary = {
        "success_rate": 0.5,
        "feasible_runs": 1,
        "observed_runs": 2,
        "status_counts": {"FEASIBLE": 1, "NO_SOLUTION": 1},
        "feasible_soft_penalty": {"median": 10.0},
        "rmst_time_to_feasibility_seconds": 1.0,
        "hard_violation_vector": {},
        "feasible_soft_penalty_median_bootstrap_95": [10.0, 10.0],
        "shared_preprocessing_seconds": {"median": 0.01},
        "execution_seconds": {"median": 0.8},
        "independent_validation_seconds": {"median": 0.02},
        "end_to_end_processing_seconds": {"median": 0.85},
        "feasible_penalty_per_meeting": {"median": 1.0},
        "feasible_normalized_quality_score": {"median": 90.0},
        "feasible_objective_components": {},
        "solver_configuration_by_run": {},
    }

    def benchmark_algorithm(
        algorithm: str,
        label: str,
        *,
        feasibility: float,
        penalty: float,
        rmst: float,
    ) -> dict[str, object]:
        return {
            "algorithm": algorithm,
            "label": label,
            "planned_runs": 2,
            "observed_runs": 2,
            "pending_runs": 0,
            "feasibility_rate": {
                "available": True,
                "value": feasibility,
                "wilson_95": [max(0.0, feasibility - 0.2), min(1.0, feasibility + 0.2)],
                "feasible_runs": 1,
                "observed_runs": 2,
                "planned_runs": 2,
                "unavailable_reason": None,
            },
            "median_feasible_raw_penalty": {
                "available": True,
                "value": penalty,
                "bootstrap_95": [penalty, penalty],
                "feasible_runs": 1,
                "unavailable_reason": None,
            },
            "rmst_time_to_feasibility_seconds": {
                "available": True,
                "value": rmst,
                "deadline_seconds": 2.0,
                "observed_runs": 2,
                "censored_runs": 1,
                "unavailable_reason": None,
            },
        }

    return {
        "batch": {
            "status": "COMPLETED",
            "seeds": [1, 2],
            "time_limit_seconds": 2,
            "snapshot_hash": "browser-snapshot",
            "objective_profile": {"hash": "browser-objective"},
            "cpu_limit": 1,
            "memory_limit_mb": 2048,
            "requested_run_configuration": {},
        },
        "algorithms": {
            "CP_SAT": dict(algorithm_summary),
            "GA": dict(algorithm_summary),
        },
        "benchmark": {
            "schema_version": "1.0",
            "state": "complete",
            "state_message": (
                "Benchmark complete: all planned CP-SAT and GA runs are terminal and "
                "protocol-compatible."
            ),
            "comparable": True,
            "comparability_reasons": [],
            "protocol_integrity": {"valid": True, "issues": []},
            "algorithm_ids": ["CP_SAT", "GA"],
            "by_algorithm": {
                "CP_SAT": benchmark_algorithm(
                    "CP_SAT",
                    "CP-SAT",
                    feasibility=0.75,
                    penalty=0.0,
                    rmst=0.8,
                ),
                "GA": benchmark_algorithm(
                    "GA",
                    "Genetic Algorithm",
                    feasibility=0.5,
                    penalty=20.0,
                    rmst=1.5,
                ),
            },
        },
        "primary_engine_decision": {"winner": None, "rationale": "No winner is forced."},
        "quality_metric_policy": {
            "normalizer_review": {"requires_stakeholder_review": False}
        },
        "comparative_tests": {"pairing_assumption": "Paired seeds.", "outcomes": {}},
        "objective_weight_sensitivity": {"available": False},
    }


def test_central_scheduler_can_sign_in_and_open_semester_import(live_server) -> None:  # type: ignore[no-untyped-def]
    models.User.objects.create_user(
        username="browser-central",
        password="browser-test-password",
        role=models.UserRole.CENTRAL_SCHEDULER,
    )

    with sync_playwright() as playwright:
        # The bundled Chromium channel uses Chromium's current headless mode
        # and avoids a second, redundant headless-shell download.
        browser = playwright.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page()
        page.goto(live_server.url, wait_until="networkidle")

        expect(page.get_by_role("heading", name="Sign in to your workspace")).to_be_visible()
        page.get_by_label("Username").fill("browser-central")
        page.get_by_label("Password").fill("browser-test-password")
        page.get_by_role("button", name="Sign in").click()

        expect(page.get_by_role("heading", name="Welcome, browser-central.")).to_be_visible()
        page.get_by_role("link", name="Data import").first.click()
        expect(page.get_by_role("heading", name="Prepare scheduling data")).to_be_visible()
        expect(page.get_by_role("link", name="Download test workbook")).to_be_visible()
        expect(page.locator("[data-file-drop]")).to_be_visible()
        expect(page.locator('input[type="file"]')).to_be_attached()
        browser.close()


def test_navigation_drawer_breakpoints_focus_and_mobile_widths(live_server) -> None:  # type: ignore[no-untyped-def]
    models.User.objects.create_user(
        username="browser-drawer",
        password="browser-test-password",
        role=models.UserRole.CENTRAL_SCHEDULER,
    )
    models.AcademicTerm.objects.create(
        academic_year="2026-2027",
        semester=models.Semester.FIRST,
        campus="Kabacan Main Campus with a deliberately long table value",
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 12, 20),
        status=models.TermStatus.ACTIVE,
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(live_server.url, wait_until="networkidle")
        page.get_by_label("Username").fill("browser-drawer")
        page.get_by_label("Password").fill("browser-test-password")
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_role("heading", name="Welcome, browser-drawer.")).to_be_visible()

        menu = page.get_by_role("button", name="Open navigation")
        close = page.get_by_role("button", name="Close navigation")
        sidebar = page.locator("#site-navigation")
        page_shell = page.locator(".page-shell")
        expect(menu).to_be_hidden()
        expect(close).to_be_hidden()
        expect(sidebar).not_to_have_attribute("aria-hidden", "true")
        assert sidebar.evaluate("element => element.inert") is False

        page.set_viewport_size({"width": 1185, "height": 820})
        expect(menu).to_be_hidden()
        expect(close).to_be_hidden()
        expect(sidebar).not_to_have_attribute("aria-hidden", "true")

        page.set_viewport_size({"width": 1184, "height": 820})
        expect(menu).to_be_visible()
        expect(sidebar).to_have_attribute("aria-hidden", "true")
        assert sidebar.evaluate("element => element.inert") is True

        menu.click()
        expect(close).to_be_focused()
        expect(sidebar).to_have_attribute("role", "dialog")
        expect(sidebar).to_have_attribute("aria-modal", "true")
        expect(page_shell).to_have_attribute("aria-hidden", "true")
        page.keyboard.press("Shift+Tab")
        expect(page.get_by_role("link", name="Help & user guide")).to_be_focused()
        page.keyboard.press("Tab")
        expect(close).to_be_focused()
        page.keyboard.press("Escape")
        expect(menu).to_be_focused()
        expect(sidebar).to_have_attribute("aria-hidden", "true")
        expect(page_shell).not_to_have_attribute("aria-hidden", "true")

        menu.click()
        page.set_viewport_size({"width": 1185, "height": 820})
        expect(menu).to_be_hidden()
        expect(sidebar).not_to_have_attribute("aria-hidden", "true")
        expect(sidebar).not_to_have_attribute("role", "dialog")
        assert sidebar.evaluate("element => element.inert") is False

        for width in (1024, 768, 375, 320):
            page.set_viewport_size({"width": width, "height": 820})
            expect(menu).to_be_visible()
            expect(sidebar).to_have_attribute("aria-hidden", "true")
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )

        expect(page.get_by_label("Account menu for browser-drawer")).to_be_visible()

        page.goto(f"{live_server.url}/terms/", wait_until="networkidle")
        table_wrapper = page.locator(".table-wrap")
        expect(table_wrapper).to_have_attribute("data-overflow", "true")
        expect(table_wrapper).to_have_attribute("tabindex", "0")
        expect(table_wrapper).to_have_attribute(
            "aria-label",
            "Scrollable table: Configured semesters",
        )
        expect(page.locator(".table-scroll-cue")).to_be_visible()
        table_wrapper.focus()
        expect(table_wrapper).to_be_focused()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )

        page.set_viewport_size({"width": 1440, "height": 900})
        expect(table_wrapper).to_have_attribute("data-overflow", "false")
        expect(table_wrapper).not_to_have_attribute("tabindex", "0")
        expect(page.locator(".table-scroll-cue")).to_be_hidden()
        browser.close()


def test_narrow_navigation_remains_available_without_javascript(live_server) -> None:  # type: ignore[no-untyped-def]
    models.User.objects.create_user(
        username="browser-no-js",
        password="browser-test-password",
        role=models.UserRole.CENTRAL_SCHEDULER,
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chromium")
        context = browser.new_context(
            java_script_enabled=False,
            viewport={"width": 320, "height": 820},
        )
        page = context.new_page()
        page.goto(live_server.url)
        page.get_by_label("Username").fill("browser-no-js")
        page.get_by_label("Password").fill("browser-test-password")
        page.get_by_role("button", name="Sign in").click(force=True)

        expect(page.get_by_role("heading", name="Welcome, browser-no-js.")).to_be_visible()
        expect(page.locator(".menu-button")).to_be_hidden()
        expect(page.locator("#site-navigation")).to_be_visible()
        page.get_by_role("link", name="Academic terms").click()
        expect(page.get_by_role("heading", name="Academic terms", exact=True)).to_be_visible()
        context.close()
        browser.close()


def test_benchmark_graph_reflows_and_keeps_exact_evidence(
    live_server,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models.User.objects.create_user(
        username="browser-benchmark",
        password="browser-test-password",
        role=models.UserRole.CENTRAL_SCHEDULER,
    )
    batch = SimpleNamespace(id=991, name="Browser benchmark")
    original_safe_get = scheduler_views._safe_get

    def fake_safe_get(model_name: str, pk: str):  # type: ignore[no-untyped-def]
        if model_name == "ExperimentBatch" and pk == "991":
            return batch
        return original_safe_get(model_name, pk)

    monkeypatch.setattr(scheduler_views, "_safe_get", fake_safe_get)
    monkeypatch.setattr(
        experiment_services,
        "summarize_experiment",
        lambda _batch: _browser_benchmark_summary(),
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page(viewport={"width": 1024, "height": 900})
        page.goto(live_server.url, wait_until="networkidle")
        page.get_by_label("Username").fill("browser-benchmark")
        page.get_by_label("Password").fill("browser-test-password")
        page.get_by_role("button", name="Sign in").click()
        page.goto(f"{live_server.url}/experiments/991/", wait_until="networkidle")

        expect(page.get_by_role("heading", name="CP-SAT and Genetic Algorithm")).to_be_visible()
        expect(page.locator("[data-benchmark-chart]")).to_have_count(3)
        expect(page.get_by_text("No mixed-unit overall score is calculated.")).to_be_visible()
        assert page.locator(".benchmark-grid").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        ) == 3
        assert page.locator('[data-benchmark-value="0.75"]').evaluate(
            "element => parseFloat(getComputedStyle(element).width) > 0"
        )
        assert page.locator('[data-benchmark-value="0.0"]').count() == 1

        page.set_viewport_size({"width": 375, "height": 900})
        assert page.locator(".benchmark-grid").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        ) == 1
        expect(page.locator("[data-benchmark-chart]")).to_have_count(3)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )

        page.set_viewport_size({"width": 320, "height": 900})
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        page.emulate_media(media="print")
        expect(page.locator("[data-benchmark-chart]").first).to_be_visible()
        expect(page.get_by_text("0.000", exact=True).first).to_be_visible()
        browser.close()
