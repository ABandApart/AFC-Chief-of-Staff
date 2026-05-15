#!/usr/bin/env bash
# =============================================================================
# keychain_verify.sh — confirm credential existence (no secrets revealed)
# =============================================================================
# Lists each expected keychain item with OK or MISSING.
# Never prints the secret values themselves.
# =============================================================================

set -euo pipefail

ITEMS=(
    "supabase-service-key"
    "supabase-anon-key"
    "supabase-db-password"
    "supabase-db-url"
    "supabase-project-url"
    "gemini-api-key"
    "anthropic-api-key"
    "github-personal-token"
)

missing=0
for name in "${ITEMS[@]}"; do
    if security find-generic-password -a "$USER" -s "$name" &gt;/dev/null 2&gt;&amp;1; then
        printf 'OK      %s\n' "$name"
    else
        printf 'MISSING %s\n' "$name"
        missing=$((missing + 1))
    fi
done

echo
if [[ $missing -eq 0 ]]; then
    echo "All ${#ITEMS[@]} credentials present."
    exit 0
else
    echo "$missing missing — run scripts/keychain_setup.sh to add them."
    exit 1
fi
