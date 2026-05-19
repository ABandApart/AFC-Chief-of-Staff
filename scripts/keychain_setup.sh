#!/usr/bin/env bash
# =============================================================================
# keychain_setup.sh — interactive credential storage in macOS Keychain
# =============================================================================
# Run on the `agent` account. Prompts for each credential and stores in the
# login keychain. Secrets are read with `read -s` so they never echo to the
# terminal or shell history.
#
# Re-running this script will overwrite existing keychain items (after
# confirmation), so you can use it to rotate a credential.
# =============================================================================

set -euo pipefail

# Whitelist of items expected in Phase 1.
# Format: keychain_item_name|description
#
# Two Phase-1 deviations from the baseline PRD are encoded here:
#
# 1. The brain is a locally-hosted Postgres 17 instance on this Mac mini
#    (not hosted Supabase). The four Supabase-specific items in the baseline
#    (service-key, anon-key, db-password, project-url) are not used. `db-url`
#    is the single Postgres connection string the agent process uses; it is
#    populated automatically by the postgres provisioning step using the role
#    password generated during install.
#
# 2. Anthropic API keys are per-agent rather than one shared key. This gives
#    spend-attribution at the Anthropic side, complementing the per-call
#    `agent_runs.agent_name` ledger that Phase 2's cost helper writes. The
#    cost helper looks up the right key by agent name. See decision-log entry
#    "Per-agent Anthropic API keys" in 70-build-order.md.
#
# `github-personal-token` is optional — only needed if barry-agent clones via
# HTTPS. If barry-agent shares barry-admin's SSH key (simpler), skip this one.
ITEMS=(
    "db-url|Postgres connection URI (auto-populated during install)"
    "gemini-api-key|Gemini API key (Tartt — Phase 4 news scraping + embeddings)"
    "anthropic-key-ted|Anthropic key for Ted (Phase 11 — health checks + alert summarization)"
    "anthropic-key-keeley-strategy|Anthropic key for Keeley Strategy (Phase 8 — content triage)"
    "anthropic-key-keeley-content|Anthropic key for Keeley Content (Phase 8 — drafting)"
    "anthropic-key-roy-kent|Anthropic key for Roy Kent (Phase 6 — inbound prospect qualifier)"
    "anthropic-key-nate-shelley|Anthropic key for Nate Shelley (Phase 10 — ICP signal synthesis)"
    "anthropic-key-higgins|Anthropic key for Higgins (Phase 11 — weekly dashboard)"
    "github-personal-token|GitHub PAT — optional, only if cloning via HTTPS; skip if using shared SSH key"
)

echo "AI Adaptive Chief of Staff — Phase 1 Keychain Setup"
echo "===================================================="
echo
echo "This will store ${#ITEMS[@]} credentials in your login keychain."
echo "Each prompt reads silently (no echo, not in shell history)."
echo
read -p "Proceed? [y/N] " -n 1 -r
echo
[[ ! $REPLY =~ ^[Yy]$ ]] && exit 0
echo

for item in "${ITEMS[@]}"; do
    name="${item%%|*}"
    desc="${item#*|}"

    # Check if it already exists.
    if security find-generic-password -a "$USER" -s "$name" -w >/dev/null 2>&1; then
        echo "[$name] already exists in keychain."
        read -p "  Overwrite? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            security delete-generic-password -a "$USER" -s "$name" >/dev/null 2>&1 || true
        else
            echo "  Skipped."
            continue
        fi
    fi

    echo "[$name]"
    echo "  $desc"
    read -s -p "  Value (input hidden): " value
    echo

    if [[ -z "$value" ]]; then
        echo "  Empty input — skipping."
        continue
    fi

    security add-generic-password \
        -a "$USER" \
        -s "$name" \
        -w "$value" \
        -T "" \
        -U
    echo "  Stored."
    echo
done

echo
echo "Setup complete. Verify with: bash scripts/keychain_verify.sh"
