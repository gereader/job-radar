"""Content hashing for dedup. Same (company+title+body) → same hash."""

from __future__ import annotations

import hashlib
import re
import unicodedata


def _normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9 \-]", "", s)
    return s.strip()


def content_hash(company: str, title: str, body: str) -> str:
    payload = f"{_normalize(company)}|{_normalize(title)}|{_normalize(body)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().lower().encode("utf-8")).hexdigest()
