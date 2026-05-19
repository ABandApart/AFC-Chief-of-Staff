#!/usr/bin/env bash
# =============================================================================
# keychain_verify.sh — confirm credential existence (no secrets revealed)
# =============================================================================
# Lists each expected keychain item with OK or MISSING.
# Never prints the secret values themselves.
# =============================================================================

set -euo pipefail

ITEMS=(
    "db-url"
    "gemini-api-key"
    "anthropic-key-ted"
    "anthropic-key-keeley-strategy"
    "anthropic-key-keeley-content"
    "anthropic-key-roy-kent"
    "anthropic-key-nate-shelley"
    "anthropic-key-higgins"
    "discord-bot-token"
)

# github-personal-token is optional (only if cloning via HTTPS rather than SSH).
# Check it separately so its absence doesn't trigger the exit-1 failure mode.
OPTIONAL_ITEMS=(
    "github-personal-token"
)

echo "Required items:"
missing=0
for name in "${ITEMS[@]}"; do
    if security find-generic-password -a "$USER" -s "$name" >/dev/null 2>&1; then
        printf '  OK      %s\n' "$name"
    else
        printf '  MISSING %s\n' "$name"
        missing=$((missing + 1))
    fi
done

echo
echo "Optional items:"
for name in "${OPTIONAL_ITEMS[@]}"; do
    if security find-generic-password -a "$USER" -s "$name" >/dev/null 2>&1; then
        printf '  OK      %s\n' "$name"
    else
        printf '  absent  %s  (optional — only needed if cloning via HTTPS)\n' "$name"
    fi
done

echo
if [[ $missing -eq 0 ]]; then
    echo "All ${#ITEMS[@]} required credentials present."
    exit 0
else
    echo "$missing required item(s) missing — run scripts/keychain_setup.sh to add them."
    exit 1
fi
