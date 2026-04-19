-- Migration 003: track Messages Batch API submissions for `jr triage --batch`.

CREATE TABLE IF NOT EXISTS batch_jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id       TEXT    NOT NULL UNIQUE,        -- returned by Anthropic
    operation      TEXT    NOT NULL,               -- triage|...
    model          TEXT    NOT NULL,
    n_requests     INTEGER NOT NULL,
    submitted_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    status         TEXT    NOT NULL DEFAULT 'submitted',
    -- submitted|in_progress|ended|cancelled|expired
    completed_at   TEXT,
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS batch_items (
    batch_id       TEXT    NOT NULL REFERENCES batch_jobs(batch_id) ON DELETE CASCADE,
    custom_id      TEXT    NOT NULL,
    job_id         INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    app_id         INTEGER REFERENCES applications(id) ON DELETE SET NULL,
    PRIMARY KEY (batch_id, custom_id)
);
