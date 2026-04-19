"""SQLite helpers: connection, migration, small query utilities.

The DB lives at `private/data/career.db` by default. Everything personal.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..config import Config

SCHEMA_VERSION = 8
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _apply_migration_idempotent(conn: sqlite3.Connection, sql: str) -> None:
    """Run a migration script, tolerating idempotent ALTER TABLE re-runs.

    sqlite has no `ADD COLUMN IF NOT EXISTS`. Try the script as-is; if it
    fails with a duplicate-column error, re-run statement-by-statement
    (splitting on statement terminators, not on every `;`) and swallow
    only duplicate-column errors.
    """
    try:
        conn.executescript(sql)
        return
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise
    # Fallback: per-statement, tolerant of duplicate-column.
    for stmt in _split_sql_statements(sql):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
    conn.commit()


def _split_sql_statements(sql: str) -> list[str]:
    """Split on statement terminators, respecting BEGIN..END blocks."""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for line in sql.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("BEGIN"):
            depth += 1
        if stripped.startswith("END"):
            depth = max(0, depth - 1)
        buf.append(line)
        if depth == 0 and line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                out.append(stmt.rstrip(";").strip())
            buf = []
    if buf:
        tail = "\n".join(buf).strip()
        if tail:
            out.append(tail.rstrip(";").strip())
    return [s for s in out if s]


def connect(cfg: Config | None = None) -> sqlite3.Connection:
    cfg = cfg or Config.load()
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    schema = _SCHEMA_PATH.read_text()
    conn.executescript(schema)
    if _MIGRATIONS_DIR.exists():
        for m in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            _apply_migration_idempotent(conn, m.read_text())
    current = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()[0]
    if current < SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_version(version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()
    return SCHEMA_VERSION


@contextmanager
def tx(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()
