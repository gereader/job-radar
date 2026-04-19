"""`jr triage --batch` — submit triage prompts via Anthropic Messages Batch API.

Batch API pricing is 50% of sync pricing; turnaround typically well under
24h. Right for a weekly refresh, wrong for interactive single-job triage.

Flow:
  1. `jr triage --batch submit` — collects all review-bucket jobs, builds
     Request objects, submits them. Writes batch_id + items to DB, exits.
  2. `jr triage --batch poll` (or `check`) — fetches status. When done,
     downloads results, parses, writes triage_verdict back to jobs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_TRIAGE = Path(__file__).parent.parent.parent / "modes" / "triage.md"


def _build_system(cfg: Config) -> str:
    shared = _SHARED.read_text()
    triage = _TRIAGE.read_text()
    profile = cfg.profile or {}
    targets = profile.get("targets", {})
    archetypes = targets.get("archetypes", [])
    dealbreakers = targets.get("dealbreakers", [])
    return (
        f"{shared}\n\n---\n\n{triage}\n\n---\n\n## Profile\n"
        f"archetypes: {json.dumps(archetypes)}\n"
        f"dealbreakers: {json.dumps(dealbreakers)}\n"
        f"comp target: {json.dumps(targets.get('comp', {}))}\n"
        f"location policy: {targets.get('location_policy', 'remote')}\n"
    )


def submit(limit: int = 0) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    try:
        from anthropic import Anthropic
    except ImportError:
        console.print("[red]anthropic sdk missing.[/red] `pip install anthropic`")
        return

    rows = conn.execute(
        """
        SELECT id, company, title, jd_path, screen_score, screen_reasons
        FROM jobs
        WHERE screen_verdict = 'review' AND triage_verdict IS NULL
        ORDER BY id ASC
        """
    ).fetchall()
    if limit:
        rows = rows[:limit]
    if not rows:
        console.print("no jobs to batch — review bucket is empty.")
        return

    system = _build_system(cfg)
    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")

    requests = []
    for r in rows:
        jd = cfg.root / (r["jd_path"] or "")
        jd_md = jd.read_text() if jd.exists() else ""
        user = (
            f"Company: {r['company']}\nTitle: {r['title']}\n"
            f"Pre-screen score: {r['screen_score']}\n"
            f"Pre-screen reasons: {r['screen_reasons']}\n\n---\n\n{jd_md[:8000]}"
        )
        requests.append({
            "custom_id": f"job-{r['id']}",
            "params": {
                "model": model,
                "max_tokens": 512,
                "system": [{"type": "text", "text": system,
                            "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}],
            },
        })

    client = Anthropic()
    batch = client.messages.batches.create(requests=requests)

    with tx(conn):
        conn.execute(
            "INSERT INTO batch_jobs(batch_id, operation, model, n_requests, status) "
            "VALUES (?, 'triage', ?, ?, ?)",
            (batch.id, model, len(requests), batch.processing_status),
        )
        for req, row in zip(requests, rows):
            conn.execute(
                "INSERT INTO batch_items(batch_id, custom_id, job_id) VALUES (?, ?, ?)",
                (batch.id, req["custom_id"], row["id"]),
            )
    console.print(
        f"[green]submitted batch[/green] id={batch.id} n={len(requests)} "
        f"model={model} (billed at 50% of sync rate)"
    )
    console.print("[dim]poll with `jr triage --batch poll` or wait for completion.[/dim]")


def poll() -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    try:
        from anthropic import Anthropic
    except ImportError:
        console.print("[red]anthropic sdk missing.[/red]")
        return

    client = Anthropic()
    pending = conn.execute(
        "SELECT batch_id, operation, model, n_requests FROM batch_jobs "
        "WHERE status NOT IN ('ended','cancelled','expired')"
    ).fetchall()
    if not pending:
        console.print("no pending batches.")
        return

    t = Table(title="Batches")
    for c in ("Batch", "Op", "Model", "N", "Status"):
        t.add_column(c)

    for row in pending:
        batch = client.messages.batches.retrieve(row["batch_id"])
        status = batch.processing_status
        t.add_row(row["batch_id"][:20] + "…", row["operation"], row["model"],
                  str(row["n_requests"]), status)
        if status == "ended":
            _ingest_results(conn, client, row["batch_id"])
            with tx(conn):
                conn.execute(
                    "UPDATE batch_jobs SET status='ended', completed_at=datetime('now') "
                    "WHERE batch_id = ?",
                    (row["batch_id"],),
                )
        else:
            with tx(conn):
                conn.execute(
                    "UPDATE batch_jobs SET status = ? WHERE batch_id = ?",
                    (status, row["batch_id"]),
                )
    console.print(t)


def _ingest_results(conn, client, batch_id: str) -> None:
    results = client.messages.batches.results(batch_id)
    for entry in results:
        custom_id = entry.custom_id
        item = conn.execute(
            "SELECT job_id FROM batch_items WHERE batch_id = ? AND custom_id = ?",
            (batch_id, custom_id),
        ).fetchone()
        if not item or item["job_id"] is None:
            continue
        result = entry.result
        if getattr(result, "type", "") != "succeeded":
            continue
        msg = result.message
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        try:
            parsed = json.loads(text.strip().strip("`"))
        except json.JSONDecodeError:
            parsed = {"verdict": "review", "notes": text[:200]}
        usage = msg.usage
        with tx(conn):
            conn.execute(
                "UPDATE jobs SET triage_verdict = ?, triage_notes = ? WHERE id = ?",
                (parsed.get("verdict", "review"), json.dumps(parsed), item["job_id"]),
            )
            conn.execute(
                "INSERT INTO llm_usage(model, operation, input_tokens, output_tokens, "
                "cached_tokens, job_id) VALUES (?, 'triage_batch', ?, ?, ?, ?)",
                (msg.model, usage.input_tokens, usage.output_tokens,
                 getattr(usage, "cache_read_input_tokens", 0) or 0, item["job_id"]),
            )
    console.print(f"[green]ingested results[/green] for batch {batch_id}")
