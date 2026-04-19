"""JD lifecycle: applied JDs kept forever, others archive after N days."""

from __future__ import annotations

from datetime import datetime

from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx

console = Console()


def archive_old(older_than_days: int = 90) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    rows = conn.execute(
        """
        SELECT j.id, j.company, j.title, j.jd_path, j.fetched_at
        FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        WHERE j.archived_at IS NULL
          AND j.fetched_at < datetime('now', ?)
          AND (a.id IS NULL OR a.status IN ('SKIP', 'Discarded', 'Rejected'))
        """,
        (f"-{older_than_days} days",),
    ).fetchall()
    moved = 0
    for r in rows:
        src = cfg.root / r["jd_path"]
        if not src.exists():
            continue
        year = (r["fetched_at"] or "")[:4] or str(datetime.utcnow().year)
        dest_dir = cfg.jds_archive / year
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        src.rename(dest)
        with tx(conn):
            conn.execute(
                "UPDATE jobs SET archived_at = datetime('now'), jd_path = ? WHERE id = ?",
                (cfg.relpath(dest), r["id"]),
            )
        moved += 1
    console.print(f"[green]archived {moved} JDs[/green] older than {older_than_days} days")


def purge_old(older_than_days: int = 365) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    rows = conn.execute(
        """
        SELECT j.id, j.jd_path
        FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        WHERE j.archived_at < datetime('now', ?)
          AND (a.id IS NULL OR a.status IN ('SKIP', 'Discarded', 'Rejected'))
        """,
        (f"-{older_than_days} days",),
    ).fetchall()
    purged = 0
    for r in rows:
        p = cfg.root / (r["jd_path"] or "")
        if p.exists():
            p.unlink()
            purged += 1
        # Keep the jobs row + hash so we still dedup on rescan.
    console.print(f"[green]purged {purged} JD files[/green]")
