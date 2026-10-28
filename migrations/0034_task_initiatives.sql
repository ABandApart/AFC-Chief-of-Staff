-- =============================================================================
-- Migration 0034: initiatives on Task Tinder cards and tasks; To-Do sync state
-- =============================================================================
-- Spec: architecture/PRD-notion-integration.md, increment N3.
--
-- 1. `okr_initiative_id` on task_candidates and tasks. NULL means Unassigned.
--    Set in Discord only (`/initiative`, or `initiative:` on `/todo`; N-S13).
--    On accept, the candidate's value copies to the task.
-- 2. `due_date` on task_candidates, so a `/todo due:` survives until accept and
--    copies to `tasks.due_date`. (The spec gives /todo a due option; candidates
--    had nowhere to hold it.)
-- 3. `notion_sync.synced_done`: the To-Do Done value both sides last agreed on.
--    The To-Do sync compares Postgres and Notion against it to tell which side
--    changed (N-S16, "latest wins").
--
-- `task_candidates.source_type` gains the value 'operator' for /todo cards. The
-- column has no CHECK constraint, so that needs no DDL.
--
-- Apply: psql aiadaptive_cos -f migrations/0034_task_initiatives.sql
-- Idempotent.
-- =============================================================================

BEGIN;

ALTER TABLE task_candidates ADD COLUMN IF NOT EXISTS okr_initiative_id BIGINT
    REFERENCES okr_initiatives(id);
ALTER TABLE task_candidates ADD COLUMN IF NOT EXISTS due_date DATE;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS okr_initiative_id BIGINT
    REFERENCES okr_initiatives(id);
ALTER TABLE notion_sync ADD COLUMN IF NOT EXISTS synced_done BOOLEAN;

CREATE INDEX IF NOT EXISTS tasks_initiative_idx ON tasks (okr_initiative_id);

COMMENT ON COLUMN tasks.okr_initiative_id IS
    'Initiative this task serves (okr_initiatives.id); NULL = Unassigned. Set with /initiative (PRD-notion-integration.md N3).';
COMMENT ON COLUMN notion_sync.synced_done IS
    'To-Do rows only: the Done value Postgres and Notion last agreed on (N-S16).';

COMMIT;
