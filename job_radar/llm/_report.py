"""Helpers shared by single-shot report-emitting LLM ops (eval/research/
interview/offer): write the report to disk, update the right DB row,
log usage. The wrapper keeps each port short.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from ..config import Config
from ..util.slugify import slugify

REPORT_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["report_md"],
    "properties": {
        "report_md": {"type": "string"},
        "score_0_5": {"type": "number"},
        "archetype": {"type": "string"},
        "comp_band_fit": {"type": "string"},
        "strengths": {"type": "array"},
        "risks": {"type": "array"},
        "counter_script_md": {"type": "string"},
        "funding": {"type": ["string", "object", "null"]},
        "headcount": {"type": ["string", "integer", "null"]},
        "signals": {"type": "array"},
        "topics": {"type": "array"},
        "recent_questions": {"type": "array"},
    },
}


def report_text(result: Any) -> str:
    if isinstance(result, dict):
        return result.get("report_md") or result.get("body_md") or str(result)
    return str(result)


def write_app_report(
    cfg: Config, app_id: int, company: str, kind: str, content: str,
) -> Path:
    app_dir = cfg.applications_dir / f"{app_id}-{slugify(company)}"
    app_dir.mkdir(parents=True, exist_ok=True)
    out = app_dir / f"{kind}-{date.today().isoformat()}.md"
    out.write_text(content)
    return out


def write_research_path(cfg: Config, company: str, app_id: int | None) -> Path:
    if app_id:
        d = cfg.applications_dir / f"{app_id}-{slugify(company)}"
    else:
        d = cfg.private / "research" / slugify(company)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"company-{date.today().isoformat()}.md"
