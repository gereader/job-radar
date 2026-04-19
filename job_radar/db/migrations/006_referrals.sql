-- Migration 006: referral tracking.
-- One referring contact per application; surfaced on the dashboard so the
-- user can see which apps came in warm.

ALTER TABLE applications ADD COLUMN referral_contact_id INTEGER
    REFERENCES contacts(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_app_referral
    ON applications(referral_contact_id);
