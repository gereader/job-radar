"""Haiku triage pass on the pre-screen 'review' bucket."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from .client import LLM

console = Console()

_SYSTEM_TEMPLATE = (Path(__file__).parent.parent.parent / "modes" / "_shared.md")
_TRIAGE_TEMPLATE = (Path(__file__).parent.parent.parent / "modes" / "triage.md")


def _build_system(cfg: Config) -> str:
    shared = _SYSTEM_TEMPLATE.read_text()
    triage = _TRIAGE_TEMPLATE.read_text()
    profile = cfg.profile or {}
    targets = profile.get("targets", {})
    archetypes = targets.get("archetypes", [])
    dealbreakers = targets.get("dealbreakers", [])
    return (
        f"{shared}\n\n---\n\n{triage}\n\n---\n\n"
        f"## Profile\n"
        f"archetypes: {json.dumps(archetypes)}\n"
        f"dealbreakers: {json.dumps(dealbreakers)}\n"
        f"comp target: {json.dumps(targets.get('comp', {}))}\n"
        f"location policy: {targets.get('location_policy', 'remote')}\n"
    )


def run_triage(limit: int = 0) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    llm = LLM(conn, default_model=model)
    system = _build_system(cfg)

    # Tier-0: auto-advance the obvious ones without spending on Haiku.
    #   - score >= 90 AND at least 3 positive-keyword hits → triage=pass
    #   - score <= 20 OR a dealbreaker reason was recorded → triage=skip
    auto = conn.execute(
        """
        SELECT id, screen_score, screen_reasons
        FROM jobs
        WHERE screen_verdict = 'review' AND triage_verdict IS NULL
        """
    ).fetchall()
    import json as _json
    skipped_auto = 0
    passed_auto = 0
    for r in auto:
        try:
            reasons = _json.loads(r["screen_reasons"] or "[]")
        except _json.JSONDecodeError:
            reasons = []
        positives = sum(1 for x in reasons if isinstance(x, str) and x.startswith("+"))
        has_dealbreaker = any(
            isinstance(x, str) and x.startswith("dealbreaker") for x in reasons
        )
        verdict = None
        if has_dealbreaker or (r["screen_score"] is not None and r["screen_score"] <= 20):
            verdict = "skip"
            skipped_auto += 1
        elif r["screen_score"] is not None and r["screen_score"] >= 90 and positives >= 3:
            verdict = "pass"
            passed_auto += 1
        if verdict:
            with tx(conn):
                conn.execute(
                    "UPDATE jobs SET triage_verdict = ?, triage_notes = ? WHERE id = ?",
                    (verdict, '{"source":"auto-advance"}', r["id"]),
                )
    if skipped_auto or passed_auto:
        console.print(
            f"[cyan]auto-advanced[/cyan] pass={passed_auto} skip={skipped_auto} "
            "(no LLM spent)"
        )

    rows = conn.execute(
        """
        SELECT id, company, title, jd_path, screen_score, screen_reasons
        FROM jobs
        WHERE screen_verdict = 'review' AND triage_verdict IS NULL
        ORDER BY id ASC
        """
    ).fetchall()
    if limit:
        rows = rows[:limit]
    if not rows:
        console.print("no ambiguous jobs — nothing for Haiku to decide.")
        return

    table = Table(title=f"Triaging {len(rows)} jobs")
    table.add_column("#", justify="right")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Verdict")
    table.add_column("Score")

    for r in rows:
        jd_path = cfg.root / r["jd_path"]
        jd_md = jd_path.read_text() if jd_path.exists() else ""
        user_prompt = (
            f"Company: {r['company']}\nTitle: {r['title']}\n"
            f"Pre-screen score: {r['screen_score']}\n"
            f"Pre-screen reasons: {r['screen_reasons']}\n\n"
            f"---\n\n{jd_md[:8000]}"  # cap to keep tokens tight
        )
        resp = llm.complete(
            system=system,
            user=user_prompt,
            operation="triage",
            job_id=r["id"],
            max_tokens=512,
        )
        try:
            parsed = json.loads(resp.text.strip().strip("`"))
        except json.JSONDecodeError:
            parsed = {"verdict": "review", "notes": resp.text[:200]}

        with tx(conn):
            conn.execute(
                """
                UPDATE jobs
                SET triage_verdict = ?, triage_notes = ?
                WHERE id = ?
                """,
                (parsed.get("verdict", "review"), json.dumps(parsed), r["id"]),
            )
        table.add_row(
            str(r["id"]), r["company"], r["title"],
            parsed.get("verdict", "?"), str(parsed.get("score_0_5", "-")),
        )

        from .autohooks import maybe_research_after_triage
        try:
            maybe_research_after_triage(conn, cfg, r["id"], parsed)
        except Exception as e:
            console.print(f"[dim]auto-research skipped: {e}[/dim]")

        # Auto-prune under-threshold jobs: delete JD file, archive row, keep
        # hash so re-scan skips the exact same post in the future.
        prune_at = float(
            (cfg.profile.get("scoring") or {}).get("auto_prune_below", 0) or 0
        )
        try:
            s05 = float(parsed.get("score_0_5", 0) or 0)
        except (TypeError, ValueError):
            s05 = 0.0
        if prune_at and s05 and s05 <= prune_at:
            jd_full = cfg.root / (jd_path if isinstance(jd_path, str) else str(jd_path))
            try:
                if jd_full.exists():
                    jd_full.unlink()
            except OSError:
                pass
            with tx(conn):
                conn.execute(
                    "UPDATE jobs SET archived_at = datetime('now') WHERE id = ?",
                    (r["id"],),
                )
    console.print(table)
