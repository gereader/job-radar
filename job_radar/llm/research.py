"""`jr research <job_id>` — Sonnet company research report. Direct or queue."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from ._report import REPORT_RESULT_SCHEMA, report_text, write_research_path
from .client import DirectLLM, QueueLLM, log_queue_ingest
from .dispatcher import build_llm

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_RESEARCH = Path(__file__).parent.parent.parent / "modes" / "research.md"


def _system() -> str:
    return _SHARED.read_text() + "\n\n---\n\n" + _RESEARCH.read_text()


def _user_prompt(cfg: Config, row) -> str:
    jd_path = cfg.root / (row["jd_path"] or "")
    jd_md = jd_path.read_text() if jd_path.exists() else ""
    return (
        f"Company: {row['company']}\nRole: {row['title']}\nURL: {row['url']}\n\n"
        f"JD excerpt for context:\n\n{jd_md[:6000]}"
    )


def _app_id_for(conn, job_id: int) -> int | None:
    a = conn.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    return a["id"] if a else None


def run_research(job_id: int, *, force_prepare: bool = False) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        console.print(f"[red]no job {job_id}[/red]")
        return

    model = (cfg.profile.get("llm") or {}).get("eval_model", "claude-sonnet-4-6")
    backend, llm = build_llm(
        conn, cfg, operation="research", default_model=model,
        result_schema=REPORT_RESULT_SCHEMA,
        force=("queue" if force_prepare else None),
    )
    system = _system()
    user = _user_prompt(cfg, row)

    if backend == "queue":
        assert isinstance(llm, QueueLLM)
        llm.enqueue(
            system=system, user=user, item_id=job_id,
            meta={"job_id": job_id, "company": row["company"], "title": row["title"]},
            max_tokens=2500,
        )
        qdir = llm.finalize()
        console.print(
            f"[green]queued[/green] research → {qdir}\n"
            f"Next: [bold]/jr consume {qdir}[/bold], "
            f"then [bold]jr research --ingest {qdir}[/bold]."
        )
        return

    assert isinstance(llm, DirectLLM)
    resp = llm.complete(
        system=system, user=user, operation="research",
        job_id=job_id, max_tokens=2500,
    )
    out = write_research_path(cfg, row["company"], _app_id_for(conn, job_id))
    out.write_text(resp.text)
    console.print(f"[green]company research[/green] → {out}")
    console.print(
        f"tokens: in={resp.input_tokens} out={resp.output_tokens} "
        f"cached={resp.cached_tokens}"
    )


def ingest_research(queue_dir: Path) -> None:
    from .queue import ingest as q_ingest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    results = q_ingest(queue_dir)
    for r in results:
        meta = r.meta or {}
        job_id = int(meta.get("job_id") or r.id)
        company = meta.get("company") or "unknown"
        out = write_research_path(cfg, company, _app_id_for(conn, job_id))
        out.write_text(report_text(r.result))
        console.print(f"[green]research[/green] job={job_id} → {out}")
        log_queue_ingest(conn, operation="research", item_count=1, job_id=job_id)
    console.print(f"\n[green]ingest complete[/green] — {queue_dir}")
    from ..dash.build import rebuild_silently
    rebuild_silently()
