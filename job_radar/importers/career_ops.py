"""One-shot importer: career-ops → job-radar.

Reads the legacy markdown artifacts (applications.md, jds/, reports/) and
the user-layer files (cv.md, config/profile.yml, article-digest.md,
modes/_profile.md, portals.yml) and populates the SQLite DB + private/ tree.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from rich.console import Console

from ..config import Config
from ..db import connect, migrate
from ..db.queries import tx
from ..util.hashing import content_hash
from ..util.slugify import slugify

console = Console()


_STATUS_MAP = {
    "evaluated": "Evaluated",
    "applied": "Applied",
    "responded": "Responded",
    "interview": "Interview",
    "offer": "Offer",
    "rejected": "Rejected",
    "discarded": "Discarded",
    "skip": "SKIP",
}


def _copy_user_files(src: Path, cfg: Config) -> None:
    cfg.ensure_dirs()
    mapping = {
        src / "cv.md": cfg.cv_path,
        src / "article-digest.md": cfg.private / "article-digest.md",
        src / "config" / "profile.yml": cfg.private / "profile.yml",
        src / "modes" / "_profile.md": cfg.private / "profile.legacy.md",
        src / "portals.yml": cfg.portals_path,
        src / "interview-prep" / "story-bank.md": cfg.story_bank_path,
    }
    for a, b in mapping.items():
        if a.exists() and not b.exists():
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(a, b)
            console.print(f"[green]copied[/green] {a.name} → {cfg.relpath(b)}")


_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|(.*)$")


def _import_applications(src: Path, cfg: Config, conn) -> int:
    apps_md = src / "data" / "applications.md"
    if not apps_md.exists():
        return 0
    n = 0
    for line in apps_md.read_text().splitlines():
        m = _ROW_RE.match(line)
        if not m or not m.group(1).isdigit():
            continue
        _num, date, company, role, score_str, status, pdf, report_link, notes = (
            g.strip() for g in m.groups()
        )
        if not company or company.lower() == "company":
            continue

        score = None
        sm = re.search(r"([0-9.]+)\s*/\s*5", score_str)
        if sm:
            try:
                score = float(sm.group(1))
            except ValueError:
                pass

        canonical = _STATUS_MAP.get(status.strip().lower())
        if not canonical:
            # leave unknown statuses as Evaluated
            canonical = "Evaluated"

        # Make a synthetic hash so dedup can still catch re-imports.
        h = content_hash(company, role, report_link or notes)
        with tx(conn):
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO jobs(hash, source, company, title, url, jd_path)
                VALUES (?, 'imported', ?, ?, '', '')
                """,
                (h, company, role),
            )
            if cur.rowcount == 0:
                job = conn.execute("SELECT id FROM jobs WHERE hash = ?", (h,)).fetchone()
                job_id = job["id"]
            else:
                job_id = cur.lastrowid
            conn.execute(
                """
                INSERT OR IGNORE INTO applications(job_id, status, score, applied_at, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, canonical, score, date or None, notes or None),
            )
        n += 1
    return n


def _import_jds(src: Path, cfg: Config, conn) -> int:
    jds_dir = src / "jds"
    if not jds_dir.exists():
        return 0
    dest_dir = cfg.jds_active
    dest_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in jds_dir.glob("*.md"):
        target = dest_dir / f.name
        if not target.exists():
            shutil.copy2(f, target)
        # Try to parse company/title from filename: company-role-slug.md
        parts = f.stem.split("-")
        company = parts[0] if parts else f.stem
        title = " ".join(parts[1:]).title() if len(parts) > 1 else f.stem
        body = f.read_text()
        h = content_hash(company, title, body)
        with tx(conn):
            conn.execute(
                """
                INSERT OR IGNORE INTO jobs(hash, source, company, title, url, jd_path, screen_verdict)
                VALUES (?, 'imported', ?, ?, '', ?, 'pass')
                """,
                (h, company, title, cfg.relpath(target)),
            )
        n += 1
    return n


def _import_reports(src: Path, cfg: Config, conn) -> int:
    r_dir = src / "reports"
    if not r_dir.exists():
        return 0
    n = 0
    for f in sorted(r_dir.glob("*.md")):
        content = f.read_text()
        # Find a company match in existing applications.
        m = re.search(r"(?im)^#\s*(?:Evaluation:\s*)?([^\n—-]+)", content)
        label = (m.group(1).strip() if m else "").split("—")[0].strip()
        if not label:
            continue
        row = conn.execute(
            """
            SELECT a.id FROM applications a JOIN jobs j ON j.id = a.job_id
            WHERE j.company = ? LIMIT 1
            """,
            (label,),
        ).fetchone()
        if not row:
            continue
        app_id = row["id"]
        app_dir = cfg.applications_dir / f"{app_id}-{slugify(label)}"
        app_dir.mkdir(parents=True, exist_ok=True)
        dest = app_dir / f.name
        if not dest.exists():
            shutil.copy2(f, dest)
        with tx(conn):
            conn.execute(
                "UPDATE applications SET report_path = ? WHERE id = ?",
                (cfg.relpath(dest), app_id),
            )
        n += 1
    return n


def run_import(path: Path) -> None:
    src = Path(path).resolve()
    if not src.exists():
        console.print(f"[red]{src} not found[/red]")
        return
    cfg = Config.load()
    cfg.ensure_dirs()
    conn = connect(cfg)
    migrate(conn)

    _copy_user_files(src, cfg)
    apps = _import_applications(src, cfg, conn)
    jds = _import_jds(src, cfg, conn)
    reports = _import_reports(src, cfg, conn)
    console.print(
        f"[green]imported[/green] "
        f"applications={apps} jds={jds} reports={reports}"
    )
