#!/bin/zsh
# Nightly logical backup of BOTH brain databases (Phase 3.5; pulled forward from
# Phase 12 in the 2026-07 refactor proposal):
#   - aiadaptive_cos     — operational state (prospects, tasks, ledger, …)
#   - aiadaptive_cognee  — the cognee knowledge graph (all captured knowledge)
# The cognee DB was added in the 3.7 pivot; before it existed only the
# operational DB was dumped, which would have left the graph unprotected.
#
# Runs as barry-agent via launchd (com.aiadaptive.cos.pg-backup.plist, 2:00).
# Uses db-url from the keychain — same credential path as the agents — and
# the keg-only Homebrew pg_dump so the dump version matches the server. The
# cognee DSN is the same connection with the dbname swapped (matches
# agents/_lib/cognee_setup.cognee_dsn).
#
# Rotation: keep 14 nightly dumps per DB. Restore drill:
#   gunzip -c <file> | psql <target-db-url>
set -euo pipefail

PG_DUMP=/opt/homebrew/opt/postgresql@17/bin/pg_dump
BACKUP_DIR="$HOME/agents/backups/nightly"
KEEP_DAYS=14
COGNEE_DB=aiadaptive_cognee

mkdir -p "$BACKUP_DIR"
DB_URL=$(security find-generic-password -a "$USER" -s db-url -w)
# Swap the trailing dbname to reach the cognee DB on the same server. The
# local db-url carries no query string (see cognee_setup.cognee_dsn), so a
# path swap is sufficient.
COGNEE_URL="${DB_URL%/*}/$COGNEE_DB"

dump_db() {
    local url="$1" name="$2"
    local out="$BACKUP_DIR/${name}_$(date +%Y-%m-%d).sql.gz"
    # Dump to a temp name and mv so a mid-dump failure never leaves a
    # truncated file that looks like a valid backup.
    "$PG_DUMP" "$url" | gzip > "$out.tmp"
    mv "$out.tmp" "$out"
    find "$BACKUP_DIR" -name "${name}_*.sql.gz" -mtime +"$KEEP_DAYS" -delete
    echo "$(date '+%F %T') backup ok: $out ($(du -h "$out" | cut -f1))"
}

dump_db "$DB_URL" aiadaptive_cos
dump_db "$COGNEE_URL" "$COGNEE_DB"
