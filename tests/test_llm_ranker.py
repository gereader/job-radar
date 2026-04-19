"""Pure-function tests for the rank_and_slice helper."""

from __future__ import annotations

from job_radar.llm.ranker import DEFAULT_LIMIT, rank_and_slice, resolved_default


def _row(id_: int, score: float):
    return {"id": id_, "score": score}


def test_rank_and_slice_default_caps_at_default_limit():
    rows = [_row(i, float(i)) for i in range(50)]
    s = rank_and_slice(rows, key=lambda r: r["score"])
    assert len(s.picked) == DEFAULT_LIMIT
    assert s.remaining == 50 - DEFAULT_LIMIT
    assert s.total == 50
    # descending by score
    assert s.picked[0]["score"] == 49


def test_rank_and_slice_respects_limit():
    rows = [_row(i, float(i)) for i in range(20)]
    s = rank_and_slice(rows, key=lambda r: r["score"], limit=5)
    assert len(s.picked) == 5
    assert s.remaining == 15


def test_rank_and_slice_all_returns_everything_with_zero_remaining():
    rows = [_row(i, float(i)) for i in range(7)]
    s = rank_and_slice(rows, key=lambda r: r["score"], all_=True)
    assert len(s.picked) == 7
    assert s.remaining == 0


def test_rank_and_slice_ascending():
    rows = [_row(i, float(i)) for i in range(5)]
    s = rank_and_slice(rows, key=lambda r: r["score"], descending=False, limit=2)
    assert [r["id"] for r in s.picked] == [0, 1]


def test_hint_is_empty_when_nothing_remaining():
    rows = [_row(i, float(i)) for i in range(5)]
    s = rank_and_slice(rows, key=lambda r: r["score"], all_=True)
    assert s.hint(command="jr triage", current_limit=10) == ""


def test_hint_doubles_limit_with_cap():
    rows = [_row(i, float(i)) for i in range(50)]
    s = rank_and_slice(rows, key=lambda r: r["score"], limit=10)
    hint = s.hint(command="jr triage", current_limit=10)
    assert "--limit 20" in hint
    assert "--all" in hint


def test_resolved_default_reads_profile():
    assert resolved_default(None) == DEFAULT_LIMIT
    assert resolved_default({}) == DEFAULT_LIMIT
    assert resolved_default({"limits": {"default_llm_batch": 5}}) == 5
    assert resolved_default({"limits": {"default_llm_batch": "garbage"}}) == DEFAULT_LIMIT
