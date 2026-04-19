"""Queue/ingest LLM plane — Claude Code does inference on the user's Max plan.

Architecture
------------
Direct API path (when ``ANTHROPIC_API_KEY`` is set) keeps using
``LLM.complete`` and returns immediately. The queue path writes a directory
of prompt packets that Claude Code (the parent harness) consumes via the
``/jr consume`` slash command, then the same CLI is re-entered with
``--ingest <dir>`` to fold the structured results back into the database.

A queue directory looks like::

    private/llm-queue/{operation}-{YYYYMMDDHHMMSS}/
        manifest.json        # operation, model hint, schema, items[]
        system.md            # cached system prompt (one per queue)
        packet-{id}.md       # one user prompt per item
        result-{id}.json     # Claude Code writes these
        consumed.flag        # touched by ingest() once results are folded in

Round-trip is opaque to callers: a command builds a list of items, hands
them to ``prepare()``, exits, then later picks up the same dir with
``ingest()`` and applies the results.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_TS_FMT = "%Y%m%d-%H%M%S"
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass
class QueueItem:
    id: str
    user_prompt: str
    meta: dict[str, Any] = field(default_factory=dict)
    max_tokens: int | None = None


@dataclass
class QueueResult:
    id: str
    meta: dict[str, Any]
    result: Any
    raw_text: str | None = None


def _safe_id(value: Any) -> str:
    return _SAFE_ID.sub("-", str(value)).strip("-") or "item"


def queue_root(private: Path) -> Path:
    return private / "llm-queue"


def prepare(
    *,
    operation: str,
    system: str,
    items: list[QueueItem],
    private: Path,
    model_hint: str | None = None,
    max_tokens: int = 1024,
    result_schema: dict[str, Any] | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> Path:
    """Write a manifest + per-item packet files. Return the queue directory.

    Idempotent on directory contents — caller picks the timestamp by
    convention; we never overwrite an existing manifest.
    """
    if not items:
        raise ValueError("prepare() called with no items")
    ts = datetime.now().strftime(_TS_FMT)
    qdir = queue_root(private) / f"{_safe_id(operation)}-{ts}"
    qdir.mkdir(parents=True, exist_ok=False)
    (qdir / "system.md").write_text(system)

    manifest_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for it in items:
        sid = _safe_id(it.id)
        if sid in seen_ids:
            raise ValueError(f"duplicate item id: {it.id}")
        seen_ids.add(sid)
        packet_name = f"packet-{sid}.md"
        (qdir / packet_name).write_text(it.user_prompt)
        manifest_items.append(
            {
                "id": sid,
                "packet": packet_name,
                "result": f"result-{sid}.json",
                "meta": it.meta,
                "max_tokens": it.max_tokens or max_tokens,
            }
        )

    manifest = {
        "operation": operation,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model_hint": model_hint,
        "default_max_tokens": max_tokens,
        "system_file": "system.md",
        "result_schema": result_schema,
        "extra_meta": extra_meta or {},
        "items": manifest_items,
    }
    (qdir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return qdir


def load_manifest(queue_dir: Path) -> dict[str, Any]:
    manifest_path = queue_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no manifest.json in {queue_dir}")
    return json.loads(manifest_path.read_text())


def pending(queue_dir: Path) -> list[dict[str, Any]]:
    """Return manifest items that don't yet have a result file."""
    m = load_manifest(queue_dir)
    out = []
    for item in m["items"]:
        rp = queue_dir / item["result"]
        if not rp.exists():
            out.append(item)
    return out


def _strip_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl > 0:
            s = s[first_nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _parse_result_file(path: Path) -> tuple[Any, str]:
    raw = path.read_text()
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError:
        pass
    stripped = _strip_fences(raw)
    try:
        return json.loads(stripped), raw
    except json.JSONDecodeError as e:
        raise ValueError(f"{path.name} is not valid JSON: {e}") from e


def ingest(queue_dir: Path, *, mark_consumed: bool = True) -> list[QueueResult]:
    """Read all result-*.json files; return parsed records in manifest order.

    Raises ``ValueError`` if any expected result file is missing or unparseable.
    Pass ``mark_consumed=False`` for dry-run inspection.
    """
    manifest = load_manifest(queue_dir)
    results: list[QueueResult] = []
    missing: list[str] = []
    for item in manifest["items"]:
        rp = queue_dir / item["result"]
        if not rp.exists():
            missing.append(item["id"])
            continue
        parsed, raw = _parse_result_file(rp)
        results.append(
            QueueResult(id=item["id"], meta=item.get("meta") or {}, result=parsed, raw_text=raw)
        )
    if missing:
        raise ValueError(
            f"{len(missing)} result file(s) missing in {queue_dir}: "
            + ", ".join(missing[:5])
            + ("..." if len(missing) > 5 else "")
        )
    if mark_consumed:
        (queue_dir / "consumed.flag").write_text(
            datetime.now().isoformat(timespec="seconds") + "\n"
        )
    return results


def is_consumed(queue_dir: Path) -> bool:
    return (queue_dir / "consumed.flag").exists()


def list_queues(private: Path) -> list[Path]:
    """Return all queue directories sorted by creation time descending."""
    root = queue_root(private)
    if not root.exists():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)


def latest_queue(private: Path, operation: str | None = None) -> Path | None:
    for q in list_queues(private):
        if operation and not q.name.startswith(f"{_safe_id(operation)}-"):
            continue
        if not is_consumed(q):
            return q
    return None
