"""Ashby scanner — public jobs feed. Zero auth, zero LLM."""

from __future__ import annotations

from collections.abc import Iterable

import httpx

from .base import RawJob

source = "ashby"


def fetch(slug: str, name: str, *, client: httpx.Client | None = None) -> Iterable[RawJob]:
    own = client is None
    client = client or httpx.Client(timeout=20.0, follow_redirects=True)
    try:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
        r = client.get(url)
        if r.status_code in (400, 404):
            return
        r.raise_for_status()
        payload = r.json()
        for j in payload.get("jobs", []):
            yield RawJob(
                source=source,
                source_id=str(j.get("id")),
                company=name,
                title=j.get("title", ""),
                url=j.get("jobUrl", ""),
                body_html=j.get("descriptionHtml", "") or "",
                location=j.get("locationName") or j.get("location"),
                posted_at=j.get("publishedAt"),
                raw={"ashby_id": j.get("id")},
            )
    finally:
        if own:
            client.close()
