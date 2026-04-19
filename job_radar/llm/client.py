"""Thin Anthropic SDK wrapper with prompt caching + usage logging."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Any

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None  # type: ignore


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    model: str


class LLM:
    """One client, many operations. Logs every call to llm_usage."""

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
