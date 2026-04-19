"""Backend selection + QueueLLM end-to-end."""

from __future__ import annotations

import json

import pytest

from job_radar.llm.client import QUEUE_MODEL_TAG, QueueLLM, log_queue_ingest
from job_radar.llm.dispatcher import build_llm, select_backend


def test_select_backend_explicit_force_wins(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert select_backend(force="queue") == "queue"
    assert select_backend(force="direct") == "direct"


def test_select_backend_env_var_overrides_default(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("JOB_RADAR_LLM_BACKEND", "direct")
    assert select_backend() == "direct"


def test_select_backend_defaults_to_queue_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("JOB_RADAR_LLM_BACKEND", raising=False)
    assert select_backend() == "queue"


def test_select_backend_picks_direct_with_key(monkeypatch):
    monkeypatch.delenv("JOB_RADAR_LLM_BACKEND", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert select_backend() == "direct"


def test_build_llm_returns_queue_when_forced(monkeypatch, conn, cfg):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    backend, llm = build_llm(conn, cfg, operation="echo")
    assert backend == "queue"
    assert isinstance(llm, QueueLLM)


def test_queue_llm_finalize_writes_packets(conn, cfg):
    q = QueueLLM(conn, operation="echo", private=cfg.private,
                 default_model="claude-haiku-4-5-20251001")
    q.enqueue(system="sys", user="u1", item_id=1, meta={"i": 1}, max_tokens=64)
    q.enqueue(system="sys", user="u2", item_id=2, meta={"i": 2}, max_tokens=64)
    qdir = q.finalize()
    assert (qdir / "manifest.json").exists()
    manifest = json.loads((qdir / "manifest.json").read_text())
    assert len(manifest["items"]) == 2
    assert manifest["model_hint"] == "claude-haiku-4-5-20251001"


def test_queue_llm_rejects_mismatched_system(conn, cfg):
    q = QueueLLM(conn, operation="echo", private=cfg.private)
    q.enqueue(system="sys-A", user="u1", item_id=1)
    with pytest.raises(ValueError, match="single shared system prompt"):
        q.enqueue(system="sys-B", user="u2", item_id=2)


def test_log_queue_ingest_records_usage(conn):
    log_queue_ingest(conn, operation="triage", item_count=3, job_id=None)
    row = conn.execute(
        "SELECT model, operation, input_tokens FROM llm_usage ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["model"] == QUEUE_MODEL_TAG
    assert row["operation"] == "triage"
    assert row["input_tokens"] == 3
