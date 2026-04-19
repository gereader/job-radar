"""Admin commands for `portals.yml` — enable/disable, ghost-cooldown.

Pure YAML rewrites. Idempotent. The orchestrator + liveness both honor
``ghosted_until: YYYY-MM-DD`` on a portal entry; once that date passes
the entry re-enables automatically.
"""

from __future__ import annotations

from datetime import date, timedelta

import yaml
from rich.console import Console

from ..config import Config

console = Console()


def _load(cfg: Config) -> dict:
    if not cfg.portals_path.exists():
        raise FileNotFoundError(
            f"{cfg.portals_path} missing — run `jr init` first."
        )
    return yaml.safe_load(cfg.portals_path.read_text()) or {}


def _save(cfg: Config, data: dict) -> None:
    cfg.portals_path.write_text(yaml.safe_dump(data, sort_keys=False))


def _match(entry: dict, name_or_slug: str) -> bool:
    return (
        entry.get("name", "").lower() == name_or_slug.lower()
        or entry.get("slug", "").lower() == name_or_slug.lower()
    )


def ghost_cooldown(name_or_slug: str, days: int = 180) -> None:
    """Set ``ghosted_until: YYYY-MM-DD`` ``days`` from today on a portal entry."""
    cfg = Config.load()
    data = _load(cfg)
    entries = data.get("companies") or []
    until = (date.today() + timedelta(days=days)).isoformat()
    hit = 0
    for entry in entries:
        if _match(entry, name_or_slug):
            entry["ghosted_until"] = until
            hit += 1
    if hit == 0:
        console.print(f"[red]no portal entry matched[/red] {name_or_slug!r}")
        return
    _save(cfg, data)
    console.print(
        f"[green]cooldown set[/green] {hit} entry(ies) → ghosted_until {until} "
        f"(re-enables automatically when the date passes)"
    )


def disable(name_or_slug: str) -> None:
    cfg = Config.load()
    data = _load(cfg)
    entries = data.get("companies") or []
    hit = 0
    for entry in entries:
        if _match(entry, name_or_slug):
            entry["enabled"] = False
            hit += 1
    if hit == 0:
        console.print(f"[red]no portal entry matched[/red] {name_or_slug!r}")
        return
    _save(cfg, data)
    console.print(f"[green]disabled[/green] {hit} entry(ies)")


def enable(name_or_slug: str) -> None:
    cfg = Config.load()
    data = _load(cfg)
    entries = data.get("companies") or []
    hit = 0
    for entry in entries:
        if _match(entry, name_or_slug):
            entry["enabled"] = True
            entry.pop("ghosted_until", None)
            hit += 1
    if hit == 0:
        console.print(f"[red]no portal entry matched[/red] {name_or_slug!r}")
        return
    _save(cfg, data)
    console.print(f"[green]enabled[/green] {hit} entry(ies)")


def list_status() -> None:
    cfg = Config.load()
    data = _load(cfg)
    today = date.today().isoformat()
    rows = []
    for entry in data.get("companies") or []:
        gh = entry.get("ghosted_until")
        gh_active = bool(gh) and str(gh) > today
        rows.append({
            "name": entry.get("name"),
            "slug": entry.get("slug"),
            "source": entry.get("source"),
            "enabled": entry.get("enabled", True),
            "ghosted_until": gh,
            "ghost_active": gh_active,
        })
    from rich.table import Table
    t = Table(title=f"Portals ({len(rows)})")
    for c in ("name", "slug", "source", "enabled", "ghosted_until"):
        t.add_column(c)
    for r in rows:
        flag = "[red]ghosted[/red]" if r["ghost_active"] else (r["ghosted_until"] or "")
        t.add_row(
            str(r["name"]), str(r["slug"]), str(r["source"]),
            "yes" if r["enabled"] else "[dim]no[/dim]", flag,
        )
    console.print(t)
