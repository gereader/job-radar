"""`jr answers <app_id>` — draft answers to common application questions.

Reads ``private/questions.yml`` (falls back to ``templates/questions.example.yml``),
the app's JD + the candidate's CV + story bank, and produces one
``app_answers`` row per question. Direct or queue.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from .client import DirectLLM, QueueLLM, log_queue_ingest
from .dispatcher import build_llm

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_ANSWERS = Path(__file__).parent.parent.parent / "modes" / "answers.md"
_QUESTIONS_EXAMPLE = (
    Path(__file__).parent.parent.parent / "templates" / "questions.example.yml"
)


ANSWERS_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["answers"],
    "properties": {
        "answers": {"type": "object"},
        "notes": {"type": "string"},
    },
}


def _questions(cfg: Config) -> list[dict[str, str]]:
    p = cfg.private / "questions.yml"
    if p.exists():
        data = yaml.safe_load(p.read_text()) or {}
    else:
        data = yaml.safe_load(_QUESTIONS_EXAMPLE.read_text()) or {}
    return data.get("questions") or []


def _system(cfg: Config) -> str:
    cv_md = cfg.cv_path.read_text() if cfg.cv_path.exists() else ""
    stories_md = cfg.story_bank_path.read_text() if cfg.story_bank_path.exists() else ""
    return (
        _SHARED.read_text() + "\n\n---\n\n" + _ANSWERS.read_text()
        + "\n\n## Candidate CV\n" + cv_md
        + "\n\n## Story bank\n" + stories_md
    )


def _row(conn, app_id: int):
    return conn.execute(
        """
        SELECT a.id AS app_id, j.id AS job_id, j.company, j.title, j.url, j.jd_path
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = ?
        """,
        (app_id,),
    ).fetchone()


def _user_prompt(cfg: Config, row, questions: list[dict], cached: dict[str, str]) -> str:
    jd_path = cfg.root / (row["jd_path"] or "")
    jd_md = jd_path.read_text() if jd_path.exists() else ""
    cached_block = ""
    if cached:
        cached_block = (
            "\n\n## Prior cached answers (different role, same candidate)\n"
            + yaml.safe_dump(cached, sort_keys=False)
        )
    return (
        f"Company: {row['company']}\nRole: {row['title']}\nURL: {row['url']}\n\n"
        f"## JD\n{jd_md}\n\n"
        f"## Questions to answer\n```yaml\n"
        f"{yaml.safe_dump({'questions': questions}, sort_keys=False)}\n```"
        f"{cached_block}"
    )


def _existing_answers(conn, app_id: int) -> dict[str, str]:
    rows = conn.execute(
        "SELECT question_key, answer_md FROM app_answers WHERE application_id = ?",
        (app_id,),
    ).fetchall()
    return {r["question_key"]: r["answer_md"] for r in rows}


def _persist_answers(conn, app_id: int, answers: dict[str, dict]) -> int:
    written = 0
    with tx(conn):
        for key, payload in answers.items():
            if not isinstance(payload, dict):
                continue
            answer_md = payload.get("answer_md") or ""
            question_text = payload.get("question") or ""
            if not answer_md.strip():
                continue
            conn.execute(
                """
                INSERT INTO app_answers(application_id, question_key, question_text,
                                         answer_md, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(application_id, question_key) DO UPDATE
                SET question_text = excluded.question_text,
                    answer_md     = excluded.answer_md,
                    updated_at    = datetime('now')
                """,
                (app_id, key, question_text, answer_md),
            )
            written += 1
    return written


def _write_markdown(cfg: Config, app_id: int, company: str, answers: dict[str, dict]) -> Path:
    from ..util.slugify import slugify
    app_dir = cfg.applications_dir / f"{app_id}-{slugify(company)}"
    app_dir.mkdir(parents=True, exist_ok=True)
    out = app_dir / "answers.md"
    blocks = []
    for key, payload in answers.items():
        if not isinstance(payload, dict):
            continue
        q = payload.get("question") or key
        a = payload.get("answer_md") or ""
        blocks.append(f"## {q}\n\n{a}\n")
    out.write_text("# Application answers\n\n" + "\n".join(blocks))
    return out


def run_answers(app_id: int, *, force_prepare: bool = False) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = _row(conn, app_id)
    if not row:
        console.print(f"[red]no application {app_id}[/red]")
        return

    questions = _questions(cfg)
    if not questions:
        console.print("[red]no questions configured[/red] — see templates/questions.example.yml")
        return
    cached = _existing_answers(conn, app_id)

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    backend, llm = build_llm(
        conn, cfg, operation="answers", default_model=model,
        result_schema=ANSWERS_RESULT_SCHEMA,
        force=("queue" if force_prepare else None),
    )
    system = _system(cfg)
    user = _user_prompt(cfg, row, questions, cached)

    if backend == "queue":
        assert isinstance(llm, QueueLLM)
        llm.enqueue(
            system=system, user=user, item_id=app_id,
            meta={"app_id": app_id, "company": row["company"], "title": row["title"]},
            max_tokens=2400,
        )
        qdir = llm.finalize()
        console.print(
            f"[green]queued[/green] answers → {qdir}\n"
            f"Next: [bold]/jr consume {qdir}[/bold], "
            f"then [bold]jr answers --ingest {qdir}[/bold]."
        )
        return

    assert isinstance(llm, DirectLLM)
    resp = llm.complete(
        system=system, user=user, operation="answers",
        app_id=app_id, max_tokens=2400,
    )
    import json
    try:
        parsed = json.loads(resp.text.strip().strip("`"))
    except json.JSONDecodeError:
        console.print(f"[red]model output not JSON:[/red] {resp.text[:300]}")
        return
    answers = parsed.get("answers") or {}
    n = _persist_answers(conn, app_id, answers)
    out = _write_markdown(cfg, app_id, row["company"], answers)
    console.print(f"[green]wrote {n} answers[/green] → {out}")


def ingest_answers(queue_dir: Path) -> None:
    from .queue import ingest as q_ingest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    results = q_ingest(queue_dir)
    for r in results:
        meta = r.meta or {}
        app_id = int(meta.get("app_id") or r.id)
        company = meta.get("company") or "unknown"
        answers = (r.result or {}).get("answers") if isinstance(r.result, dict) else {}
        if not answers:
            console.print(f"[yellow]app {app_id}: no answers in result[/yellow]")
            continue
        n = _persist_answers(conn, app_id, answers)
        out = _write_markdown(cfg, app_id, company, answers)
        console.print(f"[green]app {app_id}[/green] {n} answers → {out}")
        log_queue_ingest(conn, operation="answers", item_count=1, app_id=app_id)
    console.print(f"\n[green]ingest complete[/green] — {queue_dir}")
