"""`jr outreach <contact_id|--company X>` — Haiku draft of a short DM/email.

Direct or queue. The draft is printed (or queued); nothing is sent or
persisted automatically — the user copies, edits, sends, then can log a
touchpoint manually with ``jr touch``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from .client import DirectLLM, QueueLLM, log_queue_ingest
from .dispatcher import build_llm

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_OUTREACH = Path(__file__).parent.parent.parent / "modes" / "outreach.md"


OUTREACH_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["body_md"],
    "properties": {
        "subject": {"type": ["string", "null"]},
        "body_md": {"type": "string"},
        "tone": {"type": "string"},
        "channel": {"type": "string"},
    },
}


_KIND_HINT = {
    "recruiter": "They source candidates; ask is usually about an open role.",
    "hiring_manager": "They own the role; ask is usually a 15-min intro.",
    "peer_engineer": "Same level / craft; ask is usually a coffee/intro chat.",
    "alumni": "Shared school or company; intro chat / referral.",
}

_ASK_HINT = {
    "intro_chat": "15-minute intro call.",
    "referral": "internal referral for a specific role.",
    "role_status": "status update on a previously-applied role.",
    "coffee": "in-person or virtual coffee, no agenda.",
}


def _system() -> str:
    return _SHARED.read_text() + "\n\n---\n\n" + _OUTREACH.read_text()


def _resolve_contact(conn, contact_id: int | None, company: str | None) -> dict | None:
    if contact_id:
        row = conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        return dict(row) if row else None
    if company:
        row = conn.execute(
            "SELECT * FROM contacts WHERE lower(company) = lower(?) "
            "ORDER BY first_seen_at DESC LIMIT 1",
            (company,),
        ).fetchone()
        return dict(row) if row else None
    return None


def _user_prompt(cfg: Config, contact: dict, kind: str, ask: str,
                 channel: str, signal: str | None) -> str:
    cv_md = cfg.cv_path.read_text() if cfg.cv_path.exists() else ""
    targets = (cfg.profile.get("targets") or {})
    archetypes = targets.get("archetypes", [])
    return (
        f"Channel: {channel}\nKind: {kind} — {_KIND_HINT.get(kind, '')}\n"
        f"Ask: {ask} — {_ASK_HINT.get(ask, '')}\n"
        f"Recent signal: {signal or '(none)'}\n\n"
        f"Contact:\n"
        f"  name: {contact.get('name')}\n"
        f"  title: {contact.get('title')}\n"
        f"  company: {contact.get('company')}\n"
        f"  linkedin_url: {contact.get('linkedin_url')}\n\n"
        f"Candidate target archetypes: {archetypes}\n\n"
        f"---\n\n## Candidate CV summary\n{cv_md[:3500]}"
    )


def run_outreach(
    *,
    contact_id: int | None = None,
    company: str | None = None,
    kind: str = "recruiter",
    ask: str = "intro_chat",
    channel: str = "linkedin",
    signal: str | None = None,
    force_prepare: bool = False,
) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    contact = _resolve_contact(conn, contact_id, company)
    if not contact:
        console.print(
            "[red]no contact matched[/red]. "
            "Pass --contact <id> or --company <name>."
        )
        return

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    backend, llm = build_llm(
        conn, cfg, operation="outreach", default_model=model,
        result_schema=OUTREACH_RESULT_SCHEMA,
        force=("queue" if force_prepare else None),
    )
    system = _system()
    user = _user_prompt(cfg, contact, kind, ask, channel, signal)

    if backend == "queue":
        assert isinstance(llm, QueueLLM)
        llm.enqueue(
            system=system, user=user, item_id=contact["id"],
            meta={
                "contact_id": contact["id"], "name": contact.get("name"),
                "company": contact.get("company"), "channel": channel, "ask": ask,
            },
            max_tokens=600,
        )
        qdir = llm.finalize()
        console.print(
            f"[green]queued[/green] outreach → {qdir}\n"
            f"Next: [bold]/jr consume {qdir}[/bold], "
            f"then [bold]jr outreach --ingest {qdir}[/bold]."
        )
        return

    assert isinstance(llm, DirectLLM)
    resp = llm.complete(
        system=system, user=user, operation="outreach", max_tokens=600,
    )
    console.print(
        f"\n[bold]outreach to {contact.get('name')} @ {contact.get('company')}[/bold]\n"
    )
    console.print(resp.text)


def ingest_outreach(queue_dir: Path) -> None:
    from .queue import ingest as q_ingest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    results = q_ingest(queue_dir)
    for r in results:
        meta = r.meta or {}
        result = r.result if isinstance(r.result, dict) else {}
        subject = result.get("subject")
        body = result.get("body_md") or str(result)
        console.print(
            f"\n[bold]→ {meta.get('name', '?')} @ {meta.get('company', '?')} "
            f"({meta.get('channel', '?')})[/bold]"
        )
        if subject:
            console.print(f"Subject: {subject}")
        console.print(f"\n{body}\n")
        log_queue_ingest(conn, operation="outreach", item_count=1)
    console.print(f"\n[green]ingest complete[/green] — {queue_dir}")
