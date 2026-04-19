"""Pure-helper coverage for `jr add <url>`."""

from __future__ import annotations

from job_radar.scan.add_url import (
    _guess_company,
    _guess_source,
    _guess_source_id,
    _guess_title,
)


def test_guess_source_known_hosts():
    assert _guess_source("boards.greenhouse.io") == "greenhouse"
    assert _guess_source("jobs.ashbyhq.com") == "ashby"
    assert _guess_source("jobs.lever.co") == "lever"
    assert _guess_source("apply.workable.com") == "workable"
    assert _guess_source("www.linkedin.com") == "linkedin"
    assert _guess_source("careers.example.com") == "manual"


def test_guess_source_id_numeric():
    assert _guess_source_id("https://boards.greenhouse.io/anthropic/jobs/4567890") == "4567890"
    assert _guess_source_id("https://jobs.lever.co/foo/abc") is None


def test_guess_source_id_uuid():
    url = "https://jobs.ashbyhq.com/foo/aabbccdd-1122-3344-5566-77889900aabb"
    assert _guess_source_id(url) == "aabbccdd-1122-3344-5566-77889900aabb"


def test_guess_title_prefers_h1():
    html = "<html><title>X | Y</title><h1>Senior Network Eng</h1></html>"
    assert _guess_title(html, "fallback") == "Senior Network Eng"


def test_guess_title_falls_back_to_title_tag():
    html = "<html><title>Senior Eng - Acme Corp</title></html>"
    assert _guess_title(html, "fallback") == "Senior Eng"


def test_guess_company_from_at_pattern():
    html = "<title>Senior Network Eng at Anthropic | Greenhouse</title>"
    assert _guess_company("boards.greenhouse.io", html, "fallback") == "Anthropic"


def test_guess_company_falls_back_to_dash_split():
    html = "<title>Anthropic - Senior Eng</title>"
    assert _guess_company("boards.greenhouse.io", html, "fallback") == "Anthropic"


def test_guess_company_uses_greenhouse_slug():
    assert _guess_company(
        "boards.greenhouse.io/anthropic", "", "fallback"
    ) == "Anthropic"
