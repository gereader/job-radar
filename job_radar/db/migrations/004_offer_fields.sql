-- Migration 004: capture offer details per application.

ALTER TABLE applications ADD COLUMN offer_base      INTEGER;
ALTER TABLE applications ADD COLUMN offer_bonus     INTEGER;
ALTER TABLE applications ADD COLUMN offer_equity    TEXT;
ALTER TABLE applications ADD COLUMN offer_currency  TEXT;
ALTER TABLE applications ADD COLUMN offer_start     TEXT;
ALTER TABLE applications ADD COLUMN offer_deadline  TEXT;
ALTER TABLE applications ADD COLUMN offer_notes     TEXT;
