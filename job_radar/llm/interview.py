"""`jr interview <app_id>` — Sonnet interview-prep report."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from ..util.slugify import slugify
from .client import LLM

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_INTERVIEW = Path(__file__).parent.parent.parent / "modes" / "interview.md"


def run_interview_prep(app_id: int) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = conn.execute(
        """
        SELECT a.*, j.company, j.title, j.url, j.jd_path, j.location, j.remote,
               j.comp_min, j.comp_max, j.comp_currency
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = ?
        """,
        (app_id,),
    ).fetchone()
    if not row:
        console.print(f"[red]no application {app_id}[/red]")
        return

    jd_path = cfg.root / (row["jd_path"] or "")
    jd_md = jd_path.read_text() if jd_path.exists() else ""
    cv_md = cfg.cv_path.read_text() if cfg.cv_path.exists() else ""
    stories_md = cfg.story_bank_path.read_text() if cfg.story_bank_path.exists() else ""

    system = (
        _SHARED.read_text() + "\n\n---\n\n" + _INTERVIEW.read_text()
        + "\n\n## CV\n" + cv_md + "\n\n## Story bank\n" + stories_md
    )
    user = (
        f"Company: {row['company']}\nRole: {row['title']}\nURL: {row['url']}\n"
        f"Location: {row['location'] or '-'} | Remote: {row['remote'] or '-'}\n"
        f"Comp: {row['comp_min']}–{row['comp_max']} {row['comp_currency'] or ''}\n\n"
        f"---\n\n{jd_md}"
    )

    model = (cfg.profile.get("llm") or {}).get("eval_model", "claude-sonnet-4-6")
    llm = LLM(conn, default_model=model)
    resp = llm.complete(
        system=system, user=user, operation="interview",
        app_id=app_id, max_tokens=3500,
    )

    app_dir = cfg.applications_dir / f"{app_id}-{slugify(row['company'])}"
    app_dir.mkdir(parents=True, exist_ok=True)
    out = app_dir / f"interview-prep-{date.today().isoformat()}.md"
    out.write_text(resp.text)
    console.print(f"[green]interview prep[/green] → {out}")
    console.print(
        f"tokens: in={resp.input_tokens} out={resp.output_tokens} "
        f"cached={resp.cached_tokens}"
    )
