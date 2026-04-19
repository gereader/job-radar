"""`jr inbox paste` — Haiku extracts structured fields from a pasted thread.

Two backends:
  - Direct API: extract inline, log touchpoint, optional reply draft inline.
  - Queue: write one ``ingest_paste`` packet and exit. After ingest folds
    fields back into the DB, the user can run
    ``jr inbox draft <touch_id>`` which queues a separate ``respond_draft``
    packet using the now-extracted fields.

We never multiplex two operations in one queue dir — each queue holds a
single logical operation so ``/jr consume`` can be schema-driven.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Prompt

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from ..llm.client import DirectLLM, QueueLLM, log_queue_ingest
from ..llm.dispatcher import build_llm

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_INGEST = Path(__file__).parent.parent.parent / "modes" / "ingest.md"
_RESPOND = Path(__file__).parent.parent.parent / "modes" / "respond.md"

INGEST_PASTE_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "channel": {"type": "string"},
        "direction": {"type": "string", "enum": ["inbound", "outbound"]},
        "occurred_at": {"type": ["string", "null"]},
        "intent": {"type": "string"},
        "summary": {"type": "string"},
        "contact_name": {"type": ["string", "null"]},
        "contact_company": {"type": ["string", "null"]},
        "contact_title": {"type": ["string", "null"]},
        "contact_linkedin": {"type": ["string", "null"]},
        "contact_email": {"type": ["string", "null"]},
        "company_mentioned": {"type": ["string", "null"]},
        "role_mentioned": {"type": ["string", "null"]},
        "job_url": {"type": ["string", "null"]},
    },
}

RESPOND_DRAFT_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["draft_md"],
    "properties": {
        "draft_md": {"type": "string"},
        "intent": {"type": "string"},
        "next_action": {"type": "string"},
    },
}


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


def _apply_extraction(
    cfg: Config, conn, text: str, data: dict, *, hint_app_id: int | None,
) -> int:
    """Persist a touchpoint + optional contact upsert. Returns touchpoint id."""
    channel = data.get("channel") or "other"
    direction = data.get("direction") or "inbound"
    if not data.get("occurred_at"):
        entered = Prompt.ask(
            "message date not detected — enter YYYY-MM-DD (blank = today)",
            default="",
        ).strip()
        if entered:
            data["occurred_at"] = entered
    src_path = _save_source(cfg, text, channel, data.get("contact_name"))

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

    resolved_app = hint_app_id or _find_app(
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
                cfg.relpath(src_path),
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
    return touch_id


def ingest_paste(
    file: Path | None = None,
    app_id: int | None = None,
    draft: bool = False,
    *,
    force_prepare: bool = False,
) -> int | None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    text = _read_text(file).strip()
    if not text:
        console.print("[red]no text provided.[/red]")
        return None

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    backend, llm = build_llm(
        conn, cfg, operation="ingest_paste", default_model=model,
        result_schema=INGEST_PASTE_RESULT_SCHEMA,
        extra_meta={"want_draft": bool(draft), "hint_app_id": app_id},
        force=("queue" if force_prepare else None),
    )
    system = _SHARED.read_text() + "\n\n---\n\n" + _INGEST.read_text()

    if backend == "queue":
        assert isinstance(llm, QueueLLM)
        # Stash the raw text inside the queue dir so ingest can re-read it.
        # The packet body IS the text, but we duplicate to a stable path so
        # downstream draft generation can grab it without re-pasting.
        llm.enqueue(
            system=system, user=text, item_id="paste",
            meta={"hint_app_id": app_id, "want_draft": bool(draft)},
            max_tokens=700,
        )
        qdir = llm.finalize()
        # Save the original text alongside packets for the optional draft step.
        (qdir / "original.md").write_text(text)
        console.print(f"[green]queued[/green] inbox-paste extraction → {qdir}")
        if draft:
            console.print(
                "After ingest, run [bold]jr inbox draft <touch_id>[/bold] "
                "(or [bold]--prepare[/bold]) to draft a reply."
            )
        console.print(
            f"Next: [bold]/jr consume {qdir}[/bold], "
            f"then [bold]jr inbox paste --ingest {qdir}[/bold]."
        )
        return None

    assert isinstance(llm, DirectLLM)
    resp = llm.complete(
        system=system, user=text, operation="ingest_paste", max_tokens=700,
    )
    try:
        data = json.loads(resp.text.strip().strip("`"))
    except json.JSONDecodeError:
        console.print(f"[red]could not parse model output[/red]: {resp.text[:200]}")
        return None

    touch_id = _apply_extraction(cfg, conn, text, data, hint_app_id=app_id)
    if draft:
        # Direct path can pipeline because we still hold the LLM.
        _draft_reply_direct(
            cfg, conn, llm, text, data, _touch_app_id(conn, touch_id),
        )
    return touch_id


def ingest_paste_results(queue_dir: Path) -> None:
    from ..llm.queue import ingest as q_ingest
    from ..llm.queue import load_manifest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    manifest = load_manifest(queue_dir)
    extra = manifest.get("extra_meta") or {}
    results = q_ingest(queue_dir)
    original = (queue_dir / "original.md").read_text() if (queue_dir / "original.md").exists() else ""

    for r in results:
        data = r.result if isinstance(r.result, dict) else {}
        meta = r.meta or {}
        hint_app_id = meta.get("hint_app_id") or extra.get("hint_app_id")
        touch_id = _apply_extraction(
            cfg, conn, original, data, hint_app_id=hint_app_id,
        )
        log_queue_ingest(conn, operation="ingest_paste", item_count=1)
        if extra.get("want_draft") or meta.get("want_draft"):
            console.print(
                f"To draft a reply: [bold]jr inbox draft {touch_id}[/bold] "
                "(add --prepare for queue mode)."
            )
    console.print(f"\n[green]ingest complete[/green] — {queue_dir}")


def _touch_app_id(conn, touch_id: int) -> int | None:
    row = conn.execute(
        "SELECT application_id FROM touchpoints WHERE id = ?", (touch_id,)
    ).fetchone()
    return row["application_id"] if row else None


def _draft_user_prompt(cfg: Config, original: str, extracted: dict) -> str:
    profile_summary = {
        "targets": cfg.profile.get("targets", {}),
        "name": (cfg.profile.get("identity") or {}).get("name"),
    }
    return (
        f"Candidate profile (for constraint awareness):\n{profile_summary}\n\n"
        f"Extracted intent: {extracted.get('intent','other')}\n"
        f"Contact: {extracted.get('contact_name','unknown')} "
        f"({extracted.get('contact_title','')}) @ {extracted.get('contact_company','')}\n"
        f"Role mentioned: {extracted.get('role_mentioned','-')}\n\n"
        f"---\n\nOriginal message:\n{original}"
    )


def _draft_reply_direct(cfg, conn, llm, original: str, extracted: dict, app_id: int | None) -> None:
    system = _SHARED.read_text() + "\n\n---\n\n" + _RESPOND.read_text()
    resp = llm.complete(
        system=system, user=_draft_user_prompt(cfg, original, extracted),
        operation="respond_draft", app_id=app_id, max_tokens=400,
    )
    console.print("\n[bold]Draft reply:[/bold]\n")
    console.print(resp.text)


def draft_reply(touch_id: int, *, force_prepare: bool = False) -> None:
    """Draft a reply for an existing touchpoint. Direct or queue."""
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = conn.execute(
        """
        SELECT t.id AS touch_id, t.application_id, t.summary, t.source_msg_path,
               c.name AS contact_name, c.company AS contact_company,
               c.title AS contact_title
        FROM touchpoints t
        LEFT JOIN contacts c ON c.id = t.contact_id
        WHERE t.id = ?
        """,
        (touch_id,),
    ).fetchone()
    if not row:
        console.print(f"[red]no touchpoint {touch_id}[/red]")
        return
    src = row["source_msg_path"]
    original = ""
    if src:
        p = cfg.root / src
        if p.exists():
            original = p.read_text()
    extracted = {
        "contact_name": row["contact_name"],
        "contact_company": row["contact_company"],
        "contact_title": row["contact_title"],
        "intent": "follow_up",
        "role_mentioned": "",
    }

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    backend, llm = build_llm(
        conn, cfg, operation="respond_draft", default_model=model,
        result_schema=RESPOND_DRAFT_RESULT_SCHEMA,
        extra_meta={"touch_id": touch_id, "app_id": row["application_id"]},
        force=("queue" if force_prepare else None),
    )
    system = _SHARED.read_text() + "\n\n---\n\n" + _RESPOND.read_text()

    if backend == "queue":
        assert isinstance(llm, QueueLLM)
        llm.enqueue(
            system=system, user=_draft_user_prompt(cfg, original, extracted),
            item_id=touch_id,
            meta={"touch_id": touch_id, "app_id": row["application_id"]},
            max_tokens=400,
        )
        qdir = llm.finalize()
        console.print(
            f"[green]queued[/green] reply draft → {qdir}\n"
            f"Next: [bold]/jr consume {qdir}[/bold], "
            f"then [bold]jr inbox draft --ingest {qdir}[/bold]."
        )
        return

    assert isinstance(llm, DirectLLM)
    _draft_reply_direct(cfg, conn, llm, original, extracted, row["application_id"])


def ingest_draft(queue_dir: Path) -> None:
    from ..llm.queue import ingest as q_ingest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    results = q_ingest(queue_dir)
    for r in results:
        meta = r.meta or {}
        body = r.result.get("draft_md") if isinstance(r.result, dict) else str(r.result)
        console.print(f"\n[bold]draft (touch {meta.get('touch_id', '?')})[/bold]\n")
        console.print(body)
        log_queue_ingest(
            conn, operation="respond_draft", item_count=1,
            app_id=meta.get("app_id") or None,
        )
    console.print(f"\n[green]ingest complete[/green] — {queue_dir}")
