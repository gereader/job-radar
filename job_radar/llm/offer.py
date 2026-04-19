"""`jr offer <app_id>` — Opus offer evaluation + counter-script."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from ..util.slugify import slugify
from .client import LLM

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


def run_offer_eval(app_id: int) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = conn.execute(
        """
        SELECT a.*, j.company, j.title, j.url, j.comp_min, j.comp_max, j.comp_currency
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = ?
        """,
        (app_id,),
    ).fetchone()
    if not row:
        console.print(f"[red]no application {app_id}[/red]")
        return

    fields = _prompt_offer(dict(row))
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

    target = (cfg.profile.get("targets") or {}).get("comp") or {}
    system = _SHARED.read_text() + "\n\n---\n\n" + _OFFER.read_text()
    user = (
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
    model = (cfg.profile.get("llm") or {}).get("offers_model", "claude-opus-4-7")
    llm = LLM(conn, default_model=model)
    resp = llm.complete(
        system=system, user=user, operation="offer",
        app_id=app_id, max_tokens=3500,
    )

    app_dir = cfg.applications_dir / f"{app_id}-{slugify(row['company'])}"
    app_dir.mkdir(parents=True, exist_ok=True)
    out = app_dir / f"offer-{date.today().isoformat()}.md"
    out.write_text(resp.text)
    console.print(f"[green]offer eval[/green] → {out}")
    console.print(
        f"tokens: in={resp.input_tokens} out={resp.output_tokens} "
        f"cached={resp.cached_tokens}"
    )
