"""`jr research <job_id>` — Sonnet company research report."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from ..util.slugify import slugify
from .client import LLM

console = Console()

_SHARED = Path(__file__).parent.parent.parent / "modes" / "_shared.md"
_RESEARCH = Path(__file__).parent.parent.parent / "modes" / "research.md"


def run_research(job_id: int) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    row = conn.execute(
        "SELECT * FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if not row:
        console.print(f"[red]no job {job_id}[/red]")
        return

    jd_path = cfg.root / (row["jd_path"] or "")
    jd_md = jd_path.read_text() if jd_path.exists() else ""

    system = _SHARED.read_text() + "\n\n---\n\n" + _RESEARCH.read_text()
    user = (
        f"Company: {row['company']}\nRole: {row['title']}\nURL: {row['url']}\n\n"
        f"JD excerpt for context:\n\n{jd_md[:6000]}"
    )

    model = (cfg.profile.get("llm") or {}).get("eval_model", "claude-sonnet-4-6")
    llm = LLM(conn, default_model=model)
    resp = llm.complete(
        system=system, user=user, operation="research",
        job_id=job_id, max_tokens=2500,
    )

    # Attach report to an application if one exists; otherwise next to the JD.
    app = conn.execute(
        "SELECT id FROM applications WHERE job_id = ?", (job_id,)
    ).fetchone()
    if app:
        app_id = app["id"]
        out_dir = cfg.applications_dir / f"{app_id}-{slugify(row['company'])}"
    else:
        out_dir = cfg.private / "research" / slugify(row["company"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"company-{date.today().isoformat()}.md"
    out.write_text(resp.text)
    console.print(f"[green]company research[/green] → {out}")
    console.print(
        f"tokens: in={resp.input_tokens} out={resp.output_tokens} "
        f"cached={resp.cached_tokens}"
    )
