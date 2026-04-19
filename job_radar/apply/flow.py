"""`jr apply` — create application row and branch resume + cover."""

from __future__ import annotations

import os
import shutil
import subprocess

from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from ..util.slugify import slugify
from .cover import render_cover_template
from .render import render_application

console = Console()


def run_apply(
    job_id: int,
    open_editor: bool = True,
    referral_contact_id: int | None = None,
) -> None:
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

    referral_name = None
    if referral_contact_id:
        c = conn.execute(
            "SELECT id, name FROM contacts WHERE id = ?", (referral_contact_id,)
        ).fetchone()
        if not c:
            console.print(f"[red]no contact {referral_contact_id}[/red]")
            return
        referral_name = c["name"]

    # Reuse an existing app row (e.g. one created upstream by `jr eval`) so
    # we can top up any missing resume / cover / jd files rather than
    # bailing outright. Create only when nothing exists.
    existing = conn.execute(
        "SELECT id FROM applications WHERE job_id = ?", (job_id,)
    ).fetchone()
    if existing:
        app_id = existing["id"]
        console.print(
            f"[cyan]application {app_id} already exists for job {job_id} — "
            f"topping up any missing files[/cyan]"
        )
        if referral_contact_id:
            with tx(conn):
                conn.execute(
                    "UPDATE applications SET referral_contact_id = ? WHERE id = ?",
                    (referral_contact_id, app_id),
                )
    else:
        with tx(conn):
            cur = conn.execute(
                "INSERT INTO applications(job_id, status, referral_contact_id) "
                "VALUES (?, 'Evaluated', ?)",
                (job_id, referral_contact_id),
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

    # Pull cached app_answers — own app first, then any sibling at the
    # same company (so a "why this company" answer is reused across roles).
    cached_answers: dict[str, str] = {}
    for row in conn.execute(
        """
        SELECT question_key, answer_md
        FROM app_answers
        WHERE application_id = ?
           OR application_id IN (
               SELECT a2.id FROM applications a2 JOIN jobs j2 ON j2.id = a2.job_id
               WHERE lower(j2.company) = lower(?) AND a2.id != ?
           )
        ORDER BY (application_id = ?) DESC, updated_at DESC
        """,
        (app_id, job["company"], app_id, app_id),
    ).fetchall():
        cached_answers.setdefault(row["question_key"], row["answer_md"])

    # Render cover from template.
    cover_md = app_dir / "cover.md"
    if not cover_md.exists() and cfg.cover_template_path.exists():
        rendered = render_cover_template(
            cfg.cover_template_path.read_text(),
            cfg.profile,
            company=job["company"],
            role=job["title"],
            cached_answers=cached_answers,
            referral_name=referral_name,
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
                cfg.relpath(resume_md),
                cfg.relpath(cover_md),
                app_id,
            ),
        )
        # Log an outbound touchpoint placeholder on first creation only.
        if not existing:
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
