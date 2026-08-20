-- =============================================================================
-- Migration 0018: outreach_discoveries — the Gate 0 pool (Track O, Part 0)
-- =============================================================================
-- The top of the funnel. `35-` §5 intake assumes a populated funnel and nothing
-- populated it: all 14 targets were typed in by hand from the operator workbook.
-- This table holds firms that have been FOUND and VERIFIED but not yet judged.
--
-- Spec: `PRD-outreach-company-profile.md` Part 0 (rev 2.2).
--
-- WHY A SEPARATE TABLE RATHER THAN A STATUS ON outreach_targets (R0.2)
-- --------------------------------------------------------------------------
-- `outreach_targets.trigger_kind` and `.trigger_date` are NOT NULL, and a
-- discovered firm has no trigger. Relaxing those two to admit unreviewed rows
-- would reopen precisely the failure already sitting in production: all 14
-- targets carry the identical `trigger_date` 2026-06-10 across seven different
-- trigger kinds, because the workbook's "Date Added" column was imported into a
-- field that means "when the trigger happened". That fabricated date silently
-- drove every S1 score. A discovery therefore gets NO trigger until one is
-- observed (R0.3), and promotion to outreach_targets requires a real one.
--
-- The second reason is that a discovery carries fields a target never will: the
-- review decision, the reject reason, the sourcing channel, and the ICP score
-- with the model version that produced it.
--
-- A useful side effect: because this is a new table rather than a column on
-- outreach_targets, it does NOT force the `v_outreach_scored` drop-and-recreate
-- that 0016 needed (the view is `SELECT t.*` and its column list is frozen at
-- creation). verify_schema.sql's owner check matches `outreach%`, so this table
-- is covered there automatically.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0018_outreach_discoveries.sql
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS outreach_discoveries (
    id                      BIGSERIAL PRIMARY KEY,

    -- Identity. Domain is the dedup key, here and in outreach_targets (R0.10):
    -- company names collide and rebrand, domains do not.
    company_name            TEXT NOT NULL,
    company_domain          TEXT NOT NULL,
    company_url             TEXT,
    careers_url             TEXT,

    -- Market position. `segment` is CHECK-pinned below on the 0014 precedent,
    -- which pinned trigger_kind after the spec referenced "the eight triggers"
    -- three times and enumerated them nowhere.
    segment                 TEXT NOT NULL,
    country                 TEXT NOT NULL DEFAULT 'US',
    hq_location             TEXT,
    headcount_band          TEXT,            -- '25-50', '~30' — the workbook's own
                                             -- shape; these are estimates, not counts

    -- ARR is an ESTIMATE with its basis attached, or it is absent (R0.9).
    -- Private boutiques of 10-100 people do not publish revenue, and the
    -- headcount it derives from is itself a band. Storing the basis alongside
    -- the number is what stops it being read as reported fact later.
    arr_estimate_low        BIGINT,
    arr_estimate_high       BIGINT,
    arr_basis               TEXT,

    description             TEXT,

    -- Scoring. v1 is the workbook's own weighted-criteria arithmetic (R0.8).
    -- The model version travels with the score so Part 4 can measure a later
    -- model against this one rather than replacing it blind.
    icp_fit_score           SMALLINT,
    icp_model_version       TEXT,

    -- Contact. Part 0 fills what a company's own site publishes; a verified
    -- address needs Part 3 and its R21 retention check.
    contact_name            TEXT,
    contact_title           TEXT,
    contact_email           TEXT,
    email_confidence        TEXT,            -- the workbook's own three values
    company_linkedin_url    TEXT,            -- URL only. Never read: R14 is Policy
    contact_linkedin_url    TEXT,            -- URL only, same rule

    -- Verification (R0.5). `verified_on` names WHICH evidence held, and
    -- `verification_note` is what the card displays. A firm is not surfaced on
    -- fewer than two kinds — enforced below.
    verification_note       TEXT,
    verified_on             TEXT[] NOT NULL DEFAULT '{}',

    -- The operator's three-layer taxonomy (R0.14). Recorded now, deliberately
    -- NOT surfaced on the card and NOT an input to the ICP score, until the
    -- market picture is clearer. Scoring against a taxonomy before the market is
    -- understood would bake in the assumption this exercise exists to test.
    pain_layer              TEXT,

    -- The generated outreach hook (R0.12). Operator-facing DRAFT only: packet
    -- assembly does not read this column, and `35-` §7 carries the amendment
    -- saying generated text is permitted here and nowhere a recipient can see.
    pain_hook               TEXT,

    -- Provenance. No row exists without a source (outcome 2).
    discovered_via          TEXT NOT NULL,
    discovery_query         TEXT,
    discovered_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- The review lifecycle.
    surfaced_at             TIMESTAMPTZ,
    review_message_id       TEXT,            -- Discord snowflake; TEXT because
                                             -- snowflakes exceed 2^53 (0016)
    reviewed_at             TIMESTAMPTZ,
    review_decision         TEXT,            -- 'accept' | 'reject' | 'defer'
    reject_reason           TEXT,            -- R0.7's enumerated reasons
    reject_note             TEXT,

    promoted_target_id      BIGINT REFERENCES outreach_targets(id),

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- ---- Vocabulary, pinned (R0.1, R0.14) -----------------------------------
    CONSTRAINT outreach_discoveries_segment_ck CHECK (segment IN (
        'corporate_l_and_d',
        'coaching_leadership',
        'instructional_design',
        'engineering_consultancy',
        'product_design_agency',
        'msp_it_consultancy'
    )),

    -- The workbook's own vocabulary, reused rather than reinvented so the
    -- operator reads the same three words he already assigns by hand.
    CONSTRAINT outreach_discoveries_confidence_ck CHECK (
        email_confidence IS NULL
        OR email_confidence IN ('public', 'inferred_pattern', 'general_inbox')
    ),

    CONSTRAINT outreach_discoveries_pain_layer_ck CHECK (
        pain_layer IS NULL OR pain_layer IN ('L1', 'L2', 'L3')
    ),

    CONSTRAINT outreach_discoveries_decision_ck CHECK (
        review_decision IS NULL
        OR review_decision IN ('accept', 'reject', 'defer')
    ),

    CONSTRAINT outreach_discoveries_reason_vocab_ck CHECK (
        reject_reason IS NULL OR reject_reason IN (
            'wrong_segment',
            'too_small',
            'too_large',
            'no_pain_signal',
            'poor_contact_path',
            'geography',
            'competitor_or_conflict',
            'already_known',
            'other'
        )
    ),

    -- ---- The invariants that make Part 4 possible ---------------------------
    -- R0.7: a rejection without a reason teaches the feedback loop nothing, and
    -- the loop is the entire justification for the review effort. This is the
    -- same discipline as outreach_targets_stalled_ck and the skip-requires-reason
    -- rule in `35-` §9: an invariant is a database constraint, because nothing
    -- mediates these writes once NocoDB issues raw UPDATEs.
    CONSTRAINT outreach_discoveries_reject_reason_ck CHECK (
        review_decision IS DISTINCT FROM 'reject' OR reject_reason IS NOT NULL
    ),

    -- 'other' is only useful if it says what it means.
    CONSTRAINT outreach_discoveries_other_note_ck CHECK (
        reject_reason IS DISTINCT FROM 'other' OR reject_note IS NOT NULL
    ),

    -- A reason without a rejection is a mislabelled row, and Part 4 reads the
    -- reason distribution to decide which knob to turn (V0-7).
    CONSTRAINT outreach_discoveries_reason_scope_ck CHECK (
        reject_reason IS NULL OR review_decision = 'reject'
    ),

    -- A decision and its timestamp travel together, so R4.5's recency weighting
    -- can never meet a label with no date.
    CONSTRAINT outreach_discoveries_reviewed_ck CHECK (
        (review_decision IS NULL) = (reviewed_at IS NULL)
    ),

    -- Only an accepted firm can have been promoted.
    CONSTRAINT outreach_discoveries_promoted_ck CHECK (
        promoted_target_id IS NULL OR review_decision = 'accept'
    ),

    -- R0.5: never surface a firm on thinner evidence than two independent kinds.
    -- `35-` §3's discipline, applied one step earlier: a firm shown as verified
    -- that is not produces confident, checkable, wrong outreach.
    CONSTRAINT outreach_discoveries_verified_ck CHECK (
        surfaced_at IS NULL OR COALESCE(array_length(verified_on, 1), 0) >= 2
    ),

    CONSTRAINT outreach_discoveries_score_ck CHECK (
        icp_fit_score IS NULL OR (icp_fit_score BETWEEN 0 AND 100)
    ),

    -- A score with no model version cannot be attributed later, which defeats
    -- Part 4 outcome 2.
    CONSTRAINT outreach_discoveries_score_version_ck CHECK (
        (icp_fit_score IS NULL) = (icp_model_version IS NULL)
    ),

    CONSTRAINT outreach_discoveries_arr_ck CHECK (
        arr_estimate_low IS NULL
        OR arr_estimate_high IS NULL
        OR arr_estimate_low <= arr_estimate_high
    ),

    -- R0.9: a band with no stated basis is a number pretending to be a fact.
    CONSTRAINT outreach_discoveries_arr_basis_ck CHECK (
        (arr_estimate_low IS NULL AND arr_estimate_high IS NULL)
        OR arr_basis IS NOT NULL
    )
);

-- NOTE ON `country`: deliberately NOT CHECK-pinned to 'US'. OQ-C settled
-- 2026-08-20 on US-only, but recorded as a scoping decision the operator expects
-- to revisit. Filtering happens in the sourcing queries and the importer, so
-- reopening geography costs a query change rather than a migration.

-- The dedup key (R0.10). outreach_targets already carries the equivalent unique
-- index on company_domain; the cross-table check lives in the importer and in
-- profilable/reviewable queries.
CREATE UNIQUE INDEX IF NOT EXISTS outreach_discoveries_domain_idx
    ON outreach_discoveries (company_domain);

-- The daily window (R0.11): unreviewed, ranked by fit. Partial, because the
-- reviewed rows are Part 4's territory and grow without bound.
CREATE INDEX IF NOT EXISTS outreach_discoveries_queue_idx
    ON outreach_discoveries (icp_fit_score DESC NULLS LAST, discovered_at)
    WHERE reviewed_at IS NULL;

-- Part 4 reads labels by recency (R4.5, 8-week half-life) and by factor.
CREATE INDEX IF NOT EXISTS outreach_discoveries_labels_idx
    ON outreach_discoveries (reviewed_at DESC)
    WHERE review_decision IS NOT NULL;

CREATE INDEX IF NOT EXISTS outreach_discoveries_segment_idx
    ON outreach_discoveries (segment, review_decision);

COMMENT ON TABLE outreach_discoveries IS
    'Gate 0 pool: firms found and verified but not yet judged. Separate from '
    'outreach_targets because a discovery has no trigger, and fabricating one '
    'is the failure already visible in the 2026-06-10 trigger_date. '
    'PRD-outreach-company-profile.md Part 0.';

COMMENT ON COLUMN outreach_discoveries.pain_layer IS
    'Operator taxonomy L1/L2/L3 (R0.14). Recorded but deliberately NOT surfaced '
    'on the review card and NOT an ICP input until the market picture is clearer.';

COMMENT ON COLUMN outreach_discoveries.pain_hook IS
    'Generated draft hook (R0.12). Operator-facing only. Packet assembly must '
    'never read this column - see the 35- section 7 amendment.';

COMMENT ON COLUMN outreach_discoveries.reject_reason IS
    'The training label for Part 4. Reasons route to different knobs (R4.4): '
    'wrong_segment is about sourcing queries, poor_contact_path is about Part 3 '
    'and says nothing about the firm.';

-- Audit + updated_at, on the 0013 pattern. Every review is attributable via
-- session_user, which matters once NocoDB can edit these rows directly (R17).
DROP TRIGGER IF EXISTS outreach_discoveries_audit ON outreach_discoveries;
DROP TRIGGER IF EXISTS outreach_discoveries_touch ON outreach_discoveries;

CREATE TRIGGER outreach_discoveries_audit
    AFTER INSERT OR UPDATE OR DELETE ON outreach_discoveries
    FOR EACH ROW EXECUTE FUNCTION outreach_log_event();

CREATE TRIGGER outreach_discoveries_touch
    BEFORE UPDATE ON outreach_discoveries
    FOR EACH ROW EXECUTE FUNCTION outreach_touch_updated_at();

-- The runtime app connects as barry_agent. Forgetting this makes writes fail
-- SILENTLY - the bug 0011 fixed for tool_invocations.
ALTER TABLE outreach_discoveries OWNER TO barry_agent;
ALTER SEQUENCE outreach_discoveries_id_seq OWNER TO barry_agent;

COMMIT;
