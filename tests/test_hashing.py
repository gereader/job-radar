from job_radar.util.hashing import content_hash, url_hash


def test_content_hash_stable_across_whitespace_and_case():
    a = content_hash("Acme", "Senior SRE", "We are hiring.")
    b = content_hash("ACME  ", "senior  sre", "we are hiring.")
    assert a == b


def test_content_hash_changes_with_title():
    a = content_hash("Acme", "Senior SRE", "x")
    b = content_hash("Acme", "Staff SRE", "x")
    assert a != b


def test_url_hash_is_stable():
    assert url_hash("https://boards.greenhouse.io/acme/jobs/1") == url_hash(
        " https://boards.greenhouse.io/acme/jobs/1 "
    )
