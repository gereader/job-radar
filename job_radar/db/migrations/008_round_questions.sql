-- Migration 008: question bank captured per interview round.
-- Zero LLM at capture time; future `jr interview` runs can pull
-- same-archetype questions as added context.

CREATE TABLE IF NOT EXISTS round_questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id        INTEGER NOT NULL REFERENCES interview_rounds(id) ON DELETE CASCADE,
    question        TEXT    NOT NULL,
    asked_by        TEXT,
    topic_tags      TEXT,                                   -- comma-separated
    difficulty      INTEGER,                                -- 1..5
    answer_notes    TEXT,
    captured_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_round_questions_round
    ON round_questions(round_id);
CREATE INDEX IF NOT EXISTS idx_round_questions_tags
    ON round_questions(topic_tags);
