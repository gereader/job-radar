"""`jr call` — log a recruiter call. Zero LLM."""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Prompt

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx

console = Console()


def log_call_interactive() -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    name = Prompt.ask("Contact name")
    company = Prompt.ask("Company", default="")
    app_id_raw = Prompt.ask("Application ID (blank if none)", default="")
    app_id = int(app_id_raw) if app_id_raw.strip().isdigit() else None
    outcome = Prompt.ask(
        "Outcome",
        choices=["screened-pass", "screened-hold", "screened-pass-to-hm",
                 "rejected", "offer", "other"],
        default="other",
    )
    summary = Prompt.ask("Summary (one sentence)")
    next_at = Prompt.ask("Next follow-up date (YYYY-MM-DD, blank = +7d)", default="")

    with tx(conn):
        existing = conn.execute(
            "SELECT id FROM contacts WHERE lower(name) = lower(?) AND "
            "lower(COALESCE(company,'')) = lower(?)",
            (name, company),
        ).fetchone()
        if existing:
            contact_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO contacts(name, company) VALUES (?, ?)",
                (name, company or None),
            )
            contact_id = cur.lastrowid

        conn.execute(
            """
            INSERT INTO touchpoints(application_id, contact_id, channel, direction,
                                     summary)
            VALUES (?, ?, 'phone', 'inbound', ?)
            """,
            (app_id, contact_id, f"[{outcome}] {summary}"),
        )

        if app_id:
            conn.execute(
                "UPDATE applications SET next_action_at = COALESCE(NULLIF(?, ''), date('now','+7 days')) "
                "WHERE id = ?",
                (next_at, app_id),
            )

    console.print(
        f"[green]call logged[/green] contact={contact_id} "
        f"app={app_id or '-'} outcome={outcome}"
    )
