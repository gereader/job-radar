-- Migration 002: interview rounds per application.
-- Applied idempotently via queries.migrate() using CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS interview_rounds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    round_number    INTEGER NOT NULL,
    kind            TEXT    NOT NULL,
    -- screen|technical|hiring-manager|panel|system-design|take-home|exec|final|other
    scheduled_at    TEXT,
    duration_min    INTEGER,
    interviewer_name  TEXT,
    interviewer_title TEXT,
    interviewer_email TEXT,
    status          TEXT    NOT NULL DEFAULT 'scheduled',
    -- scheduled|completed|cancelled|no-show
    outcome         TEXT,
    -- advance|reject|pending|unknown
    notes           TEXT,
    thank_you_sent_at TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    CHECK (kind IN ('screen','technical','hiring-manager','panel',
                    'system-design','take-home','exec','final','other')),
    CHECK (status IN ('scheduled','completed','cancelled','no-show')),
    CHECK (outcome IS NULL OR outcome IN ('advance','reject','pending','unknown'))
);

CREATE INDEX IF NOT EXISTS idx_rounds_app     ON interview_rounds(application_id);
CREATE INDEX IF NOT EXISTS idx_rounds_status  ON interview_rounds(status);
CREATE INDEX IF NOT EXISTS idx_rounds_sched   ON interview_rounds(scheduled_at);

CREATE TRIGGER IF NOT EXISTS trg_rounds_updated
AFTER UPDATE ON interview_rounds
BEGIN
    UPDATE interview_rounds SET updated_at = datetime('now') WHERE id = NEW.id;
END;
