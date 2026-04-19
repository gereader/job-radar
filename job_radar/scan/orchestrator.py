"""Run all configured portals, dedup, save JDs, run pre-screen.

Parallelism: per-portal HTTP work runs concurrently in a thread pool
(configurable, default 8 workers). Each portal sleeps a small amount
before its first request to avoid stampeding upstream APIs at t=0.

Dedup chain (cheapest first):
  1. ``ghosted_until`` skip on the portal entry itself (fast yaml check).
  2. URL hash (zero-parse).
  3. (source, source_id) hit (zero-parse, second cheap gate added in
     Block 3 — most reposts share the portal-native id even when the URL
     mutates).
  4. Content hash (one parse).
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import httpx
import yaml
from rich.console import Console
from rich.progress import Progress

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from ..parse.html_to_md import html_to_markdown
from ..parse.jd_extract import extract_all
from ..screen.keywords import Ruleset, screen
from ..util.hashing import content_hash, url_hash
from . import ashby, greenhouse, lever
from .base import RawJob

console = Console()

SCANNERS: dict[str, object] = {
    "greenhouse": greenhouse,
    "ashby": ashby,
    "lever": lever,
}


def _load_optional_scanner(source_name: str):
    if source_name == "workable":
        from . import workable
        return workable
    if source_name == "deep-crawl":
        from . import deepcrawl
        return deepcrawl
    return None


def _title_allowed(title: str, filt: dict) -> bool:
    t = (title or "").lower()
    pos = [p.lower() for p in filt.get("positive", []) or []]
    neg = [n.lower() for n in filt.get("negative", []) or []]
    if pos and not any(p in t for p in pos):
        return False
    if any(n in t for n in neg):
        return False
    return True


def _entry_ghosted(entry: dict) -> bool:
    """Honor a ``ghosted_until: YYYY-MM-DD`` field on the portals.yml entry."""
    until = entry.get("ghosted_until")
    if not until:
        return False
    try:
        d = date.fromisoformat(str(until))
    except ValueError:
        return False
    return d > date.today()


def _fetch_one_portal(
    entry: dict, filt: dict, client: httpx.Client, rate_ms: int,
) -> list[RawJob]:
    """Run one scanner end-to-end. Returns list (not generator) so it's
    safe to ferry across threads."""
    if rate_ms > 0:
        time.sleep(rate_ms / 1000.0)
    scanner = SCANNERS.get(entry.get("source")) or _load_optional_scanner(
        entry.get("source", "")
    )
    if scanner is None:
        console.print(f"[yellow]skip[/yellow] unknown source: {entry}")
        return []
    try:
        return [
            j for j in scanner.fetch(entry["slug"], entry["name"], client=client)
            if _title_allowed(j.title, filt)
        ]
    except Exception as e:
        console.print(f"[red]{entry['name']} {entry['source']}: {e}[/red]")
        return []


def _iter_portal_jobs(
    portals_cfg: dict, only: str | None, *, max_workers: int, rate_ms: int,
) -> Iterable[RawJob]:
    filt = portals_cfg.get("title_filter", {}) or {}
    entries = []
    for entry in portals_cfg.get("companies", []) or []:
        if not entry.get("enabled", True):
            continue
        if _entry_ghosted(entry):
            continue
        if only and entry.get("slug") != only and entry.get("name") != only:
            continue
        entries.append(entry)

    if not entries:
        return

    client = httpx.Client(timeout=20.0, follow_redirects=True)
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_fetch_one_portal, e, filt, client, rate_ms): e
                for e in entries
            }
            for fut in as_completed(futures):
                yield from fut.result()
    finally:
        client.close()


def _save_jd(cfg: Config, job_hash: str, company: str, title: str, md: str) -> Path:
    from ..util.slugify import slugify

    fname = f"{job_hash[:12]}-{slugify(company)}-{slugify(title, 40)}.md"
    path = cfg.jds_active / fname
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# {title} — {company}\n\n{md}\n",
        encoding="utf-8",
    )
    return path


def run_scan(portal: str | None = None, limit: int = 0, dry_run: bool = False) -> None:
    cfg = Config.load()
    cfg.ensure_dirs()
    conn = connect(cfg)
    migrate(conn)

    if not cfg.portals_path.exists():
        console.print(f"[red]missing {cfg.portals_path}[/red] — run `jr init` first")
        return
    portals_cfg = yaml.safe_load(cfg.portals_path.read_text()) or {}

    rules = Ruleset.from_yaml(cfg.keywords_path) if cfg.keywords_path.exists() else Ruleset()
    pass_at = (cfg.profile.get("scoring") or {}).get("pass_at", 70)
    review_at = (cfg.profile.get("scoring") or {}).get("review_at", 40)
    transparency_states = list(cfg.profile.get("transparency_states") or [])

    scan_cfg = (cfg.profile.get("scan") or {})
    max_workers = int(scan_cfg.get("max_workers") or 8)
    rate_ms = int(scan_cfg.get("rate_ms") or 250)

    seen_url_hashes = {
        r[0] for r in conn.execute("SELECT url_hash FROM scan_history").fetchall()
    }
    seen_content_hashes = {
        r[0] for r in conn.execute("SELECT hash FROM jobs").fetchall()
    }
    seen_source_ids: set[tuple[str, str]] = {
        (r[0], r[1]) for r in conn.execute(
            "SELECT source, source_id FROM jobs WHERE source_id IS NOT NULL"
        ).fetchall()
    }

    added = 0
    dupes = 0
    skipped = 0
    to_review = 0
    to_pass = 0

    with Progress(console=console, transient=True) as progress:
        task = progress.add_task("scanning", total=None)
        for raw in _iter_portal_jobs(
            portals_cfg, portal, max_workers=max_workers, rate_ms=rate_ms,
        ):
            progress.advance(task)
            uh = url_hash(raw.url)
            if uh in seen_url_hashes:
                dupes += 1
                with tx(conn):
                    conn.execute(
                        "UPDATE scan_history SET last_seen=datetime('now') WHERE url_hash=?",
                        (uh,),
                    )
                continue

            # Cheap second gate: portal-native (source, source_id) — most
            # reposts keep the same id even when the URL mutates.
            if raw.source_id and (raw.source, str(raw.source_id)) in seen_source_ids:
                dupes += 1
                with tx(conn):
                    conn.execute(
                        """
                        INSERT INTO scan_history(url_hash, url, source, outcome)
                        VALUES (?, ?, ?, 'duplicate')
                        ON CONFLICT(url_hash) DO UPDATE SET last_seen=datetime('now')
                        """,
                        (uh, raw.url, raw.source),
                    )
                seen_url_hashes.add(uh)
                continue

            md = raw.body_markdown or html_to_markdown(raw.body_html)
            ch = content_hash(raw.company, raw.title, md)
            if ch in seen_content_hashes:
                dupes += 1
                with tx(conn):
                    conn.execute(
                        """
                        INSERT INTO scan_history(url_hash, url, source, outcome)
                        VALUES (?, ?, ?, 'duplicate')
                        ON CONFLICT(url_hash) DO UPDATE SET last_seen=datetime('now')
                        """,
                        (uh, raw.url, raw.source),
                    )
                continue

            fields = extract_all(raw.title, md)
            result = screen(
                raw.title,
                md,
                raw.location or fields.location,
                rules,
                pass_at=pass_at,
                review_at=review_at,
                transparency_states=transparency_states,
            )

            if result.verdict == "pass":
                to_pass += 1
            elif result.verdict == "review":
                to_review += 1
            else:
                skipped += 1

            if dry_run:
                continue

            jd_path = _save_jd(cfg, ch, raw.company, raw.title, md)

            with tx(conn):
                conn.execute(
                    """
                    INSERT INTO jobs(hash, source, source_id, company, title, location,
                                     remote, url, comp_min, comp_max, comp_currency,
                                     posted_at, jd_path, screen_verdict, screen_score,
                                     screen_reasons)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ch, raw.source, raw.source_id, raw.company, raw.title,
                        raw.location or fields.location, fields.remote, raw.url,
                        fields.comp_min, fields.comp_max, fields.comp_currency,
                        raw.posted_at, cfg.relpath(jd_path),
                        result.verdict, result.score, result.as_json_reasons(),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO scan_history(url_hash, url, source, outcome)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(url_hash) DO UPDATE
                      SET last_seen=datetime('now'), outcome=excluded.outcome
                    """,
                    (uh, raw.url, raw.source,
                     "screened_out" if result.verdict == "skip" else "new"),
                )
            seen_url_hashes.add(uh)
            seen_content_hashes.add(ch)
            if raw.source_id:
                seen_source_ids.add((raw.source, str(raw.source_id)))
            added += 1
            if limit and added >= limit:
                break

    console.print(
        f"[green]scan done[/green] "
        f"added={added} dup={dupes} skip={skipped} review={to_review} pass={to_pass} "
        f"workers={max_workers} rate_ms={rate_ms}"
    )
    from ..dash.build import rebuild_silently
    rebuild_silently()
