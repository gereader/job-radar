"""LLM clients: DirectLLM (Anthropic API) and QueueLLM (Claude Code Max plan).

Both expose the same surface so call sites don't branch on backend.
``DirectLLM.complete`` runs synchronously and logs to ``llm_usage`` like
before. ``QueueLLM.complete`` enqueues a packet and returns a placeholder
``LLMResponse``; the caller must later run ``--ingest`` to fold the real
result back into the database.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None  # type: ignore

from .queue import QueueItem, prepare

QUEUE_MODEL_TAG = "claude-code/max"


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    model: str
    queued: bool = False
    queue_dir: Path | None = None
    queue_item_id: str | None = None


class DirectLLM:
    """Anthropic SDK wrapper. Logs every call to ``llm_usage``."""

    def __init__(self, conn: sqlite3.Connection, default_model: str | None = None):
        if Anthropic is None:
            raise RuntimeError("anthropic SDK not installed. `pip install anthropic`.")
        self.client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.conn = conn
        self.default_model = default_model or "claude-haiku-4-5-20251001"

    def complete(
        self,
        *,
        system: str | list[dict[str, Any]],
        user: str,
        model: str | None = None,
        max_tokens: int = 1024,
        operation: str = "adhoc",
        job_id: int | None = None,
        app_id: int | None = None,
        cache_system: bool = True,
    ) -> LLMResponse:
        m = model or self.default_model
        if isinstance(system, str) and cache_system:
            sys_blocks = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        else:
            sys_blocks = system if isinstance(system, list) else [{"type": "text", "text": system}]

        resp = self.client.messages.create(
            model=m,
            max_tokens=max_tokens,
            system=sys_blocks,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        usage = resp.usage
        cached = getattr(usage, "cache_read_input_tokens", 0) or 0

        self.conn.execute(
            """
            INSERT INTO llm_usage(model, operation, input_tokens, output_tokens,
                                  cached_tokens, job_id, app_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (m, operation, usage.input_tokens, usage.output_tokens, cached, job_id, app_id),
        )
        self.conn.commit()

        return LLMResponse(
            text=text,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=cached,
            model=m,
        )


# Legacy alias — older modules import ``LLM`` directly.
LLM = DirectLLM


class QueueLLM:
    """Buffer prompts in memory, flush to a queue dir on ``finalize()``."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        operation: str,
        private: Path,
        default_model: str | None = None,
        result_schema: dict[str, Any] | None = None,
        extra_meta: dict[str, Any] | None = None,
    ):
        self.conn = conn
        self.operation = operation
        self.private = private
        self.default_model = default_model or "claude-haiku-4-5-20251001"
        self.result_schema = result_schema
        self.extra_meta = extra_meta or {}
        self._items: list[QueueItem] = []
        self._system: str | None = None
        self._max_tokens: int = 1024

    def enqueue(
        self,
        *,
        system: str,
        user: str,
        item_id: str | int,
        meta: dict[str, Any] | None = None,
        max_tokens: int = 1024,
    ) -> None:
        if self._system is None:
            self._system = system
        elif system != self._system:
            raise ValueError(
                "QueueLLM expects a single shared system prompt per queue; "
                "split into separate operations if you need different ones."
            )
        self._max_tokens = max(self._max_tokens, max_tokens)
        self._items.append(
            QueueItem(id=str(item_id), user_prompt=user, meta=meta or {}, max_tokens=max_tokens)
        )

    def finalize(self) -> Path:
        if not self._items or self._system is None:
            raise RuntimeError("QueueLLM.finalize() with nothing to flush")
        return prepare(
            operation=self.operation,
            system=self._system,
            items=self._items,
            private=self.private,
            model_hint=self.default_model,
            max_tokens=self._max_tokens,
            result_schema=self.result_schema,
            extra_meta=self.extra_meta,
        )


def log_queue_ingest(
    conn: sqlite3.Connection,
    *,
    operation: str,
    item_count: int,
    model: str = QUEUE_MODEL_TAG,
    job_id: int | None = None,
    app_id: int | None = None,
) -> None:
    """Stamp ``llm_usage`` after a queue ingest. Token counts are unknown
    on the Max plan, so we record the count of items and zero tokens."""
    conn.execute(
        """
        INSERT INTO llm_usage(occurred_at, model, operation, input_tokens,
                              output_tokens, cached_tokens, job_id, app_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().isoformat(timespec="seconds"),
            model,
            operation,
            item_count,
            0,
            0,
            job_id,
            app_id,
        ),
    )
    conn.commit()
