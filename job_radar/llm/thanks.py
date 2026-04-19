"""`jr thanks <round_id>` — Haiku draft of a thank-you note."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from .client import LLM

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_THANKS = Path(__file__).parent.parent.parent / "modes" / "thanks.md"


def run_thanks(round_id: int) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = conn.execute(
        """
        SELECT r.*, j.company, j.title, a.id AS app_id
        FROM interview_rounds r
        JOIN applications a ON a.id = r.application_id
        JOIN jobs j ON j.id = a.job_id
        WHERE r.id = ?
        """,
        (round_id,),
    ).fetchone()
    if not row:
        console.print(f"[red]no round {round_id}[/red]")
        return
    if row["status"] != "completed":
        console.print(
            f"[yellow]round {round_id} status is {row['status']} — complete it first with `jr round update`[/yellow]"
        )
        if not Confirm.ask("draft anyway?", default=False):
            return

    ident = (cfg.profile.get("identity") or {}).get("name", "").split()[0] or "Gene"
    user = (
        f"Candidate: {ident}\n"
        f"Company: {row['company']}\nRole: {row['title']}\n"
        f"Round: {row['round_number']} ({row['kind']})\n"
        f"Interviewer: {row['interviewer_name'] or 'unknown'}"
        f" ({row['interviewer_title'] or 'title unknown'})\n"
        f"When: {row['scheduled_at'] or 'recent'}\n\n"
        f"Round notes:\n{row['notes'] or '(no notes captured)'}\n"
    )
    system = _SHARED.read_text() + "\n\n---\n\n" + _THANKS.read_text()

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    llm = LLM(conn, default_model=model)
    resp = llm.complete(
        system=system, user=user, operation="thanks",
        app_id=row["app_id"], max_tokens=400,
    )

    console.print("\n[bold]Draft:[/bold]\n")
    console.print(resp.text)
    console.print()

    if Confirm.ask("mark thank-you as sent?", default=False):
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
                (row["app_id"], f"Thank-you after round {row['round_number']} ({row['kind']})"),
            )
        console.print("[green]logged.[/green]")
