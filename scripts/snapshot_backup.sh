#!/usr/bin/env bash
# =============================================================================
# snapshot_backup.sh — manual brain snapshot
# =============================================================================
# Produces a gzipped pg_dump of the entire public schema, written to
# ~/agents/backups/. Used at the end of each phase to mark a checkpoint.
#
# This is the manual version. Phase 12 (Hardening) replaces it with a
# scheduled launchd backup including encryption.
# =============================================================================

set -euo pipefail

BACKUP_DIR="${HOME}/agents/backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")
LABEL="${1:-snapshot}"
OUT="${BACKUP_DIR}/${LABEL}_${TIMESTAMP}.sql.gz"

# Pull the connection string from keychain (never log it).
DB_URL=$(security find-generic-password -a "$USER" -s supabase-db-url -w 2&gt;/dev/null) || {
    echo "FAIL: supabase-db-url not in keychain. Run scripts/keychain_setup.sh."
    exit 1
}

echo "Backing up to: $OUT"

# --no-owner and --no-acl prevent Supabase-managed role grants from leaking.
# --schema=public excludes internal Supabase schemas (auth, storage, etc.).
pg_dump "$DB_URL" \
    --schema=public \
    --no-owner \
    --no-acl \
    --quote-all-identifiers \
    | gzip &gt; "$OUT"

SIZE=$(du -h "$OUT" | cut -f1)
echo "OK   $OUT ($SIZE)"
echo
echo "To restore (for testing only):"
echo "  gunzip -c '$OUT' | psql \"\$DB_URL\""
