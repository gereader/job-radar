"""Lever scanner — public postings endpoint. Zero auth, zero LLM."""

from __future__ import annotations

from typing import Iterable

import httpx

from .base import RawJob

source = "lever"


def fetch(slug: str, name: str, *, client: httpx.Client | None = None) -> Iterable[RawJob]:
    own = client is None
    client = client or httpx.Client(timeout=20.0, follow_redirects=True)
    try:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        r = client.get(url)
        if r.status_code in (400, 404):
            return
        r.raise_for_status()
        for j in r.json():
            desc_html = j.get("descriptionHtml", "") or ""
            lists = j.get("lists") or []
            for section in lists:
                title = section.get("text", "")
                content = section.get("content", "")
                desc_html += f"<h3>{title}</h3>{content}"
            yield RawJob(
                source=source,
                source_id=j.get("id", ""),
                company=name,
                title=j.get("text", ""),
                url=j.get("hostedUrl") or j.get("applyUrl", ""),
                body_html=desc_html,
                location=(j.get("categories") or {}).get("location"),
                posted_at=j.get("createdAt"),
                raw={"lever_id": j.get("id")},
            )
    finally:
        if own:
            client.close()
