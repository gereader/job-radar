"""Contacts CRM. First-class entities with a touchpoints log."""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx

console = Console()


def add_contact_interactive() -> int:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    name = Prompt.ask("Name")
    company = Prompt.ask("Company", default="")
    title = Prompt.ask("Title", default="")
    linkedin = Prompt.ask("LinkedIn URL", default="")
    email = Prompt.ask("Email", default="")
    phone = Prompt.ask("Phone", default="")
    notes = Prompt.ask("Notes", default="")

    with tx(conn):
        cur = conn.execute(
            """
            INSERT INTO contacts(name, company, title, linkedin_url, email, phone, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(linkedin_url) DO UPDATE SET
              name=excluded.name, company=excluded.company, title=excluded.title,
              email=COALESCE(excluded.email, contacts.email),
              phone=COALESCE(excluded.phone, contacts.phone),
              notes=excluded.notes
            """,
            (name, company or None, title or None, linkedin or None,
             email or None, phone or None, notes or None),
        )
        cid = cur.lastrowid
    console.print(f"[green]contact {cid} saved[/green]")
    return cid


def list_contacts() -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)
    rows = conn.execute(
        """
        SELECT c.id, c.name, c.company, c.title,
               (SELECT COUNT(*) FROM touchpoints t WHERE t.contact_id = c.id) AS touches
        FROM contacts c
        ORDER BY c.first_seen_at DESC
        """
    ).fetchall()
    t = Table(title=f"Contacts ({len(rows)})")
    t.add_column("#", justify="right")
    t.add_column("Name")
    t.add_column("Company")
    t.add_column("Title")
    t.add_column("Touches", justify="right")
    for r in rows:
        t.add_row(str(r["id"]), r["name"], r["company"] or "", r["title"] or "",
                  str(r["touches"]))
    console.print(t)


def show_contact(contact_id: int) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    c = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not c:
        console.print(f"[red]no contact {contact_id}[/red]")
        return
    console.print(
        f"[bold]{c['name']}[/bold] — {c['title'] or ''} @ {c['company'] or ''}\n"
        f"LinkedIn: {c['linkedin_url'] or '-'}\nEmail: {c['email'] or '-'}\n"
        f"Phone: {c['phone'] or '-'}\n{c['notes'] or ''}"
    )
    rows = conn.execute(
        """
        SELECT t.occurred_at, t.channel, t.direction, t.summary, t.application_id
        FROM touchpoints t
        WHERE t.contact_id = ?
        ORDER BY t.occurred_at DESC
        """,
        (contact_id,),
    ).fetchall()
    if rows:
        t = Table(title="Touchpoints")
        for col in ("When", "Channel", "Dir", "App", "Summary"):
            t.add_column(col)
        for r in rows:
            t.add_row(
                r["occurred_at"], r["channel"], r["direction"],
                str(r["application_id"] or "-"), r["summary"] or "",
            )
        console.print(t)


def log_touchpoint(
    *,
    app_id: int,
    channel: str,
    direction: str,
    summary: str,
    contact_id: int | None = None,
) -> int:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)
    with tx(conn):
        cur = conn.execute(
            """
            INSERT INTO touchpoints(application_id, contact_id, channel, direction, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (app_id, contact_id, channel, direction, summary),
        )
        # Advance next_action_at by a week by default.
        conn.execute(
            """
            UPDATE applications
            SET next_action_at = date('now', '+7 days'),
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (app_id,),
        )
    console.print(f"[green]logged touchpoint {cur.lastrowid} on app {app_id}[/green]")
    return cur.lastrowid
