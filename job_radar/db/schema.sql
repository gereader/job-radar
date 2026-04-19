-- job-radar SQLite schema.
-- Canonical states mirror career-ops/templates/states.yml.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- jobs: one row per unique JD, identified by content hash.
CREATE TABLE IF NOT EXISTS jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    hash           TEXT    NOT NULL UNIQUE,
    source         TEXT    NOT NULL,              -- greenhouse|ashby|lever|workable|linkedin|manual
    source_id      TEXT,                          -- portal-native id if available
    company        TEXT    NOT NULL,
    title          TEXT    NOT NULL,
    location       TEXT,
    remote         TEXT,                          -- remote|hybrid|onsite|unknown
    url            TEXT    NOT NULL,
    comp_min       INTEGER,
    comp_max       INTEGER,
    comp_currency  TEXT,
    posted_at      TEXT,
    fetched_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    jd_path        TEXT    NOT NULL,              -- relative path under private/jds/
    screen_verdict TEXT,                          -- pass|review|skip
    screen_score   INTEGER,
    screen_reasons TEXT,                          -- JSON array
    triage_verdict TEXT,                          -- pass|review|skip|null
    triage_notes   TEXT,
    archived_at    TEXT,
    closed_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_source  ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(screen_verdict, triage_verdict);

-- Reposts: when same (company,title) shows up with a new hash, link them.
CREATE TABLE IF NOT EXISTS reposts (
    original_job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    repost_job_id   INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    detected_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (original_job_id, repost_job_id)
);

-- ---------------------------------------------------------------------------
-- applications: user decided to apply (or is in the funnel).
CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
    status          TEXT    NOT NULL DEFAULT 'Evaluated',
    score           REAL,                         -- 0.0 - 5.0
    archetype       TEXT,
    report_path     TEXT,                         -- private/applications/{id}/report.md
    resume_path     TEXT,                         -- .../resume.md
    resume_pdf_path TEXT,                         -- .../resume.pdf
    cover_path      TEXT,
    cover_pdf_path  TEXT,
    applied_at      TEXT,
    next_action_at  TEXT,
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    CHECK (status IN (
        'SKIP', 'Discarded', 'Rejected', 'Evaluated',
        'Applied', 'Responded', 'Interview', 'Offer'
    )),
    UNIQUE (job_id)
);

CREATE INDEX IF NOT EXISTS idx_applications_status  ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_next    ON applications(next_action_at);

CREATE TRIGGER IF NOT EXISTS trg_applications_updated
AFTER UPDATE ON applications
BEGIN
    UPDATE applications SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- ---------------------------------------------------------------------------
-- contacts: first-class recruiter / hiring manager / peer entries.
CREATE TABLE IF NOT EXISTS contacts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL,
    company        TEXT,
    title          TEXT,
    linkedin_url   TEXT,
    email          TEXT,
    phone          TEXT,
    notes          TEXT,
    first_seen_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (linkedin_url),
    UNIQUE (email)
);

CREATE INDEX IF NOT EXISTS idx_contacts_name    ON contacts(name);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company);

-- ---------------------------------------------------------------------------
-- touchpoints: every outreach event (in or out). Links to app + optional contact.
CREATE TABLE IF NOT EXISTS touchpoints (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER REFERENCES applications(id) ON DELETE CASCADE,
    contact_id      INTEGER REFERENCES contacts(id)     ON DELETE SET NULL,
    channel         TEXT    NOT NULL,               -- linkedin|email|phone|video|in-person|other
    direction       TEXT    NOT NULL,               -- inbound|outbound
    occurred_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    summary         TEXT,
    source_msg_path TEXT,                           -- optional path to saved message / transcript
    CHECK (channel IN ('linkedin', 'email', 'phone', 'video', 'in-person', 'other')),
    CHECK (direction IN ('inbound', 'outbound'))
);

CREATE INDEX IF NOT EXISTS idx_touchpoints_app      ON touchpoints(application_id);
CREATE INDEX IF NOT EXISTS idx_touchpoints_contact  ON touchpoints(contact_id);
CREATE INDEX IF NOT EXISTS idx_touchpoints_occurred ON touchpoints(occurred_at);

-- ---------------------------------------------------------------------------
-- scan_history: keeps URL hashes across scans for cheap dedup.
CREATE TABLE IF NOT EXISTS scan_history (
    url_hash    TEXT    PRIMARY KEY,
    url         TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    first_seen  TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT    NOT NULL DEFAULT (datetime('now')),
    outcome     TEXT    NOT NULL                  -- new|duplicate|stale|screened_out|applied
);

CREATE INDEX IF NOT EXISTS idx_scan_history_outcome ON scan_history(outcome);

-- ---------------------------------------------------------------------------
-- keywords: user-managed scoring rules. Loaded from private/keywords.yml.
CREATE TABLE IF NOT EXISTS keywords (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    term     TEXT    NOT NULL,
    weight   INTEGER NOT NULL DEFAULT 1,
    kind     TEXT    NOT NULL,                     -- positive|negative|dealbreaker
    field    TEXT    NOT NULL DEFAULT 'any',       -- any|title|description|location
    CHECK (kind  IN ('positive', 'negative', 'dealbreaker')),
    CHECK (field IN ('any', 'title', 'description', 'location')),
    UNIQUE (term, kind, field)
);

-- ---------------------------------------------------------------------------
-- archetypes: user's role archetypes (e.g. "Network Automation Eng").
CREATE TABLE IF NOT EXISTS archetypes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    priority    INTEGER NOT NULL DEFAULT 0,        -- higher = prefer
    description TEXT
);

CREATE TABLE IF NOT EXISTS job_archetypes (
    job_id       INTEGER NOT NULL REFERENCES jobs(id)        ON DELETE CASCADE,
    archetype_id INTEGER NOT NULL REFERENCES archetypes(id)  ON DELETE CASCADE,
    confidence   REAL    NOT NULL DEFAULT 0.0,     -- 0.0 - 1.0
    PRIMARY KEY (job_id, archetype_id)
);

-- ---------------------------------------------------------------------------
-- Cache tables: populate once, reuse across evaluations.
CREATE TABLE IF NOT EXISTS cv_skills (
    skill         TEXT    PRIMARY KEY,
    evidence_json TEXT    NOT NULL,                -- list of {role, project, metric}
    last_indexed_at TEXT  NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS story_bank (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    star_r_json TEXT    NOT NULL,                  -- {situation, task, action, result, reflection}
    tags        TEXT    NOT NULL,                  -- comma-separated
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS comp_cache (
    role        TEXT    NOT NULL,
    company     TEXT    NOT NULL,
    location    TEXT    NOT NULL DEFAULT '',
    median      INTEGER,
    p25         INTEGER,
    p75         INTEGER,
    currency    TEXT    NOT NULL DEFAULT 'USD',
    source      TEXT,
    fetched_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (role, company, location)
);

-- ---------------------------------------------------------------------------
-- LLM usage log: for cost tracking / verification that triage stays cheap.
CREATE TABLE IF NOT EXISTS llm_usage (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    model        TEXT    NOT NULL,
    operation    TEXT    NOT NULL,                 -- triage|evaluate|draft|extract|stories
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    job_id       INTEGER REFERENCES jobs(id)         ON DELETE SET NULL,
    app_id       INTEGER REFERENCES applications(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_occurred ON llm_usage(occurred_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_op       ON llm_usage(operation);
