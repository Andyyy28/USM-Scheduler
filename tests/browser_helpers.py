from __future__ import annotations

from playwright.sync_api import Page


def assert_browser_assets(page: Page) -> None:
    """Verify linked assets are reachable and the page has parsed CSS rules."""

    assets = page.locator("link[rel='stylesheet'][href], script[src]").evaluate_all(
        """
        (elements) => [...new Set(elements.map((element) => element.href || element.src))]
        """
    )
    assert assets, "The page did not link any CSS or JavaScript assets."
    responses = page.evaluate(
        """
        async (urls) => Promise.all(urls.map(async (url) => {
          const response = await fetch(url, {credentials: "same-origin", cache: "no-store"});
          return {url, status: response.status};
        }))
        """,
        assets,
    )
    failures = [item for item in responses if item["status"] != 200]
    assert not failures, f"Linked CSS/JS assets did not return HTTP 200: {failures}"
    assert page.evaluate(
        """
        [...document.styleSheets].some((sheet) => {
          try { return sheet.cssRules.length > 0; } catch (_error) { return false; }
        })
        """
    ), "No loaded stylesheet contained parsed CSS rules."

