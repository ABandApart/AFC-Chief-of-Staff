# Phase 3.1 Checklist — Discord Bot Skeleton — COMPLETE

Final state of sub-phase 3.1. All 8 acceptance criteria satisfied.
Each item maps to an AC in `architecture/PRD-phase-3.1.md`.

Runtime-side tasks (keychain, pull, smoke run) were executed by the
barry-agent monitoring agent via the shared coordination file
`/Users/Shared/afc-richmond/PHASE-3.1.md` (2026-06-16).

## Pre-flight

- [x] Phase 2 complete (commit `e46eee9`)
- [x] Discord bot application registered (app id 150637955019020706)
- [x] Bot installed to AFC Richmond server via OAuth Guild Install
- [x] Privileged intents enabled (MESSAGE CONTENT, SERVER MEMBERS)
- [x] Sub-phase 3.1 PRD reviewed

## Task 1: Create the 6 channels in AFC Richmond — user action

- [x] `#briefing` created
- [x] `#task-tinder` created
- [x] `#approvals` created
- [x] `#capture` created
- [x] `#system` created  ← 3.1 posts to this one
- [x] `#archive` created

## Task 2: Capture IDs — user action

- [x] Developer Mode enabled
- [x] Server ID captured (1499781130306588802)
- [x] All 6 channel IDs captured
- [x] IDs baked into `config.py`

## Task 3: Add discord.py dep — Claude action

- [x] `discord.py>=2.4.0,<3.0` added to pyproject.toml (installed 2.7.1)
- [x] `uv sync` succeeds
- [x] `uv.lock` updated
- [x] **AC2 ✓**: `discord.py` import works (version 2.7.1)

## Task 4: discord-bot-token into barry-agent keychain — barry-agent action

> Plan deviation: the original `sudo -u barry-agent security ...` staging
> from barry-admin failed ("A keychain cannot be found to store barry-agent"
> — keychain search path doesn't resolve under sudo). Resolved by writing
> the token **directly** in the barry-agent session with `security
> add-generic-password` (no sudo). The earlier "ghost entry" was a
> `dump-keychain` double-count artifact, not a real blocker.

- [x] barry-agent wrote `discord-bot-token` directly to its own login keychain
- [x] Value verified via `security find-generic-password -s discord-bot-token -w`
- [x] keychain_setup.sh + keychain_verify.sh ITEMS arrays updated (builder-side, committed `52b2c5a`)
- [x] As barry-agent: `keychain_verify.sh` → 9/9 required present incl. discord-bot-token
- [x] **AC1 ✓**: token retrievable from barry-agent keychain

## Task 5: Write the bot — Claude action

- [x] `agents/discord_bot/__init__.py` created
- [x] `agents/discord_bot/config.py` created with GUILD_ID + 6 channel IDs
- [x] `agents/discord_bot/cogs/__init__.py` created
- [x] `agents/discord_bot/cogs/system.py` created with startup announcement (dedup on reconnect)
- [x] `agents/discord_bot/run.py` created — entry point, async setup_hook loads cogs, bot.run()
- [x] All files import-clean

## Task 6: Smoke run — barry-agent action

> Plan deviation: barry-agent's git auth required fixing first. The shared
> SSH key is passphrase-protected (autonomous agent can't `ssh-add`), so
> barry-agent switched to HTTPS via `gh auth login` (H1, completed by Barry)
> + `gh auth setup-git`. Remote for `~/agents` is now HTTPS.

- [x] barry-agent git auth established (HTTPS + gh; remote switched from SSH)
- [x] As barry-agent: `git pull` fast-forwarded `f989158 → 52b2c5a`; `uv sync` OK
- [x] `uv run python -m agents.discord_bot.run` started
- [x] Console showed "Logged in as AFC Richmond CoS#3495 (id: 1506379550190207066)"
- [x] Bot appeared Online in AFC Richmond; connected to exactly 1 guild
- [x] Startup message posted to `#system` (msg id 1506399724578537473)
- [x] Token valid (no LoginFailure; H4 not needed)
- [x] Ctrl+C exits cleanly (verified via real PTY — "Bot shutting down...")
- [x] **AC3-AC8 ✓**: bot wiring verified end-to-end

## Done

- [x] All 8 acceptance criteria checked
- [ ] Final commit: "Phase 3.1: Discord bot skeleton online"  ← this commit
- [ ] Pushed to GitHub
- [ ] Ready to start Phase 3.2 (Capture flow)

## Notes / issues encountered

- 2026-05-19: Discord Developer Portal "Installation Contexts" defaults to User Install only in newer UIs; required toggling Guild Install on before OAuth URL would work.
- 2026-05-19: "Private application cannot have a default authorization link" error meant Install Link in Installation tab had to be set to "None" before Public Bot OFF would save.
- 2026-06-16: Cross-profile keychain staging via `sudo -u barry-agent` does not work (keychain search-path failure). The durable pattern is: barry-agent writes its own credentials directly in its own session. Updated the coordination playbook accordingly.
- 2026-06-16: barry-agent SSH key is passphrase-protected → autonomous agents must use HTTPS+gh for git. barry-agent's `~/agents` remote switched SSH → HTTPS.
- 2026-06-16: **Carry-forward to Phase 3.5 (launchd):** `run.py` has no SIGTERM handler. discord.py 2.7.1's `bot.run()` handles SIGINT (Ctrl+C) gracefully but launchd stops services with SIGTERM, which currently terminates the bot abruptly with no graceful close/log. Add an explicit SIGTERM/SIGINT handler (and remove the now-dead `except KeyboardInterrupt` branch) as the first task of 3.5, before wiring the KeepAlive plist.
