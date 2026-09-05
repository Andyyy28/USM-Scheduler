"""Capture de-identified UI evidence from a locally running test instance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture thesis-validation screenshots using a synthetic test account."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--output", type=Path, default=Path("docs/evidence/ui"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    captured: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        response = page.goto(f"{args.base_url}/accounts/login/", wait_until="networkidle")
        if response is None or not response.ok:
            raise RuntimeError("The local login page did not load successfully.")

        login_path = args.output / "login-1440.png"
        page.screenshot(path=str(login_path), full_page=True)
        captured.append(str(login_path))
        page.get_by_label("Username").fill(args.username)
        page.get_by_label("Password").fill(args.password)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_load_state("networkidle")

        pages = {
            "home-1440.png": "/",
            "generate-1440.png": "/runs/",
            "timetable-1440.png": "/schedules/",
            "reviews-1440.png": "/reviews/",
            "help-1440.png": "/help/",
        }
        for filename, route in pages.items():
            response = page.goto(f"{args.base_url}{route}", wait_until="networkidle")
            if response is None or not response.ok:
                raise RuntimeError(f"Screenshot route failed: {route}")
            destination = args.output / filename
            page.screenshot(path=str(destination), full_page=True)
            captured.append(str(destination))

        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{args.base_url}/help/", wait_until="networkidle")
        mobile_path = args.output / "help-390.png"
        page.screenshot(path=str(mobile_path), full_page=True)
        captured.append(str(mobile_path))
        browser.close()

    print(json.dumps({"captured": captured}, indent=2))


if __name__ == "__main__":
    main()
