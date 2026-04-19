"""Greenhouse scanner — uses the public boards API. Zero auth, zero LLM."""

from __future__ import annotations

from typing import Iterable

import httpx

from .base import RawJob

source = "greenhouse"


def fetch(slug: str, name: str, *, client: httpx.Client | None = None) -> Iterable[RawJob]:
    own = client is None
    client = client or httpx.Client(timeout=20.0, follow_redirects=True)
    try:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        r = client.get(url)
        if r.status_code == 404:
            return
        r.raise_for_status()
        payload = r.json()
        for j in payload.get("jobs", []):
            yield RawJob(
                source=source,
                source_id=str(j.get("id")),
                company=name,
                title=j.get("title", ""),
                url=j.get("absolute_url", ""),
                body_html=j.get("content", "") or "",
                location=(j.get("location") or {}).get("name"),
                posted_at=j.get("updated_at") or j.get("first_published"),
                raw={"gh_id": j.get("id")},
            )
    finally:
        if own:
            client.close()
