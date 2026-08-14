-- =============================================================================
-- Migration 0015: outreach_targets.function + contact_first_name (Track O)
-- =============================================================================
-- Two columns the template pack needs and the schema had no source for.
--
-- **(1) `function`** — the single most-used placeholder in the pack (57
-- occurrences of `[function]`), and nothing resolved it. `function_state` is the
-- two-tab diagnostic (self_covered / under_led / vacant_seat), not a function
-- name.
--
-- It is NOT the same field as `[Role Title]`, and one template uses both in a
-- sentence: *"I run [function] fractionally, and from the outside it looks like
-- the [Role Title] seat has been open a while."* `[function]` is a bare noun —
-- "I run revenue fractionally", "nobody owning marketing" — while `[Role Title]`
-- is a job title. Substituting the title for the function produces "I run VP of
-- Marketing fractionally", which reads fine on screen and wrong to a founder.
--
-- So: derived from an open **leadership** req's title where one exists (the
-- level stripped — "VP Revenue" → "revenue"), stored here, and overridable
-- (operator, 2026-08-14). The derivation only ever fills a NULL, so a corrected
-- value is never overwritten by a later poll.
--
-- **(2) `contact_first_name`** — populated explicitly at import rather than
-- split from `contact_name` at send time. Whitespace-splitting fails precisely
-- where it is most visible: "Dr. Jane Smith", "Jean-Pierre Dupont", "Jane van
-- der Berg", "J. Smith" all yield a wrong greeting, and the greeting is the
-- first line a cold prospect reads. NULL leaves `[First Name]` unresolved, which
-- blocks — no greeting beats "Hi Dr.,".
--
-- `contact_name` stays as the full name for records and display.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0015_outreach_function_and_first_name.sql
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

ALTER TABLE outreach_targets
    ADD COLUMN IF NOT EXISTS function            TEXT,
    ADD COLUMN IF NOT EXISTS contact_first_name  TEXT;

COMMENT ON COLUMN outreach_targets.function IS
    'The business function pitched into, as a BARE NOUN for template '
    'substitution ("revenue", "marketing", "finance") — never a job title. '
    'Auto-derived from an open leadership req where one exists (fills NULL '
    'only); operator-overridable. Distinct from function_state, which is that '
    'function''s diagnosis.';

COMMENT ON COLUMN outreach_targets.contact_first_name IS
    'The greeting name, set explicitly at import — never split from '
    'contact_name at send time, because the failure modes (honorifics, '
    'compound and multi-part surnames) all land in the first line the '
    'prospect reads. NULL leaves [First Name] unresolved, which blocks.';

COMMIT;

-- =============================================================================
-- End of migration 0015
-- =============================================================================
