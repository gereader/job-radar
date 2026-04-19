"""`jr round` — interview-round tracking per application."""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx

console = Console()

_KINDS = ["screen", "technical", "hiring-manager", "panel",
          "system-design", "take-home", "exec", "final", "other"]
_STATUSES = ["scheduled", "completed", "cancelled", "no-show"]
_OUTCOMES = ["advance", "reject", "pending", "unknown"]


def add_round(app_id: int) -> int:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    if not conn.execute("SELECT 1 FROM applications WHERE id = ?", (app_id,)).fetchone():
        console.print(f"[red]no application {app_id}[/red]")
        raise SystemExit(1)

    next_num = conn.execute(
        "SELECT COALESCE(MAX(round_number), 0) + 1 FROM interview_rounds WHERE application_id = ?",
        (app_id,),
    ).fetchone()[0]

    kind = Prompt.ask("Round kind", choices=_KINDS, default="screen")
    scheduled = Prompt.ask("When (YYYY-MM-DD HH:MM, blank if TBD)", default="")
    duration = Prompt.ask("Duration in minutes", default="45")
    name = Prompt.ask("Interviewer name", default="")
    title = Prompt.ask("Interviewer title", default="")
    email = Prompt.ask("Interviewer email", default="")
    notes = Prompt.ask("Notes (scope, prep areas)", default="")

    with tx(conn):
        cur = conn.execute(
            """
            INSERT INTO interview_rounds(application_id, round_number, kind,
                scheduled_at, duration_min, interviewer_name, interviewer_title,
                interviewer_email, notes, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled')
            """,
            (app_id, next_num, kind, scheduled or None,
             int(duration) if duration.isdigit() else None,
             name or None, title or None, email or None, notes or None),
        )
        round_id = cur.lastrowid
        conn.execute(
            "UPDATE applications SET status = CASE WHEN status IN ('Applied','Responded') "
            "THEN 'Interview' ELSE status END WHERE id = ?",
            (app_id,),
        )
    console.print(f"[green]round {round_id}[/green] scheduled (round #{next_num})")
    return round_id


def list_rounds(app_id: int) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    rows = conn.execute(
        """
        SELECT id, round_number, kind, scheduled_at, duration_min, status, outcome,
               interviewer_name, interviewer_title, thank_you_sent_at, notes
        FROM interview_rounds
        WHERE application_id = ?
        ORDER BY round_number ASC
        """,
        (app_id,),
    ).fetchall()
    if not rows:
        console.print(f"no rounds for app {app_id}.")
        return
    t = Table(title=f"Application {app_id} — interview rounds")
    for c in ("#", "Round", "Kind", "When", "Min", "Status", "Outcome",
              "Interviewer", "Thanks?", "Notes"):
        t.add_column(c)
    for r in rows:
        interviewer = " · ".join(
            x for x in (r["interviewer_name"], r["interviewer_title"]) if x
        )
        thanks = "yes" if r["thank_you_sent_at"] else "no"
        t.add_row(
            str(r["id"]), str(r["round_number"]), r["kind"],
            r["scheduled_at"] or "-", str(r["duration_min"] or "-"),
            r["status"], r["outcome"] or "-",
            interviewer or "-", thanks, (r["notes"] or "")[:60],
        )
    console.print(t)


def add_questions(round_id: int) -> None:
    """Interactive capture of questions asked in a round. Zero LLM."""
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = conn.execute(
        "SELECT id, application_id, round_number, kind FROM interview_rounds WHERE id = ?",
        (round_id,),
    ).fetchone()
    if not row:
        console.print(f"[red]no round {round_id}[/red]")
        return

    console.print(
        f"[bold]Capturing questions for round {round_id}[/bold] "
        f"(round #{row['round_number']} {row['kind']}). Empty question to stop."
    )
    captured = 0
    while True:
        q = Prompt.ask("Question", default="")
        if not q.strip():
            break
        asked_by = Prompt.ask("Asked by (interviewer)", default="")
        tags = Prompt.ask("Topic tags (comma-separated)", default="")
        difficulty_raw = Prompt.ask("Difficulty 1-5 (blank to skip)", default="")
        difficulty = int(difficulty_raw) if difficulty_raw.strip().isdigit() else None
        notes = Prompt.ask("Answer notes (what you said / what you wish you'd said)", default="")
        with tx(conn):
            conn.execute(
                """
                INSERT INTO round_questions(round_id, question, asked_by,
                    topic_tags, difficulty, answer_notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (round_id, q.strip(), asked_by or None, tags or None,
                 difficulty, notes or None),
            )
        captured += 1
    console.print(f"[green]captured {captured} question(s)[/green] for round {round_id}")


def list_questions(round_id: int) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)
    rows = conn.execute(
        """
        SELECT id, question, asked_by, topic_tags, difficulty, answer_notes,
               captured_at
        FROM round_questions
        WHERE round_id = ?
        ORDER BY id ASC
        """,
        (round_id,),
    ).fetchall()
    if not rows:
        console.print(f"(no questions captured for round {round_id})")
        return
    t = Table(title=f"Round {round_id} — {len(rows)} question(s)")
    for c in ("#", "Question", "Asked by", "Tags", "Diff"):
        t.add_column(c)
    for r in rows:
        t.add_row(
            str(r["id"]), (r["question"] or "")[:80],
            r["asked_by"] or "-", r["topic_tags"] or "-",
            str(r["difficulty"] or "-"),
        )
    console.print(t)


def update_round(round_id: int) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = conn.execute("SELECT * FROM interview_rounds WHERE id = ?", (round_id,)).fetchone()
    if not row:
        console.print(f"[red]no round {round_id}[/red]")
        return

    status = Prompt.ask("Status", choices=_STATUSES, default=row["status"])
    outcome = Prompt.ask("Outcome", choices=_OUTCOMES, default=row["outcome"] or "pending")
    notes = Prompt.ask("Append notes (blank to skip)", default="")

    with tx(conn):
        conn.execute(
            """
            UPDATE interview_rounds
            SET status = ?, outcome = ?,
                notes = CASE WHEN ? = '' THEN notes ELSE COALESCE(notes,'') || char(10) || ? END
            WHERE id = ?
            """,
            (status, outcome, notes, notes, round_id),
        )
        if status == "completed" and outcome == "advance":
            conn.execute(
                "UPDATE applications SET next_action_at = date('now','+4 days') WHERE id = ?",
                (row["application_id"],),
            )
        elif status == "completed" and outcome == "reject":
            conn.execute(
                "UPDATE applications SET status = 'Rejected' WHERE id = ?",
                (row["application_id"],),
            )
    console.print(f"[green]round {round_id} updated[/green] status={status} outcome={outcome}")
