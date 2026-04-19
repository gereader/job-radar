"""Sonnet A-F+G deep evaluation. Only for jobs the user advances."""

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
_EVAL = Path(__file__).parent.parent.parent / "modes" / "evaluate.md"


def _build_system(cfg: Config, cv_md: str, stories_md: str) -> str:
    return (
        _SHARED.read_text()
        + "\n\n---\n\n"
        + _EVAL.read_text()
        + "\n\n---\n\n## User profile (yaml)\n```yaml\n"
        + (cfg.profile and __import__('yaml').safe_dump(cfg.profile) or "")
        + "```\n\n## CV (markdown)\n"
        + cv_md
        + "\n\n## Story bank\n"
        + stories_md
    )


def run_evaluate(job_id: int) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = conn.execute(
        "SELECT * FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if not row:
        console.print(f"[red]no job {job_id}[/red]")
        return

    jd_path = cfg.root / row["jd_path"]
    jd_md = jd_path.read_text() if jd_path.exists() else ""
    cv_md = cfg.cv_path.read_text() if cfg.cv_path.exists() else ""
    stories_md = cfg.story_bank_path.read_text() if cfg.story_bank_path.exists() else ""

    model = (cfg.profile.get("llm") or {}).get("eval_model", "claude-sonnet-4-6")
    llm = LLM(conn, default_model=model)

    system = _build_system(cfg, cv_md, stories_md)
    user = (
        f"Company: {row['company']}\n"
        f"Role: {row['title']}\n"
        f"URL: {row['url']}\n"
        f"Location: {row['location'] or '-'}\n"
        f"Remote: {row['remote'] or '-'}\n"
        f"Comp: {row['comp_min']}–{row['comp_max']} {row['comp_currency'] or ''}\n\n"
        f"---\n\n{jd_md}"
    )
    resp = llm.complete(
        system=system,
        user=user,
        operation="evaluate",
        job_id=job_id,
        max_tokens=4000,
    )

    # Create or reuse an application row and write the report beside it.
    app = conn.execute(
        "SELECT id FROM applications WHERE job_id = ?", (job_id,)
    ).fetchone()
    with tx(conn):
        if app is None:
            cur = conn.execute(
                "INSERT INTO applications(job_id, status) VALUES (?, 'Evaluated')",
                (job_id,),
            )
            app_id = cur.lastrowid
        else:
            app_id = app["id"]

    app_dir = cfg.applications_dir / f"{app_id}-{slugify(row['company'])}"
    app_dir.mkdir(parents=True, exist_ok=True)
    report_path = app_dir / f"report-{date.today().isoformat()}.md"
    report_path.write_text(resp.text)

    with tx(conn):
        conn.execute(
            "UPDATE applications SET report_path = ? WHERE id = ?",
            (str(report_path.relative_to(cfg.root)), app_id),
        )

    console.print(f"[green]report[/green] → {report_path}")
    console.print(
        f"tokens: in={resp.input_tokens} out={resp.output_tokens} "
        f"cached={resp.cached_tokens}"
    )
