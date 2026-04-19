"""`jr brief` — morning report. Pure SQL, no LLM.

Sections:
  - Today's due follow-ups
  - Upcoming rounds in the next 3 days
  - New screen-passes since the last brief
  - Pattern flags (low-conversion archetypes, ghost companies)
  - LLM cost in the last 7 days

Writes ``private/exports/brief-{date}.md`` and prints a terse summary
to the console. The "since last brief" cutoff is the most recent
``brief-*.md`` file in exports/, or 24 hours ago if none.
"""

from __future__ import annotations

import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

from rich.console import Console

from ..config import Config
from ..db import connect, migrate

console = Console()


def _last_brief(cfg: Config) -> Path | None:
    candidates = sorted(cfg.exports_dir.glob("brief-*.md"), reverse=True)
    return candidates[0] if candidates else None


def _since_cutoff(cfg: Config) -> datetime:
    last = _last_brief(cfg)
    if last:
        try:
            stamp = last.stem.removeprefix("brief-")
            return datetime.fromisoformat(stamp)
        except ValueError:
            pass
    return datetime.now() - timedelta(days=1)


def run_brief(open_after: bool = False) -> Path:
    cfg = Config.load()
    cfg.ensure_dirs()
    conn = connect(cfg)
    migrate(conn)

    today = date.today()
    cutoff = _since_cutoff(cfg)

    due = conn.execute(
        """
        SELECT a.id, j.company, j.title, a.status,
               COALESCE(a.next_action_at, date(a.applied_at, '+7 days')) AS due
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.status IN ('Applied','Responded','Interview')
          AND (a.next_action_at IS NULL OR a.next_action_at <= date('now'))
        ORDER BY due ASC, a.applied_at ASC
        """
    ).fetchall()
    rounds = conn.execute(
        """
        SELECT r.id, r.round_number, r.kind, r.scheduled_at,
               r.interviewer_name, j.company, j.title
        FROM interview_rounds r
        JOIN applications a ON a.id = r.application_id
        JOIN jobs j ON j.id = a.job_id
        WHERE r.status = 'scheduled'
          AND r.scheduled_at IS NOT NULL
          AND date(r.scheduled_at) BETWEEN date('now') AND date('now', '+3 days')
        ORDER BY r.scheduled_at ASC
        """
    ).fetchall()
    new_passes = conn.execute(
        """
        SELECT j.id, j.company, j.title, j.url, j.fetched_at, j.screen_score,
               j.triage_verdict
        FROM jobs j
        WHERE (j.triage_verdict = 'pass' OR
               (j.triage_verdict IS NULL AND j.screen_verdict = 'pass'))
          AND j.fetched_at >= ?
          AND j.archived_at IS NULL
        ORDER BY j.fetched_at DESC
        LIMIT 50
        """,
        (cutoff.isoformat(timespec="seconds"),),
    ).fetchall()
    cost_summary = conn.execute(
        """
        SELECT operation, model, COUNT(*) AS calls,
               SUM(input_tokens) AS in_tok,
               SUM(output_tokens) AS out_tok
        FROM llm_usage
        WHERE occurred_at >= datetime('now', '-7 day')
        GROUP BY operation, model
        ORDER BY calls DESC
        """
    ).fetchall()
    ghost_companies = conn.execute(
        """
        SELECT j.company, COUNT(*) AS apps
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.applied_at >= date('now', '-60 day')
        GROUP BY j.company
        HAVING apps >= 3
           AND SUM(CASE WHEN a.status IN ('Responded','Interview','Offer','Rejected')
                         THEN 1 ELSE 0 END) = 0
        ORDER BY apps DESC LIMIT 10
        """
    ).fetchall()
    rejection_categories = conn.execute(
        """
        SELECT category, COUNT(*) AS n
        FROM rejection_reasons
        WHERE extracted_at >= date('now', '-30 day')
        GROUP BY category
        ORDER BY n DESC LIMIT 5
        """
    ).fetchall()

    md: list[str] = []
    md.append(f"# Brief — {today.isoformat()}")
    md.append(f"\n_Window: since {cutoff.date().isoformat()}_\n")

    md.append("\n## Follow-ups due today\n")
    if due:
        for r in due:
            md.append(f"- **app {r['id']}** {r['company']} / {r['title']} "
                      f"({r['status']}) — due {r['due'] or '—'}")
    else:
        md.append("_(none)_")

    md.append("\n## Upcoming rounds (next 3 days)\n")
    if rounds:
        for r in rounds:
            md.append(
                f"- {r['scheduled_at']} — **round {r['id']}** "
                f"r{r['round_number']} {r['kind']} @ {r['company']} ({r['title']}) "
                f"with {r['interviewer_name'] or 'TBD'}"
            )
    else:
        md.append("_(none)_")

    md.append(f"\n## New passes since last brief ({len(new_passes)})\n")
    if new_passes:
        for r in new_passes[:25]:
            md.append(
                f"- job {r['id']} **{r['company']}** / {r['title']} "
                f"(score {r['screen_score']}) → {r['url']}"
            )
        if len(new_passes) > 25:
            md.append(f"\n_(+{len(new_passes) - 25} more — see `jr triage`)_")
    else:
        md.append("_(none)_")

    if ghost_companies:
        md.append("\n## Ghost companies (≥3 apps in last 60d, zero response)\n")
        for r in ghost_companies:
            md.append(f"- **{r['company']}** — {r['apps']} apps, 0 response. "
                      f"Consider `jr portals ghost-cooldown {r['company']}`.")

    if rejection_categories:
        md.append("\n## Top rejection reasons (last 30d)\n")
        for r in rejection_categories:
            md.append(f"- {r['category']}: {r['n']}")

    md.append("\n## LLM cost (last 7d)\n")
    if cost_summary:
        md.append("| Operation | Model | Calls | In tok | Out tok |")
        md.append("|---|---|---|---|---|")
        for r in cost_summary:
            md.append(
                f"| {r['operation']} | {r['model']} | {r['calls']} | "
                f"{r['in_tok'] or 0} | {r['out_tok'] or 0} |"
            )
    else:
        md.append("_(no LLM activity)_")

    out = cfg.exports_dir / f"brief-{today.isoformat()}.md"
    out.write_text("\n".join(md) + "\n")

    console.print(f"[green]brief[/green] → {out}")
    console.print(
        f"due_today={len(due)} rounds_3d={len(rounds)} "
        f"new_passes={len(new_passes)} ghost_companies={len(ghost_companies)}"
    )
    if open_after:
        webbrowser.open(f"file://{out.resolve()}")
    return out
