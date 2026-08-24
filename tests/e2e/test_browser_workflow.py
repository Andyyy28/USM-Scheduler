from __future__ import annotations

import pytest
from playwright.sync_api import expect, sync_playwright

from scheduler import models

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.e2e]


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
