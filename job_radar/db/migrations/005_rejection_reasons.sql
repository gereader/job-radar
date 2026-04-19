-- Migration 005: structured rejection-reason capture.
-- Each rejected application can have one or more category-tagged reasons
-- so `jr patterns` can segment rejections sharper than free-text notes.

CREATE TABLE IF NOT EXISTS rejection_reasons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    category        TEXT    NOT NULL,
    -- one of: location | comp | level | stack | culture | timing | fit | other
    detail          TEXT,
    extracted_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    source          TEXT    NOT NULL DEFAULT 'llm',
    -- llm | manual
    CHECK (category IN
        ('location', 'comp', 'level', 'stack', 'culture',
         'timing', 'fit', 'other'))
);

CREATE INDEX IF NOT EXISTS idx_rejreasons_app ON rejection_reasons(application_id);
CREATE INDEX IF NOT EXISTS idx_rejreasons_cat ON rejection_reasons(category);
