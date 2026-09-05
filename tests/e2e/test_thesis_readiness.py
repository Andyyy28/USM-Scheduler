from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
from io import StringIO
from types import SimpleNamespace

import pytest
from axe_playwright_python.sync_playwright import Axe
from django.core.management import call_command
from django.db import close_old_connections
from django.test import override_settings
from django.utils import timezone
from playwright.sync_api import Page, expect, sync_playwright

from scheduler import models
from scheduler import views as scheduler_views
from scheduler.management.commands.seed_demo import (
    DEMO_DAILY_LOAD_RULE_CODE,
    DEMO_FIXED_RULE_CODE,
    build_demo_workbook_bytes,
)
from scheduler.services import workflow as workflow_services
from scheduler.services.imports import build_import_template
from scheduler.services.problem_builder import build_and_store_snapshot
from tests.browser_helpers import assert_browser_assets

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.e2e,
    pytest.mark.usefixtures("browser_static_storage"),
]

PASSWORD = "browser-test-password"
VIEWPORTS = (1440, 1184, 768, 390, 320)
PRINCIPAL_ROUTES = (
    "/",
    "/terms/",
    "/imports/",
    "/runs/",
    "/schedules/",
    "/reviews/",
    "/help/",
    "/research/",
    "/runs/compare/",
)
AXE_OPTIONS = {
    "runOnly": {
        "type": "tag",
        "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
    },
    "resultTypes": ["violations"],
}


def _seed_demo(**options: object) -> dict[str, int]:
    output = StringIO()
    call_command("seed_demo", stdout=output, **options)
    return json.loads(output.getvalue().strip().splitlines()[-1])


def _database_call(callback):  # type: ignore[no-untyped-def]
    """Run ORM work outside Playwright's event-loop thread."""

    def execute():  # type: ignore[no-untyped-def]
        close_old_connections()
        try:
            return callback()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(execute).result()


def _watch_browser_errors(page: Page) -> list[str]:
    errors: list[str] = []

    def record_console(message) -> None:  # type: ignore[no-untyped-def]
        if message.type == "error":
            errors.append(f"console: {message.text}")

    page.on("console", record_console)
    page.on("pageerror", lambda error: errors.append(f"page: {error}"))
    return errors


def _login(page: Page, base_url: str, username: str, password: str = PASSWORD) -> None:
    response = page.goto(f"{base_url}/accounts/login/", wait_until="networkidle")
    assert response is not None and response.ok
    page.get_by_label("Username").fill(username)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#main-content")).to_be_visible()


def _assert_page_health(page: Page, errors: list[str], *, route: str, width: int) -> None:
    assert_browser_assets(page)
    expect(page.locator("#main-content")).to_be_visible()
    assert len(page.locator("#main-content").inner_text().strip()) >= 20, (
        f"Blank content at {route} ({width}px)"
    )
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    ), f"Page-level horizontal overflow at {route} ({width}px)"
    clipped = page.locator("a, button, input, select, textarea, summary").evaluate_all(
        """
        (elements) => elements.filter((element) => {
          const style = getComputedStyle(element);
          if (style.display === 'none' || style.visibility === 'hidden') return false;
          if (element.closest('#site-navigation, .table-wrap, .week-board')) return false;
          if (element.closest('[hidden], .visually-hidden')) return false;
          const rect = element.getBoundingClientRect();
          if (!rect.width || !rect.height) return false;
          return rect.left < -1 || rect.right > window.innerWidth + 1;
        }).map((element) => ({
          tag: element.tagName,
          text: (element.innerText || element.value || element.getAttribute('aria-label') || '').slice(0, 80),
          left: Math.round(element.getBoundingClientRect().left),
          right: Math.round(element.getBoundingClientRect().right),
        }))
        """
    )
    assert clipped == [], f"Clipped controls at {route} ({width}px): {clipped}"
    assert errors == [], f"Browser errors at {route} ({width}px): {errors}"


def _submit_and_wait(page: Page, button_name: str, *, timeout: int = 120_000) -> None:
    with page.expect_navigation(wait_until="networkidle", timeout=timeout):
        page.get_by_role("button", name=button_name).click()


def _assert_no_axe_violations(page: Page, label: str) -> None:
    results = Axe().run(page, options=AXE_OPTIONS)
    assert results.violations_count == 0, f"{label}\n{results.generate_report()}"


def test_role_navigation_account_controls_and_direct_url_enforcement(live_server) -> None:  # type: ignore[no-untyped-def]
    _seed_demo(
        admin_username="qa-admin",
        central_username="qa-central",
        reviewer_username="qa-reviewer",
        admin_password=PASSWORD,
        central_password=PASSWORD,
        reviewer_password=PASSWORD,
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chromium")
        expectations = {
            "qa-central": {"prepare": True, "admin": False},
            "qa-reviewer": {"prepare": False, "admin": False},
            "qa-admin": {"prepare": True, "admin": True},
        }
        for username, expected in expectations.items():
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            errors = _watch_browser_errors(page)
            _login(page, live_server.url, username)

            for label in (
                "Home",
                "Academic Terms",
                "Generate Schedule",
                "Timetables",
                "Reviews",
                "Help",
                "Research tools",
            ):
                expect(page.get_by_role("link", name=label).first).to_be_visible()
            if expected["prepare"]:
                expect(page.get_by_role("link", name="Prepare Data").first).to_be_visible()
            else:
                expect(page.get_by_role("link", name="Prepare Data")).to_have_count(0)

            page.get_by_label(f"Account menu for {username}").click()
            if expected["admin"]:
                expect(page.get_by_role("link", name="Django administration")).to_be_visible()
            else:
                expect(page.get_by_role("link", name="Django administration")).to_have_count(0)

            response = page.goto(f"{live_server.url}/imports/", wait_until="networkidle")
            assert response is not None and response.ok
            if expected["prepare"]:
                expect(page.locator("[data-file-drop]")).to_be_visible()
                assert page.request.get(f"{live_server.url}/api/v1/imports/template/").status == 200
            else:
                expect(page.get_by_text("Read-only access", exact=True)).to_be_visible()
                expect(page.locator("[data-file-drop]")).to_have_count(0)
                assert page.request.get(f"{live_server.url}/api/v1/imports/template/").status == 403

                page.goto(f"{live_server.url}/runs/", wait_until="networkidle")
                expect(page.get_by_text("Schedule generation is managed centrally", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="Generate timetable")).to_have_count(0)
                page.goto(f"{live_server.url}/research/", wait_until="networkidle")
                expect(page.get_by_role("button", name="Create research batch")).to_have_count(0)

            _assert_page_health(page, errors, route=f"role:{username}", width=1440)
            context.close()
        browser.close()


def test_principal_routes_responsive_matrix_and_stress_content(
    live_server,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifiers = _seed_demo(
        central_username="matrix-central",
        central_password=PASSWORD,
    )
    central = models.User.objects.get(pk=identifiers["central_user_id"])
    term = models.AcademicTerm.objects.get(pk=identifiers["term_id"])
    revision = models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"])

    models.AcademicTerm.objects.bulk_create(
        [
            models.AcademicTerm(
                academic_year=f"{2030 + index}-{2031 + index}",
                semester=models.Semester.FIRST,
                campus=(
                    f"Kabacan Institutional Scheduling and Academic Services Campus {index:02d} "
                    "with an intentionally long approved display label"
                ),
                starts_on=date(2030 + index, 1, 10),
                ends_on=date(2030 + index, 5, 30),
                status=models.TermStatus.DRAFT,
            )
            for index in range(50)
        ]
    )
    schedule = models.ScheduleVersion.objects.create(
        term=term,
        revision=revision,
        version_number=1,
        name="Synthetic large timetable for responsive and content stress validation",
        source=models.ScheduleSource.MANUAL,
        status=models.ScheduleStatus.UNDER_REVIEW,
        created_by=central,
    )
    colleges = models.College.objects.bulk_create(
        [
            models.College(
                code=f"QA-{index:02d}",
                name=(
                    f"Synthetic College {index:02d} of Interdisciplinary Institutional Studies "
                    "and Community Extension Services"
                ),
            )
            for index in range(50)
        ]
    )
    models.ScheduleReview.objects.bulk_create(
        [
            models.ScheduleReview(
                schedule=schedule,
                college=college,
                reviewer=central,
                status=models.ReviewStatus.COMMENT,
                comment=(
                    "Synthetic review note confirming that subjects, sections, instructors, rooms, "
                    "and meeting times were checked against the approved college context. " * 5
                ),
            )
            for college in colleges
        ]
    )
    college_ids = [college.pk for college in colleges]
    monkeypatch.setattr(workflow_services, "required_review_college_ids", lambda _schedule: college_ids)

    day_labels = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
    assignments = [
        SimpleNamespace(
            id=str(index + 1),
            day=day_labels[index % len(day_labels)],
            starts_at=time(8 + (index % 8), 0),
            ends_at=time(8 + (index % 8), 30),
            subject_code=f"SYN-{index + 1:03d}",
            subject_name=(
                "Synthetic Interdisciplinary Institutional Planning and Community Extension Subject"
            ),
            section=f"Synthetic Bachelor Program Section {index + 1:03d}",
            instructor=f"Synthetic Faculty Member with Long Institutional Label {index + 1:03d}",
            room=f"SYNTHETIC-CAMPUS-LONG-ROOM-CODE-{index + 1:03d}",
            college=f"QA-{index % len(colleges):02d}",
            locked=index % 11 == 0,
        )
        for index in range(105)
    ]
    original_safe_list = scheduler_views._safe_list

    def stress_safe_list(model_name: str, **kwargs):  # type: ignore[no-untyped-def]
        if model_name == "ScheduleAssignment":
            return assignments
        return original_safe_list(model_name, **kwargs)

    monkeypatch.setattr(scheduler_views, "_safe_list", stress_safe_list)
    monkeypatch.setattr(scheduler_views, "_assignment_view", lambda assignment: assignment)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = _watch_browser_errors(page)
        _login(page, live_server.url, "matrix-central")

        for route in PRINCIPAL_ROUTES:
            for width in VIEWPORTS:
                errors.clear()
                page.set_viewport_size({"width": width, "height": 900})
                response = page.goto(f"{live_server.url}{route}", wait_until="networkidle")
                assert response is not None and response.ok, f"HTTP failure at {route} ({width}px)"
                _assert_page_health(page, errors, route=route, width=width)

        page.set_viewport_size({"width": 390, "height": 900})
        page.goto(f"{live_server.url}/schedules/?schedule={schedule.pk}", wait_until="networkidle")
        expect(page.locator(".class-card")).to_have_count(105)
        page.get_by_text("Search the detailed assignment list", exact=True).click()
        expect(page.locator("#timetable-table tbody tr")).to_have_count(105)
        expect(page.locator("#timetable-table").locator("xpath=..")).to_have_attribute(
            "data-overflow", "true"
        )

        page.goto(f"{live_server.url}/reviews/", wait_until="networkidle")
        expect(page.locator(".review-card")).to_have_count(50)
        _assert_page_health(page, errors, route="/reviews/ stress", width=390)
        browser.close()


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    SOLVER_DEFAULT_TIME_LIMIT_SECONDS=2,
)
def test_complete_synthetic_browser_journey_and_rendered_states(live_server, tmp_path) -> None:  # type: ignore[no-untyped-def]
    central = models.User.objects.create_user(
        username="journey-central",
        password=PASSWORD,
        role=models.UserRole.CENTRAL_SCHEDULER,
    )
    reviewer = models.User.objects.create_user(
        username="journey-reviewer",
        password=PASSWORD,
        role=models.UserRole.COLLEGE_REVIEWER,
    )
    term = models.AcademicTerm.objects.create(
        academic_year="2026-2027",
        semester=models.Semester.FIRST,
        campus="Kabacan Synthetic QA Campus",
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 12, 20),
        status=models.TermStatus.ACTIVE,
    )
    objective = models.ObjectiveProfile(
        name="Approved synthetic scheduling quality policy",
        version=1,
        term=term,
        is_approved=True,
        approved_by=central,
        approved_at=timezone.now(),
    )
    objective.save()
    fixed_policy = models.ConstraintPolicyVersion.objects.create(
        rule_code=DEMO_FIXED_RULE_CODE,
        version=1,
        title="Fixed 50-student meeting rule",
        definition="Every meeting contains at most 50 students.",
        classification=models.ConstraintKind.HARD,
        owner_office="Office of the University Registrar",
        source="Synthetic browser fixture",
        effective_term=term,
        parameters={"fixed_student_limit": 50},
        is_approved=True,
        approved_by=central,
        approved_at=timezone.now(),
    )
    daily_policy = models.ConstraintPolicyVersion.objects.create(
        rule_code=DEMO_DAILY_LOAD_RULE_CODE,
        version=1,
        title="Instructor daily teaching-atom limit",
        definition="Each instructor follows the approved daily teaching limit.",
        classification=models.ConstraintKind.HARD,
        owner_office="Office of Academic Affairs",
        source="Synthetic browser fixture",
        effective_term=term,
        parameters={"unit": "teaching_atom"},
        is_approved=True,
        approved_by=central,
        approved_at=timezone.now(),
    )
    invalid_path = tmp_path / "synthetic-invalid-empty.xlsx"
    invalid_path.write_bytes(build_import_template())
    valid_path = tmp_path / "synthetic-complete-workflow.xlsx"
    valid_path.write_bytes(
        build_demo_workbook_bytes(
            campus=term.campus,
            fixed_rule_hash=fixed_policy.policy_hash,
            daily_load_rule_hash=daily_policy.policy_hash,
        )
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chromium")
        central_context = browser.new_context(viewport={"width": 1440, "height": 900})
        central_page = central_context.new_page()
        central_page.on("dialog", lambda dialog: dialog.accept())
        errors = _watch_browser_errors(central_page)
        _login(central_page, live_server.url, central.username)
        expect(central_page.get_by_text("Prepare scheduling data", exact=True).first).to_be_visible()

        central_page.goto(f"{live_server.url}/imports/", wait_until="networkidle")
        central_page.select_option("#import-origin", "INSTITUTIONAL")
        central_page.select_option("#import-term", str(term.pk))
        central_page.set_input_files("#import-file", str(invalid_path))
        central_page.get_by_label(
            "I confirm that I am authorized to use this dataset for the study, that it contains no "
            "unnecessary personal data, and that it will not be placed in the public repository."
        ).check()
        _submit_and_wait(central_page, "Check workbook")
        expect(central_page.get_by_text("Invalid", exact=True)).to_be_visible()
        expect(central_page.get_by_text("Correct and upload again", exact=True)).to_be_visible()

        central_page.select_option("#import-term", str(term.pk))
        central_page.set_input_files("#import-file", str(valid_path))
        central_page.select_option("#import-origin", "SYNTHETIC")
        central_page.get_by_label(
            "I confirm that I am authorized to use this dataset for the study, that it contains no "
            "unnecessary personal data, and that it will not be placed in the public repository."
        ).check()
        _submit_and_wait(central_page, "Check workbook")
        expect(central_page.get_by_role("button", name="Save prepared data")).to_be_visible()
        _submit_and_wait(central_page, "Save prepared data")
        expect(central_page.get_by_text("Prepared", exact=True).first).to_be_visible()

        _database_call(
            lambda: models.UserCollegeScope.objects.create(
                user_id=reviewer.pk,
                college_id=models.College.objects.get(code="CSM").pk,
            )
        )

        central_page.goto(f"{live_server.url}/runs/", wait_until="networkidle")
        revision_id = _database_call(
            lambda: models.TermDatasetRevision.objects.get(term_id=term.pk).pk
        )
        central_page.select_option("#snapshot-revision", str(revision_id))
        central_page.select_option("#snapshot-objective", str(objective.pk))
        _submit_and_wait(central_page, "Check semester data")
        snapshot_id = _database_call(lambda: models.ProblemSnapshot.objects.get().pk)

        def create_render_state_runs() -> None:
            models.ScheduleRun.objects.create(
                snapshot_id=snapshot_id,
                algorithm=models.SolverAlgorithm.CP_SAT,
                seed=7001,
                status=models.RunStatus.RUNNING,
                requested_by_id=central.pk,
                started_at=timezone.now(),
            )
            models.ScheduleRun.objects.create(
                snapshot_id=snapshot_id,
                algorithm=models.SolverAlgorithm.CP_SAT,
                seed=7002,
                status=models.RunStatus.FAILED,
                requested_by_id=central.pk,
                started_at=timezone.now(),
                finished_at=timezone.now(),
                error_message="Synthetic solver failure used only to validate the rendered error state.",
            )

        _database_call(create_render_state_runs)
        central_page.reload(wait_until="networkidle")
        expect(central_page.locator(".status").filter(has_text="Running")).to_be_visible()
        expect(central_page.locator(".status").filter(has_text="Generation failed")).to_be_visible()

        central_page.select_option("#run-snapshot", str(snapshot_id))
        central_page.select_option("#run-algorithm", models.SolverAlgorithm.CP_SAT)
        central_page.fill("#run-time-limit", "2")
        central_page.fill("#run-seed", "7003")
        _submit_and_wait(central_page, "Generate timetable")
        successful_status, schedule_id = _database_call(
            lambda: (
                models.ScheduleRun.objects.get(seed=7003).status,
                models.ScheduleRun.objects.get(seed=7003).schedule_version.pk,
            )
        )
        assert successful_status in {models.RunStatus.FEASIBLE, models.RunStatus.OPTIMAL}

        central_page.goto(
            f"{live_server.url}/schedules/?schedule={schedule_id}", wait_until="networkidle"
        )
        expect(central_page.get_by_role("heading", name="Send this timetable for review")).to_be_visible()
        expect(central_page.locator(".class-card")).to_have_count(2)
        _submit_and_wait(central_page, "Check and send for review")
        expect(central_page.get_by_role("heading", name="Complete the college review")).to_be_visible()

        reviewer_context = browser.new_context(viewport={"width": 1184, "height": 900})
        reviewer_page = reviewer_context.new_page()
        reviewer_page.on("dialog", lambda dialog: dialog.accept())
        reviewer_errors = _watch_browser_errors(reviewer_page)
        _login(reviewer_page, live_server.url, reviewer.username)
        reviewer_page.goto(f"{live_server.url}/reviews/", wait_until="networkidle")
        review_form = reviewer_page.locator("form.review-form").first
        review_form.locator("select[name='status']").select_option(models.ReviewStatus.CHANGES_REQUESTED)
        review_form.locator("textarea[name='comment']").fill(
            "Synthetic change request: verify the demonstration room assignment before endorsement."
        )
        with reviewer_page.expect_navigation(wait_until="networkidle", timeout=120_000):
            review_form.get_by_role("button", name="Save review decision").click()
        expect(
            reviewer_page.locator(".status").filter(has_text="Changes requested")
        ).to_be_visible()

        review_form = reviewer_page.locator("form.review-form").first
        review_form.locator("select[name='status']").select_option(models.ReviewStatus.ENDORSED)
        review_form.locator("textarea[name='comment']").fill(
            "Synthetic re-check complete; subjects, instructor, room, day, and time are endorsed."
        )
        with reviewer_page.expect_navigation(wait_until="networkidle", timeout=120_000):
            review_form.get_by_role("button", name="Save review decision").click()
        expect(reviewer_page.locator(".status").filter(has_text="Endorsed")).to_be_visible()
        expect(reviewer_page.locator("form.review-form")).to_have_count(0)
        _assert_page_health(reviewer_page, reviewer_errors, route="journey:review", width=1184)
        reviewer_context.close()

        central_page.goto(f"{live_server.url}/reviews/", wait_until="networkidle")
        expect(central_page.locator(".status").filter(has_text="Endorsed")).to_be_visible()
        central_page.goto(
            f"{live_server.url}/schedules/?schedule={schedule_id}", wait_until="networkidle"
        )
        _submit_and_wait(central_page, "Approve final timetable")
        expect(central_page.get_by_role("heading", name="This timetable is approved")).to_be_visible()

        with central_page.expect_download() as download_info:
            central_page.get_by_role("link", name="Download Excel").click()
        assert download_info.value.suggested_filename.endswith(".xlsx")
        with central_page.expect_download() as download_info:
            central_page.get_by_role("link", name="Download CSV").click()
        assert download_info.value.suggested_filename.endswith(".csv")
        central_page.emulate_media(media="print")
        expect(central_page.get_by_role("heading", name="Classes by day")).to_be_visible()
        assert central_page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
        )
        central_page.emulate_media(media="screen")

        def archive_schedule() -> None:
            schedule = models.ScheduleVersion.objects.get(pk=schedule_id)
            schedule.status = models.ScheduleStatus.ARCHIVED
            schedule.save(update_fields=["status", "updated_at"])

        _database_call(archive_schedule)
        central_page.reload(wait_until="networkidle")
        expect(central_page.get_by_role("heading", name="This timetable is archived")).to_be_visible()
        _assert_page_health(central_page, errors, route="journey:archived", width=1440)
        central_context.close()
        browser.close()


def test_critical_pages_have_no_automated_wcag_a_or_aa_violations(live_server) -> None:  # type: ignore[no-untyped-def]
    _seed_demo(central_username="axe-central", central_password=PASSWORD)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{live_server.url}/accounts/login/", wait_until="networkidle")
        _assert_no_axe_violations(page, "Login at 1440px")
        _login(page, live_server.url, "axe-central")

        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 900})
            for route in ("/", "/imports/", "/runs/", "/schedules/", "/reviews/", "/help/"):
                response = page.goto(f"{live_server.url}{route}", wait_until="networkidle")
                assert response is not None and response.ok
                _assert_no_axe_violations(page, f"{route} at {width}px")
        browser.close()


def test_firefox_login_navigation_and_principal_workflow_smoke(live_server) -> None:  # type: ignore[no-untyped-def]
    _seed_demo(central_username="firefox-central", central_password=PASSWORD)

    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = _watch_browser_errors(page)
        _login(page, live_server.url, "firefox-central")
        routes = ("/", "/runs/", "/schedules/", "/reviews/", "/help/")
        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 900})
            for route in routes:
                errors.clear()
                response = page.goto(f"{live_server.url}{route}", wait_until="networkidle")
                assert response is not None and response.ok
                _assert_page_health(page, errors, route=f"Firefox {route}", width=width)
        browser.close()


def test_generate_schedule_renders_all_structured_preflight_issues(live_server) -> None:  # type: ignore[no-untyped-def]
    identifiers = _seed_demo(
        central_username="preflight-browser",
        central_password=PASSWORD,
    )
    central = models.User.objects.get(pk=identifiers["central_user_id"])
    revision = models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"])
    objective = models.ObjectiveProfile.objects.get(pk=identifiers["objective_profile_id"])
    snapshot, _ = build_and_store_snapshot(revision, objective, central)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live_server.url, central.username)
        response = page.goto(f"{live_server.url}/runs/", wait_until="networkidle")
        assert response is not None and response.ok
        assert_browser_assets(page)

        revision_select = page.get_by_label("Prepared semester data")
        revision_select.select_option(str(revision.pk))
        expect(page.get_by_role("heading", name="Selected dataset")).to_be_visible()
        expect(page.get_by_text("Synthetic / practice", exact=True)).to_be_visible()
        snapshot_option = page.get_by_label("Checked semester data").locator(
            f"option[value='{snapshot.pk}']"
        )
        expect(snapshot_option).to_contain_text(f"Rev {revision.revision_number}")
        expect(snapshot_option).to_contain_text(f"snapshot {snapshot.snapshot_hash[:12]}")

        page.route(
            "**/api/v1/snapshots/",
            lambda route: route.fulfill(
                status=400,
                content_type="application/json",
                body=json.dumps(
                    {
                        "code": "PREFLIGHT_FAILED",
                        "detail": "The selected revision has two scheduling issues.",
                        "issues": [
                            {
                                "code": "ROOM_PROFILE_MISSING",
                                "message": "Room RM-404 has no availability profile.",
                            },
                            {
                                "code": "INSTRUCTOR_PROFILE_MISSING",
                                "message": "Instructor FAC-404 has no availability profile.",
                            },
                        ],
                    }
                ),
            ),
        )
        page.get_by_label("Schedule quality policy").select_option(str(objective.pk))
        page.get_by_role("button", name="Check semester data").click()

        alert = page.get_by_role("alert")
        expect(alert).to_contain_text("The selected revision has two scheduling issues.")
        expect(alert).to_contain_text(
            "ROOM_PROFILE_MISSING: Room RM-404 has no availability profile."
        )
        expect(alert).to_contain_text(
            "INSTRUCTOR_PROFILE_MISSING: Instructor FAC-404 has no availability profile."
        )
        expect(alert).not_to_contain_text("[object Object]")
        # A Django debug page or expired-login redirect must never become the
        # form's error text, including on narrow screens.
        page.route(
            "**/api/v1/runs/",
            lambda route: route.fulfill(
                status=500, content_type="text/html",
                body="<!DOCTYPE html><html><body>Traceback SECRET_INTERNALS</body></html>",
            ),
        )
        page.get_by_label("Checked semester data").select_option(str(snapshot.pk))
        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 900})
            page.get_by_role("button", name="Generate timetable", exact=True).click()
            run_alert = page.locator("[data-run-form] [role=alert]")
            expect(run_alert).to_contain_text("Check Generated schedules")
            expect(run_alert).not_to_contain_text("SECRET_INTERNALS")
            expect(run_alert).not_to_contain_text("<!DOCTYPE")
            expect(page.get_by_role("button", name="Generate timetable", exact=True)).to_be_enabled()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()
