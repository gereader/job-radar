"""`jr apply` — create application row and branch resume + cover."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from ..util.slugify import slugify
from .cover import render_cover_template
from .render import render_application

console = Console()


def run_apply(job_id: int, open_editor: bool = True) -> None:
    cfg = Config.load()
    cfg.ensure_dirs()
    conn = connect(cfg)
    migrate(conn)

    job = conn.execute(
        "SELECT * FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if not job:
        console.print(f"[red]no job {job_id}[/red]")
        return

    existing = conn.execute(
        "SELECT id FROM applications WHERE job_id = ?", (job_id,)
    ).fetchone()
    if existing:
        console.print(f"[yellow]application {existing['id']} already exists for job {job_id}[/yellow]")
        return

    # Create row first so we have app_id for the directory.
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO applications(job_id, status) VALUES (?, 'Evaluated')",
            (job_id,),
        )
        app_id = cur.lastrowid

    app_dir = cfg.applications_dir / f"{app_id}-{slugify(job['company'])}"
    app_dir.mkdir(parents=True, exist_ok=True)

    # Branch resume from cv.md.
    resume_src = cfg.cv_path
    if not resume_src.exists():
        console.print(f"[red]{resume_src} missing — run `jr init` or add your CV[/red]")
        return
    resume_md = app_dir / "resume.md"
    if not resume_md.exists():
        shutil.copy2(resume_src, resume_md)

    # Freeze the JD so the archive survives portals taking it down.
    jd_src = cfg.root / job["jd_path"]
    jd_md = app_dir / "jd.md"
    if jd_src.exists():
        shutil.copy2(jd_src, jd_md)

    # Render cover from template.
    cover_md = app_dir / "cover.md"
    if not cover_md.exists() and cfg.cover_template_path.exists():
        rendered = render_cover_template(
            cfg.cover_template_path.read_text(),
            cfg.profile,
            company=job["company"],
            role=job["title"],
        )
        cover_md.write_text(rendered)

    # Empty notes pad.
    (app_dir / "notes.md").touch(exist_ok=True)

    with tx(conn):
        conn.execute(
            """
            UPDATE applications
            SET resume_path = ?, cover_path = ?
            WHERE id = ?
            """,
            (
                str(resume_md.relative_to(cfg.root)),
                str(cover_md.relative_to(cfg.root)),
                app_id,
            ),
        )
        # Log an outbound touchpoint placeholder — user fills in summary later.
        conn.execute(
            """
            INSERT INTO touchpoints(application_id, channel, direction, summary)
            VALUES (?, 'email', 'outbound', 'Application created')
            """,
            (app_id,),
        )

    console.print(f"[green]created application {app_id}[/green] at {app_dir}")

    if open_editor:
        editor = os.environ.get("EDITOR", "vi")
        try:
            subprocess.run([editor, str(resume_md), str(cover_md)], check=False)
        except FileNotFoundError:
            console.print(f"editor '{editor}' not found — open manually.")

    render_application(app_id)
