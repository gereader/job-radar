"""Smoke test for `jr brief` markdown generation."""

from __future__ import annotations


def test_brief_writes_markdown(cfg, conn, monkeypatch):
    # Seed: one job + one applied app due for follow-up.
    conn.execute(
        "INSERT INTO jobs(hash, source, company, title, url, jd_path, "
        "screen_verdict, screen_score, fetched_at) "
        "VALUES ('h1','greenhouse','Acme','SRE','http://x','jd.md',"
        "'pass', 90, datetime('now'))"
    )
    conn.execute(
        "INSERT INTO applications(job_id, status, applied_at, next_action_at) "
        "VALUES (1, 'Applied', date('now','-14 day'), date('now','-1 day'))"
    )
    conn.commit()

    from job_radar.views.brief import run_brief
    out = run_brief(open_after=False)
    assert out.exists()
    body = out.read_text()
    assert "Brief — " in body
    assert "Follow-ups due today" in body
    assert "Acme" in body
