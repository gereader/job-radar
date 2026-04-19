"""Pure-Python pre-ranking + slicing for every LLM-emitting command.

Why this exists
---------------
The Max plan has real weekly limits. Every LLM-consuming command must
select candidates in pure Python (SQL + signals) first, rank them by a
command-specific value score, then slice to a small default. This module
is the shared helper so every command behaves the same way: same default,
same flags, same "rerun with --limit N" hint, same ``--rank debug``
support.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

from rich.console import Console
from rich.table import Table

T = TypeVar("T")

DEFAULT_LIMIT = 10


@dataclass
class Sliced:
    picked: list[Any]
    remaining: int
    total: int

    def hint(self, *, command: str, current_limit: int) -> str:
        if self.remaining <= 0:
            return ""
        next_limit = min(self.total, max(current_limit * 2, current_limit + 10))
        return (
            f"sliced top {len(self.picked)} of {self.total} — "
            f"rerun with [bold]--limit {next_limit}[/bold] for more, "
            f"or [bold]--all[/bold] to include everything."
        )


def rank_and_slice(
    rows: Iterable[T],
    *,
    key: Callable[[T], float | int],
    limit: int | None = None,
    all_: bool = False,
    descending: bool = True,
) -> Sliced:
    """Rank ``rows`` by ``key`` and return ``Sliced`` with picked items.

    ``limit=None`` uses ``DEFAULT_LIMIT``. ``all_=True`` keeps every row
    (and sets ``remaining=0``).
    """
    pool = list(rows)
    pool.sort(key=key, reverse=descending)
    if all_:
        return Sliced(picked=pool, remaining=0, total=len(pool))
    n = limit if limit is not None else DEFAULT_LIMIT
    n = max(0, n)
    picked = pool[:n]
    return Sliced(picked=picked, remaining=max(0, len(pool) - n), total=len(pool))


def print_rank_debug(
    rows: list[T],
    *,
    key: Callable[[T], float | int],
    columns: list[tuple[str, Callable[[T], Any]]],
    title: str,
    console: Console | None = None,
) -> None:
    """Pretty-print the ranked list without emitting any prompt packet."""
    c = console or Console()
    pool = sorted(rows, key=key, reverse=True)
    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("score", justify="right")
    for label, _ in columns:
        table.add_column(label)
    for i, r in enumerate(pool, 1):
        table.add_row(
            str(i), f"{key(r):.2f}", *[str(getter(r)) for _, getter in columns]
        )
    c.print(table)


def resolved_default(profile: dict[str, Any] | None) -> int:
    """Read ``limits.default_llm_batch`` out of profile.yml, fall back to const."""
    if not profile:
        return DEFAULT_LIMIT
    raw = (profile.get("limits") or {}).get("default_llm_batch")
    try:
        return int(raw) if raw else DEFAULT_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
