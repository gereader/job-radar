"""Render resume.md / cover.md to PDF via WeasyPrint. No browser dep."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx

console = Console()


def _md_to_html(md: str) -> str:
    try:
        import markdown as md_lib
        return md_lib.markdown(md, extensions=["extra", "sane_lists"])
    except ImportError:
        # Trivial fallback so render works even without the markdown lib.
        import html
        return "<pre>" + html.escape(md) + "</pre>"


def _render_pdf(md_path: Path, pdf_path: Path, css_path: Path) -> None:
    try:
        from weasyprint import CSS, HTML  # type: ignore
    except ImportError:
        console.print(
            "[yellow]weasyprint not installed — skipping PDF render.[/yellow] "
            "`pip install weasyprint`"
        )
        return

    body = _md_to_html(md_path.read_text())
    html_doc = (
        "<!doctype html><html><head><meta charset='utf-8'></head>"
        f"<body>{body}</body></html>"
    )
    HTML(string=html_doc).write_pdf(
        target=str(pdf_path),
        stylesheets=[CSS(filename=str(css_path))] if css_path.exists() else [],
    )


def render_application(app_id: int) -> None:
    cfg = Config.load()
    conn = connect(cfg)
    migrate(conn)

    app = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    if not app:
        console.print(f"[red]no application {app_id}[/red]")
        return

    css_path = cfg.root / "templates" / "resume.css"

    resume_md = cfg.root / (app["resume_path"] or "")
    cover_md = cfg.root / (app["cover_path"] or "")

    updates: dict[str, str] = {}
    if resume_md.exists():
        pdf = resume_md.with_suffix(".pdf")
        _render_pdf(resume_md, pdf, css_path)
        if pdf.exists():
            updates["resume_pdf_path"] = str(pdf.relative_to(cfg.root))
            console.print(f"[green]resume.pdf[/green] → {pdf}")
    if cover_md.exists():
        pdf = cover_md.with_suffix(".pdf")
        _render_pdf(cover_md, pdf, css_path)
        if pdf.exists():
            updates["cover_pdf_path"] = str(pdf.relative_to(cfg.root))
            console.print(f"[green]cover.pdf[/green] → {pdf}")

    if updates:
        fields = ", ".join(f"{k} = ?" for k in updates)
        with tx(conn):
            conn.execute(
                f"UPDATE applications SET {fields} WHERE id = ?",
                (*updates.values(), app_id),
            )
