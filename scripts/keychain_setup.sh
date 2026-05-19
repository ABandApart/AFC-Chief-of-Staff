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
# Phase 1 deviated from the baseline PRD: the brain is a locally-hosted
# Postgres 17 instance on this Mac mini (not hosted Supabase). The four
# Supabase-specific items in the baseline (service-key, anon-key, db-password,
# project-url) are not used. `db-url` is the single Postgres connection string
# the agent process uses; it is populated automatically by the postgres
# provisioning step using the role password generated during install.
ITEMS=(
    "db-url|Postgres connection URI (postgresql://barry_agent:PW@localhost:5432/aiadaptive_cos) — auto-populated by install"
    "gemini-api-key|Gemini API key (from aistudio.google.com)"
    "anthropic-api-key|Anthropic API key (from console.anthropic.com)"
    "github-personal-token|GitHub personal access token (for git push from agent)"
)

echo "AI Adaptive Chief of Staff — Phase 1 Keychain Setup"
echo "===================================================="
echo
echo "This will store ${#ITEMS[@]} credentials in your login keychain."
echo "Each prompt reads silently (no echo, not in shell history)."
echo
read -p "Proceed? [y/N] " -n 1 -r
echo
[[ ! $REPLY =~ ^[Yy]$ ]] &amp;&amp; exit 0
echo

for item in "${ITEMS[@]}"; do
    name="${item%%|*}"
    desc="${item#*|}"

    # Check if it already exists.
    if security find-generic-password -a "$USER" -s "$name" -w &gt;/dev/null 2&gt;&amp;1; then
        echo "[$name] already exists in keychain."
        read -p "  Overwrite? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            security delete-generic-password -a "$USER" -s "$name" &gt;/dev/null 2&gt;&amp;1 || true
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
