"""`jr inbox paste` — Haiku extracts structured fields from a pasted thread."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from ..llm.client import LLM

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_INGEST = Path(__file__).parent.parent.parent / "modes" / "ingest.md"
_RESPOND = Path(__file__).parent.parent.parent / "modes" / "respond.md"


def _read_text(file: Path | None) -> str:
    if file:
        return Path(file).read_text()
    if sys.stdin.isatty():
        console.print("[yellow]paste the message, then Ctrl-D:[/yellow]")
    return sys.stdin.read()


def _find_app(conn, company: str | None, role: str | None) -> int | None:
    if not company:
        return None
    candidates = conn.execute(
        """
        SELECT a.id, j.title
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE lower(j.company) = lower(?)
        ORDER BY a.updated_at DESC
        """,
        (company,),
    ).fetchall()
    if not candidates:
        return None
    if len(candidates) == 1 or not role:
        return candidates[0]["id"]
    role_l = role.lower()
    for c in candidates:
        if role_l in (c["title"] or "").lower():
            return c["id"]
    return candidates[0]["id"]


def _save_source(cfg: Config, text: str, channel: str, contact: str | None) -> Path:
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    safe = (contact or "unknown").replace(" ", "-").lower()[:40]
    d = cfg.private / "messages" / channel
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{stamp}-{safe}.md"
    p.write_text(text)
    return p


def ingest_paste(
    file: Path | None = None,
    app_id: int | None = None,
    draft: bool = False,
) -> int | None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    text = _read_text(file).strip()
    if not text:
        console.print("[red]no text provided.[/red]")
        return None

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    llm = LLM(conn, default_model=model)
    system = _SHARED.read_text() + "\n\n---\n\n" + _INGEST.read_text()
    resp = llm.complete(
        system=system, user=text, operation="ingest_paste", max_tokens=700,
    )
    try:
        data = json.loads(resp.text.strip().strip("`"))
    except json.JSONDecodeError:
        console.print(f"[red]could not parse model output[/red]: {resp.text[:200]}")
        return None

    channel = data.get("channel") or "other"
    direction = data.get("direction") or "inbound"
    # Prompt for the date if the model couldn't find one.
    if not data.get("occurred_at"):
        entered = Prompt.ask(
            "message date not detected — enter YYYY-MM-DD (blank = today)",
            default="",
        ).strip()
        if entered:
            data["occurred_at"] = entered
    src_path = _save_source(cfg, text, channel, data.get("contact_name"))

    # Upsert contact if we have at least a name.
    contact_id = None
    if data.get("contact_name"):
        with tx(conn):
            existing = conn.execute(
                """
                SELECT id FROM contacts
                WHERE (linkedin_url IS NOT NULL AND linkedin_url = ?)
                   OR (email IS NOT NULL AND email = ?)
                   OR (lower(name) = lower(?) AND lower(COALESCE(company,'')) = lower(COALESCE(?, '')))
                LIMIT 1
                """,
                (
                    data.get("contact_linkedin"),
                    data.get("contact_email"),
                    data["contact_name"],
                    data.get("contact_company") or "",
                ),
            ).fetchone()
            if existing:
                contact_id = existing["id"]
                conn.execute(
                    """
                    UPDATE contacts
                    SET company = COALESCE(?, company),
                        title   = COALESCE(?, title),
                        linkedin_url = COALESCE(?, linkedin_url),
                        email   = COALESCE(?, email)
                    WHERE id = ?
                    """,
                    (
                        data.get("contact_company"),
                        data.get("contact_title"),
                        data.get("contact_linkedin"),
                        data.get("contact_email"),
                        contact_id,
                    ),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO contacts(name, company, title, linkedin_url, email)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        data["contact_name"],
                        data.get("contact_company"),
                        data.get("contact_title"),
                        data.get("contact_linkedin"),
                        data.get("contact_email"),
                    ),
                )
                contact_id = cur.lastrowid

    # Resolve application.
    resolved_app = app_id or _find_app(
        conn,
        data.get("contact_company") or data.get("company_mentioned"),
        data.get("role_mentioned"),
    )

    with tx(conn):
        cur = conn.execute(
            """
            INSERT INTO touchpoints(application_id, contact_id, channel, direction,
                                     occurred_at, summary, source_msg_path)
            VALUES (?, ?, ?, ?, COALESCE(?, datetime('now')), ?, ?)
            """,
            (
                resolved_app, contact_id, channel, direction,
                data.get("occurred_at"), data.get("summary"),
                str(src_path.relative_to(cfg.root)),
            ),
        )
        touch_id = cur.lastrowid
        if resolved_app:
            conn.execute(
                "UPDATE applications SET next_action_at = date('now','+7 days') WHERE id = ?",
                (resolved_app,),
            )

    console.print(
        f"[green]touchpoint {touch_id}[/green] "
        f"app={resolved_app or '-'} contact={contact_id or '-'} "
        f"channel={channel} intent={data.get('intent','-')}"
    )
    if data.get("job_url"):
        console.print(f"[cyan]job URL seen:[/cyan] {data['job_url']}")

    if draft:
        _draft_reply(cfg, conn, llm, text, data, resolved_app)

    return touch_id


def _draft_reply(cfg, conn, llm, original: str, extracted: dict, app_id: int | None) -> None:
    """Use Haiku (already warm via prompt caching) to draft a response."""
    profile_summary = {
        "targets": cfg.profile.get("targets", {}),
        "name": (cfg.profile.get("identity") or {}).get("name"),
    }
    system = _SHARED.read_text() + "\n\n---\n\n" + _RESPOND.read_text()
    user = (
        f"Candidate profile (for constraint awareness):\n{profile_summary}\n\n"
        f"Extracted intent: {extracted.get('intent','other')}\n"
        f"Contact: {extracted.get('contact_name','unknown')} "
        f"({extracted.get('contact_title','')}) @ {extracted.get('contact_company','')}\n"
        f"Role mentioned: {extracted.get('role_mentioned','-')}\n\n"
        f"---\n\nOriginal message:\n{original}"
    )
    resp = llm.complete(
        system=system, user=user, operation="respond_draft",
        app_id=app_id, max_tokens=400,
    )
    console.print("\n[bold]Draft reply:[/bold]\n")
    console.print(resp.text)
