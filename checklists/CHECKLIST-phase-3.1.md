# Phase 3.1 Checklist — Discord Bot Skeleton

Live tracker for sub-phase 3.1. Each item maps to an AC in
`architecture/PRD-phase-3.1.md`.

## Pre-flight

- [x] Phase 2 complete (commit `e46eee9`)
- [x] Discord bot application registered (app id 150637955019020706)
- [x] Bot installed to AFC Richmond server via OAuth Guild Install
- [x] Privileged intents enabled (MESSAGE CONTENT, SERVER MEMBERS)
- [x] `discord-bot-token` staged in barry-admin keychain
- [x] Sub-phase 3.1 PRD reviewed

## Task 1: Create the 6 channels in AFC Richmond — user action

- [ ] `#briefing` created (private to operator)
- [ ] `#task-tinder` created
- [ ] `#approvals` created
- [ ] `#capture` created
- [ ] `#system` created  ← 3.1 posts to this one
- [ ] `#archive` created

## Task 2: Capture IDs — user action

- [ ] Developer Mode enabled (Settings → Advanced)
- [ ] Server ID captured
- [ ] All 6 channel IDs captured
- [ ] IDs pasted to Claude for `config.py`

## Task 3: Add discord.py dep — Claude action

- [ ] `discord.py>=2.4.0,<3.0` added to pyproject.toml
- [ ] `uv sync` succeeds
- [ ] `uv.lock` updated
- [ ] **AC2 ✓**: `discord.py` import works

## Task 4: Stage token in barry-agent keychain — Claude+user

- [ ] `sudo -u barry-agent` copy of discord-bot-token executed (one password prompt)
- [ ] Staged copy deleted from barry-admin keychain
- [ ] As barry-agent: `bash scripts/keychain_verify.sh` shows discord-bot-token
- [ ] keychain_setup.sh + keychain_verify.sh ITEMS arrays updated to include discord-bot-token
- [ ] **AC1 ✓**: token retrievable from barry-agent keychain

## Task 5: Write the bot — Claude action

- [ ] `agents/discord_bot/__init__.py` created
- [ ] `agents/discord_bot/config.py` created with GUILD_ID + channel IDs
- [ ] `agents/discord_bot/cogs/__init__.py` created
- [ ] `agents/discord_bot/cogs/system.py` created with startup announcement
- [ ] `agents/discord_bot/run.py` created — entry point, loads cogs, calls `client.run()`
- [ ] All files lint-clean (no unused imports, ruff happy)

## Task 6: Smoke run — user action (as barry-agent)

- [ ] As barry-agent: `cd ~/agents && git pull && uv sync`
- [ ] As barry-agent: `uv run python -m agents.discord_bot.run` starts
- [ ] Console shows "Logged in as ..."
- [ ] Bot avatar appears Online in AFC Richmond member list
- [ ] Startup message posted to `#system` channel
- [ ] After 60s, bot still online (no spontaneous disconnect)
- [ ] Ctrl+C exits cleanly (no traceback, exit 0)
- [ ] **AC3-AC8 ✓**: bot wiring verified end-to-end

## Done

- [ ] All 8 acceptance criteria checked
- [ ] Final commit: "Phase 3.1: Discord bot skeleton online"
- [ ] Pushed to GitHub
- [ ] Ready to start Phase 3.2 (Capture flow)

## Notes / issues encountered

- 2026-05-19: Discord Developer Portal "Installation Contexts" defaults to User Install only in newer UIs; required toggling Guild Install on before OAuth URL would work.
- 2026-05-19: "Private application cannot have a default authorization link" error meant Install Link in Installation tab had to be set to "None" before Public Bot OFF would save.

-

-
