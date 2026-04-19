"""Round-trip tests for the queue/ingest plane."""

from __future__ import annotations

import json

import pytest

from job_radar.llm.queue import (
    QueueItem,
    ingest,
    is_consumed,
    latest_queue,
    list_queues,
    load_manifest,
    pending,
    prepare,
)


def _items(n: int) -> list[QueueItem]:
    return [
        QueueItem(id=str(i), user_prompt=f"prompt {i}", meta={"i": i}) for i in range(n)
    ]


def test_prepare_writes_manifest_and_packets(cfg):
    qdir = prepare(
        operation="echo",
        system="echo back the input",
        items=_items(3),
        private=cfg.private,
        model_hint="claude-haiku-4-5-20251001",
        max_tokens=128,
        result_schema={"type": "object"},
    )
    assert qdir.exists()
    manifest = load_manifest(qdir)
    assert manifest["operation"] == "echo"
    assert manifest["model_hint"] == "claude-haiku-4-5-20251001"
    assert len(manifest["items"]) == 3
    for item in manifest["items"]:
        assert (qdir / item["packet"]).exists()
        assert (qdir / "system.md").read_text() == "echo back the input"


def test_prepare_rejects_empty_items(cfg):
    with pytest.raises(ValueError):
        prepare(operation="x", system="s", items=[], private=cfg.private)


def test_prepare_rejects_duplicate_ids(cfg):
    with pytest.raises(ValueError):
        prepare(
            operation="x",
            system="s",
            items=[QueueItem(id="dup", user_prompt="a"), QueueItem(id="dup", user_prompt="b")],
            private=cfg.private,
        )


def test_pending_reflects_missing_results(cfg):
    qdir = prepare(operation="echo", system="s", items=_items(2), private=cfg.private)
    assert len(pending(qdir)) == 2
    (qdir / "result-0.json").write_text('{"echo": "0"}')
    assert len(pending(qdir)) == 1


def test_ingest_round_trip(cfg):
    qdir = prepare(operation="echo", system="s", items=_items(2), private=cfg.private)
    (qdir / "result-0.json").write_text('{"echo": "zero"}')
    (qdir / "result-1.json").write_text('```json\n{"echo": "one"}\n```')
    results = ingest(qdir)
    assert [r.id for r in results] == ["0", "1"]
    assert results[0].result == {"echo": "zero"}
    assert results[1].result == {"echo": "one"}
    assert is_consumed(qdir)


def test_ingest_raises_on_missing(cfg):
    qdir = prepare(operation="echo", system="s", items=_items(2), private=cfg.private)
    (qdir / "result-0.json").write_text('{"echo": "0"}')
    with pytest.raises(ValueError, match="result file"):
        ingest(qdir)


def test_ingest_raises_on_bad_json(cfg):
    qdir = prepare(operation="echo", system="s", items=_items(1), private=cfg.private)
    (qdir / "result-0.json").write_text("not json at all")
    with pytest.raises(ValueError, match="not valid JSON"):
        ingest(qdir)


def test_list_and_latest(cfg):
    q1 = prepare(operation="triage", system="s", items=_items(1), private=cfg.private)
    # bump timestamp to ensure ordering
    import time
    time.sleep(1.01)
    q2 = prepare(operation="triage", system="s", items=_items(1), private=cfg.private)
    queues = list_queues(cfg.private)
    assert q1 in queues and q2 in queues
    # newest first
    assert queues[0] == q2
    # latest_queue returns the newest unconsumed; consume q2 first
    (q2 / "result-0.json").write_text('{"verdict":"pass"}')
    ingest(q2)
    assert latest_queue(cfg.private, operation="triage") == q1
