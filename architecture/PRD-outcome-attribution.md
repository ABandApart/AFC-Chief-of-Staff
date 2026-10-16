# Outcome attribution: client key and outreach-target link — PRD & Build Spec

<doc:meta>
  <doc:theme>Make KR3 computable and cold-outreach revenue attributable</doc:theme>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>2026-10-05 — Increment 1 (schema, migration 0030) APPLIED to aiadaptive_cos; verify_schema.sql clean. Increment 2 (`/outcome` form + migration 0031) BUILT 2026-10-05, not deployed: unit tests and scratch-DB checks pass; migration 0031 NOT applied (ships with the cog); barry-agent smoke test pending.</doc:status>
  <doc:depends_on>`outcomes` (0001), `outreach_targets` (0013), `clients` (0030), `15-okrs-q4-2026.md` gaps M1, M4, M5</doc:depends_on>
</doc:meta>

## Problem

Two gaps in `15-okrs-q4-2026.md` §3 block the Q4 north-star KRs:

- **M5:** `outcomes` has no client key. An `engagement_signed` row and a later `maintenance_converted` row for the same client are linked only by free-text `description`, so KR3 (project → maintenance conversion) cannot be computed.
- **M1:** `outcomes.attributed_prospect_id` references `prospects` (inbound) only. A win from cold outreach cannot be traced to its `outreach_targets` row, trigger, or hypothesis.

Engagements are sold as projects first, then converted to maintenance (operator, 2026-10-05).

## S1. Outcome (whole PRD)

When both increments are done:

1. Every `engagement_signed`, `engagement_renewed`, and `maintenance_converted` outcome recorded after the cut-over names exactly one client.
2. KR3 is a single SQL query: of the clients with an `engagement_signed` outcome, the share that also has a later `maintenance_converted` outcome.
3. An outcome for a cold-outreach company names its `outreach_targets` row, so wins group by `trigger_kind` and `hypothesis_id`.

Increment 1 makes 2 and 3 *possible*. Increment 2 makes 1 and 3 *true*.

## Settled decisions (both increments)

| # | Decision |
|---|---|
| S-1 | A client is a **company**, not a person. KR3 counts companies that convert. |
| S-2 | `outcomes.attributed_target_id` references `outreach_targets(id)` with the default `NO ACTION` on delete, matching `attributed_prospect_id`. A target with a recorded outcome cannot be deleted. |
| S-3 | `clients.domain` is the dedup key, normalized by `outreach.normalize_domain()`, and nullable for clients with no web domain. |
| D1 | **New `clients` table** (operator, 2026-10-05). `people` holds persons, not companies, and `outreach_targets` misses referral and inbound clients that never entered outreach. *Correction:* an earlier draft also argued that one company can appear as several targets; it cannot — `outreach_targets_domain_idx` is UNIQUE on `company_domain` (0013:126). That makes the target lookup in increment 2 exact. |

---

## Increment 1 — schema (migration 0030) — DONE

- `clients (id, name, domain UNIQUE, created_at)`, owned by `barry_agent`, with `clients_domain_normalized_ck`.
- `outcomes.client_id BIGINT REFERENCES clients(id)`, nullable.
- `outcomes.attributed_target_id BIGINT REFERENCES outreach_targets(id)`, nullable.
- Indexes: `outcomes (client_id, outcome_type, recorded_at)` for KR3; `outcomes (attributed_target_id)`.

**Verification (passed 2026-10-05):**

1. `verify_schema.sql` on `aiadaptive_cos`: 35 tables, both columns present, `clients` owned by `barry_agent`, no FAIL lines.
2. On a scratch schema copy, the KR3 query below returned `0.5` for one converted and one unconverted client.
3. As `barry_agent`: inserted `clients` and `outcomes` rows carrying both columns; an unnormalized domain and deleting an attributed target were both rejected; the current two-column `/outcome` insert still succeeded.

```sql
SELECT count(DISTINCT m.client_id)::numeric
       / NULLIF(count(DISTINCT s.client_id), 0) AS kr3_conversion
FROM outcomes s
LEFT JOIN outcomes m
       ON m.client_id = s.client_id
      AND m.outcome_type = 'maintenance_converted'
      AND m.recorded_at > s.recorded_at
WHERE s.outcome_type = 'engagement_signed'
  AND s.client_id IS NOT NULL;
```

---

## Increment 2 — `/outcome` form

### Outcome

After increment 2 ships:

1. `/outcome` takes a **company**. Picking one sets `client_id`, and also `attributed_target_id` when an outreach target has that domain — one pick, both links.
2. The three engagement types cannot be recorded without a company: the bot refuses before the modal opens, and the database refuses an insert that bypasses the bot.
3. A company the system has never seen can be recorded without leaving Discord; it becomes a `clients` row.

### Design

**Command shape** (current shape in `agents/discord_bot/cogs/outcomes.py`):

```
/outcome type:<choice> company:<autocomplete, optional>
  → modal: Description (required) · Value $ (optional) · Client name (only for a new client)
```

**`company` autocomplete** — the `/gate0-edit` pattern (`cogs/outreach_discovery.py:628`): the display name is the company name, the value is a stable key. Up to 25 choices, in this order:

1. Existing `clients` matching the typed text on name or domain. Value: `client:<id>`.
2. `outreach_targets` matching on `company_name` or `company_domain` whose domain is not yet a client. Value: `target:<company_domain>`.
3. If the typed text matches nothing exactly, one final choice: `➕ New client: <typed text>`. Value: `new:<typed text>`.

**Resolution on submit** — a pure function plus one transaction in `brain.py`:

| Value | `client_id` | `attributed_target_id` |
|---|---|---|
| `client:<id>` | that client | the target whose `company_domain = clients.domain`, if any |
| `target:<domain>` | **create** a client from the target's `company_name` and `company_domain` (or reuse one created since the autocomplete ran) | that target |
| `new:<text>` | **create** a client. If `<text>` looks like a domain (contains a dot, no spaces), it is normalized into `domain` and the modal's Client name field supplies `name`; otherwise `<text>` is the name and `domain` is NULL. | the target with that domain, if any |
| *(none)* | NULL | NULL |

The client insert uses `INSERT … ON CONFLICT (domain) DO NOTHING` followed by a select, so two outcomes recorded for the same new company never create two clients. The client insert and the outcome insert share one transaction: a failed outcome leaves no orphan client.

**Required company** — checked in the command handler, before `send_modal`, for the contract types in D3. The refusal is ephemeral and names the fix: "Pick a company — engagement outcomes need one for KR3."

**Migration 0031** adds the database backstop:

```sql
ALTER TABLE outcomes ADD CONSTRAINT outcomes_engagement_has_client_ck CHECK (
    outcome_type NOT IN ('engagement_signed', 'engagement_renewed', 'maintenance_converted')
    OR client_id IS NOT NULL);
```

No existing row violates it (1 row on 2026-10-05, `partnership_explored`), so it is added fully validated, not `NOT VALID`. **It ships in the same deploy as the cog change** — applied before, it breaks `/outcome engagement signed`; applied after, there is a window without the backstop.

**Confirmation message** gains the company: `✅ Recorded outcome #12: engagement signed — Acme ($18,000) · linked to outreach target`.

### Non-goals

- **No prospect link.** `attributed_prospect_id` stays unused by the form. Roy Kent creates an `outreach_targets` row for every high-fit inbound lead (`36-inbound-leads.md`), so inbound wins attribute through the target link.
- **No client editing or merging.** A mistyped client is fixed with SQL. A `/client` command is a later increment if mistakes turn out common.
- **No backfill.** The one remaining row is not a contract outcome.
- **No change to `outreach_targets.status`.** Recording `engagement_signed` does not move the target to `engaged`. That is gap M2, decided separately.
- **No operator gate.** `/outcome` is unrestricted today; this increment does not change that.

### Verification

**Unit tests** (`tests/test_outcomes.py`, pure — no Discord, no DB, matching the existing file):

1. The value parser maps `client:7`, `target:acme.com`, `new:Acme Co`, and `new:https://www.Acme.com/` to the right kind and key; the last normalizes to `acme.com`.
2. Every D3 type is refused without a company; every other type is accepted without one.
3. The choice builder caps at 25, puts clients before targets, drops targets that are already clients, and adds the `➕ New client` choice only when nothing matches exactly.
4. Choice names and values are truncated to Discord's 100-character limit.

**Migration 0031** on a scratch schema copy: the CHECK rejects an `engagement_signed` insert with no `client_id` and accepts a `discovery_call_booked` insert with none.

**barry-agent smoke test** (runtime, after deploy):

1. `/outcome engagement signed` with no company → ephemeral refusal, no row.
2. `/outcome engagement signed company:<an outreach target>` → one new `clients` row, and an `outcomes` row with both `client_id` and `attributed_target_id` set.
3. The same again for the same company → no second `clients` row.
4. `/outcome engagement signed company:➕ New client: Test Co` → a `clients` row with NULL domain; `attributed_target_id` NULL.
5. The KR3 query returns a number, not NULL. Then delete the smoke-test rows.

### Build notes (2026-10-05)

- **Unit tests:** 23 in `tests/test_outcomes.py`, all passing, including a guard that `CONTRACT_TYPES` matches the 0031 CHECK.
- **Scratch DB, as `barry_agent`:** a target pick created the client and set both links; a second pick for the same company reused the client; a new client by name got a NULL domain; a new client by URL got the normalized domain; a contract outcome with no client was rejected by the 0031 CHECK; a stale client pick raised `KeyError`; the KR3 query returned 0.5. No orphan clients.
- **Deviation:** none from the design above. Typed text that was not picked from the suggestions is refused (the `/gate0-edit` rule) rather than treated as a new client; the spec's "New client" choice is the only way to create one.

### Decisions (operator, 2026-10-05)

| # | Decision |
|---|---|
| D2 | **One `company` option** sets both links. Domain uniqueness on `clients` and `outreach_targets` makes the target derivable from the client. |
| D3 | **Company required for contract outcomes only:** `engagement_signed`, `engagement_renewed`, `maintenance_converted`. Optional for every other type, including the contact stages `discovery_call_booked` and `proposal_sent`. |
| D4 | **Value is total contract value.** The modal field is labelled "Total contract value $". A project records its full value; a maintenance agreement records its first term. Settles gap M4. |
| D5 | **Row #5 was a test record** (`proposal_sent`, $1,200, "6-month retainer signed"). Deleted 2026-10-05. `outcomes` now holds 1 row (#8, `partnership_explored`). |
