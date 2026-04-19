"""Run all configured portals, dedup, save JDs, run pre-screen."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

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


def _iter_portal_jobs(portals_cfg: dict, only: str | None) -> Iterable[RawJob]:
    filt = portals_cfg.get("title_filter", {}) or {}
    client = httpx.Client(timeout=20.0, follow_redirects=True)
    try:
        for entry in portals_cfg.get("companies", []) or []:
            if not entry.get("enabled", True):
                continue
            if only and entry.get("slug") != only and entry.get("name") != only:
                continue
            scanner = SCANNERS.get(entry.get("source")) or _load_optional_scanner(
                entry.get("source", "")
            )
            if scanner is None:
                console.print(f"[yellow]skip[/yellow] unknown source: {entry}")
                continue
            try:
                for j in scanner.fetch(entry["slug"], entry["name"], client=client):
                    if _title_allowed(j.title, filt):
                        yield j
            except Exception as e:
                console.print(f"[red]{entry['name']} {entry['source']}: {e}[/red]")
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

    seen_url_hashes = {
        r[0] for r in conn.execute("SELECT url_hash FROM scan_history").fetchall()
    }
    seen_content_hashes = {
        r[0] for r in conn.execute("SELECT hash FROM jobs").fetchall()
    }

    added = 0
    dupes = 0
    skipped = 0
    to_review = 0
    to_pass = 0

    with Progress(console=console, transient=True) as progress:
        task = progress.add_task("scanning", total=None)
        for raw in _iter_portal_jobs(portals_cfg, portal):
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
                        raw.posted_at, str(jd_path.relative_to(cfg.root)),
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
            added += 1
            if limit and added >= limit:
                break

    console.print(
        f"[green]scan done[/green] "
        f"added={added} dup={dupes} skip={skipped} review={to_review} pass={to_pass}"
    )
