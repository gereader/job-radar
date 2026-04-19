"""`jr offer <app_id>` — Opus offer evaluation + counter-script. Direct or queue."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from ._report import REPORT_RESULT_SCHEMA, report_text, write_app_report
from .client import DirectLLM, QueueLLM, log_queue_ingest
from .dispatcher import build_llm

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_OFFER = Path(__file__).parent.parent.parent / "modes" / "offer.md"


def _prompt_offer(existing: dict) -> dict:
    def ask(label, default):
        return Prompt.ask(label, default=str(default) if default is not None else "") or None

    console.print("[dim]press enter to keep existing values.[/dim]")
    base = ask("Base salary (integer, e.g. 210000)", existing.get("offer_base"))
    bonus = ask("Bonus (annual target, integer)", existing.get("offer_bonus"))
    equity = ask("Equity (e.g. '$400k over 4yr' or '8000 RSUs')", existing.get("offer_equity"))
    currency = ask("Currency", existing.get("offer_currency") or "USD")
    start = ask("Start date (YYYY-MM-DD)", existing.get("offer_start"))
    deadline = ask("Response deadline (YYYY-MM-DD)", existing.get("offer_deadline"))
    notes = ask("Other notes (sign-on, PTO, remote, etc.)", existing.get("offer_notes"))
    return {
        "offer_base": int(base) if base and base.isdigit() else existing.get("offer_base"),
        "offer_bonus": int(bonus) if bonus and bonus.isdigit() else existing.get("offer_bonus"),
        "offer_equity": equity or existing.get("offer_equity"),
        "offer_currency": currency or existing.get("offer_currency"),
        "offer_start": start or existing.get("offer_start"),
        "offer_deadline": deadline or existing.get("offer_deadline"),
        "offer_notes": notes or existing.get("offer_notes"),
    }


def _persist_offer_fields(conn, app_id: int, fields: dict) -> None:
    with tx(conn):
        conn.execute(
            """
            UPDATE applications
            SET offer_base = ?, offer_bonus = ?, offer_equity = ?, offer_currency = ?,
                offer_start = ?, offer_deadline = ?, offer_notes = ?,
                status = 'Offer'
            WHERE id = ?
            """,
            (
                fields["offer_base"], fields["offer_bonus"], fields["offer_equity"],
                fields["offer_currency"], fields["offer_start"], fields["offer_deadline"],
                fields["offer_notes"], app_id,
            ),
        )


def _user_prompt(cfg: Config, row, fields: dict) -> str:
    target = (cfg.profile.get("targets") or {}).get("comp") or {}
    return (
        f"Candidate target comp: {target}\n"
        f"Profile dealbreakers: {(cfg.profile.get('targets') or {}).get('dealbreakers', [])}\n"
        f"Company: {row['company']}\nRole: {row['title']}\nURL: {row['url']}\n"
        f"JD comp band (if stated): {row['comp_min']}-{row['comp_max']} {row['comp_currency'] or ''}\n\n"
        f"Offer on the table:\n"
        f"- Base: {fields['offer_base']}\n"
        f"- Bonus: {fields['offer_bonus']}\n"
        f"- Equity: {fields['offer_equity']}\n"
        f"- Currency: {fields['offer_currency']}\n"
        f"- Start: {fields['offer_start']}\n"
        f"- Deadline: {fields['offer_deadline']}\n"
        f"- Notes: {fields['offer_notes']}\n"
    )


def _row_for(conn, app_id: int):
    return conn.execute(
        """
        SELECT a.*, j.company, j.title, j.url, j.comp_min, j.comp_max, j.comp_currency
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = ?
        """,
        (app_id,),
    ).fetchone()


def run_offer_eval(app_id: int, *, force_prepare: bool = False) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = _row_for(conn, app_id)
    if not row:
        console.print(f"[red]no application {app_id}[/red]")
        return

    fields = _prompt_offer(dict(row))
    _persist_offer_fields(conn, app_id, fields)

    model = (cfg.profile.get("llm") or {}).get("offers_model", "claude-opus-4-7")
    backend, llm = build_llm(
        conn, cfg, operation="offer", default_model=model,
        result_schema=REPORT_RESULT_SCHEMA,
        force=("queue" if force_prepare else None),
    )
    system = _SHARED.read_text() + "\n\n---\n\n" + _OFFER.read_text()
    user = _user_prompt(cfg, row, fields)

    if backend == "queue":
        assert isinstance(llm, QueueLLM)
        llm.enqueue(
            system=system, user=user, item_id=app_id,
            meta={"app_id": app_id, "company": row["company"], "title": row["title"]},
            max_tokens=3500,
        )
        qdir = llm.finalize()
        console.print(
            f"[green]queued[/green] offer eval → {qdir}\n"
            f"Next: [bold]/jr consume {qdir}[/bold], "
            f"then [bold]jr offer --ingest {qdir}[/bold]."
        )
        return

    assert isinstance(llm, DirectLLM)
    resp = llm.complete(
        system=system, user=user, operation="offer",
        app_id=app_id, max_tokens=3500,
    )
    out = write_app_report(cfg, app_id, row["company"], "offer", resp.text)
    console.print(f"[green]offer eval[/green] → {out}")
    console.print(
        f"tokens: in={resp.input_tokens} out={resp.output_tokens} "
        f"cached={resp.cached_tokens}"
    )


def ingest_offer(queue_dir: Path) -> None:
    from .queue import ingest as q_ingest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    results = q_ingest(queue_dir)
    for r in results:
        meta = r.meta or {}
        app_id = int(meta.get("app_id") or r.id)
        company = meta.get("company") or "unknown"
        out = write_app_report(cfg, app_id, company, "offer", report_text(r.result))
        console.print(f"[green]offer eval[/green] app={app_id} → {out}")
        log_queue_ingest(conn, operation="offer", item_count=1, app_id=app_id)
    console.print(f"\n[green]ingest complete[/green] — {queue_dir}")
