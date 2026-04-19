"""`jr add <url>` — fetch a single posting, parse, screen, insert.

Always ends up in the review bucket (`screen_verdict='review'`) so the
user's normal triage flow picks it up. No LLM call, ever — same Python
heuristics as `jr scan`.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from ..parse.html_to_md import html_to_markdown
from ..parse.jd_extract import extract_all
from ..screen.keywords import Ruleset, screen
from ..util.hashing import content_hash, url_hash
from ..util.slugify import slugify

console = Console()


def _guess_source(host: str) -> str:
    h = host.lower()
    if "greenhouse" in h:
        return "greenhouse"
    if "ashbyhq.com" in h or "ashby" in h:
        return "ashby"
    if "lever.co" in h or "jobs.lever" in h:
        return "lever"
    if "workable.com" in h:
        return "workable"
    if "linkedin.com" in h:
        return "linkedin"
    return "manual"


def _guess_source_id(url: str) -> str | None:
    """Pull the portal-native id out of common URL shapes."""
    m = re.search(r"/jobs/(?:detail/)?(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{6,})", url)
    if m:
        return m.group(1)
    m = re.search(r"/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})", url)
    if m:
        return m.group(1)
    return None


def _guess_company(host: str, html: str, fallback: str) -> str:
    """Best-effort company name without hitting an LLM."""
    # Title tag often "Job Title at Company" or "Company - Job Title"
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
    if m:
        title = m.group(1).strip()
        if " at " in title.lower():
            parts = re.split(r"\s+at\s+", title, maxsplit=1, flags=re.I)
            if len(parts) == 2:
                return parts[1].split("|")[0].strip()
        if " - " in title:
            return title.split(" - ", 1)[0].strip()
        if " | " in title:
            return title.split(" | ", 1)[0].strip()
    # Greenhouse: boards.greenhouse.io/<slug>
    if "greenhouse" in host:
        m2 = re.search(r"boards\.greenhouse\.io/([^/]+)", host)
        if m2:
            return m2.group(1).replace("-", " ").title()
    return fallback


def _guess_title(html: str, fallback: str) -> str:
    m = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
    if m:
        title = m.group(1).strip()
        if " at " in title.lower():
            return re.split(r"\s+at\s+", title, maxsplit=1, flags=re.I)[0].strip()
        return title.split(" - ")[0].split(" | ")[0].strip()
    return fallback


def add_url(url: str, *, force_review: bool = True) -> int | None:
    """Fetch ``url``, parse, screen, insert into ``jobs``. Return job id."""
    cfg = Config.load()
    cfg.ensure_dirs()
    conn = connect(cfg)
    migrate(conn)

    parsed = urlparse(url)
    if not parsed.scheme.startswith("http"):
        console.print(f"[red]invalid url:[/red] {url}")
        return None

    uh = url_hash(url)
    existing = conn.execute(
        "SELECT id, company, title FROM jobs j WHERE EXISTS "
        "(SELECT 1 FROM scan_history s WHERE s.url_hash = ? AND s.url = j.url) "
        "OR j.url = ?",
        (uh, url),
    ).fetchone()
    if existing:
        console.print(
            f"[yellow]already in DB[/yellow] as job {existing['id']} "
            f"({existing['company']} / {existing['title']})"
        )
        return existing["id"]

    try:
        with httpx.Client(timeout=20.0, follow_redirects=True,
                          headers={"user-agent": "job-radar/0.1"}) as client:
            r = client.get(url)
            r.raise_for_status()
            html = r.text
    except httpx.HTTPError as e:
        console.print(f"[red]fetch failed:[/red] {e}")
        return None

    md = html_to_markdown(html)
    if not md.strip():
        console.print("[red]parsed JD body is empty — nothing to screen.[/red]")
        return None

    source = _guess_source(parsed.netloc)
    source_id = _guess_source_id(url)
    company = _guess_company(parsed.netloc, html, fallback=parsed.netloc)
    title = _guess_title(html, fallback="Unknown role")

    fields = extract_all(title, md)
    rules = Ruleset.from_yaml(cfg.keywords_path) if cfg.keywords_path.exists() else Ruleset()
    pass_at = (cfg.profile.get("scoring") or {}).get("pass_at", 70)
    review_at = (cfg.profile.get("scoring") or {}).get("review_at", 40)
    transparency_states = list(cfg.profile.get("transparency_states") or [])
    result = screen(
        title, md, fields.location, rules,
        pass_at=pass_at, review_at=review_at,
        transparency_states=transparency_states,
    )
    verdict = "review" if force_review else result.verdict

    ch = content_hash(company, title, md)
    fname = f"{ch[:12]}-{slugify(company)}-{slugify(title, 40)}.md"
    jd_path = cfg.jds_active / fname
    jd_path.parent.mkdir(parents=True, exist_ok=True)
    jd_path.write_text(f"# {title} — {company}\n\n{md}\n", encoding="utf-8")

    with tx(conn):
        cur = conn.execute(
            """
            INSERT INTO jobs(hash, source, source_id, company, title, location,
                             remote, url, comp_min, comp_max, comp_currency,
                             jd_path, screen_verdict, screen_score, screen_reasons)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ch, source, source_id, company, title,
                fields.location, fields.remote, url,
                fields.comp_min, fields.comp_max, fields.comp_currency,
                cfg.relpath(jd_path),
                verdict, result.score, result.as_json_reasons(),
            ),
        )
        job_id = cur.lastrowid
        conn.execute(
            """
            INSERT INTO scan_history(url_hash, url, source, outcome)
            VALUES (?, ?, ?, 'new')
            ON CONFLICT(url_hash) DO UPDATE
              SET last_seen=datetime('now'), outcome=excluded.outcome
            """,
            (uh, url, source),
        )

    console.print(
        f"[green]added job {job_id}[/green] {company} / {title} "
        f"(score={result.score} → {verdict})\n"
        f"[dim]{cfg.relpath(jd_path)}[/dim]"
    )
    return job_id
