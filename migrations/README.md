# Migrations

Numbered SQL migrations applied in order. Never edit an applied migration —
add a new one with the next sequential number.

## How to apply

Apply via `psql` against the local Postgres instance. The DB URL lives in
keychain (no `.env` files, never on the command line as a literal):

```bash
export DB_URL=$(security find-generic-password -a "$USER" -s db-url -w)
psql "$DB_URL" -f migrations/000N_description.sql
```

## How to verify

```bash
psql "$DB_URL" -f migrations/verify_schema.sql
```

The verification script is updated alongside each new migration to expect the
new tables.

## Current state

| # | Name | Applied | Phase |
|---|------|---------|-------|
| 0001 | initial schema | pending | 1 |
