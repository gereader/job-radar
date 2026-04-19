"""`jr jd list` — inventory of saved JDs."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from ..config import Config
from ..db import connect, migrate

console = Console()


def list_jds(state: str = "active") -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)
    if state == "archived":
        sql = "SELECT id, company, title, posted_at, archived_at FROM jobs WHERE archived_at IS NOT NULL ORDER BY archived_at DESC"
    elif state == "applied":
        sql = (
            "SELECT j.id, j.company, j.title, j.posted_at, a.applied_at "
            "FROM jobs j JOIN applications a ON a.job_id = j.id "
            "WHERE a.status IN ('Applied', 'Responded', 'Interview', 'Offer') "
            "ORDER BY a.applied_at DESC"
        )
    else:
        sql = (
            "SELECT id, company, title, posted_at, screen_verdict, triage_verdict "
            "FROM jobs WHERE archived_at IS NULL ORDER BY id DESC"
        )
    rows = conn.execute(sql).fetchall()
    t = Table(title=f"JDs ({state}): {len(rows)}")
    for k in rows[0].keys() if rows else ["no rows"]:
        t.add_column(k)
    for r in rows:
        t.add_row(*[str(r[k]) if r[k] is not None else "-" for k in r.keys()])
    console.print(t)
