from job_radar.learn.patterns import _segment


def test_segment_groups_and_computes_conversion(conn):
    conn.executemany(
        "INSERT INTO jobs(hash, source, company, title, url, jd_path, remote) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            (f"h{i}", "manual", "Acme", f"Role {i}", "u", "p", "remote")
            for i in range(4)
        ] + [
            (f"h{i+10}", "manual", "Acme", f"Role {i}", "u", "p", "onsite")
            for i in range(3)
        ],
    )
    jobs = list(conn.execute("SELECT id FROM jobs"))
    for job, status in zip(jobs, [
        "Applied", "Interview", "Rejected", "SKIP",
        "Rejected", "Rejected", "SKIP",
    ]):
        conn.execute(
            "INSERT INTO applications(job_id, status) VALUES (?, ?)",
            (job["id"], status),
        )
    conn.commit()

    rows = _segment(conn, "j.remote", "remote")
    by = {r["bucket"]: r for r in rows}
    assert by["remote"]["total"] == 4
    assert by["remote"]["pos"] == 2
    assert by["onsite"]["pos"] == 0
    assert by["remote"]["conversion"] == 50.0
