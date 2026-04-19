"""job-radar CLI — both `jr` and `job-radar` resolve here."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import Config
from .db import connect, migrate

app = typer.Typer(
    name="jr",
    help="Python-first job search pipeline. Aliases: jr, job-radar.",
    no_args_is_help=True,
    add_completion=True,
)
console = Console()


# ---------------------------------------------------------------------------
# Subcommand groups
contact_app = typer.Typer(help="Contacts CRM.")
jd_app = typer.Typer(help="JD lifecycle management.")
db_app = typer.Typer(help="Database housekeeping.")
import_app = typer.Typer(help="Importers from other systems.")

app.add_typer(contact_app, name="contact")
app.add_typer(jd_app, name="jd")
app.add_typer(db_app, name="db")
app.add_typer(import_app, name="import")


# ---------------------------------------------------------------------------
# Top-level commands
@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Print version and exit."),
):
    if version:
        console.print(f"job-radar {__version__}")
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit(0)


@app.command()
def init(
    private: Path | None = typer.Option(
        None,
        "--private",
        help="Path to use for private/ (defaults to ./private).",
    ),
):
    """Create private/ tree, seed example config, run DB migrations."""
    from .init_flow import run_init

    run_init(private)


@app.command()
def scan(
    portal: str | None = typer.Option(None, "--portal", help="Run only one portal."),
    limit: int = typer.Option(0, "--limit", help="Max jobs to process (0 = unlimited)."),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Scan portals, dedup via hash, save new JDs, run pre-screen."""
    from .scan.orchestrator import run_scan

    run_scan(portal=portal, limit=limit, dry_run=dry_run)


@app.command()
def triage(
    limit: int = typer.Option(0, "--limit", help="Max jobs to triage."),
    batch: str | None = typer.Option(
        None, "--batch",
        help="'submit' queues a Batch API job at 50% cost; 'poll' fetches results.",
    ),
):
    """Haiku pass over the pre-screen 'review' bucket."""
    if batch == "submit":
        from .llm.batch_triage import submit
        submit(limit=limit)
        return
    if batch == "poll" or batch == "check":
        from .llm.batch_triage import poll
        poll()
        return
    if batch:
        console.print(f"[red]unknown --batch mode:[/red] {batch} (use submit|poll)")
        raise typer.Exit(2)
    from .llm.triage import run_triage

    run_triage(limit=limit)


@app.command()
def show(job_id: int):
    """Print a JD, its screen result, and triage verdict."""
    from .views.show import show_job

    show_job(job_id)


@app.command(name="eval")
def eval_cmd(job_id: int):
    """Run Sonnet deep evaluation (A-F+G) and write a report."""
    from .llm.evaluate import run_evaluate

    run_evaluate(job_id)


@app.command()
def apply(
    job_id: int,
    open_editor: bool = typer.Option(True, "--edit/--no-edit"),
):
    """Create application row, branch resume + cover from templates."""
    from .apply.flow import run_apply

    run_apply(job_id, open_editor=open_editor)


@app.command()
def render(app_id: int):
    """Regenerate resume.pdf and cover.pdf for an application."""
    from .apply.render import render_application

    render_application(app_id)


@app.command()
def status():
    """Tracker overview: active apps, stale, needs-followup."""
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM applications
        GROUP BY status
        ORDER BY n DESC
        """
    ).fetchall()
    t = Table(title="Applications by status")
    t.add_column("Status")
    t.add_column("Count", justify="right")
    for r in rows:
        t.add_row(r["status"], str(r["n"]))
    console.print(t)


@app.command()
def touch(
    app_id: int,
    channel: str = typer.Option("email", "--channel"),
    direction: str = typer.Option("outbound", "--direction"),
    summary: str = typer.Option(..., "--summary", "-m"),
    contact_id: int | None = typer.Option(None, "--contact"),
):
    """Log a touchpoint (LinkedIn / email / call) on an application."""
    from .contacts.crm import log_touchpoint

    log_touchpoint(
        app_id=app_id,
        channel=channel,
        direction=direction,
        summary=summary,
        contact_id=contact_id,
    )


@app.command()
def followup(
    draft: int | None = typer.Option(None, "--draft", help="App id to draft a message for."),
):
    """Show queued follow-ups; with --draft, Haiku composes a check-in."""
    from .contacts.followup import show_queue, draft_followup

    if draft is not None:
        draft_followup(draft)
    else:
        show_queue()


@app.command()
def export():
    """Regenerate markdown views under private/exports/."""
    from .export.markdown import export_all

    export_all()


@app.command()
def dash(open_browser: bool = typer.Option(True, "--open/--no-open")):
    """Build a static HTML dashboard and open it in the browser."""
    from .dash.build import build_dashboard

    build_dashboard(open_browser=open_browser)


@app.command()
def costs(since_days: int = typer.Option(7, "--since")):
    """Cost telemetry: tokens + calls by operation/model over N days."""
    from .views.costs import show_costs

    show_costs(since_days)


@app.command()
def patterns():
    """Analyze rejection/conversion patterns; suggest portal/keyword tweaks."""
    from .learn.patterns import run_patterns

    run_patterns()


@app.command()
def interview(app_id: int):
    """Sonnet interview prep report for an application."""
    from .llm.interview import run_interview_prep

    run_interview_prep(app_id)


@app.command()
def research(job_id: int):
    """Sonnet company research: funding, headcount, signals, risks."""
    from .llm.research import run_research

    run_research(job_id)


@app.command()
def call():
    """Log a recruiter call (interactive, zero LLM)."""
    from .contacts.call import log_call_interactive

    log_call_interactive()


inbox_app = typer.Typer(help="Ingest LinkedIn/email threads.")
app.add_typer(inbox_app, name="inbox")


@inbox_app.command("paste")
def inbox_paste(
    file: Path | None = typer.Option(None, "--file", "-f"),
    app_id: int | None = typer.Option(None, "--app", help="Link to this application."),
    draft: bool = typer.Option(False, "--draft", help="After ingest, Haiku drafts a reply."),
):
    """Paste raw text (LinkedIn DM, email thread). Haiku extracts fields."""
    from .ingest.paste import ingest_paste

    ingest_paste(file=file, app_id=app_id, draft=draft)


@inbox_app.command("email")
def inbox_email(
    path: Path = typer.Argument(..., help=".eml or .mbox file"),
):
    """Ingest an email. Headers deterministic, Haiku classifies intent."""
    from .ingest.email_eml import ingest_email

    ingest_email(path)


round_app = typer.Typer(help="Interview round tracking.")
app.add_typer(round_app, name="round")


@round_app.command("add")
def round_add(app_id: int):
    from .rounds.cli import add_round

    add_round(app_id)


@round_app.command("list")
def round_list(app_id: int):
    from .rounds.cli import list_rounds

    list_rounds(app_id)


@round_app.command("update")
def round_update(round_id: int):
    from .rounds.cli import update_round

    update_round(round_id)


@app.command()
def thanks(round_id: int):
    """Haiku draft of a thank-you note for a completed round."""
    from .llm.thanks import run_thanks

    run_thanks(round_id)


learn_app = typer.Typer(help="Learning loops (human-in-the-loop).")
app.add_typer(learn_app, name="learn")


@learn_app.command("keywords")
def learn_keywords():
    """Propose new negative/positive keywords from outcome history."""
    from .learn.keywords import run_learn_keywords

    run_learn_keywords()


# --- contact subcommands ----------------------------------------------------
@contact_app.command("add")
def contact_add():
    from .contacts.crm import add_contact_interactive

    add_contact_interactive()


@contact_app.command("list")
def contact_list():
    from .contacts.crm import list_contacts

    list_contacts()


@contact_app.command("show")
def contact_show(contact_id: int):
    from .contacts.crm import show_contact

    show_contact(contact_id)


# --- jd subcommands ---------------------------------------------------------
@jd_app.command("list")
def jd_list(state: str = typer.Option("active", "--state")):
    from .views.jd import list_jds

    list_jds(state=state)


@jd_app.command("archive")
def jd_archive(older_than_days: int = typer.Option(90, "--older-than")):
    from .jd.lifecycle import archive_old

    archive_old(older_than_days)


@jd_app.command("purge")
def jd_purge(older_than_days: int = typer.Option(365, "--older-than")):
    from .jd.lifecycle import purge_old

    purge_old(older_than_days)


# --- db subcommands ---------------------------------------------------------
@db_app.command("migrate")
def db_migrate():
    cfg = Config.load()
    conn = connect(cfg)
    v = migrate(conn)
    console.print(f"Schema at version [bold]{v}[/bold] ({cfg.db_path})")


@db_app.command("backup")
def db_backup(dest: Path = typer.Argument(...)):
    cfg = Config.load()
    dest.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(cfg)
    with sqlite_backup(conn, dest):
        pass
    console.print(f"Backup written to {dest}")


@db_app.command("query")
def db_query(sql: str):
    """Run an arbitrary SELECT (read-only) for debugging."""
    cfg = Config.load()
    conn = connect(cfg)
    try:
        rows = conn.execute(sql).fetchall()
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    if not rows:
        console.print("(no rows)")
        return
    t = Table()
    for k in rows[0].keys():
        t.add_column(k)
    for r in rows:
        t.add_row(*[str(r[k]) for k in r.keys()])
    console.print(t)


# --- import subcommands -----------------------------------------------------
@import_app.command("career-ops")
def import_career_ops(path: Path = typer.Argument(...)):
    """One-shot migration from an existing career-ops checkout."""
    from .importers.career_ops import run_import

    run_import(path)


@app.command("migrate-portals")
def migrate_portals(path: Path = typer.Argument(..., help="career-ops checkout path")):
    """Port 500+ companies from a career-ops portals.yml, inferring source+slug."""
    from .importers.portals import run_migrate_portals

    run_migrate_portals(path)


# ---------------------------------------------------------------------------
# helpers
def sqlite_backup(src, dest):
    import sqlite3
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        bck = sqlite3.connect(dest)
        try:
            src.backup(bck)
            yield
        finally:
            bck.close()

    return _ctx()


if __name__ == "__main__":
    app()
