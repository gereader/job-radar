import pytest


def test_migrate_creates_tables(conn):
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    for required in (
        "jobs", "applications", "contacts", "touchpoints", "scan_history",
        "keywords", "archetypes", "job_archetypes", "cv_skills", "story_bank",
        "comp_cache", "llm_usage",
    ):
        assert required in tables


def test_status_check_constraint(conn):
    # Insert a job first
    conn.execute(
        "INSERT INTO jobs(hash, source, company, title, url, jd_path) "
        "VALUES ('h1', 'manual', 'Acme', 'SRE', 'http://x', 'x.md')"
    )
    conn.execute("INSERT INTO applications(job_id, status) VALUES (1, 'Applied')")
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO applications(job_id, status) VALUES (1, 'Bogus')"
        )
