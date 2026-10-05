-- 0029: autonomous outreach sourcing (PRD-outreach-autonomous-sourcing.md, I1).
--
-- Numbered 0029, not 0028: 0028 (icp_syntheses) is taken by the unmerged Phase 10
-- branch. Nothing here depends on it.
--
-- What this changes, each tied to a PRD section:
--   1. Segments move from a CHECK list to a table (D8), so a validated hypothesis
--      becomes a segment without a migration (§7.4).
--   2. outreach_hypotheses (§7.1), and candidates outside the current list must
--      carry one (§5) - enforced here, not in a prompt.
--   3. Discoveries carry the agent's dossier: summary, why now, cited evidence,
--      proposed scores, contact method, the run that produced it, and any check
--      failures (§6.5, §6.6).
--   4. R0.5 rewritten (§4 gate 10): a row may be surfaced on two verification
--      kinds as before, OR on evidence from two distinct registrable domains.
--   5. New trigger kinds agent_sourced and hypothesis_test (§4 gate 15).
--   6. Live-sequence cap 15 -> 150 (D1, operator 2026-10-05).
--   7. outreach_sourcing_runs, one row per daily run (§6.3, §6.4, the spend notice).
--   8. v_outreach_hypothesis_results (§7.4).

BEGIN;

-- 1. Segments -----------------------------------------------------------------

CREATE TABLE outreach_hypotheses (
    id                  BIGSERIAL PRIMARY KEY,
    statement           TEXT NOT NULL,
    pattern             TEXT NOT NULL,
    rationale           TEXT,
    rationale_sources   TEXT[] NOT NULL DEFAULT '{}',
    test_plan_contacts  INTEGER NOT NULL DEFAULT 10,
    success_metric      TEXT NOT NULL DEFAULT
        'At least 2 conversations or 1 call booked from the planned contacts, once every arc has finished',
    status              TEXT NOT NULL DEFAULT 'proposed',
    origin              TEXT NOT NULL,
    proposed_segment    TEXT NOT NULL,
    status_changed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    verdict_note        TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT outreach_hypotheses_status_ck CHECK (
        status IN ('proposed', 'testing', 'validated', 'refuted', 'retired')),
    CONSTRAINT outreach_hypotheses_origin_ck CHECK (origin IN ('agent', 'operator')),
    CONSTRAINT outreach_hypotheses_plan_ck CHECK (test_plan_contacts BETWEEN 1 AND 100),
    CONSTRAINT outreach_hypotheses_segment_key_ck CHECK (
        proposed_segment ~ '^[a-z][a-z0-9_]{2,60}$')
);

CREATE TABLE outreach_segments (
    key             TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    in_list         BOOLEAN NOT NULL,
    origin          TEXT NOT NULL,
    hypothesis_id   BIGINT REFERENCES outreach_hypotheses(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT outreach_segments_key_ck CHECK (key ~ '^[a-z][a-z0-9_]{2,60}$'),
    CONSTRAINT outreach_segments_origin_ck CHECK (origin IN ('workbook', 'hypothesis')),
    -- A segment born from a hypothesis names it; a workbook segment never does.
    CONSTRAINT outreach_segments_origin_link_ck CHECK (
        (origin = 'hypothesis') = (hypothesis_id IS NOT NULL))
);

INSERT INTO outreach_segments (key, label, in_list, origin) VALUES
    ('corporate_l_and_d',       'Corporate L&D',            true, 'workbook'),
    ('coaching_leadership',     'Coaching and leadership',  true, 'workbook'),
    ('instructional_design',    'Instructional design',     true, 'workbook'),
    ('engineering_consultancy', 'Engineering consultancy',  true, 'workbook'),
    ('product_design_agency',   'Product design agency',    true, 'workbook'),
    ('msp_it_consultancy',      'MSP / IT consultancy',     true, 'workbook');

ALTER TABLE outreach_discoveries DROP CONSTRAINT outreach_discoveries_segment_ck;
ALTER TABLE outreach_discoveries
    ADD CONSTRAINT outreach_discoveries_segment_fk
    FOREIGN KEY (segment) REFERENCES outreach_segments(key);

ALTER TABLE outreach_segment_scores DROP CONSTRAINT outreach_segment_scores_segment_ck;
ALTER TABLE outreach_segment_scores
    ADD CONSTRAINT outreach_segment_scores_segment_fk
    FOREIGN KEY (segment) REFERENCES outreach_segments(key);

-- 2-3. The dossier on a discovery --------------------------------------------

ALTER TABLE outreach_discoveries
    ADD COLUMN hypothesis_id      BIGINT REFERENCES outreach_hypotheses(id),
    ADD COLUMN summary            TEXT,
    ADD COLUMN why_now            TEXT,
    ADD COLUMN evidence           JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN evidence_domains   TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN proposed_scores    JSONB,
    ADD COLUMN contact_method     TEXT,
    ADD COLUMN sourcing_run_id    TEXT,
    ADD COLUMN check_failures     TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN suggested_angle    TEXT;

ALTER TABLE outreach_discoveries
    ADD CONSTRAINT outreach_discoveries_contact_method_ck CHECK (
        contact_method IS NULL OR contact_method IN (
            'published', 'apollo', 'pattern_inferred', 'generic_inbox', 'none_found',
            'operator')),
    ADD CONSTRAINT outreach_discoveries_evidence_array_ck CHECK (
        jsonb_typeof(evidence) = 'array');

-- §5: outside the current list means "must carry a hypothesis". A CHECK cannot
-- read outreach_segments, so a trigger enforces it; the rule stays in the DB.
CREATE FUNCTION outreach_discoveries_require_hypothesis() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.hypothesis_id IS NULL AND NOT EXISTS (
        SELECT 1 FROM outreach_segments s WHERE s.key = NEW.segment AND s.in_list
    ) THEN
        RAISE EXCEPTION
            'segment % is outside the current candidate list; a hypothesis_id is required',
            NEW.segment
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER outreach_discoveries_hypothesis
    BEFORE INSERT OR UPDATE OF segment, hypothesis_id ON outreach_discoveries
    FOR EACH ROW EXECUTE FUNCTION outreach_discoveries_require_hypothesis();

-- 4. R0.5, rewritten -----------------------------------------------------------

CREATE FUNCTION outreach_distinct_count(TEXT[]) RETURNS INTEGER
LANGUAGE sql IMMUTABLE AS $$
    SELECT count(DISTINCT lower(x))::INTEGER FROM unnest($1) AS x WHERE x <> ''
$$;

ALTER TABLE outreach_discoveries DROP CONSTRAINT outreach_discoveries_verified_ck;
ALTER TABLE outreach_discoveries
    ADD CONSTRAINT outreach_discoveries_verified_ck CHECK (
        surfaced_at IS NULL
        OR COALESCE(array_length(verified_on, 1), 0) >= 2
        OR outreach_distinct_count(evidence_domains) >= 2
    );

-- 5. Trigger kinds and the hypothesis on a target ------------------------------

ALTER TABLE outreach_targets DROP CONSTRAINT outreach_targets_trigger_kind_ck;
ALTER TABLE outreach_targets
    ADD CONSTRAINT outreach_targets_trigger_kind_ck CHECK (
        trigger_kind = ANY (ARRAY[
            'executive_departure', 'request_open_past_45_days', 'new_executive_hire',
            'second_raise', 'funding_announced', 'restructuring_or_layoffs',
            'market_or_region_expansion', 'product_launch', 'inbound_enquiry',
            'operator_selected', 'agent_sourced', 'hypothesis_test'
        ])
    );

ALTER TABLE outreach_targets
    ADD COLUMN hypothesis_id BIGINT REFERENCES outreach_hypotheses(id);

-- A hypothesis test names its hypothesis.
ALTER TABLE outreach_targets
    ADD CONSTRAINT outreach_targets_hypothesis_ck CHECK (
        trigger_kind <> 'hypothesis_test' OR hypothesis_id IS NOT NULL);

-- 6. Capacity: 150 live cold sequences (about 20 sends a day) ------------------

CREATE OR REPLACE VIEW v_outreach_capacity AS
SELECT count(*) FILTER (WHERE NOT is_reengagement) AS cold_live,
       count(*) FILTER (WHERE is_reengagement)     AS reengagement_live,
       150 AS cold_ceiling,
       3   AS reengagement_ceiling
FROM outreach_targets
WHERE status = ANY (ARRAY['in_sequence', 'conversation', 'call_booked']);

-- 7. One row per sourcing run ---------------------------------------------------

CREATE TABLE outreach_sourcing_runs (
    run_id          TEXT PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    target_n        INTEGER NOT NULL,
    researched      INTEGER NOT NULL DEFAULT 0,
    passed          INTEGER NOT NULL DEFAULT 0,
    failed_checks   INTEGER NOT NULL DEFAULT 0,
    usd_cost        NUMERIC(14, 8) NOT NULL DEFAULT 0,
    stop_reason     TEXT,
    dry_run         BOOLEAN NOT NULL DEFAULT false,
    notes           TEXT,
    CONSTRAINT outreach_sourcing_runs_stop_ck CHECK (
        stop_reason IS NULL OR stop_reason IN (
            'target_met', 'budget', 'deadline', 'no_briefs', 'credit', 'error')),
    CONSTRAINT outreach_sourcing_runs_n_ck CHECK (target_n BETWEEN 1 AND 100)
);

-- 8. Hypothesis results ----------------------------------------------------------

CREATE VIEW v_outreach_hypothesis_results AS
SELECT h.id AS hypothesis_id,
       h.status,
       h.statement,
       h.proposed_segment,
       h.test_plan_contacts,
       (SELECT count(*) FROM outreach_discoveries d
         WHERE d.hypothesis_id = h.id AND d.surfaced_at IS NOT NULL) AS surfaced,
       (SELECT count(*) FROM outreach_discoveries d
         WHERE d.hypothesis_id = h.id AND d.review_decision = 'accept') AS approved,
       (SELECT count(*) FROM outreach_discoveries d
         WHERE d.hypothesis_id = h.id AND d.review_decision = 'reject') AS rejected,
       (SELECT count(*) FROM outreach_touches x JOIN outreach_targets t ON t.id = x.target_id
         WHERE t.hypothesis_id = h.id AND x.sent_at IS NOT NULL) AS touches_sent,
       (SELECT count(DISTINCT x.target_id) FROM outreach_touches x
          JOIN outreach_targets t ON t.id = x.target_id
         WHERE t.hypothesis_id = h.id AND x.replied_at IS NOT NULL) AS targets_replied,
       (SELECT count(*) FROM outreach_targets t
         WHERE t.hypothesis_id = h.id AND t.status IN ('conversation', 'call_booked'))
           AS conversations,
       (SELECT count(*) FROM outreach_targets t
         WHERE t.hypothesis_id = h.id AND t.status = 'call_booked') AS calls_booked,
       (SELECT count(*) FROM outreach_targets t
         WHERE t.hypothesis_id = h.id AND t.status = 'in_sequence') AS arcs_open
FROM outreach_hypotheses h;

-- Audit + updated_at, as every other operator-editable outreach table.
CREATE TRIGGER outreach_hypotheses_audit
    AFTER INSERT OR UPDATE OR DELETE ON outreach_hypotheses
    FOR EACH ROW EXECUTE FUNCTION outreach_log_event();
CREATE TRIGGER outreach_hypotheses_touch
    BEFORE UPDATE ON outreach_hypotheses
    FOR EACH ROW EXECUTE FUNCTION outreach_touch_updated_at();
CREATE TRIGGER outreach_segments_audit
    AFTER INSERT OR UPDATE OR DELETE ON outreach_segments
    FOR EACH ROW EXECUTE FUNCTION outreach_log_event();

-- The runtime app connects as barry_agent; forgetting this makes writes fail
-- silently (the bug 0011 fixed).
ALTER TABLE outreach_hypotheses OWNER TO barry_agent;
ALTER SEQUENCE outreach_hypotheses_id_seq OWNER TO barry_agent;
ALTER TABLE outreach_segments OWNER TO barry_agent;
ALTER TABLE outreach_sourcing_runs OWNER TO barry_agent;
ALTER VIEW v_outreach_capacity OWNER TO barry_agent;
ALTER VIEW v_outreach_hypothesis_results OWNER TO barry_agent;
ALTER FUNCTION outreach_discoveries_require_hypothesis() OWNER TO barry_agent;
ALTER FUNCTION outreach_distinct_count(TEXT[]) OWNER TO barry_agent;

COMMIT;
