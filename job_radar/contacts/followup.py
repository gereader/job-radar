"""Follow-up cadence: pure SQL, optional Haiku drafts."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from ..config import Config
from ..db import connect, migrate
from ..llm.client import LLM

console = Console()


_CADENCE_SQL = """
SELECT a.id, j.company, j.title, a.status,
       COALESCE(a.next_action_at, date(a.applied_at, '+7 days')) AS due,
       (SELECT MAX(occurred_at) FROM touchpoints t WHERE t.application_id = a.id) AS last_touch
FROM applications a
JOIN jobs j ON j.id = a.job_id
WHERE a.status IN ('Applied', 'Responded', 'Interview')
  AND (
    a.next_action_at IS NULL
    OR a.next_action_at <= date('now')
  )
ORDER BY due ASC NULLS FIRST
"""


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


def draft_followup(app_id: int) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)
    row = conn.execute(
        """
        SELECT a.*, j.company, j.title, j.url
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = ?
        """,
        (app_id,),
    ).fetchone()
    if not row:
        console.print(f"[red]no application {app_id}[/red]")
        return

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    llm = LLM(conn, default_model=model)
    ident = (cfg.profile.get("identity") or {})
    sys = (
        "You write short, professional follow-up emails for a job candidate. "
        "Tone: warm but not chatty. No fluff, no cliches, no emojis. "
        "Under 120 words. Sign with the candidate's first name only."
    )
    user = (
        f"Candidate: {ident.get('name', '')}\n"
        f"Company: {row['company']}\n"
        f"Role: {row['title']}\n"
        f"Applied: {row['applied_at'] or 'recent'}\n"
        f"Status: {row['status']}\n\n"
        f"Draft a follow-up asking for a status update. No attachments referenced."
    )
    resp = llm.complete(
        system=sys, user=user, operation="draft", app_id=app_id, max_tokens=400,
    )
    console.print(resp.text)
