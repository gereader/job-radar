"""Sonnet A-F+G deep evaluation. Direct or queue."""

from __future__ import annotations

from pathlib import Path

import yaml
from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from ..util.slugify import slugify
from ._report import REPORT_RESULT_SCHEMA, report_text
from .client import DirectLLM, QueueLLM, log_queue_ingest
from .dispatcher import build_llm

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_EVAL = Path(__file__).parent.parent.parent / "modes" / "evaluate.md"


def _system(cfg: Config) -> str:
    cv_md = cfg.cv_path.read_text() if cfg.cv_path.exists() else ""
    stories_md = cfg.story_bank_path.read_text() if cfg.story_bank_path.exists() else ""
    profile_yaml = yaml.safe_dump(cfg.profile or {}) if cfg.profile else ""
    return (
        _SHARED.read_text()
        + "\n\n---\n\n"
        + _EVAL.read_text()
        + "\n\n---\n\n## User profile (yaml)\n```yaml\n"
        + profile_yaml
        + "```\n\n## CV (markdown)\n"
        + cv_md
        + "\n\n## Story bank\n"
        + stories_md
    )


def _user_prompt(cfg: Config, row) -> str:
    jd_path = cfg.root / row["jd_path"]
    jd_md = jd_path.read_text() if jd_path.exists() else ""
    return (
        f"Company: {row['company']}\n"
        f"Role: {row['title']}\n"
        f"URL: {row['url']}\n"
        f"Location: {row['location'] or '-'}\n"
        f"Remote: {row['remote'] or '-'}\n"
        f"Comp: {row['comp_min']}–{row['comp_max']} {row['comp_currency'] or ''}\n\n"
        f"---\n\n{jd_md}"
    )


def _ensure_app(conn, job_id: int) -> int:
    app = conn.execute(
        "SELECT id FROM applications WHERE job_id = ?", (job_id,)
    ).fetchone()
    if app:
        return app["id"]
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO applications(job_id, status) VALUES (?, 'Evaluated')",
            (job_id,),
        )
        return cur.lastrowid


def _persist_report(cfg: Config, conn, app_id: int, company: str, content: str) -> Path:
    from datetime import date
    app_dir = cfg.applications_dir / f"{app_id}-{slugify(company)}"
    app_dir.mkdir(parents=True, exist_ok=True)
    report_path = app_dir / f"report-{date.today().isoformat()}.md"
    report_path.write_text(content)
    with tx(conn):
        conn.execute(
            "UPDATE applications SET report_path = ? WHERE id = ?",
            (cfg.relpath(report_path), app_id),
        )
    return report_path


def run_evaluate(job_id: int, *, force_prepare: bool = False) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        console.print(f"[red]no job {job_id}[/red]")
        return

    model = (cfg.profile.get("llm") or {}).get("eval_model", "claude-sonnet-4-6")
    backend, llm = build_llm(
        conn, cfg, operation="evaluate", default_model=model,
        result_schema=REPORT_RESULT_SCHEMA,
        force=("queue" if force_prepare else None),
    )
    system = _system(cfg)
    user = _user_prompt(cfg, row)

    if backend == "queue":
        assert isinstance(llm, QueueLLM)
        llm.enqueue(
            system=system, user=user, item_id=job_id,
            meta={"job_id": job_id, "company": row["company"], "title": row["title"]},
            max_tokens=4000,
        )
        qdir = llm.finalize()
        console.print(
            f"[green]queued[/green] evaluation → {qdir}\n"
            f"Next: [bold]/jr consume {qdir}[/bold], "
            f"then [bold]jr eval --ingest {qdir}[/bold]."
        )
        return

    assert isinstance(llm, DirectLLM)
    resp = llm.complete(
        system=system, user=user, operation="evaluate",
        job_id=job_id, max_tokens=4000,
    )
    app_id = _ensure_app(conn, job_id)
    out = _persist_report(cfg, conn, app_id, row["company"], resp.text)
    console.print(f"[green]report[/green] → {out}")
    console.print(
        f"tokens: in={resp.input_tokens} out={resp.output_tokens} "
        f"cached={resp.cached_tokens}"
    )


def ingest_evaluate(queue_dir: Path) -> None:
    from .queue import ingest as q_ingest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    results = q_ingest(queue_dir)
    for r in results:
        meta = r.meta or {}
        job_id = int(meta.get("job_id") or r.id)
        company = meta.get("company") or "unknown"
        app_id = _ensure_app(conn, job_id)
        out = _persist_report(cfg, conn, app_id, company, report_text(r.result))
        console.print(f"[green]eval[/green] job={job_id} → {out}")
        if isinstance(r.result, dict) and "score_0_5" in r.result:
            try:
                score = float(r.result["score_0_5"])
                with tx(conn):
                    conn.execute(
                        "UPDATE applications SET score = ? WHERE id = ?",
                        (score, app_id),
                    )
            except (TypeError, ValueError):
                pass
        log_queue_ingest(
            conn, operation="evaluate", item_count=1,
            job_id=job_id, app_id=app_id,
        )
    console.print(f"\n[green]ingest complete[/green] — {queue_dir}")
