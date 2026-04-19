from job_radar.screen.keywords import Rule, Ruleset, screen


def _rs():
    return Ruleset(
        positive=[
            Rule(term="python", weight=8, field="any"),
            Rule(term="senior", weight=4, field="title"),
        ],
        negative=[Rule(term="intern", weight=20, field="title")],
        dealbreaker=[Rule(term="security clearance", field="description")],
    )


def test_pass_verdict():
    r = screen("Senior Python Engineer", "Build with Python", None, _rs(), pass_at=10, review_at=4)
    assert r.verdict == "pass"
    assert r.score >= 10


def test_dealbreaker_short_circuits():
    r = screen("Python Engineer", "Must hold an active security clearance.", None, _rs())
    assert r.verdict == "skip"
    assert any("dealbreaker" in x for x in r.reasons)


def test_negative_tanks_score():
    r = screen("Intern Python Engineer", "", None, _rs())
    assert r.verdict == "skip"


def test_transparency_state_flag_fires_when_no_comp_signal():
    r = screen(
        "Senior Engineer", "We use Kubernetes and Go. Great culture.",
        "Seattle, WA", _rs(),
        transparency_states=["WA"],
    )
    assert any("transparency" in reason for reason in r.reasons)


def test_transparency_state_flag_silent_when_comp_present():
    r = screen(
        "Senior Engineer", "Comp range: $180k-$220k.",
        "Seattle, WA", _rs(),
        transparency_states=["WA"],
    )
    assert not any("transparency" in reason for reason in r.reasons)


def test_transparency_state_flag_skipped_outside_state():
    r = screen(
        "Senior Engineer", "We're great.",
        "Austin, TX", _rs(),
        transparency_states=["WA", "CA"],
    )
    assert not any("transparency" in reason for reason in r.reasons)
