"""Haiku triage pass on the pre-screen 'review' bucket.

Two backends, one entry point:

* Direct API (``ANTHROPIC_API_KEY`` set) — fires a Haiku request per row,
  writes the verdict back inline, same as before.
* Queue (Max plan) — ``--prepare`` writes one packet per row to
  ``private/llm-queue/triage-{ts}/`` and exits. Claude Code runs
  ``/jr consume`` to fill in ``result-*.json`` files. ``--ingest <dir>``
  then folds verdicts back into ``jobs.triage_verdict``.

Pre-ranking is mandatory: a candidate's value score is
``screen_score * 1.0 + positive_keyword_count * 5 - age_days * 0.2`` and we
always slice the top N (default 10) before either backend touches anything.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from .client import DirectLLM, QueueLLM, log_queue_ingest
from .dispatcher import build_llm
from .ranker import print_rank_debug, rank_and_slice, resolved_default

console = Console()

_SYSTEM_TEMPLATE = (Path(__file__).parent.parent.parent / "modes" / "_shared.md")
_TRIAGE_TEMPLATE = (Path(__file__).parent.parent.parent / "modes" / "triage.md")

TRIAGE_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["verdict"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "review", "skip"]},
        "score_0_5": {"type": "number"},
        "rationale": {"type": "string"},
        "archetype": {"type": "string"},
    },
}


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


def _value_score(row: Any) -> float:
    """``screen_score * 1.0 + positives * 5 - age_days * 0.2``."""
    score = float(row["screen_score"] or 0)
    try:
        reasons = json.loads(row["screen_reasons"] or "[]")
    except (json.JSONDecodeError, TypeError):
        reasons = []
    positives = sum(1 for x in reasons if isinstance(x, str) and x.startswith("+"))
    fetched = row["fetched_at"]
    age_days = 0.0
    if fetched:
        try:
            t = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
            age_days = max(0.0, (datetime.now(t.tzinfo) - t).total_seconds() / 86400.0)
        except (TypeError, ValueError):
            age_days = 0.0
    return score + positives * 5 - age_days * 0.2


_WORD_RE = re.compile(r"[a-z0-9]+")


def _jaccard(a: str, b: str) -> float:
    ta = set(_WORD_RE.findall(a.lower()))
    tb = set(_WORD_RE.findall(b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _pre_skip_already_seen(conn, cfg: Config) -> int:
    """Skip jobs whose body closely matches an application-linked twin.

    Candidate twins share (company, normalized_title). Confirmation path:
      - If both the candidate and twin have JD files on disk, require a
        Jaccard similarity above the configured threshold (defaults 0.80).
      - If the twin has no JD body (e.g. career-ops-imported apps), fall
        back to the title-match alone.
    """
    threshold = float(
        (cfg.profile.get("scoring") or {}).get("dup_jaccard_threshold", 0.80)
    )
    candidates = conn.execute(
        """
        SELECT j.id AS new_id, j.jd_path AS new_jd,
               j2.id AS old_id, j2.jd_path AS old_jd
        FROM jobs j
        JOIN applications a ON a.job_id != j.id
        JOIN jobs j2 ON j2.id = a.job_id
        WHERE j.triage_verdict IS NULL
          AND j.screen_verdict IN ('review','pass')
          AND LOWER(TRIM(j2.company)) = LOWER(TRIM(j.company))
          AND LOWER(TRIM(j2.title)) = LOWER(TRIM(j.title))
        """
    ).fetchall()

    # A new row may match multiple old twins; the first hit wins.
    decided: dict[int, str] = {}
    for c in candidates:
        nid = c["new_id"]
        if nid in decided:
            continue
        new_path = cfg.root / c["new_jd"] if c["new_jd"] else None
        old_path = cfg.root / c["old_jd"] if c["old_jd"] else None
        new_body = new_path.read_text() if new_path and new_path.exists() else ""
        old_body = old_path.read_text() if old_path and old_path.exists() else ""
        if new_body and old_body:
            sim = _jaccard(new_body, old_body)
            if sim >= threshold:
                decided[nid] = f"already_seen_jaccard_{sim:.2f}"
        elif not old_body:
            decided[nid] = "already_seen_title_only"

    for nid, reason in decided.items():
        note = json.dumps({"source": "auto-advance", "reason": reason})
        with tx(conn):
            conn.execute(
                "UPDATE jobs SET triage_verdict=?, triage_notes=? WHERE id=?",
                ("skip", note, nid),
            )
    return len(decided)


# Tokens that confirm a role is US-open. Split into two patterns so the
# 2-letter state codes don't match common English words ("in", "or", "co"):
#   - phrases/cities: matched case-insensitively
#   - state abbrevs: matched case-sensitively and must follow ", "
_US_PHRASE_RE = re.compile(
    r"\b(united states|usa|u\.s\.a?\.?|"
    r"us[-\s](?:remote|only|based)|remote[-\s](?:us|in the us|anywhere in the us)|"
    r"new york|nyc|san francisco|seattle|bellevue|austin|"
    r"boston|chicago|los angeles|denver|portland|philadelphia|"
    r"washington, ?d\.?c\.?|sunnyvale|livingston|dallas|atlanta|miami)\b",
    re.I,
)
_US_STATE_RE = re.compile(
    r",\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|"
    r"MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|"
    r"UT|VT|VA|WA|WV|WI|WY)\b"
)


def _has_us_marker(text: str) -> bool:
    if not text:
        return False
    return bool(_US_PHRASE_RE.search(text) or _US_STATE_RE.search(text))


def _pre_skip_non_us_geo(conn, cfg: Config) -> int:
    """Auto-skip roles with a non-US-only location.

    A role is treated as US-open when any US marker (state abbrev, major
    city, 'United States'/'USA', 'US remote'/'remote US', or a generic
    'remote' token) appears either in the ``location`` field or in the
    first few KB of the JD body. Only rows with *zero* US markers in
    both places get auto-skipped.
    """
    rows = conn.execute(
        """
        SELECT id, location, jd_path FROM jobs
        WHERE triage_verdict IS NULL
          AND screen_verdict IN ('review','pass')
        """
    ).fetchall()
    skipped = 0
    for r in rows:
        loc = (r["location"] or "").strip()
        if not loc:
            # Empty location — leave for the LLM to judge.
            continue
        jd_body = ""
        if r["jd_path"]:
            p = cfg.root / r["jd_path"]
            if p.exists():
                jd_body = p.read_text()[:4000]
        if _has_us_marker(loc) or _has_us_marker(jd_body):
            continue
        note = json.dumps(
            {"source": "auto-advance", "reason": "non_us_geo", "location": loc}
        )
        with tx(conn):
            conn.execute(
                "UPDATE jobs SET triage_verdict=?, triage_notes=? WHERE id=?",
                ("skip", note, r["id"]),
            )
        skipped += 1
    return skipped


def _auto_advance(conn, cfg: Config) -> tuple[int, int]:
    """Cheap heuristic before Haiku spend: very-good and very-bad rows.

    Also pre-skips:
      - jobs whose body closely matches an already-applied twin
        (see ``_pre_skip_already_seen``)
      - jobs whose location is non-US-only with no US remote signal
        (see ``_pre_skip_non_us_geo``)
    """
    skipped_already = _pre_skip_already_seen(conn, cfg)
    skipped_geo = _pre_skip_non_us_geo(conn, cfg)

    auto = conn.execute(
        """
        SELECT id, screen_score, screen_reasons
        FROM jobs
        WHERE screen_verdict = 'review' AND triage_verdict IS NULL
        """
    ).fetchall()
    skipped_auto = passed_auto = 0
    skipped_auto += skipped_already + skipped_geo
    for r in auto:
        try:
            reasons = json.loads(r["screen_reasons"] or "[]")
        except json.JSONDecodeError:
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
    return passed_auto, skipped_auto


def _user_prompt(cfg: Config, row: Any) -> str:
    jd_path = cfg.root / row["jd_path"]
    jd_md = jd_path.read_text() if jd_path.exists() else ""
    return (
        f"Company: {row['company']}\nTitle: {row['title']}\n"
        f"Pre-screen score: {row['screen_score']}\n"
        f"Pre-screen reasons: {row['screen_reasons']}\n\n"
        f"---\n\n{jd_md[:8000]}"
    )


def _apply_verdict(conn, cfg: Config, job_id: int, parsed: dict[str, Any], jd_rel: str) -> None:
    with tx(conn):
        conn.execute(
            "UPDATE jobs SET triage_verdict = ?, triage_notes = ? WHERE id = ?",
            (parsed.get("verdict", "review"), json.dumps(parsed), job_id),
        )
    from .autohooks import maybe_research_after_triage
    try:
        maybe_research_after_triage(conn, cfg, job_id, parsed)
    except Exception as e:
        console.print(f"[dim]auto-research skipped: {e}[/dim]")

    prune_at = float(
        (cfg.profile.get("scoring") or {}).get("auto_prune_below", 0) or 0
    )
    try:
        s05 = float(parsed.get("score_0_5", 0) or 0)
    except (TypeError, ValueError):
        s05 = 0.0
    if prune_at and s05 and s05 <= prune_at:
        # Safety: never prune a job whose application is in the active
        # funnel — Applied/Responded/Interview/Offer should never have
        # their JD file deleted regardless of triage score.
        active = conn.execute(
            "SELECT 1 FROM applications WHERE job_id = ? AND status IN "
            "('Applied','Responded','Interview','Offer') LIMIT 1",
            (job_id,),
        ).fetchone()
        if active:
            return
        jd_full = cfg.root / jd_rel
        try:
            if jd_full.exists():
                jd_full.unlink()
        except OSError:
            pass
        with tx(conn):
            conn.execute(
                "UPDATE jobs SET archived_at = datetime('now') WHERE id = ?",
                (job_id,),
            )


def run_triage(
    *,
    limit: int = 0,
    all_: bool = False,
    rank_debug: bool = False,
    force_prepare: bool = False,
) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    passed_auto, skipped_auto = _auto_advance(conn, cfg)
    if passed_auto or skipped_auto:
        console.print(
            f"[cyan]auto-advanced[/cyan] pass={passed_auto} skip={skipped_auto} "
            "(no LLM spent)"
        )

    rows = conn.execute(
        """
        SELECT id, company, title, jd_path, fetched_at, screen_score, screen_reasons
        FROM jobs
        WHERE screen_verdict = 'review' AND triage_verdict IS NULL
        ORDER BY id ASC
        """
    ).fetchall()
    if not rows:
        console.print("no ambiguous jobs — nothing for Haiku to decide.")
        return

    default_n = resolved_default(cfg.profile)
    requested = limit if limit > 0 else default_n
    sliced = rank_and_slice(rows, key=_value_score, limit=requested, all_=all_)

    if rank_debug:
        print_rank_debug(
            list(rows),
            key=_value_score,
            columns=[
                ("id", lambda r: r["id"]),
                ("company", lambda r: r["company"]),
                ("title", lambda r: r["title"]),
                ("screen", lambda r: r["screen_score"]),
            ],
            title=f"Triage rank ({len(rows)} candidates)",
            console=console,
        )
        return

    picked = sliced.picked
    hint = sliced.hint(command="jr triage", current_limit=requested)

    model = (cfg.profile.get("llm") or {}).get("triage_model", "claude-haiku-4-5-20251001")
    backend, llm = build_llm(
        conn,
        cfg,
        operation="triage",
        default_model=model,
        result_schema=TRIAGE_RESULT_SCHEMA,
        force=("queue" if force_prepare else None),
    )
    system = _build_system(cfg)

    if backend == "queue":
        assert isinstance(llm, QueueLLM)
        for r in picked:
            llm.enqueue(
                system=system,
                user=_user_prompt(cfg, r),
                item_id=r["id"],
                meta={"job_id": r["id"], "company": r["company"], "title": r["title"],
                      "jd_path": r["jd_path"]},
                max_tokens=512,
            )
        qdir = llm.finalize()
        console.print(f"[green]queued[/green] {len(picked)} packets → {qdir}")
        if hint:
            console.print(hint)
        console.print(
            "Next: ask Claude Code to run [bold]/jr consume "
            f"{qdir}[/bold], then [bold]jr triage --ingest {qdir}[/bold]."
        )
        return

    assert isinstance(llm, DirectLLM)
    table = Table(title=f"Triaging {len(picked)} of {len(rows)} jobs")
    table.add_column("#", justify="right")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Verdict")
    table.add_column("Score")

    for r in picked:
        resp = llm.complete(
            system=system,
            user=_user_prompt(cfg, r),
            operation="triage",
            job_id=r["id"],
            max_tokens=512,
        )
        try:
            parsed = json.loads(resp.text.strip().strip("`"))
        except json.JSONDecodeError:
            parsed = {"verdict": "review", "notes": resp.text[:200]}
        _apply_verdict(conn, cfg, r["id"], parsed, r["jd_path"])
        table.add_row(
            str(r["id"]), r["company"], r["title"],
            parsed.get("verdict", "?"), str(parsed.get("score_0_5", "-")),
        )

    console.print(table)
    if hint:
        console.print(hint)


def ingest_triage(queue_dir: Path) -> None:
    """Fold result-*.json files from a queue dir into the DB."""
    from .queue import ingest as q_ingest

    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    results = q_ingest(queue_dir)
    table = Table(title=f"Ingested {len(results)} triage results")
    table.add_column("job_id", justify="right")
    table.add_column("company")
    table.add_column("verdict")
    table.add_column("score")

    for r in results:
        meta = r.meta or {}
        job_id = int(meta.get("job_id") or r.id)
        parsed = r.result if isinstance(r.result, dict) else {"verdict": "review",
                                                              "notes": str(r.result)[:200]}
        jd_rel = meta.get("jd_path") or ""
        _apply_verdict(conn, cfg, job_id, parsed, jd_rel)
        table.add_row(
            str(job_id), str(meta.get("company", "?")),
            str(parsed.get("verdict", "?")), str(parsed.get("score_0_5", "-")),
        )
        log_queue_ingest(conn, operation="triage", item_count=1, job_id=job_id)

    console.print(table)
    console.print(f"[green]ingest complete[/green] — {queue_dir}")
    from ..dash.build import rebuild_silently
    rebuild_silently()
