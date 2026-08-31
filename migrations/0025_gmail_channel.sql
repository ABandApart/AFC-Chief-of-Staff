-- =============================================================================
-- Migration 0025: the Gmail draft + send-capture channel (Track O send half)
-- =============================================================================
-- `PRD-outreach-gmail-channel.md` §8 step 2. Adds the state the drafting loop and
-- the send-capture poller need, shaped by the V1-V3 probe run against the live
-- Workspace (barry-agent, 2026-08-31):
--
--   V1 FAIL — a custom `X-AIA-Touch` header does NOT survive draft->sent.
--   V2 PASS — `gmail.metadata` scope allows `history.list` (so G2 stays; no
--             `gmail.readonly` reopen).
--   V3      — the draft's `message.id` is reassigned on send.
--
-- => Correlation is **threadId + the BCC token**, NOT the header or the draft id
--    (§4 plan-B). `gmail_thread_id` is therefore the load-bearing correlation
--    column; `gmail_message_id` is stored only as the captured sent-id, never a
--    match key.
--
-- Three parts:
--   1. gmail_* columns on outreach_touches (draft management + correlation).
--   2. gmail_channel_state — a singleton `history_id` watermark for the poller,
--      mirroring channel_state (0007).
--   3. The ready-guard trigger amended per §6: an OBSERVED send (capture paths
--      gmail_api/bcc) records UNCONDITIONALLY -- the mail is already in the
--      prospect's inbox, so refusing the write only loses the record; the
--      not-ready case is surfaced by a Ted alert from the capture code, not
--      blocked here. Assertion paths (a human "mark as sent": shortcut/nocodb)
--      stay guarded. The invariant stays in the DB rather than app code.
--
-- No view rebuild (nothing is `SELECT * FROM outreach_touches`). New table needs
-- OWNER TO barry_agent or the runtime writes fail silently.
--
-- Apply via:  psql aiadaptive_cos -f migrations/0025_gmail_channel.sql
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

-- --- 1. Draft + correlation state on each touch ------------------------------
ALTER TABLE outreach_touches
    ADD COLUMN IF NOT EXISTS gmail_draft_id    TEXT,   -- idempotent draft mgmt
    ADD COLUMN IF NOT EXISTS gmail_thread_id   TEXT,   -- THE correlation key (V1/V3 fallback)
    ADD COLUMN IF NOT EXISTS gmail_message_id  TEXT,   -- captured sent id; not a match key (V3)
    ADD COLUMN IF NOT EXISTS draft_created_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS draft_body_hash   TEXT;   -- update-in-place only while unedited

COMMENT ON COLUMN outreach_touches.gmail_thread_id IS
    'Gmail threadId — the correlation key for send capture. The custom X-AIA-Touch '
    'header does NOT survive draft->sent (V1, 2026-08-31), so a send is matched to '
    'its touch by threadId + the BCC token, never the header or the draft id.';
COMMENT ON COLUMN outreach_touches.draft_body_hash IS
    'Hash of the packet body_filled the draft was created from. The drafting loop '
    'updates a draft in place ONLY when this still matches — a changed hash means '
    'the operator started editing, and their work is never overwritten.';

-- One draft per touch: a partial unique index so re-runs upsert rather than
-- accumulate drafts (idempotency owned by the DB, R0.4/G-R4).
CREATE UNIQUE INDEX IF NOT EXISTS outreach_touches_gmail_draft_idx
    ON outreach_touches (gmail_draft_id) WHERE gmail_draft_id IS NOT NULL;
-- The poller matches an observed send by thread; index the lookup.
CREATE INDEX IF NOT EXISTS outreach_touches_gmail_thread_idx
    ON outreach_touches (gmail_thread_id) WHERE gmail_thread_id IS NOT NULL;

-- --- 2. The history_id watermark (singleton) ---------------------------------
-- Mirrors channel_state (0007). Singleton-guarded: `only_row` is a boolean PK
-- pinned true, so at most one row can ever exist.
CREATE TABLE IF NOT EXISTS gmail_channel_state (
    only_row    BOOLEAN PRIMARY KEY DEFAULT true CHECK (only_row),
    history_id  TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE gmail_channel_state OWNER TO barry_agent;

-- --- 3. Ready-guard trigger amended for observed captures (§6) ----------------
CREATE OR REPLACE FUNCTION outreach_touch_ready_guard()
    RETURNS trigger
    LANGUAGE plpgsql
AS $function$
DECLARE
    v_ready BOOLEAN;
BEGIN
    IF NEW.sent_at IS NOT NULL AND OLD.sent_at IS NULL THEN
        -- §6: a capture path records an OBSERVED send unconditionally — the mail
        -- has already gone, so refusing the write loses the record and leaves the
        -- arc thinking the touch is still due. The not-ready case is surfaced by a
        -- Ted alert from the capture code, not blocked here. Only assertion paths
        -- (a human "mark as sent") stay guarded.
        IF NEW.sent_via IN ('gmail_api', 'bcc') THEN
            RETURN NEW;
        END IF;
        -- `id DESC` breaks the tie when a regeneration wrote several packets in
        -- one transaction (identical `assembled_at` — now() is transaction time).
        SELECT p.ready INTO v_ready
          FROM outreach_packets p
         WHERE p.touch_id = NEW.id
         ORDER BY p.assembled_at DESC, p.id DESC
         LIMIT 1;
        IF v_ready IS NOT NULL AND v_ready = false THEN
            RAISE EXCEPTION
                'touch % cannot be marked sent: its packet is not ready '
                '(unresolved operator slot, or the driving fact is stale)', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

COMMIT;
