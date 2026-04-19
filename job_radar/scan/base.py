"""Shared scanner types. Each portal module yields RawJob instances."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol


@dataclass
class RawJob:
    source: str
    source_id: str
    company: str
    title: str
    url: str
    body_html: str = ""
    body_markdown: str = ""
    location: str | None = None
    posted_at: str | None = None       # ISO 8601 if known
    raw: dict = field(default_factory=dict)  # keep upstream payload for debugging


class Scanner(Protocol):
    source: str

    def fetch(self, slug: str, name: str) -> Iterable[RawJob]: ...
