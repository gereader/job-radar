"""`jr patterns` — pure SQL segmentation + short Haiku summary."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..config import Config
from ..db import connect, migrate

console = Console()


_POSITIVE = ("Applied", "Responded", "Interview", "Offer")
_NEGATIVE = ("SKIP", "Discarded", "Rejected")


def _segment(conn, column: str, label: str) -> list[dict]:
    sql = f"""
    SELECT COALESCE({column}, '-') AS bucket,
           SUM(CASE WHEN a.status IN ('Applied','Responded','Interview','Offer') THEN 1 ELSE 0 END) AS pos,
           SUM(CASE WHEN a.status IN ('SKIP','Discarded','Rejected') THEN 1 ELSE 0 END) AS neg,
           COUNT(*) AS total
    FROM applications a JOIN jobs j ON j.id = a.job_id
    GROUP BY bucket
    HAVING total >= 3
    ORDER BY total DESC
    """
    rows = conn.execute(sql).fetchall()
    return [
        {
            "label": label,
            "bucket": r["bucket"],
            "pos": r["pos"],
            "neg": r["neg"],
            "total": r["total"],
            "conversion": round(100 * r["pos"] / r["total"], 1) if r["total"] else 0.0,
        }
        for r in rows
    ]


def run_patterns() -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    overall = conn.execute(
        """
        SELECT status, COUNT(*) AS n FROM applications GROUP BY status
        """
    ).fetchall()
    if not overall:
        console.print("no applications yet.")
        return

    segments = {
        "remote": _segment(conn, "j.remote", "remote"),
        "archetype": _segment(conn, "a.archetype", "archetype"),
        "company": _segment(conn, "j.company", "company"),
    }

    md: list[str] = ["# Patterns\n"]
    md.append("## Overall\n")
    md.append("| Status | Count |\n|---|---|")
    for r in overall:
        md.append(f"| {r['status']} | {r['n']} |")
    md.append("")

    for name, rows in segments.items():
        if not rows:
            continue
        md.append(f"## By {name}\n")
        md.append("| " + name + " | Total | Pos | Neg | Conv % |\n|---|---|---|---|---|")
        for r in rows[:15]:
            md.append(
                f"| {r['bucket']} | {r['total']} | {r['pos']} | {r['neg']} | {r['conversion']} |"
            )
        md.append("")

        t = Table(title=f"Conversion by {name}")
        for c in (name, "Total", "Pos", "Neg", "Conv %"):
            t.add_column(c, justify="right" if c != name else "left")
        for r in rows[:10]:
            t.add_row(r["bucket"], str(r["total"]), str(r["pos"]),
                      str(r["neg"]), f"{r['conversion']}%")
        console.print(t)

    # Low-conversion signals
    worst_archetypes = [r for r in segments["archetype"]
                        if r["total"] >= 5 and r["conversion"] <= 20.0]
    worst_companies = [r for r in segments["company"]
                       if r["total"] >= 3 and r["conversion"] == 0.0]
    if worst_archetypes:
        md.append("## Low-conversion archetypes (≥5 apps, ≤20% conv)\n")
        for r in worst_archetypes:
            md.append(f"- **{r['bucket']}** — {r['total']} apps, {r['conversion']}% conv")
    if worst_companies:
        md.append("\n## 0% conversion companies (≥3 apps)\n")
        for r in worst_companies:
            md.append(f"- **{r['bucket']}** — consider disabling in portals.yml")

    out = cfg.exports_dir / "patterns.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md) + "\n")
    console.print(f"[green]wrote[/green] {out}")
