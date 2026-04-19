import pytest


def test_migrate_creates_tables(conn):
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    for required in (
        "jobs", "applications", "contacts", "touchpoints", "scan_history",
        "keywords", "archetypes", "job_archetypes", "cv_skills", "story_bank",
        "comp_cache", "llm_usage",
        # Block 2/4 additions:
        "rejection_reasons", "app_answers", "round_questions",
    ):
        assert required in tables


def test_referral_column_exists(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(applications)")}
    assert "referral_contact_id" in cols


def test_rejection_reasons_check_constraint(conn):
    import pytest
    conn.execute(
        "INSERT INTO jobs(hash, source, company, title, url, jd_path) "
        "VALUES ('rh', 'manual', 'X', 'Y', 'u', 'p.md')"
    )
    conn.execute("INSERT INTO applications(job_id, status) VALUES (1, 'Rejected')")
    conn.execute(
        "INSERT INTO rejection_reasons(application_id, category, detail) "
        "VALUES (1, 'comp', 'too low')"
    )
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO rejection_reasons(application_id, category) "
            "VALUES (1, 'bogus')"
        )


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
