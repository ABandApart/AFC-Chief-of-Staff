# Migrations

Numbered SQL migrations applied in order. Never edit an applied migration —
add a new one with the next sequential number.

## How to apply

As **barry-admin**, use the local socket (barry-admin has no keychain `db-url`;
see the repo `CLAUDE.md`):

```bash
psql aiadaptive_cos -f migrations/00NN_description.sql
```

New tables need `ALTER TABLE … OWNER TO barry_agent` in the same migration: the
runtime connects as that role, and a table it does not own fails writes silently
(the bug 0011 fixed).

## How to verify

```bash
psql aiadaptive_cos -f migrations/verify_schema.sql
```

Update `verify_schema.sql` in the same change as every migration that adds a
table or view. It is the source of truth for what should exist.

## Numbering gaps

- **0017** was never used.
- **0028** (`icp_syntheses`) exists only on the unmerged `phase-10-icp-intelligence`
  branch. 0029 does not depend on it.
