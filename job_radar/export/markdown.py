"""Generate markdown views (applications, contacts, outreach) from DB."""

from __future__ import annotations

from rich.console import Console

from ..config import Config
from ..db import connect, migrate

console = Console()


def _table(headers: list[str], rows: list[tuple]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join("---" for _ in headers) + "|"
    body = "\n".join("| " + " | ".join(str(c) if c is not None else "" for c in r) + " |"
                     for r in rows)
    return "\n".join([head, sep, body]) if rows else head + "\n" + sep


def export_all() -> None:
    cfg = Config.load()
    cfg.ensure_dirs()
    conn = connect(cfg)
    migrate(conn)

    # applications.md
    rows = conn.execute(
        """
        SELECT a.id, a.applied_at, j.company, j.title, a.score, a.status,
               CASE WHEN a.resume_pdf_path IS NOT NULL THEN 'yes' ELSE 'no' END AS pdf,
               a.report_path, a.notes
        FROM applications a JOIN jobs j ON j.id = a.job_id
        ORDER BY a.id ASC
        """
    ).fetchall()
    out = cfg.exports_dir / "applications.md"
    out.write_text(
        "# Applications\n\n"
        + _table(
            ["#", "Applied", "Company", "Role", "Score", "Status", "PDF", "Report", "Notes"],
            [tuple(r[k] for k in r.keys()) for r in rows],
        )
        + "\n"
    )
    console.print(f"[green]exported[/green] {cfg.relpath(out)}")

    # contacts.md
    rows = conn.execute(
        """
        SELECT c.id, c.name, c.company, c.title, c.linkedin_url, c.email,
               (SELECT COUNT(*) FROM touchpoints t WHERE t.contact_id = c.id) AS touches
        FROM contacts c
        ORDER BY c.first_seen_at DESC
        """
    ).fetchall()
    out = cfg.exports_dir / "contacts.md"
    out.write_text(
        "# Contacts\n\n"
        + _table(
            ["#", "Name", "Company", "Title", "LinkedIn", "Email", "Touches"],
            [tuple(r[k] for k in r.keys()) for r in rows],
        )
        + "\n"
    )
    console.print(f"[green]exported[/green] {cfg.relpath(out)}")

    # outreach.md
    rows = conn.execute(
        """
        SELECT t.occurred_at, j.company, j.title, t.channel, t.direction,
               COALESCE(c.name, '-') AS contact, t.summary
        FROM touchpoints t
        LEFT JOIN applications a ON a.id = t.application_id
        LEFT JOIN jobs j ON j.id = a.job_id
        LEFT JOIN contacts c ON c.id = t.contact_id
        ORDER BY t.occurred_at DESC
        """
    ).fetchall()
    out = cfg.exports_dir / "outreach.md"
    out.write_text(
        "# Outreach\n\n"
        + _table(
            ["When", "Company", "Role", "Channel", "Dir", "Contact", "Summary"],
            [tuple(r[k] for k in r.keys()) for r in rows],
        )
        + "\n"
    )
    console.print(f"[green]exported[/green] {cfg.relpath(out)}")
