-- =============================================================================
-- Migration 0037: plans and plan_items (the daily plan)
-- =============================================================================
-- Spec: architecture/PRD-notion-integration.md, increment N7 (N-S18, operator
-- 2026-11-11).
--
-- `cli.notion_plan_import` reads a plan page from Notion into these tables. Each
-- weekday the `notion-plan-daily` loop (`agents/notion/plan_daily.py`) creates
-- that day's tasks from `plan_items` and sets `task_id`. An item with no task yet
-- can be replaced by a re-import; one with a task is never changed.
--
-- `standing` marks the daily block that repeats on every plan day. An unfinished
-- standing task closes as `skipped` instead of carrying forward (N-D14).
-- `tasks.status` and `follow_ups.status` gain the value `skipped`; neither column
-- has a CHECK, so that needs no DDL.
--
-- Apply: psql aiadaptive_cos -f migrations/0037_plans.sql
-- Idempotent.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS plans (
    id             BIGSERIAL PRIMARY KEY,
    notion_page_id TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    starts_on      DATE NOT NULL,
    ends_on        DATE NOT NULL CHECK (ends_on >= starts_on),
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    imported_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one active plan.
CREATE UNIQUE INDEX IF NOT EXISTS plans_one_active ON plans ((true)) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS plan_items (
    id                BIGSERIAL PRIMARY KEY,
    plan_id           BIGINT NOT NULL REFERENCES plans(id),
    day               DATE NOT NULL,
    position          SMALLINT NOT NULL,
    title             TEXT NOT NULL,
    minutes           SMALLINT,
    okr_initiative_id BIGINT REFERENCES okr_initiatives(id),
    context           TEXT,
    standing          BOOLEAN NOT NULL DEFAULT false,
    cut_first         BOOLEAN NOT NULL DEFAULT false,
    task_id           BIGINT UNIQUE REFERENCES tasks(id),
    UNIQUE (plan_id, day, position)
);

CREATE INDEX IF NOT EXISTS plan_items_day_idx ON plan_items (day) WHERE task_id IS NULL;

ALTER TABLE plans OWNER TO barry_agent;
ALTER SEQUENCE plans_id_seq OWNER TO barry_agent;
ALTER TABLE plan_items OWNER TO barry_agent;
ALTER SEQUENCE plan_items_id_seq OWNER TO barry_agent;

COMMENT ON TABLE plans IS
    'Plans imported from Notion by cli.notion_plan_import (PRD-notion-integration.md N7).';
COMMENT ON TABLE plan_items IS
    'One task per plan day; task_id is set when notion-plan-daily creates its task (N7).';

COMMIT;
