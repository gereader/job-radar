"""Liveness check: detect closed/404 postings and auto-archive them.

httpx first (cheap). If the response looks alive AND the body contains
"no longer accepting" / "position has been filled" / "job has closed",
still mark as closed.
"""

from __future__ import annotations

import re

import httpx
from rich.console import Console
from rich.progress import Progress

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx

console = Console()


_EXPIRED = re.compile(
    r"(job\s+(is\s+)?no\s+longer\s+(accepting|available|open)"
    r"|position\s+(has\s+)?been\s+filled"
    r"|this\s+posting\s+(has|is)\s+(been\s+)?(closed|expired)"
    r"|role\s+is\s+no\s+longer\s+available"
    r"|the\s+job\s+you\s+are\s+looking\s+for\s+is\s+not\s+found)",
    re.I,
)


def _check(url: str, client: httpx.Client) -> tuple[str, str]:
    try:
        r = client.get(url)
    except Exception as e:
        return "error", str(e)
    if r.status_code == 404 or r.status_code == 410:
        return "closed", f"http {r.status_code}"
    if r.status_code >= 500:
        return "unknown", f"http {r.status_code}"
    if _EXPIRED.search(r.text):
        return "closed", "expired text"
    if r.status_code == 200:
        return "alive", "200"
    return "unknown", f"http {r.status_code}"


def run_liveness(limit: int = 0, include_applied: bool = False) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    sql = """
    SELECT j.id, j.url
    FROM jobs j
    LEFT JOIN applications a ON a.job_id = j.id
    WHERE j.archived_at IS NULL
      AND j.url != ''
    """
    if not include_applied:
        sql += """ AND (a.id IS NULL
                       OR a.status IN ('SKIP','Discarded','Rejected','Evaluated'))"""
    sql += " ORDER BY j.id DESC"
    rows = conn.execute(sql).fetchall()
    if limit:
        rows = rows[:limit]
    if not rows:
        console.print("nothing to check.")
        return

    closed = 0
    alive = 0
    unknown = 0
    with httpx.Client(timeout=15.0, follow_redirects=True) as client, \
         Progress(console=console, transient=True) as progress:
        task = progress.add_task("liveness", total=len(rows))
        for r in rows:
            state, reason = _check(r["url"], client)
            progress.advance(task)
            if state == "closed":
                closed += 1
                with tx(conn):
                    conn.execute(
                        "UPDATE jobs SET archived_at = datetime('now'), closed_at = datetime('now') "
                        "WHERE id = ?",
                        (r["id"],),
                    )
            elif state == "alive":
                alive += 1
            else:
                unknown += 1

    console.print(
        f"[green]liveness done[/green] "
        f"alive={alive} closed={closed} unknown={unknown} "
        f"(closed posts auto-archived)"
    )
