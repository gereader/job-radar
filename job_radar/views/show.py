"""`jr show` — print a JD + screen + triage result."""

from __future__ import annotations

from rich.console import Console

from ..config import Config
from ..db import connect, migrate

console = Console()


def show_job(job_id: int) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        console.print(f"[red]no job {job_id}[/red]")
        return
    console.rule(f"{row['company']} — {row['title']}")
    console.print(f"URL: {row['url']}")
    console.print(
        f"Screen: {row['screen_verdict']} ({row['screen_score']}) | "
        f"Triage: {row['triage_verdict'] or '-'}"
    )
    console.print(
        f"Comp: {row['comp_min']}–{row['comp_max']} {row['comp_currency'] or ''} "
        f"| Remote: {row['remote']} | Location: {row['location'] or '-'}"
    )
    jd_path = cfg.root / row["jd_path"]
    if jd_path.exists():
        console.rule("JD")
        console.print(jd_path.read_text())
