"""`jr thanks <round_id>` — Haiku draft of a thank-you note.

Two backends: direct API or queue/ingest. ``run_thanks(round_id)`` either
prints a draft inline (direct) or writes a one-item queue dir (queue);
``ingest_thanks(queue_dir)`` folds a previously-prepared draft back in
and offers to mark the round + log a touchpoint.

Bulk path: ``run_thanks_due()`` selects rounds with ``status='completed'
AND thank_you_sent_at IS NULL`` (ranked soonest-completed first), slices
to the default LLM batch, and queues a packet per round.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Confirm

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from .client import DirectLLM, QueueLLM, log_queue_ingest
from .dispatcher import build_llm
from .ranker import rank_and_slice, resolved_default

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_THANKS = Path(__file__).parent.parent.parent / "modes" / "thanks.md"

THANKS_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["body_md"],
    "properties": {
        "subject": {"type": "string"},
        "body_md": {"type": "string"},
    },
}


def _system(cfg: Config) -> str:
    return _SHARED.read_text() + "\n\n---\n\n" + _THANKS.read_text()


def _user_prompt(cfg: Config, row) -> str:
    ident = (cfg.profile.get("identity") or {}).get("name", "").split()
    first = ident[0] if ident else "Candidate"
    return (
        f"Candidate: {first}\n"
        f"Company: {row['company']}\nRole: {row['title']}\n"
        f"Round: {row['round_number']} ({row['kind']})\n"
        f"Interviewer: {row['interviewer_name'] or 'unknown'}"
        f" ({row['interviewer_title'] or 'title unknown'})\n"
        f"When: {row['scheduled_at'] or 'recent'}\n\n"
        f"Round notes:\n{row['notes'] or '(no notes captured)'}\n"
    )


def _round(conn, round_id: int):
    return conn.execute(
        """
        SELECT r.*, j.company, j.title, a.id AS app_id
        FROM interview_rounds r
        JOIN applications a ON a.id = r.application_id
        JOIN jobs j ON j.id = a.job_id
        WHERE r.id = ?
        """,
        (round_id,),
    ).fetchone()


def _mark_sent(conn, round_id: int, app_id: int, summary: str) -> None:
    with tx(conn):
        conn.execute(
            "UPDATE interview_rounds SET thank_you_sent_at = datetime('now') WHERE id = ?",
            (round_id,),
        )
        conn.execute(
            """
            INSERT INTO touchpoints(application_id, channel, direction, summary)
            VALUES (?, 'email', 'outbound', ?)
            """,
            (app_id, summary),
        )


def run_thanks(round_id: int, *, force_prepare: bool = False) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = _round(conn, round_id)
    if not row:
        console.print(f"[red]no round {round_id}[/red]")
        return
    if row["status"] != "completed":
        console.print(
            f"[yellow]round {round_id} status is {row['status']} — "
            "complete it first with `jr round update`[/yellow]"
        )
        if not Confirm.ask("draft anyway?", default=False):
            return

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    backend, llm = build_llm(
        conn, cfg,
        operation="thanks",
        default_model=model,
        result_schema=THANKS_RESULT_SCHEMA,
        force=("queue" if force_prepare else None),
    )
    system = _system(cfg)
    user = _user_prompt(cfg, row)

    if backend == "queue":
        assert isinstance(llm, QueueLLM)
        llm.enqueue(
            system=system, user=user, item_id=round_id,
            meta={"round_id": round_id, "app_id": row["app_id"],
                  "round_number": row["round_number"], "kind": row["kind"]},
            max_tokens=400,
        )
        qdir = llm.finalize()
        console.print(f"[green]queued[/green] thanks draft → {qdir}")
        console.print(
            f"Next: [bold]/jr consume {qdir}[/bold], "
            f"then [bold]jr thanks --ingest {qdir}[/bold]."
        )
        return

    assert isinstance(llm, DirectLLM)
    resp = llm.complete(
        system=system, user=user, operation="thanks",
        app_id=row["app_id"], max_tokens=400,
    )
    console.print("\n[bold]Draft:[/bold]\n")
    console.print(resp.text)
    console.print()
    if Confirm.ask("mark thank-you as sent?", default=False):
        _mark_sent(conn, round_id, row["app_id"],
                   f"Thank-you after round {row['round_number']} ({row['kind']})")
        console.print("[green]logged.[/green]")


def run_thanks_due(*, limit: int = 0, all_: bool = False, rank_debug: bool = False,
                   force_prepare: bool = False) -> None:
    """Bulk path: queue thank-you drafts for every completed-but-unsent round."""
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    rows = conn.execute(
        """
        SELECT r.id AS round_id, r.round_number, r.kind, r.scheduled_at,
               r.interviewer_name, r.interviewer_title, r.notes, r.status,
               a.id AS app_id, j.company, j.title
        FROM interview_rounds r
        JOIN applications a ON a.id = r.application_id
        JOIN jobs j ON j.id = a.job_id
        WHERE r.status = 'completed' AND r.thank_you_sent_at IS NULL
        ORDER BY r.scheduled_at DESC
        """
    ).fetchall()
    if not rows:
        console.print("(no completed-but-unsent rounds)")
        return

    def _key(r) -> float:
        sched = r["scheduled_at"]
        if not sched:
            return 0.0
        try:
            t = datetime.fromisoformat(sched.replace("Z", "+00:00"))
            return t.timestamp()
        except (TypeError, ValueError):
            return 0.0

    default_n = resolved_default(cfg.profile)
    requested = limit if limit > 0 else default_n
    sliced = rank_and_slice(rows, key=_key, limit=requested, all_=all_)

    if rank_debug:
        from .ranker import print_rank_debug
        print_rank_debug(
            list(rows), key=_key,
            columns=[
                ("round", lambda r: r["round_id"]),
                ("company", lambda r: r["company"]),
                ("kind", lambda r: r["kind"]),
                ("when", lambda r: r["scheduled_at"]),
            ],
            title=f"Thanks rank ({len(rows)} due)", console=console,
        )
        return

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    backend, llm = build_llm(
        conn, cfg, operation="thanks", default_model=model,
        result_schema=THANKS_RESULT_SCHEMA,
        force=("queue" if force_prepare else None),
    )
    system = _system(cfg)

    if backend == "direct":
        assert isinstance(llm, DirectLLM)
        for r in sliced.picked:
            resp = llm.complete(
                system=system, user=_user_prompt(cfg, r),
                operation="thanks", app_id=r["app_id"], max_tokens=400,
            )
            console.print(f"\n[bold]round {r['round_id']} — {r['company']}[/bold]\n")
            console.print(resp.text)
        hint = sliced.hint(command="jr thanks --due", current_limit=requested)
        if hint:
            console.print(hint)
        return

    assert isinstance(llm, QueueLLM)
    for r in sliced.picked:
        llm.enqueue(
            system=system, user=_user_prompt(cfg, r), item_id=r["round_id"],
            meta={"round_id": r["round_id"], "app_id": r["app_id"],
                  "round_number": r["round_number"], "kind": r["kind"],
                  "company": r["company"]},
            max_tokens=400,
        )
    qdir = llm.finalize()
    console.print(f"[green]queued[/green] {len(sliced.picked)} thanks drafts → {qdir}")
    hint = sliced.hint(command="jr thanks --due", current_limit=requested)
    if hint:
        console.print(hint)
    console.print(
        f"Next: [bold]/jr consume {qdir}[/bold], "
        f"then [bold]jr thanks --ingest {qdir}[/bold]."
    )


def ingest_thanks(queue_dir: Path) -> None:
    """Print drafts; offer to mark each round sent."""
    from .queue import ingest as q_ingest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    results = q_ingest(queue_dir)
    for r in results:
        meta = r.meta or {}
        round_id = int(meta.get("round_id") or r.id)
        app_id = int(meta.get("app_id") or 0)
        body = r.result.get("body_md") if isinstance(r.result, dict) else str(r.result)
        subject = r.result.get("subject") if isinstance(r.result, dict) else None
        console.print(f"\n[bold]round {round_id}[/bold] — {meta.get('company', '?')}")
        if subject:
            console.print(f"Subject: {subject}")
        console.print(f"\n{body}\n")
        log_queue_ingest(conn, operation="thanks", item_count=1, app_id=app_id or None)
        if app_id and Confirm.ask(f"mark round {round_id} thank-you as sent?", default=False):
            _mark_sent(
                conn, round_id, app_id,
                f"Thank-you after round {meta.get('round_number', '?')} "
                f"({meta.get('kind', '?')})",
            )
            console.print("[green]logged.[/green]")
    console.print(f"\n[green]ingest complete[/green] — {queue_dir}")
