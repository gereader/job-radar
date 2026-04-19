"""`jr interview <app_id>` — Sonnet interview-prep report. Direct or queue."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from ._report import REPORT_RESULT_SCHEMA, report_text, write_app_report
from .client import DirectLLM, QueueLLM, log_queue_ingest
from .dispatcher import build_llm

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_INTERVIEW = Path(__file__).parent.parent.parent / "modes" / "interview.md"


def _system(cfg: Config) -> str:
    cv_md = cfg.cv_path.read_text() if cfg.cv_path.exists() else ""
    stories_md = cfg.story_bank_path.read_text() if cfg.story_bank_path.exists() else ""
    return (
        _SHARED.read_text() + "\n\n---\n\n" + _INTERVIEW.read_text()
        + "\n\n## CV\n" + cv_md + "\n\n## Story bank\n" + stories_md
    )


def _user_prompt(cfg: Config, row) -> str:
    jd_path = cfg.root / (row["jd_path"] or "")
    jd_md = jd_path.read_text() if jd_path.exists() else ""
    return (
        f"Company: {row['company']}\nRole: {row['title']}\nURL: {row['url']}\n"
        f"Location: {row['location'] or '-'} | Remote: {row['remote'] or '-'}\n"
        f"Comp: {row['comp_min']}–{row['comp_max']} {row['comp_currency'] or ''}\n\n"
        f"---\n\n{jd_md}"
    )


def _app_row(conn, app_id: int):
    return conn.execute(
        """
        SELECT a.id AS app_id, j.id AS job_id, j.company, j.title, j.url, j.jd_path,
               j.location, j.remote, j.comp_min, j.comp_max, j.comp_currency
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = ?
        """,
        (app_id,),
    ).fetchone()


def run_interview_prep(app_id: int, *, force_prepare: bool = False) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = _app_row(conn, app_id)
    if not row:
        console.print(f"[red]no application {app_id}[/red]")
        return

    model = (cfg.profile.get("llm") or {}).get("eval_model", "claude-sonnet-4-6")
    backend, llm = build_llm(
        conn, cfg, operation="interview", default_model=model,
        result_schema=REPORT_RESULT_SCHEMA,
        force=("queue" if force_prepare else None),
    )
    system = _system(cfg)
    user = _user_prompt(cfg, row)

    if backend == "queue":
        assert isinstance(llm, QueueLLM)
        llm.enqueue(
            system=system, user=user, item_id=app_id,
            meta={"app_id": app_id, "company": row["company"], "title": row["title"]},
            max_tokens=3500,
        )
        qdir = llm.finalize()
        console.print(
            f"[green]queued[/green] interview prep → {qdir}\n"
            f"Next: [bold]/jr consume {qdir}[/bold], "
            f"then [bold]jr interview --ingest {qdir}[/bold]."
        )
        return

    assert isinstance(llm, DirectLLM)
    resp = llm.complete(
        system=system, user=user, operation="interview",
        app_id=app_id, max_tokens=3500,
    )
    out = write_app_report(cfg, app_id, row["company"], "interview-prep", resp.text)
    console.print(f"[green]interview prep[/green] → {out}")
    console.print(
        f"tokens: in={resp.input_tokens} out={resp.output_tokens} "
        f"cached={resp.cached_tokens}"
    )


def ingest_interview(queue_dir: Path) -> None:
    from .queue import ingest as q_ingest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    results = q_ingest(queue_dir)
    for r in results:
        meta = r.meta or {}
        app_id = int(meta.get("app_id") or r.id)
        company = meta.get("company") or "unknown"
        out = write_app_report(cfg, app_id, company, "interview-prep", report_text(r.result))
        console.print(f"[green]interview prep[/green] app={app_id} → {out}")
        log_queue_ingest(conn, operation="interview", item_count=1, app_id=app_id)
    console.print(f"\n[green]ingest complete[/green] — {queue_dir}")
