from __future__ import annotations

from slugify import slugify as _slugify


def slugify(s: str, max_length: int = 60) -> str:
    return _slugify(s or "", max_length=max_length, lowercase=True) or "unknown"
