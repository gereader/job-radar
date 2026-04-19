"""`jr costs` — token + call telemetry from llm_usage."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from ..config import Config
from ..db import connect, migrate

console = Console()


# Anthropic public pricing (per million tokens) as of 2026-Q1. User can
# override via profile.llm.pricing.{model}.{in,out,cache_read}.
_DEFAULT_PRICING = {
    "claude-haiku-4-5-20251001": {"in": 1.0, "out": 5.0, "cache_read": 0.1},
    "claude-sonnet-4-6":         {"in": 3.0, "out": 15.0, "cache_read": 0.3},
    "claude-opus-4-7":           {"in": 15.0, "out": 75.0, "cache_read": 1.5},
}


def _price(cfg, model: str) -> dict:
    overrides = ((cfg.profile.get("llm") or {}).get("pricing") or {}).get(model, {})
    base = _DEFAULT_PRICING.get(model, {"in": 3.0, "out": 15.0, "cache_read": 0.3})
    return {**base, **overrides}


def show_costs(since_days: int = 7) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    rows = conn.execute(
        """
        SELECT operation, model,
               COUNT(*)            AS calls,
               SUM(input_tokens)   AS in_tok,
               SUM(output_tokens)  AS out_tok,
               SUM(cached_tokens)  AS cache_tok
        FROM llm_usage
        WHERE occurred_at >= datetime('now', ?)
        GROUP BY operation, model
        ORDER BY in_tok DESC NULLS LAST
        """,
        (f"-{since_days} days",),
    ).fetchall()
    if not rows:
        console.print(f"no LLM calls in last {since_days}d.")
        return

    t = Table(title=f"LLM spend, last {since_days}d")
    for c in ("Op", "Model", "Calls", "In", "Out", "Cached", "Cache %", "$"):
        t.add_column(c, justify="right" if c not in ("Op", "Model") else "left")

    total = 0.0
    for r in rows:
        p = _price(cfg, r["model"])
        in_tok = r["in_tok"] or 0
        out_tok = r["out_tok"] or 0
        cache_tok = r["cache_tok"] or 0
        fresh_in = max(0, in_tok - cache_tok)
        cost = (
            fresh_in * p["in"] / 1_000_000
            + cache_tok * p["cache_read"] / 1_000_000
            + out_tok * p["out"] / 1_000_000
        )
        total += cost
        cache_pct = round(100 * cache_tok / in_tok, 1) if in_tok else 0.0
        t.add_row(
            r["operation"], r["model"], str(r["calls"]),
            f"{in_tok:,}", f"{out_tok:,}", f"{cache_tok:,}",
            f"{cache_pct}%", f"${cost:.3f}",
        )
    t.caption = f"total: ${total:.3f}"
    console.print(t)
