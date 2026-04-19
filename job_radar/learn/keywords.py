"""`jr learn keywords` — interactive keyword-learning loop.

Pulls outcome-labeled JDs, asks Haiku to propose rule changes, shows the
user each suggestion with evidence, and writes accepted ones into
`private/keywords.yml` (never auto-writes).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.prompt import Confirm

from ..config import Config
from ..db import connect, migrate
from ..llm.client import LLM

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_LEARN = Path(__file__).parent.parent.parent / "modes" / "learn-keywords.md"

_POS = ("Applied", "Responded", "Interview", "Offer")
_NEG = ("SKIP", "Discarded", "Rejected")


def _corpus(conn, statuses: tuple[str, ...], limit: int = 30) -> list[str]:
    rows = conn.execute(
        f"""
        SELECT j.company, j.title, j.location, j.jd_path
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.status IN ({','.join('?' * len(statuses))})
        ORDER BY a.updated_at DESC LIMIT ?
        """,
        (*statuses, limit),
    ).fetchall()
    cfg = Config.load()
    out: list[str] = []
    for r in rows:
        body = ""
        if r["jd_path"]:
            p = cfg.root / r["jd_path"]
            if p.exists():
                body = p.read_text()[:1500]
        out.append(
            f"- {r['company']} / {r['title']} / {r['location'] or ''}\n{body}\n"
        )
    return out


def _load_keywords(cfg: Config) -> dict[str, list[dict[str, Any]]]:
    if not cfg.keywords_path.exists():
        return {"positive": [], "negative": [], "dealbreaker": []}
    data = yaml.safe_load(cfg.keywords_path.read_text()) or {}
    return {
        "positive": data.get("positive") or [],
        "negative": data.get("negative") or [],
        "dealbreaker": data.get("dealbreaker") or [],
    }


def _dump_keywords(cfg: Config, data: dict) -> None:
    cfg.keywords_path.write_text(yaml.safe_dump(data, sort_keys=False))


def _exists(rules: list[dict], term: str) -> bool:
    t = term.lower().strip()
    return any((r.get("term") or "").lower().strip() == t for r in rules)


def run_learn_keywords() -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    pos = _corpus(conn, _POS)
    neg = _corpus(conn, _NEG)
    if len(neg) < 5:
        console.print(f"[yellow]only {len(neg)} negative examples — need ≥5 for useful signal.[/yellow]")
        return

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    llm = LLM(conn, default_model=model)
    system = _SHARED.read_text() + "\n\n---\n\n" + _LEARN.read_text()
    user = (
        f"# POSITIVE corpus ({len(pos)} jobs)\n\n" + "\n".join(pos) + "\n\n"
        f"# NEGATIVE corpus ({len(neg)} jobs)\n\n" + "\n".join(neg)
    )
    resp = llm.complete(
        system=system, user=user, operation="learn_keywords", max_tokens=1200,
    )
    try:
        proposals = json.loads(resp.text.strip().strip("`"))
    except json.JSONDecodeError:
        console.print(f"[red]model output not JSON:[/red] {resp.text[:300]}")
        return

    rules = _load_keywords(cfg)
    changed = False
    for kind_key, file_key in (
        ("add_positive", "positive"),
        ("add_negative", "negative"),
        ("add_dealbreaker", "dealbreaker"),
    ):
        for p in proposals.get(kind_key, []):
            term = (p.get("term") or "").strip()
            if not term or _exists(rules[file_key], term):
                continue
            console.print(
                f"\n[bold]{file_key}[/bold]: '{term}' @{p.get('field','any')} "
                f"weight={p.get('weight', 0) if file_key != 'dealbreaker' else '-'}\n"
                f"  evidence: {p.get('evidence', '')}"
            )
            if Confirm.ask("add this rule?", default=True):
                entry = {"term": term, "field": p.get("field", "any")}
                if file_key != "dealbreaker":
                    entry["weight"] = int(p.get("weight", 4))
                rules[file_key].append(entry)
                changed = True

    for p in proposals.get("retire", []):
        term = (p.get("term") or "").strip()
        if not term:
            continue
        for kind in ("positive", "negative", "dealbreaker"):
            before = len(rules[kind])
            rules[kind] = [
                r for r in rules[kind]
                if (r.get("term") or "").lower().strip() != term.lower()
            ]
            if len(rules[kind]) < before:
                console.print(f"\n[yellow]retire[/yellow] {kind}:'{term}' — {p.get('reason','')}")
                if Confirm.ask("remove this rule?", default=False):
                    changed = True
                else:
                    # put back
                    pass

    if changed:
        _dump_keywords(cfg, rules)
        console.print(f"[green]updated[/green] {cfg.keywords_path}")
    else:
        console.print("no changes accepted.")

    if proposals.get("notes"):
        console.print(f"\n[dim]notes:[/dim] {proposals['notes']}")
