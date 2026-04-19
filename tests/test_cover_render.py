"""Cover-letter rendering with cached answers + referrals."""

from __future__ import annotations

from job_radar.apply.cover import render_cover_template

_TEMPLATE = """{{date}}

{{greeting_target}},

{{hook_paragraph}}

{{fit_paragraph}}

{{close_paragraph}}

— {{full_name}}
"""


def test_default_placeholders_used_without_cache():
    out = render_cover_template(
        _TEMPLATE, {"identity": {"name": "G"}},
        company="Acme", role="SRE",
    )
    assert "I'm writing about the SRE role at Acme." in out
    assert "[One paragraph" in out


def test_cached_answers_lift_into_hook_and_fit():
    out = render_cover_template(
        _TEMPLATE, {"identity": {"name": "G"}},
        company="Acme", role="SRE",
        cached_answers={
            "why_company": "Acme's recent SRE incident retrospectives were a big draw.",
            "biggest_challenge": "I migrated 12k microservices off VLAN-locked DCs.",
        },
    )
    assert "Acme's recent SRE incident retrospectives" in out
    assert "12k microservices" in out
    assert "[One paragraph" not in out


def test_referral_name_prepends_to_hook():
    out = render_cover_template(
        _TEMPLATE, {"identity": {"name": "G"}},
        company="Acme", role="SRE",
        referral_name="Pat Smith",
    )
    assert "Pat Smith suggested" in out
