"""Follow-up cadence: pure SQL, optional Haiku drafts.

Two backends as everywhere else: direct API or queue. Bulk path
``draft_followup_all`` ranks the queue by ``next_action_at`` ascending
(oldest-due first), slices to the default LLM batch, and either prints
each draft inline (direct) or emits one packet per app (queue).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from ..config import Config
from ..db import connect, migrate
from ..llm.client import DirectLLM, QueueLLM, log_queue_ingest
from ..llm.dispatcher import build_llm
from ..llm.ranker import rank_and_slice, resolved_default

console = Console()


_CADENCE_SQL = """
SELECT a.id, j.company, j.title, j.url, a.status, a.applied_at,
       COALESCE(a.next_action_at, date(a.applied_at, '+7 days')) AS due,
       (SELECT MAX(occurred_at) FROM touchpoints t WHERE t.application_id = a.id) AS last_touch
FROM applications a
JOIN jobs j ON j.id = a.job_id
WHERE a.status IN ('Applied', 'Responded', 'Interview')
  AND (
    a.next_action_at IS NULL
    OR a.next_action_at <= date('now')
  )
ORDER BY due ASC, a.applied_at ASC
"""


FOLLOWUP_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["body_md"],
    "properties": {
        "subject": {"type": "string"},
        "body_md": {"type": "string"},
    },
}


_FOLLOWUP_SYSTEM = (
    "You write short, professional follow-up emails for a job candidate. "
    "Tone: warm but not chatty. No fluff, no cliches, no emojis. "
    "Under 120 words. Sign with the candidate's first name only."
)


def _user_prompt(cfg: Config, row) -> str:
    ident = (cfg.profile.get("identity") or {})
    return (
        f"Candidate: {ident.get('name', '')}\n"
        f"Company: {row['company']}\n"
        f"Role: {row['title']}\n"
        f"Applied: {row['applied_at'] or 'recent'}\n"
        f"Status: {row['status']}\n\n"
        f"Draft a follow-up asking for a status update. No attachments referenced."
    )


def show_queue() -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)
    rows = conn.execute(_CADENCE_SQL).fetchall()
    if not rows:
        console.print("queue is clean — no follow-ups due.")
        return
    t = Table(title=f"Follow-ups due ({len(rows)})")
    for col in ("#", "Company", "Role", "Status", "Due", "Last touch"):
        t.add_column(col)
    for r in rows:
        t.add_row(
            str(r["id"]), r["company"], r["title"], r["status"],
            r["due"] or "-", r["last_touch"] or "-",
        )
    console.print(t)


def draft_followup(app_id: int, *, force_prepare: bool = False) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)
    row = conn.execute(
        """
        SELECT a.id, a.status, a.applied_at, j.company, j.title, j.url
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = ?
        """,
        (app_id,),
    ).fetchone()
    if not row:
        console.print(f"[red]no application {app_id}[/red]")
        return

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    backend, llm = build_llm(
        conn, cfg, operation="followup_draft", default_model=model,
        result_schema=FOLLOWUP_RESULT_SCHEMA,
        force=("queue" if force_prepare else None),
    )

    if backend == "queue":
        assert isinstance(llm, QueueLLM)
        llm.enqueue(
            system=_FOLLOWUP_SYSTEM, user=_user_prompt(cfg, row), item_id=app_id,
            meta={"app_id": app_id, "company": row["company"], "title": row["title"]},
            max_tokens=400,
        )
        qdir = llm.finalize()
        console.print(
            f"[green]queued[/green] follow-up draft → {qdir}\n"
            f"Next: [bold]/jr consume {qdir}[/bold], "
            f"then [bold]jr followup --ingest {qdir}[/bold]."
        )
        return

    assert isinstance(llm, DirectLLM)
    resp = llm.complete(
        system=_FOLLOWUP_SYSTEM, user=_user_prompt(cfg, row),
        operation="followup_draft", app_id=app_id, max_tokens=400,
    )
    console.print(resp.text)


def draft_followup_all(
    *, limit: int = 0, all_: bool = False, rank_debug: bool = False,
    force_prepare: bool = False,
) -> None:
    """Bulk: draft follow-ups for every app due in the queue."""
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    rows = conn.execute(_CADENCE_SQL).fetchall()
    if not rows:
        console.print("queue is clean — no follow-ups due.")
        return

    def _key(r) -> float:
        # Earlier `due` ranks higher → return negative timestamp.
        due = r["due"]
        if not due:
            return 0.0
        # YYYY-MM-DD strings sort lexically; convert to a small int we
        # can negate so rank_and_slice (descending) yields oldest-first.
        try:
            return -float(due.replace("-", ""))
        except (ValueError, AttributeError):
            return 0.0

    default_n = resolved_default(cfg.profile)
    requested = limit if limit > 0 else default_n
    sliced = rank_and_slice(rows, key=_key, limit=requested, all_=all_)

    if rank_debug:
        from ..llm.ranker import print_rank_debug
        print_rank_debug(
            list(rows), key=_key,
            columns=[
                ("app", lambda r: r["id"]),
                ("company", lambda r: r["company"]),
                ("status", lambda r: r["status"]),
                ("due", lambda r: r["due"]),
            ],
            title=f"Follow-up rank ({len(rows)} due)", console=console,
        )
        return

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    backend, llm = build_llm(
        conn, cfg, operation="followup_draft", default_model=model,
        result_schema=FOLLOWUP_RESULT_SCHEMA,
        force=("queue" if force_prepare else None),
    )

    if backend == "direct":
        assert isinstance(llm, DirectLLM)
        for r in sliced.picked:
            console.print(f"\n[bold]app {r['id']} — {r['company']} ({r['title']})[/bold]\n")
            resp = llm.complete(
                system=_FOLLOWUP_SYSTEM, user=_user_prompt(cfg, r),
                operation="followup_draft", app_id=r["id"], max_tokens=400,
            )
            console.print(resp.text)
        hint = sliced.hint(command="jr followup --draft-all", current_limit=requested)
        if hint:
            console.print(hint)
        return

    assert isinstance(llm, QueueLLM)
    for r in sliced.picked:
        llm.enqueue(
            system=_FOLLOWUP_SYSTEM, user=_user_prompt(cfg, r), item_id=r["id"],
            meta={"app_id": r["id"], "company": r["company"], "title": r["title"]},
            max_tokens=400,
        )
    qdir = llm.finalize()
    console.print(
        f"[green]queued[/green] {len(sliced.picked)} follow-up drafts → {qdir}"
    )
    hint = sliced.hint(command="jr followup --draft-all", current_limit=requested)
    if hint:
        console.print(hint)
    console.print(
        f"Next: [bold]/jr consume {qdir}[/bold], "
        f"then [bold]jr followup --ingest {qdir}[/bold]."
    )


def ingest_followup(queue_dir: Path) -> None:
    from ..llm.queue import ingest as q_ingest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    results = q_ingest(queue_dir)
    for r in results:
        meta = r.meta or {}
        body = r.result.get("body_md") if isinstance(r.result, dict) else str(r.result)
        subject = r.result.get("subject") if isinstance(r.result, dict) else None
        console.print(f"\n[bold]app {meta.get('app_id', '?')} — {meta.get('company', '?')}[/bold]")
        if subject:
            console.print(f"Subject: {subject}")
        console.print(f"\n{body}")
        log_queue_ingest(
            conn, operation="followup_draft", item_count=1,
            app_id=meta.get("app_id") or None,
        )
    console.print(f"\n[green]ingest complete[/green] — {queue_dir}")
