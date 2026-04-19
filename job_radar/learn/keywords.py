"""`jr learn keywords` — interactive keyword-learning loop.

Pulls outcome-labeled JDs, asks Haiku to propose rule changes, shows the
user each suggestion with evidence, and writes accepted ones into
``private/keywords.yml`` (never auto-writes).

Two backends: direct API or queue. ``run_learn_keywords()`` either calls
Haiku inline (direct) or enqueues a single packet and exits (queue);
``ingest_learn_keywords(queue_dir)`` then reads the proposals back and
runs the same interactive accept/reject loop.

Pre-ranking: applications modified in the last 30 days only — we want
fresh signal, not history.
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
from ..llm.client import DirectLLM, QueueLLM, log_queue_ingest
from ..llm.dispatcher import build_llm

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_LEARN = Path(__file__).parent.parent.parent / "modes" / "learn-keywords.md"

_POS = ("Applied", "Responded", "Interview", "Offer")
_NEG = ("SKIP", "Discarded", "Rejected")

LEARN_KEYWORDS_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "add_positive": {"type": "array"},
        "add_negative": {"type": "array"},
        "add_dealbreaker": {"type": "array"},
        "retire": {"type": "array"},
        "notes": {"type": "string"},
    },
}


def _corpus(conn, statuses: tuple[str, ...], limit: int = 30) -> list[str]:
    """Most-recently-updated apps in the given outcome buckets, last 30 days."""
    rows = conn.execute(
        f"""
        SELECT j.company, j.title, j.location, j.jd_path
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.status IN ({','.join('?' * len(statuses))})
          AND a.updated_at >= datetime('now', '-30 day')
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


def _build_inputs(cfg: Config, conn) -> tuple[str, str, int, int] | None:
    pos = _corpus(conn, _POS)
    neg = _corpus(conn, _NEG)
    if len(neg) < 5:
        console.print(
            f"[yellow]only {len(neg)} negative examples in last 30 days "
            "— need ≥5 for useful signal.[/yellow]"
        )
        return None
    system = _SHARED.read_text() + "\n\n---\n\n" + _LEARN.read_text()
    user = (
        f"# POSITIVE corpus ({len(pos)} jobs)\n\n" + "\n".join(pos) + "\n\n"
        f"# NEGATIVE corpus ({len(neg)} jobs)\n\n" + "\n".join(neg)
    )
    return system, user, len(pos), len(neg)


def _apply_proposals(cfg: Config, proposals: dict) -> None:
    rules = _load_keywords(cfg)
    changed = False
    for kind_key, file_key in (
        ("add_positive", "positive"),
        ("add_negative", "negative"),
        ("add_dealbreaker", "dealbreaker"),
    ):
        for p in proposals.get(kind_key, []) or []:
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

    for p in proposals.get("retire", []) or []:
        term = (p.get("term") or "").strip()
        if not term:
            continue
        for kind in ("positive", "negative", "dealbreaker"):
            before = len(rules[kind])
            after = [
                r for r in rules[kind]
                if (r.get("term") or "").lower().strip() != term.lower()
            ]
            if len(after) < before:
                console.print(
                    f"\n[yellow]retire[/yellow] {kind}:'{term}' — {p.get('reason','')}"
                )
                if Confirm.ask("remove this rule?", default=False):
                    rules[kind] = after
                    changed = True

    if changed:
        _dump_keywords(cfg, rules)
        console.print(f"[green]updated[/green] {cfg.keywords_path}")
    else:
        console.print("no changes accepted.")

    if proposals.get("notes"):
        console.print(f"\n[dim]notes:[/dim] {proposals['notes']}")


def run_learn_keywords(*, force_prepare: bool = False) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    inputs = _build_inputs(cfg, conn)
    if inputs is None:
        return
    system, user, n_pos, n_neg = inputs

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    backend, llm = build_llm(
        conn, cfg, operation="learn_keywords", default_model=model,
        result_schema=LEARN_KEYWORDS_RESULT_SCHEMA,
        force=("queue" if force_prepare else None),
    )

    if backend == "queue":
        assert isinstance(llm, QueueLLM)
        llm.enqueue(
            system=system, user=user, item_id="all",
            meta={"positive_count": n_pos, "negative_count": n_neg},
            max_tokens=1200,
        )
        qdir = llm.finalize()
        console.print(
            f"[green]queued[/green] keyword-learning packet → {qdir}\n"
            f"Next: [bold]/jr consume {qdir}[/bold], "
            f"then [bold]jr learn keywords --ingest {qdir}[/bold]."
        )
        return

    assert isinstance(llm, DirectLLM)
    resp = llm.complete(
        system=system, user=user, operation="learn_keywords", max_tokens=1200,
    )
    try:
        proposals = json.loads(resp.text.strip().strip("`"))
    except json.JSONDecodeError:
        console.print(f"[red]model output not JSON:[/red] {resp.text[:300]}")
        return
    _apply_proposals(cfg, proposals)


def ingest_learn_keywords(queue_dir: Path) -> None:
    from ..llm.queue import ingest as q_ingest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    results = q_ingest(queue_dir)
    if not results:
        console.print("(empty queue)")
        return
    proposals = results[0].result if isinstance(results[0].result, dict) else {}
    log_queue_ingest(conn, operation="learn_keywords", item_count=1)
    _apply_proposals(cfg, proposals)
    console.print(f"\n[green]ingest complete[/green] — {queue_dir}")
