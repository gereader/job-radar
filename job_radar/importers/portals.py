"""Migrate a career-ops portals.yml (533+ companies) into job-radar format.

career-ops entries look like:
  - name: "Anthropic"
    enabled: true
    careers_url: "https://www.anthropic.com/jobs"
    greenhouse_slug: "anthropic"      # optional
    ashby_slug: "anthropic"           # optional
    lever_slug: "anthropic"           # optional

job-radar entries are simpler:
  - { name, source: greenhouse|ashby|lever|workable|manual, slug, enabled }

We infer `source`+`slug` from explicit slug fields first, then from the
careers_url. Anything we can't resolve is tagged `source: manual` so the
user can triage after.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from rich.console import Console

from ..config import Config

console = Console()


_URL_PATTERNS = [
    (re.compile(r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)"), "greenhouse"),
    (re.compile(r"job-boards\.greenhouse\.io/([A-Za-z0-9_-]+)"), "greenhouse"),
    (re.compile(r"greenhouse\.io/([A-Za-z0-9_-]+)"), "greenhouse"),
    (re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)"), "ashby"),
    (re.compile(r"ashbyhq\.com/([A-Za-z0-9_-]+)"), "ashby"),
    (re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)"), "lever"),
    (re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)"), "workable"),
]


def _infer(entry: dict) -> tuple[str, str] | None:
    if entry.get("greenhouse_slug"):
        return "greenhouse", entry["greenhouse_slug"]
    if entry.get("ashby_slug"):
        return "ashby", entry["ashby_slug"]
    if entry.get("lever_slug"):
        return "lever", entry["lever_slug"]
    if entry.get("workable_slug"):
        return "workable", entry["workable_slug"]
    url = entry.get("careers_url") or ""
    for pat, src in _URL_PATTERNS:
        m = pat.search(url)
        if m:
            return src, m.group(1)
    return None


def run_migrate_portals(career_ops_path: Path) -> None:
    cfg = Config.load()
    cfg.ensure_dirs()
    src = Path(career_ops_path).resolve()
    src_yml = src / "portals.yml"
    if not src_yml.exists():
        console.print(f"[red]no portals.yml at {src_yml}[/red]")
        return
    data = yaml.safe_load(src_yml.read_text()) or {}

    title_filter = data.get("title_filter") or {}
    companies = data.get("tracked_companies") or data.get("companies") or []

    out_companies = []
    unresolved = []
    inferred_counts = {"greenhouse": 0, "ashby": 0, "lever": 0, "workable": 0, "manual": 0}

    for c in companies:
        name = c.get("name")
        if not name:
            continue
        enabled = c.get("enabled", True)
        inferred = _infer(c)
        if inferred:
            source, slug = inferred
            inferred_counts[source] += 1
            out_companies.append(
                {"name": name, "source": source, "slug": slug, "enabled": enabled}
            )
        else:
            inferred_counts["manual"] += 1
            unresolved.append(
                {
                    "name": name,
                    "source": "manual",
                    "slug": "",
                    "enabled": False,
                    "careers_url": c.get("careers_url", ""),
                }
            )

    final = {
        "title_filter": {
            "positive": title_filter.get("positive") or [],
            "negative": title_filter.get("negative") or [],
        },
        "companies": out_companies + unresolved,
    }

    target = cfg.portals_path
    if target.exists():
        backup = target.with_suffix(".yml.bak")
        backup.write_text(target.read_text())
        console.print(f"backed up existing portals.yml → {backup.name}")

    target.write_text(yaml.safe_dump(final, sort_keys=False, width=120))
    total = sum(inferred_counts.values())
    console.print(f"[green]migrated[/green] {total} companies → {target}")
    for src_name, n in inferred_counts.items():
        console.print(f"  {src_name}: {n}")
    if unresolved:
        console.print(
            f"[yellow]{len(unresolved)} entries need manual review[/yellow] "
            "(source=manual, disabled by default). Edit portals.yml to set source+slug."
        )
