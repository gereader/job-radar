"""Zero-LLM pre-screen. Decides pass / review / skip before any paid model."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

Verdict = Literal["pass", "review", "skip"]


@dataclass
class Rule:
    term: str
    weight: int = 0
    field: str = "any"


@dataclass
class Ruleset:
    positive: list[Rule] = field(default_factory=list)
    negative: list[Rule] = field(default_factory=list)
    dealbreaker: list[Rule] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> "Ruleset":
        data = yaml.safe_load(path.read_text()) or {}
        return cls(
            positive=[Rule(**r) for r in data.get("positive", [])],
            negative=[Rule(**r) for r in data.get("negative", [])],
            dealbreaker=[Rule(**r) for r in data.get("dealbreaker", [])],
        )


@dataclass
class ScreenResult:
    score: int
    verdict: Verdict
    reasons: list[str]

    def as_json_reasons(self) -> str:
        import json
        return json.dumps(self.reasons, ensure_ascii=False)


def _field_text(fields: dict[str, str], field: str) -> str:
    if field == "any":
        return " \n ".join(fields.values()).lower()
    return (fields.get(field, "") or "").lower()


def screen(
    title: str,
    description: str,
    location: str | None,
    ruleset: Ruleset,
    pass_at: int = 70,
    review_at: int = 40,
) -> ScreenResult:
    fields = {
        "title": title or "",
        "description": description or "",
        "location": location or "",
    }
    score = 0
    reasons: list[str] = []

    for rule in ruleset.dealbreaker:
        if rule.term.lower() in _field_text(fields, rule.field):
            reasons.append(f"dealbreaker:{rule.term}@{rule.field}")
            return ScreenResult(score=0, verdict="skip", reasons=reasons)

    for rule in ruleset.positive:
        if rule.term.lower() in _field_text(fields, rule.field):
            score += rule.weight
            reasons.append(f"+{rule.weight}:{rule.term}@{rule.field}")

    for rule in ruleset.negative:
        if rule.term.lower() in _field_text(fields, rule.field):
            score -= rule.weight
            reasons.append(f"-{rule.weight}:{rule.term}@{rule.field}")

    score = max(0, min(100, score))
    if score >= pass_at:
        verdict: Verdict = "pass"
    elif score >= review_at:
        verdict = "review"
    else:
        verdict = "skip"

    return ScreenResult(score=score, verdict=verdict, reasons=reasons)
