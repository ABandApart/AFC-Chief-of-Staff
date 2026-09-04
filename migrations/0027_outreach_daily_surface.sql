-- Migration 0027: the #outreach daily surface (Track O, PRD-outreach-daily-surface.md)
--
-- Adds the three columns the Discord daily-contact surface needs on
-- outreach_touches. All additive and idempotent; barry-agent applies.
--
--   * marked_working_at — D-B/R2: the Contact button records intent ("working
--     this today"). Advisory only — it changes no touch state and gates nothing.
--   * snooze_note        — R3: the required note captured when the operator
--     defers a card. Required-on-defer is enforced at the surface (the Discord
--     modal field is mandatory, and _lib.outreach_daily_surface.defer refuses a
--     blank note). It is NOT a DB CHECK: existing snoozes written from NocoDB
--     may legitimately carry no note, and a CHECK would reject them on write.
--   * daily_message_id   — the #outreach card <-> touch bridge, mirroring
--     outreach_targets.intake_message_id, so a card's buttons re-bind after a
--     bot restart and the card can be edited in place.
--
-- The outreach_log_event() AFTER trigger on outreach_touches already diffs
-- changed columns via jsonb_each, so all three are captured in outreach_events
-- automatically — no trigger change needed.
--
-- These columns live on outreach_touches, NOT outreach_targets, so the frozen
-- column list of v_outreach_scored (the DROP+CREATE gotcha) does not apply and
-- no view is rebuilt.

ALTER TABLE outreach_touches
    ADD COLUMN IF NOT EXISTS marked_working_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS snooze_note       TEXT,
    ADD COLUMN IF NOT EXISTS daily_message_id  TEXT;

COMMENT ON COLUMN outreach_touches.marked_working_at IS
    'Contact button intent flag (#outreach surface); advisory, gates nothing.';
COMMENT ON COLUMN outreach_touches.snooze_note IS
    'Required note captured on Defer from the #outreach surface (R3).';
COMMENT ON COLUMN outreach_touches.daily_message_id IS
    'Discord message id of this touch''s #outreach card; bridges the card to the touch.';

-- One live card per touch; lets the poller skip already-carded touches and
-- re-attach the persistent View after a restart.
CREATE UNIQUE INDEX IF NOT EXISTS outreach_touches_daily_msg_idx
    ON outreach_touches (daily_message_id) WHERE daily_message_id IS NOT NULL;
