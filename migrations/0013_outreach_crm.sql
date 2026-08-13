-- =============================================================================
-- Migration 0013: Outreach CRM schema (Track O, build step 2)
-- =============================================================================
-- The cold-outreach engine's operational state: trigger-driven targets, typed
-- dated evidence, the five-touch schedule, assembled packets, watch signals, and
-- an audit log. Spec: `architecture/35-outreach-crm.md` v0.3.0 §2 (schema), §3
-- (staleness), §4 (scoring), §9 (invariants). Map: `37-outreach-workflow.md` D5.
--
-- (The spec says "migration 0007" / "numbering continues from 0006" — it was
-- written 2026-08-08, before 0007–0012 landed. This is that migration, renumbered.)
--
-- Placement (§1): outreach is **operational state**, so it is plain SQL here in
-- `aiadaptive_cos`. Company/person *background* lives in the cognee graph and is
-- reached through `outreach_targets.cognee_node_id` — a TEXT column joined in app
-- code, never a cross-DB FK.
--
-- **Every invariant is a database constraint or trigger, not application logic**
-- (§2 enforcement note). No bot mediates these writes: NocoDB issues raw UPDATEs
-- and the Shortcut writes through a thin endpoint, so anything the application
-- layer "promises" is unenforced. The six invariants from §9 are implemented as:
--   * skip-requires-reason          → outreach_touches_skip_ck
--   * watchlist-requires-stalled    → outreach_targets_stalled_ck
--   * sent-XOR-skipped              → outreach_touches_sent_xor_skipped_ck
--   * reply-implies-send            → outreach_touches_reply_ck
--   * snooze-cannot-cross-windows   → outreach_touches_snooze_ck
--   * not-ready-cannot-be-sent      → outreach_touch_ready_guard() BEFORE trigger
--     (cross-table: `ready` lives on the packet, so a CHECK cannot see it)
--
-- Three tables here (`outreach_touches`, `outreach_watch_signals`,
-- `outreach_events`) are marked "unchanged from 0.2.0" in the 0.3.0 spec, but
-- 0.2.0's DDL was never committed to this repo — they are designed here from the
-- behaviour the spec states in §5, §8, §9, §10 and R17. Where a choice was not
-- dictated, the reasoning is in a comment on the column.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0013_outreach_crm.sql
-- Verify via:
--   psql aiadaptive_cos -f migrations/verify_schema.sql
--
-- Idempotent: safe to re-run (IF NOT EXISTS / CREATE OR REPLACE throughout).
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Targets — the unit of work: a company in a function state at a moment (§2)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS outreach_targets (
    id                      BIGSERIAL PRIMARY KEY,
    company_name            TEXT NOT NULL,
    company_domain          TEXT NOT NULL,   -- normalized; THE IMPORT DEDUP KEY (§5)
    company_url             TEXT,
    careers_url             TEXT,            -- polled by the evidence loop (§6)
    sector                  TEXT,

    stage                   TEXT,            -- 'seed'|'series_a'|'series_b_plus'
                                             -- NOT NULL for cold targets; see
                                             -- outreach_targets_stage_ck below
    function_state          TEXT,            -- 'self_covered'|'under_led'|'vacant_seat'
                                             -- NULL until the two-tab diagnostic is done

    contact_name            TEXT,
    contact_role            TEXT,
    contact_email           TEXT,
    contact_linkedin_url    TEXT,

    trigger_kind            TEXT NOT NULL,   -- the eight triggers, plus 'inbound_enquiry'
    trigger_date            DATE NOT NULL,   -- the arc anchors HERE
    trigger_source_url      TEXT,

    -- Scoring. S1 is DERIVED (outreach_s1(), below) and deliberately absent here.
    s2_stage_fit            SMALLINT,
    s3_sector_match         SMALLINT,
    s4_leadership_gap       SMALLINT,        -- human-observed, informed by evidence
    s5_team_build_below     SMALLINT,        -- human-observed, informed by evidence
    signals_observed_at     DATE,

    status                  TEXT NOT NULL DEFAULT 'candidate',
                                             -- 'candidate'
                                             -- 'in_sequence'   · COUNTS AGAINST CAP
                                             -- 'conversation'  · COUNTS
                                             -- 'call_booked'   · COUNTS
                                             -- 'engaged' | 'watchlist' | 'dropped'
                                             -- 'lost_to_hire' | 'archived'

    is_reengagement         BOOLEAN NOT NULL DEFAULT false,
    prospect_id             BIGINT REFERENCES prospects(id),   -- inbound — see 36-
    cognee_node_id          TEXT,            -- traversal root for background (§7)

    sequence_started_at     DATE,
    sequence_completed_at   DATE,
    stalled_reason          TEXT,
    watch_trigger           TEXT,
    watch_until             DATE,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT outreach_targets_stalled_ck
        CHECK (status <> 'watchlist' OR stalled_reason IS NOT NULL),
    CONSTRAINT outreach_targets_watch_ck
        CHECK (status NOT IN ('watchlist','lost_to_hire') OR watch_until IS NOT NULL),
    CONSTRAINT outreach_targets_scores_ck
        CHECK (COALESCE(s2_stage_fit,1) IN (1,3,5)
           AND COALESCE(s3_sector_match,1) IN (1,3,5)
           AND COALESCE(s4_leadership_gap,1) IN (1,3,5)
           AND COALESCE(s5_team_build_below,1) IN (1,3,5)),
    CONSTRAINT outreach_targets_seq_ck
        CHECK (status <> 'in_sequence' OR (function_state IS NOT NULL
                                           AND sequence_started_at IS NOT NULL
                                           AND stage IS NOT NULL)),
    -- DEVIATION from 35- §2, which has `stage NOT NULL`. An inbound lead
    -- (`36-inbound-leads.md` I2/D2: Roy Kent creates the row on a high-fit
    -- WordPress submission) has no knowable funding stage — a scorecard does not
    -- report one. NOT NULL would force a fabricated value into the column S2
    -- scores on and the Selector grid keys on, which is worse than absent: a
    -- wrong stage scores silently, a missing one does not score at all.
    -- So stage is required for every COLD target and optional for inbound, and
    -- `outreach_targets_seq_ck` above makes it mandatory again before anything
    -- can enter a sequence — which is the only place it is actually consumed.
    CONSTRAINT outreach_targets_stage_ck
        CHECK (stage IS NOT NULL OR trigger_kind = 'inbound_enquiry')
);

CREATE UNIQUE INDEX IF NOT EXISTS outreach_targets_domain_idx ON outreach_targets (company_domain);
CREATE INDEX IF NOT EXISTS outreach_targets_status_idx        ON outreach_targets (status, trigger_date DESC);
CREATE INDEX IF NOT EXISTS outreach_targets_watch_idx         ON outreach_targets (watch_until)
    WHERE status IN ('watchlist','lost_to_hire');
-- The evidence poller's work queue: targets with a careers page still worth polling.
CREATE INDEX IF NOT EXISTS outreach_targets_careers_idx       ON outreach_targets (careers_url)
    WHERE careers_url IS NOT NULL AND status NOT IN ('archived','dropped','engaged');

-- -----------------------------------------------------------------------------
-- Evidence — typed, sourced, DATED facts (§2). first_seen_at is the datum the
-- whole method depends on and the one no provider reliably sells (§6).
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS outreach_evidence (
    id              BIGSERIAL PRIMARY KEY,
    target_id       BIGINT NOT NULL REFERENCES outreach_targets(id) ON DELETE CASCADE,

    fact_kind       TEXT NOT NULL,   -- 'open_role'          feeds S4, T10, the arithmetic
                                     -- 'leadership_member'  feeds function_state, S4
                                     -- 'ic_hire'            feeds S5, the compound signal
                                     -- 'funding_round'      feeds trigger_date, T2, T12
                                     -- 'stated_use_of_funds' feeds T12, T21
                                     -- 'expansion' | 'departure' | 'headcount'
    payload         JSONB NOT NULL,  -- typed per fact_kind. SHORT BOUNDED FIELDS ONLY (§11 H1)

    source_kind     TEXT NOT NULL,   -- 'careers_page'|'theirstack'|'crunchbase'|'apollo'
                                     -- |'rss'|'manual'|'granola'
    source_url      TEXT,
    source_excerpt  TEXT,            -- verbatim, ≤500 chars, for provenance display

    first_seen_at   DATE NOT NULL,   -- PROPRIETARY — only longitudinal observation creates it
    last_seen_at    DATE NOT NULL,   -- refreshed every confirming poll
    closed_at       DATE,            -- when it stopped appearing
    dedup_key       TEXT NOT NULL,   -- stable identity across polls

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (target_id, fact_kind, dedup_key),
    CONSTRAINT outreach_evidence_dates_ck CHECK (last_seen_at >= first_seen_at),
    CONSTRAINT outreach_evidence_excerpt_ck CHECK (length(source_excerpt) <= 500)
);

CREATE INDEX IF NOT EXISTS outreach_evidence_target_idx ON outreach_evidence (target_id, fact_kind, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS outreach_evidence_open_idx   ON outreach_evidence (target_id, first_seen_at)
    WHERE closed_at IS NULL;

-- -----------------------------------------------------------------------------
-- Touches — the five-slot arc, anchored on trigger_date (§5 slot table, §8)
-- -----------------------------------------------------------------------------
-- DESIGNED HERE (see header). Field set derives from: §5 (five slots with
-- window_opens/due_date/window_closes from the Selector; slots already closed at
-- admission are created pre-skipped so the completion metric is not poisoned),
-- §8 (bcc_token per touch, send capture, reply at TOUCH grain, sent_via
-- provenance), §9 (snooze never shifts other slots and may not exceed
-- window_closes; on-schedule is window-based, not due_date-based).

CREATE TABLE IF NOT EXISTS outreach_touches (
    id              BIGSERIAL PRIMARY KEY,
    target_id       BIGINT NOT NULL REFERENCES outreach_targets(id) ON DELETE CASCADE,
    slot            SMALLINT NOT NULL,       -- 1..5 (Recognition → Close the loop)
    template_code   TEXT NOT NULL,           -- deterministic Selector lookup (§5)

    window_opens    DATE NOT NULL,
    due_date        DATE NOT NULL,
    window_closes   DATE NOT NULL,
    snoozed_until   DATE,                    -- never shifts other slots (§9)

    -- Token-exact BCC matching (§8). UNIQUE is what makes the IMAP matcher
    -- idempotent — a second match on an already-sent row is a no-op (R18).
    bcc_token       TEXT NOT NULL UNIQUE,

    sent_at         TIMESTAMPTZ,
    sent_body       TEXT,                    -- verbatim, BCC-captured
    sent_via        TEXT,                    -- 'bcc'|'shortcut'|'nocodb'
    skipped_at      TIMESTAMPTZ,
    skip_reason     TEXT,
    replied_at      TIMESTAMPTZ,
    reply_kind      TEXT,                    -- recorded at touch grain (§8)

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (target_id, slot),
    CONSTRAINT outreach_touches_slot_ck   CHECK (slot BETWEEN 1 AND 5),
    CONSTRAINT outreach_touches_window_ck CHECK (window_opens <= due_date AND due_date <= window_closes),
    -- §9 invariants ------------------------------------------------------------
    CONSTRAINT outreach_touches_skip_ck
        CHECK (skipped_at IS NULL OR skip_reason IS NOT NULL),
    CONSTRAINT outreach_touches_sent_xor_skipped_ck
        CHECK (sent_at IS NULL OR skipped_at IS NULL),
    CONSTRAINT outreach_touches_reply_ck
        CHECK (replied_at IS NULL OR sent_at IS NOT NULL),
    CONSTRAINT outreach_touches_snooze_ck
        CHECK (snoozed_until IS NULL OR snoozed_until <= window_closes),
    -- A send always carries its provenance, so touch-of-first-reply stays trustworthy.
    CONSTRAINT outreach_touches_sent_via_ck
        CHECK (sent_at IS NULL OR sent_via IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS outreach_touches_due_idx ON outreach_touches (due_date)
    WHERE sent_at IS NULL AND skipped_at IS NULL;
CREATE INDEX IF NOT EXISTS outreach_touches_target_idx ON outreach_touches (target_id, slot);

-- -----------------------------------------------------------------------------
-- Packets — the assembled work payload. NO GENERATED CONTENT (§7, v0.3.0)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS outreach_packets (
    id                  BIGSERIAL PRIMARY KEY,
    touch_id            BIGINT NOT NULL REFERENCES outreach_touches(id) ON DELETE CASCADE,
    assembled_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject_line        TEXT NOT NULL,
    body_filled         TEXT NOT NULL,    -- template with `auto` placeholders substituted;
                                          -- `observed` and `operator` slots left OPEN
    evidence_ids        BIGINT[] NOT NULL,-- the typed facts shown, in display order
    arithmetic          JSONB NOT NULL,   -- precomputed: posting age, search-window math
    staleness_days      INTEGER,          -- age of the OLDEST displayed fact (R19)
    unresolved_slots    TEXT[],           -- `operator` placeholders awaiting the human
    failure_mode        TEXT NOT NULL,    -- verbatim from the template index
    ready               BOOLEAN NOT NULL DEFAULT false
);

-- Packets are regenerated, never edited (§14) — the newest per touch is the live
-- one. `id DESC` is part of the ordering, not decoration: `assembled_at` defaults
-- to now(), which is *transaction* time, so a regeneration that writes two packets
-- for one touch in a single transaction gives them identical timestamps. Ordering
-- on the timestamp alone would then pick between them arbitrarily — and the
-- ready-guard below reads "the newest packet" to decide whether a send is allowed.
DROP INDEX IF EXISTS outreach_packets_touch_idx;
CREATE INDEX IF NOT EXISTS outreach_packets_touch_idx
    ON outreach_packets (touch_id, assembled_at DESC, id DESC);

-- -----------------------------------------------------------------------------
-- Watch signals — Trent Crimm's detection + classification queue (§10)
-- -----------------------------------------------------------------------------
-- DESIGNED HERE (see header). Fields derive from `40-action-layer.md`'s
-- Trent_Crimm spec: the evidence poller writes detections; Trent Crimm classifies
-- each with a forced tool call `{trigger_kind, confidence, rationale}` and writes
-- classified_as/confidence/surfaced_at; only items matching the target's
-- `watch_trigger` or classified `executive_departure` surface as cards —
-- everything else is stored, never shown.

CREATE TABLE IF NOT EXISTS outreach_watch_signals (
    id                  BIGSERIAL PRIMARY KEY,
    target_id           BIGINT NOT NULL REFERENCES outreach_targets(id) ON DELETE CASCADE,

    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_kind         TEXT NOT NULL,   -- 'careers_page'|'rss'|'manual' …
    source_url          TEXT,
    excerpt             TEXT,            -- ≤500 chars (H1 — bounded, never a page dump)
    dedup_key           TEXT NOT NULL,   -- stable identity so a re-poll re-detects once

    classified_as       TEXT,            -- a trigger kind | 'executive_departure' | 'none'
    confidence          REAL,
    rationale           TEXT,
    classified_at       TIMESTAMPTZ,

    surfaced_at         TIMESTAMPTZ,     -- when the Task Tinder card was posted
    discord_message_id  TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (target_id, dedup_key),
    CONSTRAINT outreach_watch_signals_excerpt_ck
        CHECK (length(excerpt) <= 500),
    CONSTRAINT outreach_watch_signals_conf_ck
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);

-- Trent Crimm's queue: detected but not yet classified.
CREATE INDEX IF NOT EXISTS outreach_watch_signals_unclassified_idx
    ON outreach_watch_signals (detected_at) WHERE classified_at IS NULL;

-- -----------------------------------------------------------------------------
-- Events — the audit log, written by TRIGGER so NocoDB's raw UPDATEs are caught
-- -----------------------------------------------------------------------------
-- DESIGNED HERE (see header). R17 + §9: with no bot mediating writes, audit
-- history cannot be application-level. `actor` is `session_user`, which is what
-- distinguishes a NocoDB grid edit from an agent write (`50-channel-layer.md`).

CREATE TABLE IF NOT EXISTS outreach_events (
    id              BIGSERIAL PRIMARY KEY,
    entity_table    TEXT NOT NULL,          -- 'outreach_targets'|'outreach_touches'|'outreach_evidence'
    entity_id       BIGINT NOT NULL,
    op              TEXT NOT NULL,          -- 'INSERT'|'UPDATE'|'DELETE'
    actor           TEXT NOT NULL,          -- session_user — the NocoDB-vs-agent discriminator
    changed         JSONB NOT NULL,         -- UPDATE: {col: {from, to}} for changed cols only
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS outreach_events_entity_idx ON outreach_events (entity_table, entity_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS outreach_events_time_idx   ON outreach_events (occurred_at DESC);

CREATE OR REPLACE FUNCTION outreach_log_event() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_entity_id BIGINT;
    v_changed   JSONB;
    v_old       JSONB;
    v_new       JSONB;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_entity_id := OLD.id;
        v_changed   := jsonb_build_object('deleted', to_jsonb(OLD));
    ELSIF TG_OP = 'INSERT' THEN
        v_entity_id := NEW.id;
        v_changed   := jsonb_build_object('inserted', to_jsonb(NEW));
    ELSE
        v_entity_id := NEW.id;
        v_old := to_jsonb(OLD);
        v_new := to_jsonb(NEW);
        -- Changed columns only. `updated_at` is excluded: it changes on every
        -- write by definition, and logging it would bury the real diff.
        SELECT jsonb_object_agg(key, jsonb_build_object('from', v_old -> key, 'to', v_new -> key))
          INTO v_changed
          FROM jsonb_each(v_new)
         WHERE key <> 'updated_at'
           AND v_new -> key IS DISTINCT FROM v_old -> key;
        -- A no-op UPDATE (or one touching only updated_at) is not history.
        IF v_changed IS NULL THEN
            RETURN NULL;
        END IF;
    END IF;

    INSERT INTO outreach_events (entity_table, entity_id, op, actor, changed)
    VALUES (TG_TABLE_NAME, v_entity_id, TG_OP, session_user, v_changed);
    RETURN NULL;   -- AFTER trigger: return value is ignored
END;
$$;

-- Keep `updated_at` honest without the app having to remember (and without
-- polluting the audit diff — the function above skips the column).
CREATE OR REPLACE FUNCTION outreach_touch_updated_at() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- not-ready-cannot-be-sent (§7, §9). Cross-table, so it cannot be a CHECK: the
-- `ready` flag lives on the touch's newest packet. Blocks the transition to sent
-- on EVERY path — Shortcut endpoint, NocoDB raw UPDATE, and the BCC matcher.
--
-- A touch with no packet at all is NOT blocked: that is a send made outside the
-- assembly path (a LinkedIn send logged from the phone before the 05:45 run),
-- and refusing to record history that already happened would lose the send
-- rather than prevent it. The guard exists to stop an *unready* packet going
-- out, not to stop the operator logging reality.
CREATE OR REPLACE FUNCTION outreach_touch_ready_guard() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_ready BOOLEAN;
BEGIN
    IF NEW.sent_at IS NOT NULL AND OLD.sent_at IS NULL THEN
        -- `id DESC` breaks the tie when a regeneration wrote several packets in
        -- one transaction (identical `assembled_at` — now() is transaction time).
        -- Without it this picks arbitrarily and can block a send whose current
        -- packet is ready.
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
$$;

-- Triggers (dropped first so re-running the migration re-creates them cleanly).
DROP TRIGGER IF EXISTS outreach_targets_audit   ON outreach_targets;
DROP TRIGGER IF EXISTS outreach_touches_audit   ON outreach_touches;
DROP TRIGGER IF EXISTS outreach_evidence_audit  ON outreach_evidence;
DROP TRIGGER IF EXISTS outreach_targets_touch   ON outreach_targets;
DROP TRIGGER IF EXISTS outreach_touches_touch   ON outreach_touches;
DROP TRIGGER IF EXISTS outreach_touches_ready   ON outreach_touches;

CREATE TRIGGER outreach_targets_audit
    AFTER INSERT OR UPDATE OR DELETE ON outreach_targets
    FOR EACH ROW EXECUTE FUNCTION outreach_log_event();

CREATE TRIGGER outreach_touches_audit
    AFTER INSERT OR UPDATE OR DELETE ON outreach_touches
    FOR EACH ROW EXECUTE FUNCTION outreach_log_event();

-- §2: "Add the audit trigger to outreach_evidence as well, so a corrected fact
-- is traceable."
CREATE TRIGGER outreach_evidence_audit
    AFTER INSERT OR UPDATE OR DELETE ON outreach_evidence
    FOR EACH ROW EXECUTE FUNCTION outreach_log_event();

CREATE TRIGGER outreach_targets_touch
    BEFORE UPDATE ON outreach_targets
    FOR EACH ROW EXECUTE FUNCTION outreach_touch_updated_at();

CREATE TRIGGER outreach_touches_touch
    BEFORE UPDATE ON outreach_touches
    FOR EACH ROW EXECUTE FUNCTION outreach_touch_updated_at();

CREATE TRIGGER outreach_touches_ready
    BEFORE UPDATE ON outreach_touches
    FOR EACH ROW EXECUTE FUNCTION outreach_touch_ready_guard();

-- -----------------------------------------------------------------------------
-- S1 — trigger recency (§4). THE AUTHORITATIVE definition of the bands (R6):
-- the playbook prose does not restate them.
-- -----------------------------------------------------------------------------
-- STABLE, not a generated column: Postgres requires generated-column expressions
-- to be IMMUTABLE and S1 depends on CURRENT_DATE.

CREATE OR REPLACE FUNCTION outreach_s1(p_trigger_date DATE, p_asof DATE DEFAULT CURRENT_DATE)
RETURNS SMALLINT LANGUAGE sql STABLE AS $$
  -- Bands are NON-MONOTONIC by design: 5 → 3 → 5 → 1. The second 5 is the day-60
  -- hinge — the touch-five window, the highest-converting moment in the method.
  -- Widened from "crossing day 60" to 58–68 so a weekly sweep cannot step over it.
  SELECT (CASE
    WHEN p_asof - p_trigger_date <= 14             THEN 5
    WHEN p_asof - p_trigger_date BETWEEN 58 AND 68 THEN 5   -- THE HINGE
    WHEN p_asof - p_trigger_date > 90              THEN 1
    ELSE 3
  END)::SMALLINT;
$$;

-- -----------------------------------------------------------------------------
-- Views
-- -----------------------------------------------------------------------------

-- Freshness tiers (§3). Display rules the packet enforces: fresh ≤7d cite
-- freely · ageing 8–14 amber · stale >14 struck through AND excluded from the
-- arithmetic · closed = history only, never a live signal.
CREATE OR REPLACE VIEW v_outreach_evidence_display AS
SELECT e.*,
       CURRENT_DATE - e.last_seen_at        AS days_since_confirmed,
       CURRENT_DATE - e.first_seen_at       AS age_days,
       CASE WHEN e.closed_at IS NOT NULL              THEN 'closed'
            WHEN CURRENT_DATE - e.last_seen_at > 14   THEN 'stale'
            WHEN CURRENT_DATE - e.last_seen_at > 7    THEN 'ageing'
            ELSE 'fresh' END                AS freshness
FROM outreach_evidence e;

-- The scored view (§4). Score is NULL unless all of S2–S5 are set — a partial
-- rubric must not read as a low score.
--
-- Two derivations the spec describes in prose rather than SQL:
--   * `compound_signal` — the §5 card's ⚡ marker. Both human-judged signals at
--     their top band: a leadership gap AND hiring below it (the "three AEs hired,
--     no VP Revenue on the leadership list" shape from §6).
--   * `signals_stale` — S4/S5 are human-judged on a 30-day cadence (§4 table),
--     so a judgement older than that is due for the re-check card.
CREATE OR REPLACE VIEW v_outreach_scored AS
SELECT t.*,
       outreach_s1(t.trigger_date)              AS s1_trigger_recency,
       CURRENT_DATE - t.trigger_date            AS days_since_trigger,
       CASE WHEN t.s2_stage_fit IS NULL OR t.s3_sector_match IS NULL
              OR t.s4_leadership_gap IS NULL OR t.s5_team_build_below IS NULL
            THEN NULL
            ELSE outreach_s1(t.trigger_date) + t.s2_stage_fit + t.s3_sector_match
                 + t.s4_leadership_gap + t.s5_team_build_below
       END                                      AS score,
       CASE WHEN t.s2_stage_fit IS NULL OR t.s3_sector_match IS NULL
              OR t.s4_leadership_gap IS NULL OR t.s5_team_build_below IS NULL
            THEN NULL
            WHEN outreach_s1(t.trigger_date) + t.s2_stage_fit + t.s3_sector_match
                 + t.s4_leadership_gap + t.s5_team_build_below >= 20 THEN 'work'
            WHEN outreach_s1(t.trigger_date) + t.s2_stage_fit + t.s3_sector_match
                 + t.s4_leadership_gap + t.s5_team_build_below >= 14 THEN 'watch'
            ELSE 'drop'
       END                                      AS treatment,
       (t.s4_leadership_gap = 5 AND t.s5_team_build_below = 5)
                                                AS compound_signal,
       (t.signals_observed_at IS NULL
        OR t.signals_observed_at < CURRENT_DATE - 30)
                                                AS signals_stale
FROM outreach_targets t;

-- Capacity (§8). Three statuses consume a slot, not one — a booked call still
-- occupies attention, which is what the cap actually meters. Cold ceiling 15;
-- the E1 re-engagement allowance of 3 rides above it.
CREATE OR REPLACE VIEW v_outreach_capacity AS
SELECT
    count(*) FILTER (WHERE NOT is_reengagement)         AS cold_live,
    count(*) FILTER (WHERE is_reengagement)             AS reengagement_live,
    15                                                  AS cold_ceiling,
    3                                                   AS reengagement_ceiling
FROM outreach_targets
WHERE status IN ('in_sequence','conversation','call_booked');

-- -----------------------------------------------------------------------------
-- Ownership — the runtime app connects as `barry_agent`; migrations run as the
-- socket superuser. Without this the app cannot write (the bug migration 0011
-- fixed for tool_invocations, where audit rows failed silently).
-- -----------------------------------------------------------------------------

ALTER TABLE outreach_targets       OWNER TO barry_agent;
ALTER TABLE outreach_evidence      OWNER TO barry_agent;
ALTER TABLE outreach_touches       OWNER TO barry_agent;
ALTER TABLE outreach_packets       OWNER TO barry_agent;
ALTER TABLE outreach_watch_signals OWNER TO barry_agent;
ALTER TABLE outreach_events        OWNER TO barry_agent;

ALTER VIEW v_outreach_evidence_display OWNER TO barry_agent;
ALTER VIEW v_outreach_scored           OWNER TO barry_agent;
ALTER VIEW v_outreach_capacity         OWNER TO barry_agent;

ALTER FUNCTION outreach_s1(DATE, DATE)        OWNER TO barry_agent;
ALTER FUNCTION outreach_log_event()           OWNER TO barry_agent;
ALTER FUNCTION outreach_touch_updated_at()    OWNER TO barry_agent;
ALTER FUNCTION outreach_touch_ready_guard()   OWNER TO barry_agent;

COMMIT;

-- =============================================================================
-- End of migration 0013
-- =============================================================================
