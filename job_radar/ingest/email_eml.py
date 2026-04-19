"""`jr inbox email <path>` — .eml / .mbox ingestion.

Headers are parsed deterministically; only the body + intent classification
touches Haiku.
"""

from __future__ import annotations

import email
import email.policy
import mailbox
from collections.abc import Iterable
from pathlib import Path

from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from .paste import ingest_paste as _ingest_text_payload  # reuse extractor

console = Console()


def _iter_messages(path: Path) -> Iterable[email.message.EmailMessage]:
    if path.suffix.lower() == ".mbox" or path.is_dir():
        mb = mailbox.mbox(str(path))
        for msg in mb:
            yield email.message_from_bytes(bytes(msg), policy=email.policy.default)
    else:
        yield email.message_from_bytes(path.read_bytes(), policy=email.policy.default)


def _plain_body(msg: email.message.EmailMessage) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_content()
                except Exception:
                    continue
        # fall back to any text/html stripped
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    from ..parse.html_to_md import html_to_markdown

                    return html_to_markdown(part.get_content())
                except Exception:
                    continue
    try:
        return msg.get_content()
    except Exception:
        return msg.get_payload(decode=True).decode(errors="ignore")


def ingest_email(path: Path) -> int:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    n = 0
    for msg in _iter_messages(path):
        from_ = msg["From"] or ""
        subject = msg["Subject"] or ""
        date = msg["Date"] or ""
        body = _plain_body(msg) or ""
        payload = (
            f"From: {from_}\n"
            f"Subject: {subject}\n"
            f"Date: {date}\n\n"
            f"{body}"
        )
        # Write a temp file for paste ingest to read via its file pathway.
        tmp = cfg.private / "messages" / "email" / "raw"
        tmp.mkdir(parents=True, exist_ok=True)
        stamp = (msg["Message-ID"] or f"{n}").replace("<", "").replace(">", "")[:60]
        t = tmp / f"{stamp}.eml.txt"
        t.write_text(payload)
        _ingest_text_payload(file=t)
        n += 1

    console.print(f"[green]ingested {n} email(s)[/green]")
    return n
