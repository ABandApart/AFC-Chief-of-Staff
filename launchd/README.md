# launchd jobs (Phase 3.5)

Three user LaunchAgents, all installed in **barry-agent's** session (the
keychain that holds `discord-bot-token` / `db-url` is only unlocked in a
logged-in user session — a system LaunchDaemon can't read it; Fast User
Switching keeps the session alive).

| Plist | What | When |
|-------|------|------|
| `com.aiadaptive.cos.discord-bot.plist` | Discord bot, supervised | RunAtLoad; relaunch on crash (clean exit 0 stays down) |
| `com.aiadaptive.cos.briefing.plist` | Briefing skeleton → #briefing | 6:00 local daily |
| `com.aiadaptive.cos.pg-backup.plist` | `scripts/pg_backup.sh` → `~/agents/backups/nightly/` | 2:00 local daily, keep 14 |

## Install (barry-agent)

```bash
cd ~/agents
mkdir -p logs backups/nightly
chmod +x scripts/pg_backup.sh
cp launchd/*.plist ~/Library/LaunchAgents/

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.aiadaptive.cos.discord-bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.aiadaptive.cos.briefing.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.aiadaptive.cos.pg-backup.plist

launchctl list | grep com.aiadaptive   # all three present; bot has a PID
```

Stop / start the bot without unloading:

```bash
launchctl kill SIGTERM gui/$(id -u)/com.aiadaptive.cos.discord-bot  # clean exit 0 → stays down
launchctl kickstart gui/$(id -u)/com.aiadaptive.cos.discord-bot
```

Uninstall a job:

```bash
launchctl bootout gui/$(id -u)/com.aiadaptive.cos.discord-bot
```

## Notes

- Plists run `/bin/zsh -lc '…'` so `uv` resolves from the login PATH —
  launchd's own environment is nearly empty.
- If a plist changes, `bootout` then `bootstrap` again (launchd caches).
- Job output lands in `~/agents/logs/*.log`; check there first when a
  scheduled run didn't happen.
- The bot plist's `KeepAlive.SuccessfulExit=false` pairs with `run.py`'s
  SIGTERM handler: crashes (non-zero exit) relaunch, clean shutdowns don't.
