-- Migration 007: per-application answer cache.
-- One row per (application, question_key) so the "why this company" answer
-- is written once per app and reused by `jr apply` and the cover renderer.

CREATE TABLE IF NOT EXISTS app_answers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    question_key    TEXT    NOT NULL,
    question_text   TEXT,
    answer_md       TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (application_id, question_key)
);

CREATE INDEX IF NOT EXISTS idx_app_answers_app ON app_answers(application_id);
