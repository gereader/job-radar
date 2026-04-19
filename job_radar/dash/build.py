"""`jr dash` — build a single-file static HTML dashboard from the DB.

Everything inlined: CSS, a tiny bit of JS for sort/filter, JSON data.
No web server, no external requests. Open with file://.
"""

from __future__ import annotations

import json
import webbrowser
from datetime import date
from pathlib import Path

from rich.console import Console

from ..config import Config
from ..db import connect, migrate

console = Console()

_TEMPLATE_PATH = Path(__file__).parent / "template.html"


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _file_url(root: Path, rel: str | None) -> str | None:
    if not rel:
        return None
    p = (root / rel).resolve()
    return f"file://{p}"


def _scan_app_artifacts(cfg: Config, app_id: int, company: str) -> dict[str, str | None]:
    """Find latest artifacts in private/applications/<id-slug>/."""
    from ..util.slugify import slugify
    out: dict[str, str | None] = {
        "jd_file_url": None,
        "report_file_url": None,
        "resume_pdf_url": None,
        "cover_pdf_url": None,
        "answers_url": None,
        "interview_prep_url": None,
        "offer_url": None,
        "research_url": None,
    }
    app_dir = cfg.applications_dir / f"{app_id}-{slugify(company)}"
    if not app_dir.exists():
        return out
    for prefix, key in (
        ("report-", "report_file_url"),
        ("interview-prep-", "interview_prep_url"),
        ("offer-", "offer_url"),
        ("company-", "research_url"),
    ):
        files = sorted(app_dir.glob(f"{prefix}*.md"), reverse=True)
        if files:
            out[key] = f"file://{files[0].resolve()}"
    if (app_dir / "answers.md").exists():
        out["answers_url"] = f"file://{(app_dir / 'answers.md').resolve()}"
    return out


def _collect(conn, cfg: Config) -> dict:
    target = (cfg.profile.get("targets") or {}).get("comp") or {}
    comp_target = {
        "min": target.get("min"),
        "target": target.get("target"),
        "max": target.get("max"),
        "currency": target.get("currency") or "USD",
    }
    pipeline = _rows_to_dicts(conn.execute(
        """
        SELECT j.id, j.company, j.title, j.location, j.remote,
               j.comp_min, j.comp_max, j.comp_currency,
               j.screen_verdict, j.screen_score, j.triage_verdict,
               j.url, j.jd_path, j.archived_at,
               a.id AS app_id, a.status AS app_status, a.score AS app_score
        FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        ORDER BY j.id DESC
        """
    ).fetchall())
    for row in pipeline:
        row["jd_file_url"] = _file_url(cfg.root, row.get("jd_path"))
    apps = _rows_to_dicts(conn.execute(
        """
        SELECT a.id, a.status, a.score, a.applied_at, a.next_action_at,
               a.report_path, a.resume_pdf_path, a.cover_pdf_path,
               a.referral_contact_id,
               c.name AS referral_name,
               j.company, j.title, j.url, j.jd_path
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        LEFT JOIN contacts c ON c.id = a.referral_contact_id
        ORDER BY CASE a.status
          WHEN 'Offer' THEN 0 WHEN 'Interview' THEN 1 WHEN 'Responded' THEN 2
          WHEN 'Applied' THEN 3 WHEN 'Evaluated' THEN 4 WHEN 'SKIP' THEN 5
          WHEN 'Discarded' THEN 6 WHEN 'Rejected' THEN 7 END,
          a.updated_at DESC
        """
    ).fetchall())
    for row in apps:
        artifacts = _scan_app_artifacts(cfg, row["id"], row["company"])
        row.update(artifacts)
        # Server-side computed file:// URLs win over the DB-relative ones for
        # report/resume/cover when both exist.
        row["jd_file_url"] = _file_url(cfg.root, row.get("jd_path"))
        if not row.get("report_file_url"):
            row["report_file_url"] = _file_url(cfg.root, row.get("report_path"))
        if not row.get("resume_pdf_url"):
            row["resume_pdf_url"] = _file_url(cfg.root, row.get("resume_pdf_path"))
        if not row.get("cover_pdf_url"):
            row["cover_pdf_url"] = _file_url(cfg.root, row.get("cover_pdf_path"))
    contacts = _rows_to_dicts(conn.execute(
        """
        SELECT c.id, c.name, c.company, c.title, c.linkedin_url, c.email,
               (SELECT COUNT(*) FROM touchpoints t WHERE t.contact_id = c.id) AS touches,
               (SELECT MAX(t.occurred_at) FROM touchpoints t WHERE t.contact_id = c.id) AS last_touch
        FROM contacts c ORDER BY c.first_seen_at DESC
        """
    ).fetchall())
    touchpoints = _rows_to_dicts(conn.execute(
        """
        SELECT t.occurred_at, t.channel, t.direction, t.summary,
               COALESCE(c.name, '-') AS contact,
               j.company, j.title, a.id AS app_id
        FROM touchpoints t
        LEFT JOIN applications a ON a.id = t.application_id
        LEFT JOIN jobs j ON j.id = a.job_id
        LEFT JOIN contacts c ON c.id = t.contact_id
        ORDER BY t.occurred_at DESC LIMIT 200
        """
    ).fetchall())
    followups = _rows_to_dicts(conn.execute(
        """
        SELECT a.id, j.company, j.title, a.status,
               COALESCE(a.next_action_at, date(a.applied_at, '+7 days')) AS due,
               (SELECT MAX(occurred_at) FROM touchpoints t WHERE t.application_id = a.id) AS last_touch
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.status IN ('Applied','Responded','Interview')
          AND (a.next_action_at IS NULL OR a.next_action_at <= date('now','+3 days'))
        ORDER BY due ASC
        """
    ).fetchall())
    costs = _rows_to_dicts(conn.execute(
        """
        SELECT date(occurred_at) AS day, operation, model,
               SUM(input_tokens)  AS in_tok,
               SUM(output_tokens) AS out_tok,
               SUM(cached_tokens) AS cache_tok,
               COUNT(*) AS calls
        FROM llm_usage
        GROUP BY day, operation, model
        ORDER BY day DESC, operation
        LIMIT 200
        """
    ).fetchall())
    status_counts = _rows_to_dicts(conn.execute(
        "SELECT status, COUNT(*) AS n FROM applications GROUP BY status"
    ).fetchall())
    screen_counts = _rows_to_dicts(conn.execute(
        """
        SELECT COALESCE(screen_verdict,'unscreened') AS v, COUNT(*) AS n
        FROM jobs GROUP BY v
        """
    ).fetchall())

    rounds = _rows_to_dicts(conn.execute(
        """
        SELECT r.id, r.application_id, r.round_number, r.kind, r.scheduled_at,
               r.status, r.outcome, r.interviewer_name, r.thank_you_sent_at,
               j.company, j.title
        FROM interview_rounds r
        JOIN applications a ON a.id = r.application_id
        JOIN jobs j ON j.id = a.job_id
        ORDER BY r.scheduled_at ASC NULLS LAST, r.id ASC
        """
    ).fetchall())

    return {
        "generated_at": date.today().isoformat(),
        "status_counts": status_counts,
        "screen_counts": screen_counts,
        "comp_target": comp_target,
        "pipeline": pipeline,
        "applications": apps,
        "contacts": contacts,
        "touchpoints": touchpoints,
        "followups": followups,
        "rounds": rounds,
        "costs": costs,
    }


def build_dashboard(open_browser: bool = True) -> Path:
    cfg = Config.load()
    cfg.ensure_dirs()
    conn = connect(cfg)
    migrate(conn)

    data = _collect(conn, cfg)
    template = _TEMPLATE_PATH.read_text()
    html = template.replace("/*__JR_DATA__*/{}", json.dumps(data, default=str))

    out = cfg.exports_dir / "dashboard.html"
    out.write_text(html)
    console.print(f"[green]dashboard[/green] → {out}")
    if open_browser:
        webbrowser.open(f"file://{out}")
    return out


def rebuild_silently() -> None:
    """Best-effort dashboard rebuild; swallows errors.

    Called from state-changing ingest points so the HTML view stays
    current without requiring a separate `jr dash` run.
    """
    try:
        build_dashboard(open_browser=False)
    except Exception as e:
        console.print(f"[dim]dash rebuild skipped: {e}[/dim]")
