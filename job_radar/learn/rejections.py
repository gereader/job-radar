"""`jr learn rejections` — extract structured rejection_reasons from notes.

For every Rejected app that has notes but no rows in ``rejection_reasons``
yet (or all of them with ``--reextract``), enqueue a Haiku packet to
classify into category + detail. The bulk path uses the same dispatcher
+ ranker pattern; ranks newest-rejected-first.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from ..llm.client import DirectLLM, QueueLLM, log_queue_ingest
from ..llm.dispatcher import build_llm
from ..llm.ranker import rank_and_slice, resolved_default

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_REJ = Path(__file__).parent.parent.parent / "modes" / "rejection-reasons.md"


REJECTION_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["rows"],
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["category"],
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["location", "comp", "level", "stack",
                                 "culture", "timing", "fit", "other"],
                    },
                    "detail": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
}


def _candidates(conn, *, reextract: bool) -> list:
    if reextract:
        sql = """
        SELECT a.id, a.notes, a.updated_at, j.company, j.title
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.status = 'Rejected' AND COALESCE(a.notes, '') != ''
        ORDER BY a.updated_at DESC
        """
        return conn.execute(sql).fetchall()
    sql = """
    SELECT a.id, a.notes, a.updated_at, j.company, j.title
    FROM applications a JOIN jobs j ON j.id = a.job_id
    WHERE a.status = 'Rejected'
      AND COALESCE(a.notes, '') != ''
      AND NOT EXISTS (
          SELECT 1 FROM rejection_reasons r WHERE r.application_id = a.id
      )
    ORDER BY a.updated_at DESC
    """
    return conn.execute(sql).fetchall()


def _user_prompt(cfg: Config, row) -> str:
    targets = (cfg.profile.get("targets") or {})
    return (
        f"Application: {row['company']} / {row['title']}\n"
        f"Updated: {row['updated_at']}\n"
        f"Candidate target archetypes: {targets.get('archetypes', [])}\n"
        f"Candidate comp target: {targets.get('comp', {})}\n\n"
        f"---\n\nNotes:\n{row['notes']}\n"
    )


def _persist(conn, app_id: int, rows: list[dict], *, replace: bool) -> int:
    written = 0
    with tx(conn):
        if replace:
            conn.execute(
                "DELETE FROM rejection_reasons WHERE application_id = ?",
                (app_id,),
            )
        for r in rows:
            cat = (r.get("category") or "other").strip().lower()
            if cat not in {"location", "comp", "level", "stack",
                           "culture", "timing", "fit", "other"}:
                cat = "other"
            detail = r.get("detail")
            conn.execute(
                """
                INSERT INTO rejection_reasons(application_id, category, detail, source)
                VALUES (?, ?, ?, 'llm')
                """,
                (app_id, cat, detail),
            )
            written += 1
    return written


def run_learn_rejections(
    *, limit: int = 0, all_: bool = False, reextract: bool = False,
    rank_debug: bool = False, force_prepare: bool = False,
) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    rows = _candidates(conn, reextract=reextract)
    if not rows:
        console.print("(no rejected apps with un-extracted notes)")
        return

    def _key(r) -> float:
        ts = r["updated_at"]
        if not ts:
            return 0.0
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return t.timestamp()
        except (TypeError, ValueError):
            return 0.0

    default_n = resolved_default(cfg.profile)
    requested = limit if limit > 0 else default_n
    sliced = rank_and_slice(rows, key=_key, limit=requested, all_=all_)

    if rank_debug:
        from ..llm.ranker import print_rank_debug
        print_rank_debug(
            list(rows), key=_key,
            columns=[
                ("app", lambda r: r["id"]),
                ("company", lambda r: r["company"]),
                ("title", lambda r: r["title"]),
                ("updated", lambda r: r["updated_at"]),
            ],
            title=f"Rejection rank ({len(rows)} candidates)", console=console,
        )
        return

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    backend, llm = build_llm(
        conn, cfg, operation="rejection_reason", default_model=model,
        result_schema=REJECTION_RESULT_SCHEMA,
        extra_meta={"reextract": bool(reextract)},
        force=("queue" if force_prepare else None),
    )
    system = _SHARED.read_text() + "\n\n---\n\n" + _REJ.read_text()

    if backend == "direct":
        assert isinstance(llm, DirectLLM)
        import json
        for r in sliced.picked:
            resp = llm.complete(
                system=system, user=_user_prompt(cfg, r),
                operation="rejection_reason", app_id=r["id"], max_tokens=400,
            )
            try:
                parsed = json.loads(resp.text.strip().strip("`"))
            except json.JSONDecodeError:
                console.print(f"[red]app {r['id']}: bad JSON[/red] — skipping")
                continue
            n = _persist(conn, r["id"], parsed.get("rows") or [], replace=reextract)
            console.print(f"[green]app {r['id']}[/green] {r['company']} → {n} rows")
        hint = sliced.hint(command="jr learn rejections", current_limit=requested)
        if hint:
            console.print(hint)
        return

    assert isinstance(llm, QueueLLM)
    for r in sliced.picked:
        llm.enqueue(
            system=system, user=_user_prompt(cfg, r), item_id=r["id"],
            meta={"app_id": r["id"], "company": r["company"], "reextract": bool(reextract)},
            max_tokens=400,
        )
    qdir = llm.finalize()
    console.print(
        f"[green]queued[/green] {len(sliced.picked)} rejection-reason packets → {qdir}"
    )
    hint = sliced.hint(command="jr learn rejections", current_limit=requested)
    if hint:
        console.print(hint)
    console.print(
        f"Next: [bold]/jr consume {qdir}[/bold], "
        f"then [bold]jr learn rejections --ingest {qdir}[/bold]."
    )


def ingest_learn_rejections(queue_dir: Path) -> None:
    from ..llm.queue import ingest as q_ingest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    results = q_ingest(queue_dir)
    table = Table(title=f"Ingested rejection reasons ({len(results)})")
    table.add_column("app", justify="right")
    table.add_column("company")
    table.add_column("rows", justify="right")

    for r in results:
        meta = r.meta or {}
        app_id = int(meta.get("app_id") or r.id)
        replace = bool(meta.get("reextract"))
        rows = (r.result or {}).get("rows") if isinstance(r.result, dict) else []
        n = _persist(conn, app_id, rows or [], replace=replace)
        table.add_row(str(app_id), str(meta.get("company", "?")), str(n))
        log_queue_ingest(
            conn, operation="rejection_reason", item_count=1, app_id=app_id,
        )
    console.print(table)
    console.print(f"[green]ingest complete[/green] — {queue_dir}")


def show_breakdown() -> None:
    """Print a category-count breakdown — read-only."""
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)
    rows = conn.execute(
        """
        SELECT category, COUNT(*) AS n
        FROM rejection_reasons
        GROUP BY category
        ORDER BY n DESC
        """
    ).fetchall()
    if not rows:
        console.print("(no rejection reasons captured yet — try `jr learn rejections`)")
        return
    t = Table(title=f"Rejection reasons by category ({sum(r['n'] for r in rows)} rows)")
    t.add_column("Category")
    t.add_column("Count", justify="right")
    for r in rows:
        t.add_row(r["category"], str(r["n"]))
    console.print(t)
