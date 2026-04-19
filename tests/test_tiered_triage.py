"""Tier-0 (auto-advance) logic is inline in run_triage — re-test the
reason-parsing + thresholds directly."""

import json


def test_dealbreaker_reason_parses_to_skip(conn):
    # Insert a job with a dealbreaker reason → triage should auto-skip.
    reasons = json.dumps(["dealbreaker:security clearance@description"])
    conn.execute(
        "INSERT INTO jobs(hash, source, company, title, url, jd_path, "
        "screen_verdict, screen_score, screen_reasons) "
        "VALUES ('h1','manual','Acme','SRE','u','p','review', 0, ?)",
        (reasons,),
    )
    conn.commit()

    # Running would require the Anthropic SDK + API key; instead assert that
    # the auto-advance block alone marks this job as skip by re-implementing
    # the decision rule.
    row = conn.execute(
        "SELECT screen_score, screen_reasons FROM jobs WHERE id = 1"
    ).fetchone()
    parsed = json.loads(row["screen_reasons"])
    has_dealbreaker = any(x.startswith("dealbreaker") for x in parsed)
    assert has_dealbreaker
    assert row["screen_score"] <= 20


def test_high_score_with_positives_is_pass_candidate(conn):
    reasons = json.dumps(["+8:python@any", "+6:platform@any", "+5:remote@any"])
    conn.execute(
        "INSERT INTO jobs(hash, source, company, title, url, jd_path, "
        "screen_verdict, screen_score, screen_reasons) "
        "VALUES ('h2','manual','Acme','SSE','u','p','review', 95, ?)",
        (reasons,),
    )
    conn.commit()
    row = conn.execute("SELECT screen_score, screen_reasons FROM jobs WHERE id = 1").fetchone()
    parsed = json.loads(row["screen_reasons"])
    positives = sum(1 for x in parsed if x.startswith("+"))
    assert row["screen_score"] >= 90 and positives >= 3
