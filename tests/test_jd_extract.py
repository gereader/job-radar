from job_radar.parse.jd_extract import extract_all, extract_comp, extract_remote


def test_comp_range_k_shorthand():
    lo, hi, cur = extract_comp("The range is $160k - $220k per year.")
    assert (lo, hi, cur) == (160000, 220000, "USD")


def test_remote_detected():
    assert extract_remote("This is a fully remote role.") == "remote"
    assert extract_remote("Hybrid — 2 days in office.") == "hybrid"
    assert extract_remote("Must be on-site in NYC.") == "onsite"


def test_extract_all_populates_fields():
    md = (
        "**Location:** Remote, US\n\n"
        "We are hiring a Senior Engineer. Salary range $180,000 - $240,000.\n\n"
        "- 5+ years of Python\n- Strong CI/CD chops\n"
    )
    f = extract_all("Senior Engineer", md)
    assert f.title == "Senior Engineer"
    assert f.location == "Remote, US"
    assert f.remote == "remote"
    assert f.comp_min == 180000 and f.comp_max == 240000
    assert len(f.requirements) == 2
