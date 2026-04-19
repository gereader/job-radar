"""`jr init` — bootstrap the private/ tree and migrate the DB."""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console

from .config import Config
from .db import connect, migrate

console = Console()


def run_init(private: Path | None = None) -> None:
    cfg = Config.load()
    if private is not None:
        cfg.private = Path(private).resolve()
    cfg.ensure_dirs()

    root = cfg.root
    examples = {
        root / "templates" / "profile.example.yml": cfg.private / "profile.yml",
        root / "templates" / "keywords.example.yml": cfg.private / "keywords.yml",
        root / "templates" / "portals.example.yml": cfg.private / "portals.yml",
        root / "templates" / "cv.example.md": cfg.private / "cv.md",
        root / "templates" / "cover-template.example.md": cfg.private / "cover-template.md",
        root / "templates" / "story-bank.example.md": cfg.private / "story-bank.md",
    }
    for src, dest in examples.items():
        if not dest.exists() and src.exists():
            shutil.copy2(src, dest)
            console.print(f"[green]seeded[/green] {dest.relative_to(cfg.root)}")
        elif dest.exists():
            console.print(f"[yellow]kept[/yellow]   {dest.relative_to(cfg.root)}")

    conn = connect(cfg)
    v = migrate(conn)
    console.print(f"DB migrated to schema v{v} at {cfg.db_path.relative_to(cfg.root)}")

    console.print(
        "\n[bold]Next:[/bold] edit [cyan]private/profile.yml[/cyan], "
        "[cyan]private/keywords.yml[/cyan], and [cyan]private/cv.md[/cyan], "
        "then run [green]jr scan[/green]."
    )
