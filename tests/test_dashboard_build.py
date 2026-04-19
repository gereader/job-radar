def test_dash_renders_with_empty_db(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: None)
    from job_radar.dash.build import build_dashboard

    out = build_dashboard(open_browser=False)
    html = out.read_text()
    assert "job-radar" in html
    assert "/*__JR_DATA__*/" not in html  # placeholder was substituted


def test_dash_reflects_inserted_rows(conn, cfg, monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: None)
    conn.execute(
        "INSERT INTO jobs(hash, source, company, title, url, jd_path, screen_verdict) "
        "VALUES ('h','manual','Acme','SRE','u','p','pass')"
    )
    conn.execute("INSERT INTO applications(job_id, status) VALUES (1, 'Applied')")
    conn.commit()

    from job_radar.dash.build import build_dashboard

    out = build_dashboard(open_browser=False)
    html = out.read_text()
    assert "Acme" in html
    assert "Applied" in html
