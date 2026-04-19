"""Choose direct API vs queue backend; build the right LLM object."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Literal

from ..config import Config
from .client import DirectLLM, QueueLLM

Backend = Literal["direct", "queue"]


def select_backend(*, force: Backend | None = None) -> Backend:
    """Pick a backend.

    Order of precedence:
      1. ``force`` argument (explicit override from CLI flag).
      2. ``JOB_RADAR_LLM_BACKEND`` env var (``direct`` or ``queue``).
      3. ``ANTHROPIC_API_KEY`` present → ``direct``.
      4. Default: ``queue`` (Max-plan path).
    """
    if force in ("direct", "queue"):
        return force
    env = os.environ.get("JOB_RADAR_LLM_BACKEND", "").strip().lower()
    if env in ("direct", "queue"):
        return env  # type: ignore[return-value]
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "direct"
    return "queue"


def build_llm(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    operation: str,
    default_model: str | None = None,
    result_schema: dict[str, Any] | None = None,
    extra_meta: dict[str, Any] | None = None,
    force: Backend | None = None,
) -> tuple[Backend, DirectLLM | QueueLLM]:
    """Return ``(backend_name, client)`` ready for the caller to use."""
    backend = select_backend(force=force)
    if backend == "direct":
        return backend, DirectLLM(conn, default_model=default_model)
    return backend, QueueLLM(
        conn,
        operation=operation,
        private=Path(cfg.private),
        default_model=default_model,
        result_schema=result_schema,
        extra_meta=extra_meta,
    )
