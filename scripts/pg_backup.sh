#!/bin/zsh
# Nightly logical backup of aiadaptive_cos (Phase 3.5; pulled forward from
# Phase 12 in the 2026-07 refactor proposal).
#
# Runs as barry-agent via launchd (com.aiadaptive.cos.pg-backup.plist, 2:00).
# Uses db-url from the keychain — same credential path as the agents — and
# the keg-only Homebrew pg_dump so the dump version matches the server.
#
# Rotation: keep 14 nightly dumps. Restore drill:
#   gunzip -c <file> | psql <target-db-url>
set -euo pipefail

PG_DUMP=/opt/homebrew/opt/postgresql@17/bin/pg_dump
BACKUP_DIR="$HOME/agents/backups/nightly"
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"
DB_URL=$(security find-generic-password -a "$USER" -s db-url -w)

OUT="$BACKUP_DIR/aiadaptive_cos_$(date +%Y-%m-%d).sql.gz"
# Dump to a temp name and mv so a mid-dump failure never leaves a
# truncated file that looks like a valid backup.
"$PG_DUMP" "$DB_URL" | gzip > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"

find "$BACKUP_DIR" -name 'aiadaptive_cos_*.sql.gz' -mtime +"$KEEP_DAYS" -delete

echo "$(date '+%F %T') backup ok: $OUT ($(du -h "$OUT" | cut -f1))"
