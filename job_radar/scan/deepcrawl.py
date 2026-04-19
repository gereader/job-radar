"""Generic careers-page crawler for companies with no known ATS.

Heuristic: follow any anchor whose href or text suggests a job posting, then
on the target page extract title + body. Brittle by nature — use only for
companies you care about enough to tolerate false positives.
"""

from __future__ import annotations

from collections.abc import Iterable

from .base import RawJob

source = "deep-crawl"


def fetch(slug: str, name: str, **_kw) -> Iterable[RawJob]:
    """`slug` here is the careers URL, passed through portals.yml."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        raise RuntimeError(
            "playwright not installed. `pip install -e '.[playwright]' && "
            "playwright install chromium`"
        )

    careers_url = slug
    if not careers_url.startswith("http"):
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(careers_url, wait_until="networkidle", timeout=30000)
        except Exception:
            context.close(); browser.close()
            return

        # Gather candidate job links.
        links = page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]'))
              .map(a => ({href: a.href, text: (a.innerText||'').trim()}))
              .filter(x => x.text.length > 6 && x.text.length < 160)
              .filter(x => /job|position|role|opening|career|apply/i.test(x.href + ' ' + x.text))
        """)
        seen = set()
        for link in links[:60]:
            href = link["href"]
            if href in seen or href == careers_url:
                continue
            seen.add(href)
            try:
                jp = context.new_page()
                jp.goto(href, wait_until="domcontentloaded", timeout=20000)
                title_el = jp.query_selector("h1") or jp.query_selector("h2")
                title = title_el.inner_text().strip() if title_el else link["text"]
                body_html = (
                    jp.query_selector("main") or jp.query_selector("article")
                    or jp.query_selector("body")
                ).inner_html()
                jp.close()
                yield RawJob(
                    source=source, source_id=href, company=name,
                    title=title, url=href, body_html=body_html,
                )
            except Exception:
                continue

        context.close()
        browser.close()
