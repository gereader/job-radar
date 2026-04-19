"""Extract structured fields from a normalized JD markdown blob.

Pure-Python heuristics. Anything ambiguous is left blank for Haiku to fill
later; that way we don't spend tokens on jobs that get screened out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class JDFields:
    title: str | None = None
    location: str | None = None
    remote: str | None = None            # remote|hybrid|onsite|unknown
    comp_min: int | None = None
    comp_max: int | None = None
    comp_currency: str | None = None
    requirements: list[str] | None = None


_MONEY_RE = re.compile(
    r"""
    (?P<currency>\$|USD|EUR|GBP|£|€)?\s?
    (?P<low>\d{2,3}(?:[.,]\d{3})?|\d{2,3}[kK])
    \s?(?:-|–|—|to)\s?
    (?P<currency2>\$|USD|EUR|GBP|£|€)?\s?
    (?P<high>\d{2,3}(?:[.,]\d{3})?|\d{2,3}[kK])
    """,
    re.VERBOSE,
)

_REMOTE_RE = re.compile(r"\b(fully\s+remote|100%\s+remote|remote[-\s]?first|remote)\b", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
_ONSITE_RE = re.compile(r"\b(on[-\s]?site|in[-\s]?office)\b", re.I)

_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.*\S)", re.M)


def _to_int(money: str) -> int | None:
    s = money.replace(",", "").replace(".", "").lower()
    if s.endswith("k"):
        try:
            return int(float(s[:-1]) * 1000)
        except ValueError:
            return None
    try:
        n = int(s)
    except ValueError:
        return None
    # 60 → 60000 when it's clearly "$60k" shorthand without the k
    if n < 1000:
        n *= 1000
    return n


def extract_comp(md: str) -> tuple[int | None, int | None, str | None]:
    m = _MONEY_RE.search(md)
    if not m:
        return None, None, None
    lo = _to_int(m.group("low"))
    hi = _to_int(m.group("high"))
    cur = m.group("currency") or m.group("currency2") or ""
    cur = {"$": "USD", "£": "GBP", "€": "EUR"}.get(cur, cur) or None
    if lo and hi and lo > hi:
        lo, hi = hi, lo
    return lo, hi, cur


def extract_remote(md: str) -> str:
    if _REMOTE_RE.search(md):
        return "remote"
    if _HYBRID_RE.search(md):
        return "hybrid"
    if _ONSITE_RE.search(md):
        return "onsite"
    return "unknown"


def extract_location(md: str) -> str | None:
    # Look for "Location:" lines or headings.
    m = re.search(r"(?im)^\**\s*location\s*[:\-]\s*(.+)$", md)
    if m:
        return m.group(1).strip(" *_")
    return None


def extract_requirements(md: str, limit: int = 30) -> list[str]:
    bullets = _BULLET_RE.findall(md)
    # Prefer bullets under headings that look like requirements.
    reqs: list[str] = []
    for line in bullets:
        if len(line) > 300:
            continue
        reqs.append(line.strip())
        if len(reqs) >= limit:
            break
    return reqs


def extract_all(title: str, md: str) -> JDFields:
    lo, hi, cur = extract_comp(md)
    return JDFields(
        title=title,
        location=extract_location(md),
        remote=extract_remote(md),
        comp_min=lo,
        comp_max=hi,
        comp_currency=cur,
        requirements=extract_requirements(md),
    )
