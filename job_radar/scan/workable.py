"""Workable / generic-browser scanner — uses playwright-python.

Only imported when `jr scan` actually encounters a playwright source, so the
base install stays light. Install with `pip install -e '.[playwright]'`.
"""

from __future__ import annotations

from collections.abc import Iterable

from .base import RawJob

source = "workable"


def fetch(slug: str, name: str, **_kw) -> Iterable[RawJob]:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        raise RuntimeError(
            "playwright not installed. Run: pip install -e '.[playwright]' "
            "&& playwright install chromium"
        )

    board_url = f"https://apply.workable.com/{slug}/"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.goto(board_url, wait_until="networkidle", timeout=30000)
        # Workable lists jobs as <a> links with role="link"
        anchors = page.query_selector_all("a[href*='/j/']")
        hrefs = list({a.get_attribute("href") for a in anchors if a.get_attribute("href")})

        for href in hrefs:
            url = href if href.startswith("http") else f"https://apply.workable.com{href}"
            try:
                jp = context.new_page()
                jp.goto(url, wait_until="networkidle", timeout=30000)
                title_el = jp.query_selector("h1, h2")
                title = title_el.inner_text().strip() if title_el else ""
                body_html = (
                    jp.query_selector("main") or jp.query_selector("body")
                ).inner_html()
                loc_el = jp.query_selector("[data-ui='job-location']")
                loc = loc_el.inner_text().strip() if loc_el else None
                jp.close()
                yield RawJob(
                    source=source,
                    source_id=url.rsplit("/", 1)[-1],
                    company=name,
                    title=title,
                    url=url,
                    body_html=body_html,
                    location=loc,
                )
            except Exception:
                continue

        context.close()
        browser.close()
